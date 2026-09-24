"""Unit-Tests für `DatexIIGermanyConstructionProvider` (NRW Mobilitätsdaten, DE).

Default DE construction-zone provider (see module docstring in
`providers_de_datexii.py`). Fetches and combines the long-duration ("ld")
and short-duration ("kd") Autobahn Arbeitsstellen DATEX II feeds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from tripplanner.construction.models import ClosureType, Land
from tripplanner.construction.providers_de_datexii import (
    NRW_ARBEITSSTELLEN_KD_URL,
    NRW_ARBEITSSTELLEN_LD_URL,
    DatexIIGermanyConstructionProvider,
)
from tripplanner.routing.models import Route, RouteSegment

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "construction"
NRW_XML = (FIXTURES_DIR / "nrw_arbeitsstellen_autobahn_example.xml").read_text()
EMPTY_FEED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<d2LogicalModel xmlns="http://datex2.eu/schema/2/2_0"><payloadPublication '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="SituationPublication">'
    "</payloadPublication></d2LogicalModel>"
)


def _make_route_near_maintenance_zone() -> Route:
    """Route whose single segment sits directly on the fixture's MaintenanceWorks zone."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(51.210673, 14.553138), (51.211490, 14.567740)],
        length_m=1000.0,
        strassenklasse="MOTORWAY",
        speed_limit_kmh=100,
        bearing_deg=45.0,
    )
    return Route(
        segments=[segment],
        gesamtlaenge_m=1000.0,
        geometrie=segment.geometrie,
    )


def _make_route_far_from_any_zone() -> Route:
    """Route far from both fixture zones."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(10.0, 10.0), (10.01, 10.01)],
        length_m=1000.0,
        strassenklasse="MOTORWAY",
        speed_limit_kmh=100,
        bearing_deg=45.0,
    )
    return Route(
        segments=[segment],
        gesamtlaenge_m=1000.0,
        geometrie=segment.geometrie,
    )


def _make_provider(cache_dir: str | None = None) -> DatexIIGermanyConstructionProvider:
    """Erstelle Provider-Instanz mit gemocktem httpx-Client."""
    provider = DatexIIGermanyConstructionProvider(cache_dir=cache_dir or tempfile.mkdtemp())
    provider._client = MagicMock(spec=httpx.AsyncClient)
    provider._client.get = AsyncMock()
    return provider


def _xml_response(text: str) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.text = text
    return resp


class TestFetchConstructionZones:
    """Tests für `fetch_construction_zones`."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_de_not_requested(self) -> None:
        """Wird DE nicht angefragt, liefert der Provider `[]` ohne HTTP-Call."""
        provider = _make_provider()
        route = _make_route_near_maintenance_zone()

        zones = await provider.fetch_construction_zones(route, [Land.DK])

        assert zones == []
        provider._client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetches_both_feeds_and_matches_zone_near_route(self) -> None:
        """Beide Feeds (ld+kd) werden abgefragt; eine nahe Zone wird gematcht."""
        provider = _make_provider()
        route = _make_route_near_maintenance_zone()

        async def get_side(url: str, **kw: object) -> MagicMock:
            if url == NRW_ARBEITSSTELLEN_LD_URL:
                return _xml_response(NRW_XML)
            if url == NRW_ARBEITSSTELLEN_KD_URL:
                return _xml_response(EMPTY_FEED_XML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._client.get = AsyncMock(side_effect=get_side)

        zones = await provider.fetch_construction_zones(route, [Land.DE])

        assert len(zones) == 1
        zone = zones[0]
        assert zone.land == Land.DE
        assert zone.betroffene_segmente == [0]
        assert zone.closure_type == ClosureType.PARTIALLY_CLOSED  # MaintenanceWorks
        assert zone.speed_limit_kmh == 80  # no delayBand in feed -> default
        assert zone.gueltig_von.isoformat() == "2024-11-18T07:00:00+00:00"
        assert zone.gueltig_bis is not None
        assert provider._client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_zone_far_from_route_is_not_matched(self) -> None:
        """Eine Zone weit weg von jedem Route-Segment wird nicht zurückgegeben."""
        provider = _make_provider()
        route = _make_route_far_from_any_zone()
        provider._client.get = AsyncMock(return_value=_xml_response(NRW_XML))

        zones = await provider.fetch_construction_zones(route, [Land.DE])

        assert zones == []

    @pytest.mark.asyncio
    async def test_construction_works_type_maps_to_partially_closed(self) -> None:
        """Der zweite Fixture-Datensatz (ConstructionWorks) mappt auf PARTIALLY_CLOSED."""
        provider = _make_provider()
        segment = RouteSegment(
            segment_index=0,
            geometrie=[(48.357943, 7.797467), (48.357985, 7.797550)],
            length_m=1000.0,
            strassenklasse="MOTORWAY",
            speed_limit_kmh=100,
            bearing_deg=45.0,
        )
        route = Route(segments=[segment], gesamtlaenge_m=1000.0, geometrie=segment.geometrie)

        async def get_side(url: str, **kw: object) -> MagicMock:
            if url == NRW_ARBEITSSTELLEN_LD_URL:
                return _xml_response(NRW_XML)
            return _xml_response(EMPTY_FEED_XML)

        provider._client.get = AsyncMock(side_effect=get_side)

        zones = await provider.fetch_construction_zones(route, [Land.DE])

        assert len(zones) == 1
        assert zones[0].closure_type == ClosureType.PARTIALLY_CLOSED


class TestFeedCache:
    """Tests for `_fetch_feed` caching (raw XML text)."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_http_call(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Repeat fetch of the same feed URL skips HTTP via cache hit."""
        provider = _make_provider(cache_dir=str(tmp_path))
        provider._client.get = AsyncMock(return_value=_xml_response(EMPTY_FEED_XML))

        xml1 = await provider._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_ld")
        assert xml1 == EMPTY_FEED_XML
        assert provider._client.get.call_count == 1

        xml2 = await provider._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_ld")
        assert xml2 == EMPTY_FEED_XML
        assert provider._client.get.call_count == 1  # unchanged

        with caplog.at_level("DEBUG"):
            await provider._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_ld")
        assert "cache hit" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_different_cache_keys_trigger_separate_calls(self, tmp_path: Path) -> None:
        """Ld and kd feeds are cached independently."""
        provider = _make_provider(cache_dir=str(tmp_path))
        provider._client.get = AsyncMock(return_value=_xml_response(EMPTY_FEED_XML))

        await provider._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_ld")
        await provider._fetch_feed(NRW_ARBEITSSTELLEN_KD_URL, "nrw_kd")
        await provider._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_ld")

        assert provider._client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_http_error_not_cached(self, tmp_path: Path) -> None:
        """Failed HTTP response is never cached — next call retries."""
        provider = _make_provider(cache_dir=str(tmp_path))
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=resp
        )
        provider._client.get = AsyncMock(return_value=resp)

        with pytest.raises(httpx.HTTPStatusError):
            await provider._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_ld")
        with pytest.raises(httpx.HTTPStatusError):
            await provider._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_ld")

        assert provider._client.get.call_count == 2


class TestConstructorAndLifecycle:
    """Tests für Konstruktion (eager client / injizierter client) und Lifecycle."""

    def test_default_client_is_created(self) -> None:
        """Ohne expliziten Client wird ein neuer erstellt."""
        provider = _make_provider()
        assert provider._client is not None

    @pytest.mark.asyncio
    async def test_close_closes_http_client(self) -> None:
        """Schließt den HTTP-Client beim Beenden."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.aclose = AsyncMock()
        provider = DatexIIGermanyConstructionProvider(client=mock_client)

        await provider.close()

        mock_client.aclose.assert_awaited_once()
