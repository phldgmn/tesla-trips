"""Unit-Tests für `AutobahnConstructionProvider` (Autobahn GmbH open API, DE).

Relocated from `test_providers_impl.py` when DE was extracted into its own
provider (`providers_de_autobahn.py`), superseded by
`DatexIIGermanyConstructionProvider` as the default DE provider but kept for
revert (see `providers_de_datexii.py`).
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tripplanner.construction.matching import build_strtree, nearest_segment_index
from tripplanner.construction.models import Land, Sperrungstyp
from tripplanner.construction.providers_de_autobahn import (
    AutobahnConstructionProvider,
    _extract_autobahn_ids,
    _parse_autobahn_roadwork,
)
from tripplanner.routing.models import Route, RouteSegment


def _make_route() -> Route:
    """Erstelle eine einfache Test-Route mit einem PRIMARY-Segment (keine Autobahn)."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.52, 13.405), (52.522, 13.407)],
        laenge_m=150.0,
        strassenklasse="PRIMARY",
        tempolimit_kmh=100,
        bearing_deg=35.0,
    )
    return Route(
        segments=[segment],
        gesamtlaenge_m=150.0,
        geometrie=segment.geometrie,
    )


def _make_motorway_route(strassenref: str | None = "A 5") -> Route:
    """Create a test route with a single MOTORWAY segment, for DE Autobahn tests."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(50.0000, 9.0000), (50.0100, 9.0100)],
        laenge_m=1500.0,
        strassenklasse="MOTORWAY",
        tempolimit_kmh=100,
        bearing_deg=45.0,
        strassenref=strassenref,
    )
    return Route(
        segments=[segment],
        gesamtlaenge_m=1500.0,
        geometrie=segment.geometrie,
    )


def _make_provider(cache_dir: str | None = None) -> AutobahnConstructionProvider:
    """Erstelle Provider-Instanz mit gemocktem httpx-Client."""
    provider = AutobahnConstructionProvider(cache_dir=cache_dir or tempfile.mkdtemp())
    provider._client = MagicMock(spec=httpx.AsyncClient)
    provider._client.get = AsyncMock()
    return provider


def _sample_autobahn_entry(**overrides: Any) -> dict[str, Any]:
    """A sample Autobahn GmbH `roadworks[]` JSON entry (verified live response shape)."""
    entry: dict[str, Any] = {
        "coordinate": {
            "lat": 50.0010,
            "long": 9.0010,
            "geometry": {"type": "Point", "coordinates": [9.0000, 50.0000, 0.0]},
        },
        "impact": {"symbols": ["ARROW_DOWN"]},
        "display_type": "ROADWORKS",
        "startTimestamp": "2024-06-01T08:00:00+02:00",
        "description": ["Bauarbeiten auf der A9"],
    }
    for key, value in overrides.items():
        if "." in key:
            parts = key.split(".")
            d = entry
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value
        else:
            entry[key] = value
    return entry


def _build_strtree(route: Route):
    """Helper: build STRtree and segment_geoms for a test route."""
    return build_strtree(route.segments)


class TestExtractAutobahnIds:
    """Tests für `_extract_autobahn_ids`."""

    def test_extracts_id_from_motorway_segment_with_strassenref(self) -> None:
        """MOTORWAY-Segment mit strassenref 'A 5' liefert normalisiert 'A5' als ID."""
        route = _make_motorway_route(strassenref="A 5")
        assert _extract_autobahn_ids(route) == {"A5"}

    def test_extracts_id_from_strassenref_without_space(self) -> None:
        """strassenref 'A9' (ohne Leerzeichen) wird korrekt extrahiert."""
        route = _make_motorway_route(strassenref="A9")
        assert _extract_autobahn_ids(route) == {"A9"}

    def test_extracts_id_from_descriptive_ref(self) -> None:
        """Autobahn-ID wird auch aus einem beschreibenden Ref extrahiert."""
        seg1 = RouteSegment(
            segment_index=0,
            geometrie=[(50.0, 9.0), (50.01, 9.01)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenref="Bundesautobahn A 9",
            bearing_deg=10.0,
        )
        route = Route(
            segments=[seg1],
            gesamtlaenge_m=500.0,
            geometrie=seg1.geometrie,
        )
        assert _extract_autobahn_ids(route) == {"A9"}

    def test_skips_non_motorway_segments(self) -> None:
        """Nicht-MOTORWAY-Segmente werden ignoriert."""
        assert _extract_autobahn_ids(_make_route()) == set()

    def test_skips_motorway_segment_without_strassenref(self) -> None:
        """MOTORWAY-Segment ohne strassenref wird übersprungen."""
        route = _make_motorway_route(strassenref=None)
        assert _extract_autobahn_ids(route) == set()

    def test_deduplicates_multiple_segments_same_autobahn(self) -> None:
        """Mehrere Segmente derselben Autobahn liefern nur eine ID."""
        seg1 = RouteSegment(
            segment_index=0,
            geometrie=[(50.0, 9.0), (50.01, 9.01)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenref="A 5",
            bearing_deg=10.0,
        )
        seg2 = RouteSegment(
            segment_index=1,
            geometrie=[(50.01, 9.01), (50.02, 9.02)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenref="A 5",
            bearing_deg=10.0,
        )
        route = Route(
            segments=[seg1, seg2],
            gesamtlaenge_m=1000.0,
            geometrie=seg1.geometrie + seg2.geometrie,
        )
        assert _extract_autobahn_ids(route) == {"A5"}

    def test_extract_multiple_different_autobahns(self) -> None:
        """Verschiedene Autobahnen auf derselben Route ergeben mehrere IDs."""
        seg1 = RouteSegment(
            segment_index=0,
            geometrie=[(50.0, 9.0), (50.01, 9.01)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenref="A 5",
            bearing_deg=10.0,
        )
        seg2 = RouteSegment(
            segment_index=1,
            geometrie=[(50.01, 9.01), (50.02, 9.02)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenref="A 9",
            bearing_deg=10.0,
        )
        seg3 = RouteSegment(
            segment_index=2,
            geometrie=[(50.02, 9.02), (50.03, 9.03)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenref="A 5",
            bearing_deg=10.0,
        )
        route = Route(
            segments=[seg1, seg2, seg3],
            gesamtlaenge_m=1500.0,
            geometrie=seg1.geometrie + seg2.geometrie + seg3.geometrie,
        )
        assert _extract_autobahn_ids(route) == {"A5", "A9"}

    def test_skips_strassenref_without_a_prefix(self) -> None:
        """Non-Autobahn refs (B, K, L) werden nicht extrahiert."""
        seg1 = RouteSegment(
            segment_index=0,
            geometrie=[(50.0, 9.0), (50.01, 9.01)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenref="B 3",
            bearing_deg=10.0,
        )
        route = Route(
            segments=[seg1],
            gesamtlaenge_m=500.0,
            geometrie=seg1.geometrie,
        )
        assert _extract_autobahn_ids(route) == set()


class TestNearestSegmentIndex:
    """Tests für `matching.nearest_segment_index`."""

    def test_finds_nearest_segment(self) -> None:
        """Findet den Index des nächstgelegenen Segments per Haversine."""
        route = _make_motorway_route()
        idx, dist = nearest_segment_index((50.0050, 9.0050), route.segments)
        assert idx == 0
        assert dist < 1000.0

    def test_empty_segments_returns_none(self) -> None:
        """Leere Segmentliste liefert (None, inf)."""
        idx, dist = nearest_segment_index((50.0, 9.0), [])
        assert idx is None
        assert dist == float("inf")


class TestParseAutobahnRoadwork:
    """Tests für `_parse_autobahn_roadwork`."""

    def test_parses_matching_entry(self) -> None:
        """Ein Roadwork-Entry nahe der Route wird korrekt zu ConstructionZone gemappt."""
        route = _make_motorway_route()
        zone = _parse_autobahn_roadwork(_sample_autobahn_entry(), route)

        assert zone is not None
        assert zone.land == Land.DE
        assert zone.betroffene_segmente == [0]
        assert zone.sperrungstyp == Sperrungstyp.TEMPORARY_SPEED_LIMIT
        assert zone.tempolimit_kmh == 80
        assert zone.gueltig_von == datetime.fromisoformat("2024-06-01T08:00:00+02:00")
        assert zone.gueltig_bis is None
        assert zone.umleitungshinweis is None

    def test_closed_symbol_maps_to_partially_closed(self) -> None:
        """impact.symbols mit 'CLOSED' mappt auf PARTIALLY_CLOSED."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry(impact={"symbols": ["CLOSED", "ARROW_DOWN"]})

        zone = _parse_autobahn_roadwork(entry, route)

        assert zone is not None
        assert zone.sperrungstyp == Sperrungstyp.PARTIALLY_CLOSED
        assert zone.tempolimit_kmh == 80

    def test_missing_start_timestamp_falls_back_to_now(self) -> None:
        """Ohne startTimestamp wird die aktuelle Zeit (UTC) als gueltig_von genutzt."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry()
        del entry["startTimestamp"]

        before = datetime.now(UTC)
        zone = _parse_autobahn_roadwork(entry, route)
        after = datetime.now(UTC)

        assert zone is not None
        assert before <= zone.gueltig_von <= after

    def test_entry_far_from_route_is_discarded(self) -> None:
        """Ein Entry weit weg von jedem Route-Segment liefert None."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry(coordinate={"lat": 10.0, "long": 10.0})

        assert _parse_autobahn_roadwork(entry, route) is None

    def test_missing_coordinate_returns_none(self) -> None:
        """Fehlendes coordinate-Feld liefert None statt einer Exception."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry()
        del entry["coordinate"]

        assert _parse_autobahn_roadwork(entry, route) is None

    def test_roadwork_within_threshold_but_not_at_vertex_matches(self) -> None:
        """DE roadwork within the shared distance threshold but off-vertex should match."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry(coordinate={"lat": 50.0015, "long": 9.0})

        zone = _parse_autobahn_roadwork(entry, route)

        assert zone is not None
        assert zone.betroffene_segmente == [0]

    def test_roadwork_beyond_threshold_returns_none(self) -> None:
        """DE roadwork beyond the shared distance threshold returns None."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry(coordinate={"lat": 52.0, "long": 13.0})

        assert _parse_autobahn_roadwork(entry, route) is None


class TestFetchDeRoadworks:
    """Tests für `_fetch_de_roadworks` (Autobahn GmbH JSON-Pfad)."""

    @pytest.mark.asyncio
    async def test_fetches_roadworks_for_extracted_ids_only(self) -> None:
        """Holt nur Roadworks für die aus strassenref extrahierten IDs — keine Liste."""
        provider = _make_provider()
        route = _make_motorway_route(strassenref="A 5")

        # Mock roadworks response (no list endpoint call anymore)
        sample_entry = _sample_autobahn_entry()
        roadworks_resp = MagicMock(spec=httpx.Response)
        roadworks_resp.raise_for_status = MagicMock()
        roadworks_resp.json = MagicMock(return_value={"roadworks": [sample_entry]})

        async def get_side(url: str, **kw: Any):
            return roadworks_resp

        provider._client.get = AsyncMock(side_effect=get_side)

        strtree, seg_geoms = _build_strtree(route)

        zones = await provider._fetch_de_roadworks(route, strtree, seg_geoms)

        assert len(zones) == 1
        for z in zones:
            assert z.land == Land.DE
        # Verify no list endpoint was called
        calls = provider._client.get.call_args_list
        assert len(calls) == 1  # Only 1 roadworks call, not a list + roadworks
        assert "services/roadworks" in str(calls[0])

    @pytest.mark.asyncio
    async def test_requests_url_without_space_for_strassenref_with_space(self) -> None:
        """strassenref 'A 5' (mit Leerzeichen) darf die API-URL nicht mit Leerzeichen aufrufen.

        Live-verifiziert: `.../A%205/services/roadworks` (URL-kodiertes
        Leerzeichen) liefert HTTP 200 mit `{"roadworks": []}` statt eines
        Fehlers — ein stillschweigend leeres Ergebnis statt eines erkennbaren
        Fehlschlags. Die ID muss daher vor dem Request normalisiert werden.
        """
        provider = _make_provider()
        route = _make_motorway_route(strassenref="A 5")

        roadworks_resp = MagicMock(spec=httpx.Response)
        roadworks_resp.raise_for_status = MagicMock()
        roadworks_resp.json = MagicMock(return_value={"roadworks": [_sample_autobahn_entry()]})
        provider._client.get = AsyncMock(return_value=roadworks_resp)

        strtree, seg_geoms = _build_strtree(route)
        await provider._fetch_de_roadworks(route, strtree, seg_geoms)

        requested_url = str(provider._client.get.call_args_list[0])
        assert "A5" in requested_url
        assert "A 5" not in requested_url
        assert "A%205" not in requested_url

    @pytest.mark.asyncio
    async def test_no_zones_when_no_motorway_segments(self) -> None:
        """PRIMARY-Route ohne MOTORWAY-Segmente: keine API-Calls."""
        provider = _make_provider()
        route = _make_route()

        strtree, seg_geoms = _build_strtree(route)

        zones = await provider._fetch_de_roadworks(route, strtree, seg_geoms)

        assert zones == []
        provider._client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_zones_when_strassenref_is_none(self) -> None:
        """MOTORWAY ohne strassenref: keine API-Calls."""
        provider = _make_provider()
        route = _make_motorway_route(strassenref=None)

        strtree, seg_geoms = _build_strtree(route)

        zones = await provider._fetch_de_roadworks(route, strtree, seg_geoms)

        assert zones == []
        provider._client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_request_failure_for_one_id_does_not_abort_others(self) -> None:
        """Fehler bei einer Autobahn-ID bricht die anderen nicht ab."""
        provider = _make_provider()
        # Route with TWO motorway segments: A 5 and A 9
        route = Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[(50.0, 9.0), (50.01, 9.01)],
                    laenge_m=1500.0,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=100,
                    bearing_deg=45.0,
                    strassenref="A 5",
                ),
                RouteSegment(
                    segment_index=1,
                    geometrie=[(50.01, 9.01), (50.02, 9.02)],
                    laenge_m=1500.0,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=100,
                    bearing_deg=45.0,
                    strassenref="A 9",
                ),
            ],
            gesamtlaenge_m=3000.0,
            geometrie=[(50.0, 9.0), (50.01, 9.01), (50.02, 9.02)],
        )

        # A 9 succeeds
        success_entry = _sample_autobahn_entry()
        success_resp = MagicMock(spec=httpx.Response)
        success_resp.raise_for_status = MagicMock()
        success_resp.json = MagicMock(return_value={"roadworks": [success_entry]})

        # A 5 fails
        fail_resp = MagicMock(spec=httpx.Response)
        fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=fail_resp
        )

        async def get_side(url: str, **kw: Any):
            if "A5" in url:
                raise fail_resp
            return success_resp

        provider._client.get = AsyncMock(side_effect=get_side)

        strtree, seg_geoms = _build_strtree(route)

        zones = await provider._fetch_de_roadworks(route, strtree, seg_geoms)

        # A 5 fails, A 9 succeeds → only A 9 zones
        assert len(zones) == 1
        assert zones[0].land == Land.DE


class TestFetchConstructionZonesPublicApi:
    """Tests für `fetch_construction_zones` (public entry point)."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_de_not_requested(self) -> None:
        """Wird DE nicht angefragt, liefert der Provider sofort `[]` ohne HTTP-Call."""
        provider = _make_provider()
        route = _make_motorway_route()

        zones = await provider.fetch_construction_zones(route, [Land.DK])

        assert zones == []
        provider._client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_builds_own_strtree_and_returns_de_zones(self) -> None:
        """`fetch_construction_zones([Land.DE])` baut den eigenen STRtree und liefert Zonen."""
        provider = _make_provider()
        route = _make_motorway_route(strassenref="A 5")

        roadworks_resp = MagicMock(spec=httpx.Response)
        roadworks_resp.raise_for_status = MagicMock()
        roadworks_resp.json = MagicMock(return_value={"roadworks": [_sample_autobahn_entry()]})
        provider._client.get = AsyncMock(return_value=roadworks_resp)

        zones = await provider.fetch_construction_zones(route, [Land.DE])

        assert len(zones) == 1
        assert zones[0].land == Land.DE


class TestAutobahnCache:
    """Tests for `_fetch_autobahn_roadworks` caching."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_http_call(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Repeat call with same autobahn_id skips HTTP via cache hit."""
        provider = _make_provider(cache_dir=str(tmp_path))

        entry = _sample_autobahn_entry()
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"roadworks": [entry]})
        provider._client.get.return_value = resp

        # First call — misses cache, makes HTTP call
        zones1 = await provider._fetch_autobahn_roadworks("A5")
        assert len(zones1) == 1
        assert provider._client.get.call_count == 1

        # Second call — cache hit, no HTTP
        zones2 = await provider._fetch_autobahn_roadworks("A5")
        assert len(zones2) == 1
        assert provider._client.get.call_count == 1  # unchanged

        with caplog.at_level("DEBUG"):
            await provider._fetch_autobahn_roadworks("A5")
        assert "cache hit" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_different_autobahn_ids_trigger_new_calls(self, tmp_path: Path) -> None:
        """Different autobahn_id triggers a new HTTP call."""
        provider = _make_provider(cache_dir=str(tmp_path))

        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"roadworks": [_sample_autobahn_entry()]})
        provider._client.get.return_value = resp

        await provider._fetch_autobahn_roadworks("A5")
        await provider._fetch_autobahn_roadworks("A9")
        await provider._fetch_autobahn_roadworks("A5")

        assert provider._client.get.call_count == 2  # A5 miss, A9 miss, A5 cache hit

    @pytest.mark.asyncio
    async def test_http_error_not_cached(self, tmp_path: Path) -> None:
        """Failed HTTP response is never cached — next call retries."""
        provider = _make_provider(cache_dir=str(tmp_path))

        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=resp
        )
        provider._client.get.return_value = resp

        with pytest.raises(httpx.HTTPStatusError):
            await provider._fetch_autobahn_roadworks("A5")

        # Second call — cache miss, retries HTTP
        with pytest.raises(httpx.HTTPStatusError):
            await provider._fetch_autobahn_roadworks("A5")

        assert provider._client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_result_is_cacheable(self, tmp_path: Path) -> None:
        """Empty-but-successful result (zero roadworks) is cached."""
        provider = _make_provider(cache_dir=str(tmp_path))

        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"roadworks": []})
        provider._client.get.return_value = resp

        await provider._fetch_autobahn_roadworks("A5")
        assert provider._client.get.call_count == 1

        # Repeated calls — cache hit
        await provider._fetch_autobahn_roadworks("A5")
        await provider._fetch_autobahn_roadworks("A5")
        assert provider._client.get.call_count == 1


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
        provider = AutobahnConstructionProvider(client=mock_client)

        await provider.close()

        mock_client.aclose.assert_awaited_once()


class TestDeDirectionLimitation:
    """DE roadworks (point-only, no direction data) are unaffected by direction filtering.

    Relocated from `test_direction_matching.py`: no direction data is
    available for DE, so proximity-only matching applies regardless of which
    carriageway the roadwork is actually on — a documented data-source
    limitation of the Autobahn GmbH API (point coordinate only, no
    LineString/direction field).
    """

    def test_de_roadwork_opposite_side_of_road_still_matches(self) -> None:
        """No direction data available for DE -> proximity-only matching still applies."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry(coordinate={"lat": 50.0005, "long": 9.0005})

        zone = _parse_autobahn_roadwork(entry, route)

        assert zone is not None
        assert zone.betroffene_segmente == [0]


class TestDeLaengeM:
    """`laenge_m` derivation for DE: matched-segment span (no LineString geometry).

    Relocated from `test_direction_matching.py`.
    """

    def test_de_roadwork_laenge_m_from_matched_segment_span(self) -> None:
        """DE (point-only) derives laenge_m from the matched route segment's own length."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry(coordinate={"lat": 50.0005, "long": 9.0005})

        zone = _parse_autobahn_roadwork(entry, route)

        assert zone is not None
        assert zone.laenge_m == pytest.approx(route.segments[0].laenge_m)

    def test_de_roadwork_no_match_has_no_laenge_m(self) -> None:
        """A DE roadwork beyond the distance threshold yields no zone (and no laenge_m)."""
        route = _make_motorway_route()
        entry = _sample_autobahn_entry(coordinate={"lat": 52.0, "long": 13.0})

        assert _parse_autobahn_roadwork(entry, route) is None
