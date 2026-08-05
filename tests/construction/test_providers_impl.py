"""Unit-Tests für `ConstructionProviderImpl`: Parameter-Builder, HTTP-Transport,
XML-Parsing-Integration und Lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tripplanner.construction.models import ConstructionZone, Land, Sperrungstyp
from tripplanner.construction.parser import DATEXIIConstructionZoneInternal
from tripplanner.construction.providers import (
    ConstructionProviderConfig,
    ConstructionProviderImpl,
    FakeConstructionProvider,
)
from tripplanner.routing.models import Route, RouteSegment

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "construction"

VALID_DE_XML = (FIXTURES_DIR / "datexii_germany_roadworks_example.xml").read_text()
VALID_DK_XML = (FIXTURES_DIR / "datexii_denmark_lane_closure.xml").read_text()
VALID_SE_XML = (FIXTURES_DIR / "datexii_sweden_temp_limit.xml").read_text()


def _make_route() -> Route:
    """Erstelle eine einfache Test-Route mit einem Segment."""
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


def _make_config() -> ConstructionProviderConfig:
    """Erstelle eine Test-Konfiguration."""
    return ConstructionProviderConfig(
        mdm_username="user",
        mdm_password="pass",
        dk_service_account="dk-account",
        dk_api_key="dk-key",
        tv_api_key="se-key",
        timeout_seconds=10.0,
    )


def _make_provider(config: ConstructionProviderConfig | None = None) -> ConstructionProviderImpl:
    """Erstelle Provider-Instanz mit gemocktem httpx-Client."""
    provider = ConstructionProviderImpl(config or _make_config())
    # Mock AsyncClient transport
    provider._client = MagicMock(spec=httpx.AsyncClient)
    provider._client.timeout = 10.0
    provider._client.get = AsyncMock()
    return provider


class TestParameterBuilders:
    """Tests für die länderspezifischen Query-Parameter-Builder."""

    def test_build_de_params_contains_required_keys(self) -> None:
        """DE-Parameter enthalten query, coords, validity, format."""
        provider = _make_provider()
        route = _make_route()
        params = provider._build_de_params(route)

        assert params["query"] == "roadworks"
        assert "coords" in params
        assert params["validity"] == "active"
        assert params["format"] == "xml"

    def test_build_de_params_coords_format(self) -> None:
        """DE-Koordinaten im Format 'min_lat,min_lon,max_lat,max_lon'."""
        provider = _make_provider()
        route = _make_route()
        params = provider._build_de_params(route)

        coords = params["coords"]
        assert isinstance(coords, str)
        parts = coords.split(",")
        assert len(parts) == 4
        assert all(part.replace(".", "").replace("-", "").isdigit() for part in parts)

    def test_empty_route_returns_empty_string(self) -> None:
        """Route ohne Segmente liefert leeren String."""
        provider = _make_provider()
        empty_route = Route(
            segments=[],
            gesamtlaenge_m=1.0,  # muss > 0 sein
            geometrie=[],
        )
        bbox = provider._route_to_bounding_box(empty_route)
        assert bbox == ""
        """DK startDate ist ISO 8601 mit 'Z' Suffix (UTC)."""
        provider = _make_provider()
        route = _make_route()
        params = provider._build_dk_params(route)

        start_date = params["startDate"]
        assert start_date.endswith("Z")
        # Parsable als ISO datetime
        datetime.fromisoformat(start_date.replace("Z", "+00:00"))

    def test_build_se_params_contains_required_keys(self) -> None:
        """SE-Parameter enthalten query mit location/geometry und API-Key."""
        provider = _make_provider()
        route = _make_route()
        params = provider._build_se_params(route)

        assert "query" in params
        assert "key" in params
        assert params["key"] == "se-key"

    def test_build_se_params_query_contains_geometry_and_active(self) -> None:
        """SE query enthält Bounding Box und status/type Filter."""
        provider = _make_provider()
        route = _make_route()
        params = provider._build_se_params(route)

        query = params["query"]
        assert "location geometry" in query
        assert "status 'active'" in query
        assert "type 'roadworks'" in query


class TestRouteToBoundingBox:
    """Tests für `_route_to_bounding_box`."""

    def test_computes_correct_bbox(self) -> None:
        """Bounding Box berechnet min/max aus allen Segment-Koordinaten."""
        provider = _make_provider()
        route = _make_route()
        bbox = provider._route_to_bounding_box(route)

        parts = bbox.split(",")
        assert len(parts) == 4
        assert parts[0] == "52.52"  # min_lat
        assert parts[1] == "13.405"  # min_lon
        assert parts[2] == "52.522"  # max_lat
        assert parts[3] == "13.407"  # max_lon

    def test_empty_route_returns_empty_string(self) -> None:
        """Route ohne Segmente liefert leeren String."""
        provider = _make_provider()
        empty_route = Route(
            segments=[],
            gesamtlaenge_m=1.0,
            geometrie=[],
        )
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
        # Shapely speichert (x, y) = (lon, lat) aber wir konstruieren mit (lat, lon)
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

        # Zone genau über dem Route-Segment
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


class TestMapSperrungstyp:
    """Tests für `_map_sperrungstyp`."""

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
        assert provider._map_sperrungstyp(xsi_type) == expected


class TestFetchConstructionZones:
    """Integrationstests für `fetch_construction_zones` mit gemocktem HTTP."""

    @pytest.mark.asyncio
    async def test_raises_runtime_error_without_context_manager(self) -> None:
        """Ohne async Context-Manager wird RuntimeError geworfen."""
        config = _make_config()
        provider = ConstructionProviderImpl(config)
        # _client ist None, da nicht in Context Manager

        with pytest.raises(RuntimeError, match="must be used as async context manager"):
            await provider.fetch_construction_zones(_make_route(), [Land.DE])

    async def _run_with_mocked_client(
        self,
        provider: ConstructionProviderImpl,
        mock_get: AsyncMock,
    ) -> list:
        """Helper: rufe __aenter__, setze mock_get, führe fetch aus, rufe __aexit__."""
        await provider.__aenter__()
        # Überschreibe den echten Client.get mit unserem Mock
        provider._client.get = mock_get
        try:
            countries = [Land.DE, Land.DK, Land.SE]
            return await provider.fetch_construction_zones(_make_route(), countries)
        finally:
            await provider.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_de_fetch_success_parses_xml_and_maps_segments(self) -> None:
        """DE: Erfolgreicher HTTP-GET, XML-Parsing, Segment-Mapping."""
        provider = _make_provider()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = VALID_DE_XML
        mock_response.raise_for_status = MagicMock()
        mock_get = AsyncMock(return_value=mock_response)

        zones = await self._run_with_mocked_client(provider, mock_get)

        # Filter nur DE-Zonen
        de_zones = [z for z in zones if z.land == Land.DE]
        assert len(de_zones) >= 1
        zone = de_zones[0]
        assert zone.land == Land.DE
        expected_types = (Sperrungstyp.PARTIALLY_CLOSED, Sperrungstyp.TEMPORARY_SPEED_LIMIT)
        assert zone.sperrungstyp in expected_types
        assert zone.tempolimit_kmh is not None

    @pytest.mark.asyncio
    async def test_dk_fetch_success(self) -> None:
        """DK: Erfolgreicher HTTP-GET und Parsing."""
        provider = _make_provider()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = VALID_DK_XML
        mock_response.raise_for_status = MagicMock()
        mock_get = AsyncMock(return_value=mock_response)

        zones = await self._run_with_mocked_client(provider, mock_get)

        dk_zones = [z for z in zones if z.land == Land.DK]
        assert len(dk_zones) >= 1
        assert dk_zones[0].land == Land.DK

    @pytest.mark.asyncio
    async def test_se_fetch_success(self) -> None:
        """SE: Erfolgreicher HTTP-GET und Parsing."""
        provider = _make_provider()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = VALID_SE_XML
        mock_response.raise_for_status = MagicMock()
        mock_get = AsyncMock(return_value=mock_response)

        zones = await self._run_with_mocked_client(provider, mock_get)

        se_zones = [z for z in zones if z.land == Land.SE]
        assert len(se_zones) >= 1
        assert se_zones[0].land == Land.SE

    @pytest.mark.asyncio
    async def test_multiple_countries_combined(self) -> None:
        """Mehrere Länder in einem Aufruf werden zusammengeführt."""
        provider = _make_provider()

        mock_response_de = MagicMock(spec=httpx.Response)
        mock_response_de.text = VALID_DE_XML
        mock_response_de.raise_for_status = MagicMock()

        mock_response_dk = MagicMock(spec=httpx.Response)
        mock_response_dk.text = VALID_DK_XML
        mock_response_dk.raise_for_status = MagicMock()

        mock_response_se = MagicMock(spec=httpx.Response)
        mock_response_se.text = VALID_SE_XML
        mock_response_se.raise_for_status = MagicMock()

        mock_get = AsyncMock(side_effect=[mock_response_de, mock_response_dk, mock_response_se])

        zones = await self._run_with_mocked_client(provider, mock_get)

        assert len(zones) >= 3
        lands = {z.land for z in zones}
        assert Land.DE in lands
        assert Land.DK in lands
        assert Land.SE in lands


class TestFetchErrorHandling:
    """Tests für Fehlerbehandlung bei HTTP-Fehlern."""

    async def _run_with_mocked_client(
        self,
        provider: ConstructionProviderImpl,
        mock_get: AsyncMock,
    ) -> list:
        """Helper: rufe __aenter__, setze mock_get, führe fetch aus, rufe __aexit__."""
        await provider.__aenter__()
        provider._client.get = mock_get
        try:
            return await provider.fetch_construction_zones(_make_route(), [Land.DE])
        finally:
            await provider.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_list(self) -> None:
        """httpx.TimeoutException liefert leere Liste statt Exception."""
        provider = _make_provider()
        mock_get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        zones = await self._run_with_mocked_client(provider, mock_get)

        assert zones == []

    @pytest.mark.asyncio
    async def test_http_status_error_returns_empty_list(self) -> None:
        """HTTP 4xx/5xx Fehler (nach raise_for_status) liefert leere Liste."""
        provider = _make_provider()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=mock_response
        )
        mock_get = AsyncMock(return_value=mock_response)

        zones = await self._run_with_mocked_client(provider, mock_get)

        assert zones == []

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_list(self) -> None:
        """Unerwartete Exceptions werden gefangen und liefern leere Liste."""
        provider = _make_provider()
        mock_get = AsyncMock(side_effect=ValueError("unexpected"))

        zones = await self._run_with_mocked_client(provider, mock_get)

        assert zones == []


class TestContextManager:
    """Tests für async Context-Manager Lifecycle."""

    @pytest.mark.asyncio
    async def test_aenter_creates_client(self) -> None:
        """__aenter__ initialisiert den httpx AsyncClient."""
        config = _make_config()
        provider = ConstructionProviderImpl(config)

        assert provider._client is None

        entered = await provider.__aenter__()

        assert entered is provider
        assert provider._client is not None
        assert isinstance(provider._client, httpx.AsyncClient)
        # httpx.Timeout ist ein Objekt mit connect/read/write/pool
        assert provider._client.timeout.connect == 10.0

        await provider.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_aexit_closes_client(self) -> None:
        """__aexit__ schließt den Client und setzt auf None."""
        config = _make_config()
        provider = ConstructionProviderImpl(config)

        await provider.__aenter__()
        client = provider._client
        assert client is not None

        await provider.__aexit__(None, None, None)

        assert provider._client is None
        assert client.is_closed


class TestContextManagerEdgeCases:
    """Edge-Case Tests für Context-Manager."""

    @pytest.mark.asyncio
    async def test_context_manager_block(self) -> None:
        """`async with` Block initialisiert und schließt korrekt."""
        config = _make_config()

        async with ConstructionProviderImpl(config) as provider:
            assert provider._client is not None
            assert not provider._client.is_closed


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
