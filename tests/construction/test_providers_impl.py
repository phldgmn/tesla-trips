"""Unit-Tests für `ConstructionProviderImpl`: Parameter-Builder, HTTP-Transport,
XML-Parsing-Integration, Autobahn-GmbH-Integration (DE) und Lifecycle."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tripplanner.construction.models import ConstructionZone, Land, Sperrungstyp
from tripplanner.construction.parser import DATEXIIConstructionZoneInternal
from tripplanner.construction.providers import (
    AUTOBAHN_BASE_URL,
    ConstructionProviderConfig,
    ConstructionProviderImpl,
    FakeConstructionProvider,
    _extract_autobahn_ids,
    _nearest_segment_index,
    _parse_autobahn_roadwork,
)
from tripplanner.routing.models import Route, RouteSegment

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "construction"

VALID_DK_XML = (FIXTURES_DIR / "datexii_denmark_lane_closure.xml").read_text()
VALID_SE_XML = (FIXTURES_DIR / "datexii_sweden_temp_limit.xml").read_text()


def _make_route() -> Route:
    """Erstelle eine einfache Test-Route mit einem PRIMARY-Segment."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.5200, 13.4050), (52.5210, 13.4060), (52.5220, 13.4070)],
        laenge_m=150.0,
        strassenklasse="PRIMARY",
        tempolimit_kmh=100,
        bearing_deg=35.0,
    )
    return Route(
        segments=[segment],
        gesamtlaenge_m=150.0,
        geometrie=[(52.5200, 13.4050), (52.5210, 13.4060), (52.5220, 13.4070)],
    )


def _make_motorway_route(strassenname: str | None = "A9") -> Route:
    """Create a test route with a single MOTORWAY segment, for DE Autobahn tests."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(50.0000, 9.0000), (50.0100, 9.0100)],
        laenge_m=1500.0,
        strassenklasse="MOTORWAY",
        strassenname=strassenname,
        bearing_deg=45.0,
    )
    return Route(
        segments=[segment],
        gesamtlaenge_m=1500.0,
        geometrie=segment.geometrie,
    )


def _make_config() -> ConstructionProviderConfig:
    """Erstelle eine Test-Konfiguration mit DK/SE-Credentials."""
    return ConstructionProviderConfig(
        dk_client_id="dk-client",
        dk_secret="dk-secret",
        tv_api_key="se-key",
        timeout_seconds=10.0,
    )


def _make_provider(config: ConstructionProviderConfig | None = None) -> ConstructionProviderImpl:
    """Erstelle Provider-Instanz mit gemocktem httpx-Client."""
    provider = ConstructionProviderImpl(config or _make_config())
    provider._client = MagicMock(spec=httpx.AsyncClient)
    provider._client.get = AsyncMock()
    provider._client.post = AsyncMock()
    return provider


def _sample_autobahn_entry(**overrides: Any) -> dict[str, Any]:
    """A sample Autobahn GmbH `roadworks[]` JSON entry (verified live response shape)."""
    entry: dict[str, Any] = {
        "coordinate": {"lat": 50.0010, "long": 9.0010},
        "geometry": {"type": "LineString", "coordinates": [[9.0000, 50.0000], [9.0100, 50.0100]]},
        "impact": {"symbols": ["ARROW_DOWN"]},
        "display_type": "ROADWORKS",
        "startTimestamp": "2024-06-01T08:00:00+02:00",
        "description": ["Bauarbeiten auf der A9"],
    }
    entry.update(overrides)
    return entry


class TestBuildDkParams:
    """Tests für `_build_dk_params`."""

    def test_start_date_is_iso8601_with_z_suffix(self) -> None:
        """DK startDate ist ISO 8601 mit 'Z' Suffix (UTC)."""
        provider = _make_provider()
        params = provider._build_dk_params(_make_route())

        start_date = params["startDate"]
        assert start_date.endswith("Z")
        datetime.fromisoformat(start_date.replace("Z", "+00:00"))

    def test_contains_coords_and_format(self) -> None:
        """DK-Parameter enthalten coords und format, aber keine Credentials."""
        provider = _make_provider()
        params = provider._build_dk_params(_make_route())

        assert "coords" in params
        assert params["format"] == "datex2"
        assert "key" not in params
        assert "clientid" not in params


class TestBuildSeRequestXml:
    """Tests für `_build_se_request_xml`."""

    def test_contains_authenticationkey_and_query(self) -> None:
        """SE XML-Body enthält LOGIN mit authenticationkey und Situation-Query."""
        config = ConstructionProviderConfig(tv_api_key="se-secret-key")
        provider = _make_provider(config)

        xml_body = provider._build_se_request_xml(_make_route())

        assert "<REQUEST>" in xml_body
        assert 'authenticationkey="se-secret-key"' in xml_body
        assert 'objecttype="Situation"' in xml_body
        assert "WITHIN" in xml_body


class TestRouteToBoundingBox:
    """Tests für `_route_to_bounding_box`."""

    def test_computes_correct_bbox(self) -> None:
        """Bounding Box berechnet min/max aus allen Segment-Koordinaten."""
        provider = _make_provider()
        bbox = provider._route_to_bounding_box(_make_route())

        parts = bbox.split(",")
        assert len(parts) == 4
        assert parts[0] == "52.52"  # min_lat
        assert parts[1] == "13.405"  # min_lon
        assert parts[2] == "52.522"  # max_lat
        assert parts[3] == "13.407"  # max_lon

    def test_empty_route_returns_empty_string(self) -> None:
        """Route ohne Segmente liefert leeren String."""
        provider = _make_provider()
        empty_route = Route(segments=[], gesamtlaenge_m=1.0, geometrie=[])
        bbox = provider._route_to_bounding_box(empty_route)
        assert bbox == ""


class TestZoneToGeometry:
    """Tests für `_zone_to_geometry`."""

    def test_converts_lat_lon_to_linestring(self) -> None:
        """DATEX II (lon, lat) wird zu Shapely LineString (lat, lon) konvertiert."""
        provider = _make_provider()
        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(13.4050, 52.5200), (13.4060, 52.5210)],  # (lon, lat)
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )
        geom = provider._zone_to_geometry(zone)

        assert geom.geom_type == "LineString"
        coords = list(geom.coords)
        assert coords[0] == (52.5200, 13.4050)  # (lat, lon)
        assert coords[1] == (52.5210, 13.4060)


class TestMapToSegmentIds:
    """Tests für `_map_to_segment_ids`."""

    @pytest.mark.asyncio
    async def test_maps_zone_to_segment_when_intersects(self) -> None:
        """Zone, die Route-Segment schneidet, liefert Segment-ID."""
        provider = _make_provider()
        route = _make_route()

        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(13.4050, 52.5200), (13.4060, 52.5210)],  # (lon, lat)
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        ids = await provider._map_to_segment_ids(zone, route)
        assert ids == [0]

    @pytest.mark.asyncio
    async def test_no_intersection_returns_empty_list(self) -> None:
        """Zone weit weg von der Route liefert leere Liste."""
        provider = _make_provider()
        route = _make_route()

        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(9.0, 53.0), (9.1, 53.1)],  # Hamburg, nicht Berlin
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        ids = await provider._map_to_segment_ids(zone, route)
        assert ids == []


class TestMapClosureType:
    """Tests für `_map_closure_type`."""

    @pytest.mark.parametrize(
        "xsi_type,expected",
        [
            ("fullyClosed", Sperrungstyp.FULLY_CLOSED),
            ("partiallyClosed", Sperrungstyp.PARTIALLY_CLOSED),
            ("laneClosed", Sperrungstyp.LANE_CLOSED),
            ("temporarySpeedLimit", Sperrungstyp.TEMPORARY_SPEED_LIMIT),
            ("reducedLanes", Sperrungstyp.REDUCED_LANES),
            ("detrourRequired", Sperrungstyp.DETOUR_REQUIRED),
            ("Roadworks", Sperrungstyp.PARTIALLY_CLOSED),
            ("MaintenanceWorks", Sperrungstyp.PARTIALLY_CLOSED),
            ("UnknownType", Sperrungstyp.PARTIALLY_CLOSED),  # Default-Fallback
        ],
    )
    def test_maps_known_types(self, xsi_type: str, expected: Sperrungstyp) -> None:
        """Bekannte DATEX II xsi:type Werte werden korrekt gemappt."""
        provider = _make_provider()
        assert provider._map_closure_type(xsi_type) == expected


class TestHasCredentials:
    """Tests für `_has_credentials`."""

    def test_de_always_true(self) -> None:
        """DE benötigt keine Credentials (Autobahn GmbH API unauthentifiziert)."""
        provider = _make_provider(ConstructionProviderConfig())
        assert provider._has_credentials(Land.DE) is True

    def test_dk_true_when_both_present(self) -> None:
        """DK erfordert dk_client_id und dk_secret."""
        provider = _make_provider(ConstructionProviderConfig(dk_client_id="id", dk_secret="secret"))
        assert provider._has_credentials(Land.DK) is True

    @pytest.mark.parametrize(
        "dk_client_id,dk_secret",
        [(None, "secret"), ("id", None), (None, None)],
    )
    def test_dk_false_when_missing(self, dk_client_id: str | None, dk_secret: str | None) -> None:
        """DK ohne vollständige Credentials ist nicht 'has_credentials'."""
        provider = _make_provider(
            ConstructionProviderConfig(dk_client_id=dk_client_id, dk_secret=dk_secret)
        )
        assert provider._has_credentials(Land.DK) is False

    def test_se_true_when_present(self) -> None:
        """SE erfordert tv_api_key."""
        provider = _make_provider(ConstructionProviderConfig(tv_api_key="key"))
        assert provider._has_credentials(Land.SE) is True

    def test_se_false_when_missing(self) -> None:
        """SE ohne tv_api_key ist nicht 'has_credentials'."""
        provider = _make_provider(ConstructionProviderConfig(tv_api_key=None))
        assert provider._has_credentials(Land.SE) is False


class TestFetchConstructionZonesCredentialSkip:
    """Missing DK/SE credentials skip that country instead of failing the whole request."""

    @pytest.mark.asyncio
    async def test_dk_skipped_without_credentials(self, caplog: pytest.LogCaptureFixture) -> None:
        """DK ohne Credentials wird übersprungen, kein HTTP-Request wird gesendet."""
        provider = _make_provider(ConstructionProviderConfig(tv_api_key="se-key"))

        with caplog.at_level(logging.WARNING):
            zones = await provider.fetch_construction_zones(_make_route(), [Land.DK])

        assert zones == []
        provider._client.get.assert_not_awaited()
        assert "missing credentials" in caplog.text
        assert "DK" in caplog.text

    @pytest.mark.asyncio
    async def test_se_skipped_without_credentials(self, caplog: pytest.LogCaptureFixture) -> None:
        """SE ohne Credentials wird übersprungen, kein HTTP-Request wird gesendet."""
        provider = _make_provider(ConstructionProviderConfig(dk_client_id="id", dk_secret="secret"))

        with caplog.at_level(logging.WARNING):
            zones = await provider.fetch_construction_zones(_make_route(), [Land.SE])

        assert zones == []
        provider._client.post.assert_not_awaited()
        assert "missing credentials" in caplog.text
        assert "SE" in caplog.text

    @pytest.mark.asyncio
    async def test_de_is_never_skipped_for_missing_credentials(self) -> None:
        """DE wird nie wegen fehlender Credentials übersprungen (braucht keine)."""
        provider = _make_provider(ConstructionProviderConfig())
        # No MOTORWAY segments -> empty result, but not due to a credential skip.
        zones = await provider.fetch_construction_zones(_make_route(), [Land.DE])
        assert zones == []
        provider._client.get.assert_not_awaited()


class TestFetchErrorHandling:
    """Tests für differenzierte Fehlerbehandlung im DATEX-II-Pfad (DK/SE)."""

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_list_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """httpx.TimeoutException liefert leere Liste und einen Warning-Log."""
        provider = _make_provider()
        provider._client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with caplog.at_level(logging.WARNING):
            zones = await provider._fetch_landscape_zones(_make_route(), Land.DK)

        assert zones == []
        assert "timed out" in caplog.text

    @pytest.mark.asyncio
    async def test_401_status_error_logs_error_not_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ein 401 wird als Error geloggt, nicht als stille leere Liste."""
        provider = _make_provider()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=mock_response
        )
        provider._client.get = AsyncMock(return_value=mock_response)

        with caplog.at_level(logging.ERROR):
            zones = await provider._fetch_landscape_zones(_make_route(), Land.DK)

        assert zones == []
        assert any(rec.levelno == logging.ERROR for rec in caplog.records)
        assert "auth failed" in caplog.text

    @pytest.mark.asyncio
    async def test_403_status_error_logs_error_not_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ein 403 wird ebenfalls als Auth-Fehler (Error) geloggt."""
        provider = _make_provider()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden", request=MagicMock(), response=mock_response
        )
        provider._client.get = AsyncMock(return_value=mock_response)

        with caplog.at_level(logging.ERROR):
            zones = await provider._fetch_landscape_zones(_make_route(), Land.DK)

        assert zones == []
        assert "auth failed" in caplog.text

    @pytest.mark.asyncio
    async def test_non_auth_http_status_error_logs_warning_not_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ein 5xx-Fehler wird als Warning geloggt (kein Auth-Problem)."""
        provider = _make_provider()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error", request=MagicMock(), response=mock_response
        )
        provider._client.get = AsyncMock(return_value=mock_response)

        with caplog.at_level(logging.WARNING):
            zones = await provider._fetch_landscape_zones(_make_route(), Land.DK)

        assert zones == []
        assert not any(rec.levelno == logging.ERROR for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_list(self) -> None:
        """Unerwartete Exceptions werden gefangen und liefern leere Liste."""
        provider = _make_provider()
        provider._client.get = AsyncMock(side_effect=ValueError("unexpected"))

        zones = await provider._fetch_landscape_zones(_make_route(), Land.DK)

        assert zones == []


class TestDkRequestAuth:
    """Tests für HTTP Basic Auth auf dem DK-Request."""

    @pytest.mark.asyncio
    async def test_dk_request_uses_basic_auth_with_configured_credentials(self) -> None:
        """DK-Request nutzt httpx.BasicAuth mit dk_client_id/dk_secret."""
        config = ConstructionProviderConfig(dk_client_id="client-1", dk_secret="secret-1")
        provider = _make_provider(config)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.text = VALID_DK_XML
        provider._client.get = AsyncMock(return_value=mock_response)

        with patch("tripplanner.construction.providers.httpx.BasicAuth") as mock_basic_auth:
            mock_basic_auth.return_value = "auth-sentinel"
            await provider._fetch_landscape_zones(_make_route(), Land.DK)

        mock_basic_auth.assert_called_once_with("client-1", "secret-1")
        _, kwargs = provider._client.get.call_args
        assert kwargs["auth"] == "auth-sentinel"


class TestSeRequestXml:
    """Tests für den SE-Request (POST mit XML-Body statt GET-Query)."""

    @pytest.mark.asyncio
    async def test_se_request_is_post_with_xml_body_and_authenticationkey(self) -> None:
        """SE sendet einen POST mit XML-Body, der den authenticationkey enthält."""
        config = ConstructionProviderConfig(tv_api_key="se-secret-key")
        provider = _make_provider(config)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.text = VALID_SE_XML
        provider._client.post = AsyncMock(return_value=mock_response)

        await provider._fetch_landscape_zones(_make_route(), Land.SE)

        provider._client.post.assert_awaited_once()
        args, kwargs = provider._client.post.call_args
        assert args[0] == "https://api.trafikinfo.trafikverket.se/v2/data.json"
        assert 'authenticationkey="se-secret-key"' in kwargs["content"]
        assert kwargs["headers"]["Accept"] == "application/xml"
        provider._client.get.assert_not_awaited()


class TestExtractAutobahnIds:
    """Tests für `_extract_autobahn_ids`."""

    def test_extracts_id_from_motorway_segment(self) -> None:
        """MOTORWAY-Segment mit exaktem Autobahn-Namen liefert die ID."""
        route = _make_motorway_route(strassenname="A9")
        assert _extract_autobahn_ids(route) == {"A9"}

    def test_extracts_id_from_descriptive_name(self) -> None:
        """Autobahn-ID wird auch aus einem beschreibenden Straßennamen extrahiert."""
        route = _make_motorway_route(strassenname="Bundesautobahn A9 Richtung München")
        assert _extract_autobahn_ids(route) == {"A9"}

    def test_skips_non_motorway_segments(self) -> None:
        """Nicht-MOTORWAY-Segmente werden ignoriert."""
        assert _extract_autobahn_ids(_make_route()) == set()

    def test_skips_motorway_segment_without_street_name(self) -> None:
        """MOTORWAY-Segment ohne strassenname wird sicher übersprungen."""
        route = _make_motorway_route(strassenname=None)
        assert _extract_autobahn_ids(route) == set()

    def test_deduplicates_multiple_segments_same_autobahn(self) -> None:
        """Mehrere Segmente derselben Autobahn liefern nur eine ID."""
        seg1 = RouteSegment(
            segment_index=0,
            geometrie=[(50.0, 9.0), (50.01, 9.01)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenname="A9",
            bearing_deg=10.0,
        )
        seg2 = RouteSegment(
            segment_index=1,
            geometrie=[(50.01, 9.01), (50.02, 9.02)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenname="A9 Nord",
            bearing_deg=10.0,
        )
        route = Route(
            segments=[seg1, seg2],
            gesamtlaenge_m=1000.0,
            geometrie=seg1.geometrie + seg2.geometrie,
        )
        assert _extract_autobahn_ids(route) == {"A9"}


class TestNearestSegmentIndex:
    """Tests für `_nearest_segment_index`."""

    def test_finds_nearest_segment(self) -> None:
        """Findet den Index des nächstgelegenen Segments per Haversine."""
        route = _make_motorway_route()
        idx, dist = _nearest_segment_index((50.0050, 9.0050), route.segments)
        assert idx == 0
        assert dist < 1000.0

    def test_empty_segments_returns_none(self) -> None:
        """Leere Segmentliste liefert (None, inf)."""
        idx, dist = _nearest_segment_index((50.0, 9.0), [])
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


class TestFetchDeRoadworks:
    """Tests für `_fetch_de_roadworks` (Autobahn GmbH JSON-Pfad)."""

    @pytest.mark.asyncio
    async def test_fetches_and_parses_roadworks_for_autobahn_id(self) -> None:
        """Ruft die Roadworks für jede erkannte Autobahn-ID unauthentifiziert ab."""
        provider = _make_provider()
        route = _make_motorway_route(strassenname="A9")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"roadworks": [_sample_autobahn_entry()]})
        provider._client.get = AsyncMock(return_value=mock_response)

        zones = await provider._fetch_de_roadworks(route)

        assert len(zones) == 1
        assert zones[0].land == Land.DE
        provider._client.get.assert_awaited_once_with(f"{AUTOBAHN_BASE_URL}/A9/services/roadworks")

    @pytest.mark.asyncio
    async def test_no_autobahn_ids_returns_empty_without_request(self) -> None:
        """Ohne erkennbare Autobahn-ID wird kein HTTP-Request gesendet."""
        provider = _make_provider()
        zones = await provider._fetch_de_roadworks(_make_route())

        assert zones == []
        provider._client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_request_failure_for_one_id_does_not_abort_others(self) -> None:
        """Ein fehlgeschlagener Request für eine Autobahn-ID bricht die anderen nicht ab."""
        provider = _make_provider()
        seg_a9 = RouteSegment(
            segment_index=0,
            geometrie=[(50.0, 9.0), (50.01, 9.01)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenname="A9",
            bearing_deg=10.0,
        )
        seg_a1 = RouteSegment(
            segment_index=1,
            geometrie=[(53.0, 10.0), (53.01, 10.01)],
            laenge_m=500.0,
            strassenklasse="MOTORWAY",
            strassenname="A1",
            bearing_deg=10.0,
        )
        route = Route(
            segments=[seg_a9, seg_a1],
            gesamtlaenge_m=1000.0,
            geometrie=seg_a9.geometrie + seg_a1.geometrie,
        )

        ok_response = MagicMock(spec=httpx.Response)
        ok_response.raise_for_status = MagicMock()
        ok_response.json = MagicMock(
            return_value={
                "roadworks": [_sample_autobahn_entry(coordinate={"lat": 50.001, "long": 9.001})]
            }
        )

        async def get_side_effect(url: str) -> MagicMock:
            if "/A1/" in url:
                raise httpx.TimeoutException("timeout")
            return ok_response

        provider._client.get = AsyncMock(side_effect=get_side_effect)

        zones = await provider._fetch_de_roadworks(route)

        assert len(zones) == 1
        assert zones[0].betroffene_segmente == [0]


class TestFetchConstructionZones:
    """Integrationstests für `fetch_construction_zones` mit gemocktem HTTP."""

    @pytest.mark.asyncio
    async def test_de_dispatches_to_autobahn_path(self) -> None:
        """Land.DE wird über den Autobahn-GmbH-JSON-Pfad abgefragt, nicht DATEX II."""
        provider = _make_provider()
        route = _make_motorway_route(strassenname="A9")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"roadworks": [_sample_autobahn_entry()]})
        provider._client.get = AsyncMock(return_value=mock_response)

        zones = await provider.fetch_construction_zones(route, [Land.DE])

        assert len(zones) == 1
        assert zones[0].land == Land.DE

    @pytest.mark.asyncio
    async def test_dk_fetch_success(self) -> None:
        """DK: Erfolgreicher HTTP-GET (mit Basic Auth) und Parsing."""
        provider = _make_provider()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = VALID_DK_XML
        mock_response.raise_for_status = MagicMock()
        provider._client.get = AsyncMock(return_value=mock_response)

        zones = await provider.fetch_construction_zones(_make_route(), [Land.DK])

        assert len(zones) >= 1
        assert zones[0].land == Land.DK

    @pytest.mark.asyncio
    async def test_se_fetch_success(self) -> None:
        """SE: Erfolgreicher HTTP-POST (XML-Body) und Parsing."""
        provider = _make_provider()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = VALID_SE_XML
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        zones = await provider.fetch_construction_zones(_make_route(), [Land.SE])

        assert len(zones) >= 1
        assert zones[0].land == Land.SE

    @pytest.mark.asyncio
    async def test_multiple_countries_combined(self) -> None:
        """Mehrere Länder (DE/DK/SE) in einem Aufruf werden zusammengeführt."""
        provider = _make_provider()

        de_response = MagicMock(spec=httpx.Response)
        de_response.raise_for_status = MagicMock()
        de_response.json = MagicMock(return_value={"roadworks": [_sample_autobahn_entry()]})

        dk_response = MagicMock(spec=httpx.Response)
        dk_response.raise_for_status = MagicMock()
        dk_response.text = VALID_DK_XML

        se_response = MagicMock(spec=httpx.Response)
        se_response.raise_for_status = MagicMock()
        se_response.text = VALID_SE_XML

        async def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "verkehr.autobahn.de" in url:
                return de_response
            return dk_response

        async def post_side_effect(url: str, **kwargs: Any) -> MagicMock:
            return se_response

        provider._client.get = AsyncMock(side_effect=get_side_effect)
        provider._client.post = AsyncMock(side_effect=post_side_effect)

        route = _make_motorway_route(strassenname="A9")
        zones = await provider.fetch_construction_zones(route, [Land.DE, Land.DK, Land.SE])

        lands = {z.land for z in zones}
        assert lands == {Land.DE, Land.DK, Land.SE}


class TestConstructorAndLifecycle:
    """Tests für Konstruktion (eager client / injizierter client) und Lifecycle."""

    @pytest.mark.asyncio
    async def test_init_creates_client_eagerly(self) -> None:
        """__init__ erstellt einen httpx.AsyncClient, auch ohne `async with`."""
        provider = ConstructionProviderImpl(_make_config())

        assert isinstance(provider._client, httpx.AsyncClient)
        assert provider._client.timeout.connect == 10.0

        await provider.close()

    @pytest.mark.asyncio
    async def test_init_reuses_provided_client(self) -> None:
        """Ein übergebener httpx.AsyncClient wird direkt wiederverwendet (Prozess-weit)."""
        client = httpx.AsyncClient()
        provider = ConstructionProviderImpl(_make_config(), client=client)

        assert provider._client is client

        await client.aclose()

    @pytest.mark.asyncio
    async def test_close_closes_client(self) -> None:
        """close() schließt den httpx-Client."""
        provider = ConstructionProviderImpl(_make_config())
        await provider.close()
        assert provider._client.is_closed

    @pytest.mark.asyncio
    async def test_async_context_manager_still_supported(self) -> None:
        """`async with` bleibt als Nutzungsmuster unterstützt (schließt bei Exit)."""
        config = _make_config()

        async with ConstructionProviderImpl(config) as provider:
            assert not provider._client.is_closed

        assert provider._client.is_closed


class TestFakeConstructionProvider:
    """Tests für FakeConstructionProvider (Fehlende Coverage)."""

    def test_init_with_none_creates_empty_list(self) -> None:
        """__init__ mit None initialisiert leere test_zones Liste."""
        provider = FakeConstructionProvider(test_zones=None)
        assert provider.test_zones == []

    def test_init_with_zones_stores_them(self) -> None:
        """__init__ mit Zonen speichert diese."""
        zones = [
            ConstructionZone(
                betroffene_segmente=[0],
                tempolimit_kmh=80,
                sperrungstyp=Sperrungstyp.TEMPORARY_SPEED_LIMIT,
                umleitungshinweis=None,
                land=Land.DE,
                gueltig_von=datetime.now(UTC),
                gueltig_bis=None,
            )
        ]
        provider = FakeConstructionProvider(test_zones=zones)
        assert provider.test_zones == zones

    @pytest.mark.asyncio
    async def test_fetch_filters_by_land(self) -> None:
        """fetch_construction_zones filtert nach Ländern."""
        zones = [
            ConstructionZone(
                betroffene_segmente=[0],
                tempolimit_kmh=80,
                sperrungstyp=Sperrungstyp.TEMPORARY_SPEED_LIMIT,
                umleitungshinweis=None,
                land=Land.DE,
                gueltig_von=datetime.now(UTC),
                gueltig_bis=None,
            ),
            ConstructionZone(
                betroffene_segmente=[0],
                tempolimit_kmh=60,
                sperrungstyp=Sperrungstyp.LANE_CLOSED,
                umleitungshinweis=None,
                land=Land.DK,
                gueltig_von=datetime.now(UTC),
                gueltig_bis=None,
            ),
        ]
        provider = FakeConstructionProvider(test_zones=zones)

        de_zones = await provider.fetch_construction_zones(_make_route(), [Land.DE])
        assert len(de_zones) == 1
        assert de_zones[0].land == Land.DE

        dk_zones = await provider.fetch_construction_zones(_make_route(), [Land.DK])
        assert len(dk_zones) == 1
        assert dk_zones[0].land == Land.DK

    @pytest.mark.asyncio
    async def test_fetch_returns_copy_when_no_land_filter(self) -> None:
        """fetch ohne Länder-Filter gibt Kopie der Test-Zonen zurück."""
        zones = [
            ConstructionZone(
                betroffene_segmente=[0],
                tempolimit_kmh=80,
                sperrungstyp=Sperrungstyp.TEMPORARY_SPEED_LIMIT,
                umleitungshinweis=None,
                land=Land.DE,
                gueltig_von=datetime.now(UTC),
                gueltig_bis=None,
            )
        ]
        provider = FakeConstructionProvider(test_zones=zones)

        result = await provider.fetch_construction_zones(_make_route(), [])
        assert result == zones
        assert result is not zones  # Copy, nicht dieselbe Referenz
