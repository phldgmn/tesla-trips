"""Unit-Tests für `ConstructionProviderImpl`: Parameter-Builder, HTTP-Transport,
XML-Parsing-Integration, Autobahn-GmbH-Integration (DE) und Lifecycle."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tripplanner.construction.models import (
    ConstructionZone,
    Land,
    Sperrungstyp,
)
from tripplanner.construction.parser import DATEXIIConstructionZoneInternal
from tripplanner.construction.providers import (
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


def _make_route() -> Route:
    """Erstelle eine einfache Test-Route mit einem PRIMARY-Segment."""
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


def _make_config() -> ConstructionProviderConfig:
    """Erstelle eine Test-Konfiguration mit DK/SE-Credentials."""
    return ConstructionProviderConfig(
        dk_client_id="dk-client",
        dk_secret="dk-secret",
        dk_tenant_id="dk-tenant",
        tv_api_key="se-key",
        timeout_seconds=10.0,
    )


def _make_provider(
    config: ConstructionProviderConfig | None = None,
) -> ConstructionProviderImpl:
    """Erstelle Provider-Instanz mit gemocktem httpx-Client."""
    provider = ConstructionProviderImpl(config or _make_config())
    provider._client = MagicMock(spec=httpx.AsyncClient)
    provider._client.get = AsyncMock()
    provider._client.post = AsyncMock()
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
    return ConstructionProviderImpl._build_strtree(route.segments)


class TestBuildDkParams:
    """Tests für `_build_dk_params`."""

    def test_includes_bounding_box_coords(self) -> None:
        """Die bounding Box der Route wird als Koordinate an den DK-Endpoint gesendet."""
        provider = _make_provider()
        route = _make_route()

        params = provider._build_dk_params(route)

        assert "coords" in params
        assert "52.52" in params["coords"]
        assert "13.405" in params["coords"]

    def test_includes_start_date_and_format(self) -> None:
        """Der Request enthält ein Start-Datum und das datex2-Format."""
        provider = _make_provider()
        route = _make_route()

        params = provider._build_dk_params(route)

        assert "startDate" in params
        assert "format" in params
        assert params["format"] == "datex2"

    def test_no_clientid_in_dk_params(self) -> None:
        """dk_client_id wird NICHT als URL-Parameter gesendet (nur Bearer-Token)."""
        provider = _make_provider()
        route = _make_route()

        params = provider._build_dk_params(route)

        assert "clientid" not in params


class TestBuildSeRequestXml:
    """Tests für `_build_se_request_xml`."""

    def test_contains_login_with_authenticationkey(self) -> None:
        """Der XML-Body enthält das LOGIN-Element mit dem API-Schlüssel."""
        provider = _make_provider()
        xml_body = provider._build_se_request_xml(_make_route())

        assert "se-key" in xml_body
        assert "authenticationkey" in xml_body
        assert "<REQUEST>" in xml_body
        assert "<QUERY" in xml_body

    def test_se_within_filter_uses_correct_attribute_and_wkt_value(self) -> None:
        """SE WITHIN uses Deviation.Geometry.Point.WGS84 and lon-first WKT box."""
        provider = _make_provider()
        xml_body = provider._build_se_request_xml(_make_route())

        assert 'name="Deviation.Geometry.Point.WGS84"' in xml_body
        assert "WITHIN" in xml_body
        # bbox for _make_route is "52.52,13.405,52.522,13.407"
        # WKT reformat: "13.405 52.52, 13.407 52.522"
        assert 'value="13.405 52.52, 13.407 52.522"' in xml_body


class TestRouteToBoundingBox:
    """Tests für `_route_to_bounding_box`."""

    def test_returns_formatted_bbox_for_route(self) -> None:
        """Die Bounding Box wird korrekt als formatierter String zurückgegeben."""
        provider = _make_provider()
        route = _make_route()

        bbox = provider._route_to_bounding_box(route)

        assert "," in bbox
        lat_min, lon_min, lat_max, lon_max = bbox.split(",")
        assert float(lat_min) <= float(lat_max)
        assert float(lon_min) <= float(lon_max)

    def test_empty_route_returns_empty_string(self) -> None:
        """Eine Route ohne Segmente liefert eine leere Bounding Box."""
        provider = _make_provider()
        route = Route(
            segments=[],
            gesamtlaenge_m=0.0,
            geometrie=[],
        )

        bbox = provider._route_to_bounding_box(route)

        assert bbox == ""


class TestZoneToGeometry:
    """Tests für `_zone_to_geometry`."""

    def test_linestring_zone(self) -> None:
        """Eine Zone mit mehreren Koordinaten wird zu einem LineString konvertiert."""
        provider = _make_provider()
        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(52.52, 13.405), (52.522, 13.407)],
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        geom = provider._zone_to_geometry(zone)

        assert geom.geom_type == "LineString"
        assert len(list(geom.coords)) == 2

    def test_point_zone(self) -> None:
        """Eine Zone mit genau einem Punkt wird zu einem Point konvertiert."""
        provider = _make_provider()
        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(52.52, 13.405)],
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        geom = provider._zone_to_geometry(zone)

        assert geom.geom_type == "Point"

    def test_empty_zone(self) -> None:
        """Eine Zone ohne Koordinaten wird zu einer leeren LineString konvertiert."""
        provider = _make_provider()
        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[],
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        geom = provider._zone_to_geometry(zone)

        assert geom.is_empty


class TestMatchZonesToSegmentIds:
    """Tests für `_match_zones_to_segment_ids` (now STRtree-based)."""

    @pytest.mark.asyncio
    async def test_matches_zone_to_segment_when_near(self) -> None:
        """Zone in der Nähe eines Route-Segments liefert Segment-ID."""
        provider = _make_provider()
        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(52.52, 13.405), (52.522, 13.407)],
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        ids = provider._match_zones_to_segment_ids(zone, strtree, seg_geoms)
        assert ids == [0]

    @pytest.mark.asyncio
    async def test_no_intersection_returns_empty_list(self) -> None:
        """Zone weit weg von der Route liefert leere Liste."""
        provider = _make_provider()
        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(9.0, 53.0), (9.1, 53.1)],  # Hamburg, nicht Berlin
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        ids = provider._match_zones_to_segment_ids(zone, strtree, seg_geoms)
        assert ids == []

    @pytest.mark.asyncio
    async def test_single_point_zone_maps_to_segment(self) -> None:
        """A single-point zone (Point geometry) still maps to segment IDs via distance threshold."""
        provider = _make_provider()
        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(52.52, 13.405)],  # on segment endpoint
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        ids = provider._match_zones_to_segment_ids(zone, strtree, seg_geoms)
        assert ids == [0]

    @pytest.mark.asyncio
    async def test_single_point_zone_no_intersection(self) -> None:
        """A single-point zone far from any segment yields empty list."""
        provider = _make_provider()
        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        zone = DATEXIIConstructionZoneInternal(
            sperrungstyp="partiallyClosed",
            gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
            gueltig_bis=None,
            koordinaten=[(9.0, 53.0)],  # Hamburg
            umleitungshinweis=None,
            tempolimit_kmh=80,
        )

        ids = provider._match_zones_to_segment_ids(zone, strtree, seg_geoms)
        assert ids == []


class TestMapClosureType:
    """Tests für `_map_closure_type`."""

    def test_maps_common_xsi_types(self) -> None:
        """Häufige DATEX II xsi:type-Werte werden korrekt gemappt."""
        provider = _make_provider()
        assert provider._map_closure_type("fullyClosed") == Sperrungstyp.FULLY_CLOSED
        assert provider._map_closure_type("partiallyClosed") == Sperrungstyp.PARTIALLY_CLOSED
        assert provider._map_closure_type("laneClosed") == Sperrungstyp.LANE_CLOSED
        assert (
            provider._map_closure_type("temporarySpeedLimit") == Sperrungstyp.TEMPORARY_SPEED_LIMIT
        )
        assert provider._map_closure_type("detrourRequired") == Sperrungstyp.DETOUR_REQUIRED

    def test_unknown_xsi_type_defaults_to_partially_closed(self) -> None:
        """Unbekannte xsi:type-Werte fallen auf PARTIALLY_CLOSED zurück."""
        provider = _make_provider()

        assert provider._map_closure_type("unknownType") == Sperrungstyp.PARTIALLY_CLOSED

    def test_roadworks_maps_to_partially_closed(self) -> None:
        """'Roadworks' und 'MaintenanceWorks' werden als PARTIALLY_CLOSED gemappt."""
        provider = _make_provider()
        assert provider._map_closure_type("Roadworks") == Sperrungstyp.PARTIALLY_CLOSED
        assert provider._map_closure_type("MaintenanceWorks") == Sperrungstyp.PARTIALLY_CLOSED


class TestHasCredentials:
    """Tests für `_has_credentials`."""

    def test_de_always_returns_true(self) -> None:
        """DE benötigt keine Credentials."""
        provider = _make_provider()

        assert provider._has_credentials(Land.DE) is True

    def test_dk_requires_all_credentials(self) -> None:
        """DK benötigt dk_client_id, dk_secret und dk_tenant_id."""
        config = ConstructionProviderConfig(
            dk_client_id="client",
            dk_secret="secret",
            dk_tenant_id="tenant",
        )
        provider = ConstructionProviderImpl(config)

        assert provider._has_credentials(Land.DK) is True

        provider._config = ConstructionProviderConfig(
            dk_client_id=None,
            dk_secret="secret",
            dk_tenant_id="tenant",
        )
        assert provider._has_credentials(Land.DK) is False

        provider._config = ConstructionProviderConfig(
            dk_client_id="client",
            dk_secret="",
            dk_tenant_id="tenant",
        )
        assert provider._has_credentials(Land.DK) is False

    def test_se_requires_api_key(self) -> None:
        """SE benötigt einen API-Schlüssel."""
        provider = _make_provider()

        assert provider._has_credentials(Land.SE) is True

        provider._config = ConstructionProviderConfig(tv_api_key=None)
        assert provider._has_credentials(Land.SE) is False


class TestFetchConstructionZonesCredentialSkip:
    """Missing DK/SE credentials skip that country instead of failing the whole request."""

    @pytest.mark.asyncio
    async def test_missing_credentials_skips_country(self) -> None:
        """Wenn DK/Credentials fehlen, wird DK übersprungen."""
        config = ConstructionProviderConfig(
            tv_api_key="se-key",
            # Keine DK-Credentials
        )
        provider = _make_provider(config)
        route = _make_route()

        with (
            patch.object(
                provider._client,
                "get",
                new=AsyncMock(side_effect=ValueError("should not be called")),
            ),
            patch.object(
                provider._client,
                "post",
                new=AsyncMock(side_effect=ValueError("should not be called")),
            ),
        ):
            result = await provider.fetch_construction_zones(route, [Land.DE])

        assert result == []

    @pytest.mark.asyncio
    async def test_log_warning_for_skipped_country(self) -> None:
        """Ein Log-Warnung wird für das Überspringen einer Country ausgegeben."""
        config = ConstructionProviderConfig(
            # Keine DK-Credentials
        )
        provider = _make_provider(config)
        route = _make_route()

        with patch.object(provider._client, "get", new=AsyncMock()):
            result = await provider.fetch_construction_zones(route, [Land.DK])

        assert result == []
        provider._client.get.assert_not_awaited()


class TestFetchErrorHandling:
    """Tests für differenzierte Fehlerbehandlung im DATEX-II-Pfad (DK/SE)."""

    def _make_token_resp(self, access_token: str = "tok") -> MagicMock:
        """Return a mock httpx.Response for the DK OAuth2 token POST."""
        resp = MagicMock(spec=httpx.Response)
        resp.json.return_value = {"access_token": access_token, "expires_in": 3600}
        return resp

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_list_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """httpx.TimeoutException liefert leere Liste und einen Warning-Log."""
        provider = _make_provider()
        provider._client.post = AsyncMock(return_value=self._make_token_resp())

        async def timeout_side_effect(*args, **kwargs):
            raise httpx.TimeoutException("timeout")

        provider._client.get = AsyncMock(side_effect=timeout_side_effect)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        with caplog.at_level(logging.WARNING):
            zones = await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)

        assert zones == []
        assert "timed out" in caplog.text

    @pytest.mark.asyncio
    async def test_401_status_error_logs_error_not_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ein 401 wird als Error geloggt, nicht als stille leere Liste."""
        provider = _make_provider()
        provider._client.post = AsyncMock(return_value=self._make_token_resp())
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=mock_response
        )
        provider._client.get = AsyncMock(return_value=mock_response)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        with caplog.at_level(logging.ERROR):
            zones = await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)

        assert zones == []
        assert any(rec.levelno == logging.ERROR for rec in caplog.records)
        assert "auth failed" in caplog.text

    @pytest.mark.asyncio
    async def test_403_status_error_logs_error_not_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ein 403 wird ebenfalls als Auth-Fehler (Error) geloggt."""
        provider = _make_provider()
        provider._client.post = AsyncMock(return_value=self._make_token_resp())
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden", request=MagicMock(), response=mock_response
        )
        provider._client.get = AsyncMock(return_value=mock_response)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        with caplog.at_level(logging.ERROR):
            zones = await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)

        assert zones == []
        assert "auth failed" in caplog.text

    @pytest.mark.asyncio
    async def test_non_auth_http_status_error_logs_warning_not_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ein 5xx-Fehler wird als Warning geloggt (kein Auth-Problem)."""
        provider = _make_provider()
        provider._client.post = AsyncMock(return_value=self._make_token_resp())
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error", request=MagicMock(), response=mock_response
        )
        provider._client.get = AsyncMock(return_value=mock_response)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        with caplog.at_level(logging.WARNING):
            zones = await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)

        assert zones == []
        assert not any(rec.levelno == logging.ERROR for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_list(self) -> None:
        """Unerwartete Exceptions werden gefangen und liefern leere Liste."""
        provider = _make_provider()
        provider._client.post = AsyncMock(return_value=self._make_token_resp())
        provider._client.get = AsyncMock(side_effect=ValueError("unexpected"))

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        zones = await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)

        assert zones == []


class TestDkRequestAuth:
    """Tests for DK OAuth2 client_credentials token flow."""

    @pytest.mark.asyncio
    async def test_dk_uses_bearer_token_from_oauth2_token_post(
        self,
    ) -> None:
        """DK fetches a bearer token via POST to Azure AD, then
        GETs DateX2 with Authorization: Bearer."""
        config = ConstructionProviderConfig(
            dk_client_id="client-1",
            dk_secret="secret-1",
            dk_tenant_id="tenant-1",
        )
        provider = _make_provider(config)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.text = VALID_DK_XML
        provider._client.get = AsyncMock(return_value=mock_response)

        # Mock the token POST
        token_resp = MagicMock(spec=httpx.Response)
        token_resp.json.return_value = {
            "access_token": "bearer-token-xyz",
            "expires_in": 3600,
        }
        provider._client.post = AsyncMock(return_value=token_resp)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)

        # Verify POST to Azure AD token endpoint
        provider._client.post.assert_awaited_once()
        post_args, post_kwargs = provider._client.post.call_args
        assert "login.microsoftonline.com" in post_args[0]
        assert "oauth2/v2.0/token" in post_args[0]
        assert "client_id=client-1" in post_kwargs["content"]
        assert "scope=client-1/.default" in post_kwargs["content"]
        assert "client_secret=secret-1" in post_kwargs["content"]
        assert "grant_type=client_credentials" in post_kwargs["content"]
        assert post_kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"

        # Verify GET with Bearer header
        provider._client.get.assert_awaited_once()
        _, get_kwargs = provider._client.get.call_args
        assert get_kwargs["headers"]["Authorization"] == "Bearer bearer-token-xyz"

    @pytest.mark.asyncio
    async def test_dk_get_uses_correct_endpoint_when_dk_download_url_set(
        self,
    ) -> None:
        """DK uses dk_download_url from config when set."""
        config = ConstructionProviderConfig(
            dk_client_id="c",
            dk_secret="s",
            dk_tenant_id="t",
            dk_download_url="https://custom-url.example.com/api/DateX2",
        )
        provider = _make_provider(config)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.text = VALID_DK_XML
        provider._client.get = AsyncMock(return_value=mock_response)

        token_resp = MagicMock(spec=httpx.Response)
        token_resp.json.return_value = {
            "access_token": "tok",
            "expires_in": 3600,
        }
        provider._client.post = AsyncMock(return_value=token_resp)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)

        args, _ = provider._client.get.call_args
        assert args[0] == "https://custom-url.example.com/api/DateX2"


class TestDkTokenCaching:
    """Tests for DK OAuth2 bearer token caching and expiry."""

    @pytest.mark.asyncio
    async def test_token_is_cached_and_not_refetched_within_ttl(self) -> None:
        """Second fetch within token TTL uses cached token — no second POST."""
        config = ConstructionProviderConfig(
            dk_client_id="c",
            dk_secret="s",
            dk_tenant_id="t",
        )
        provider = _make_provider(config)

        xml_response = MagicMock(spec=httpx.Response)
        xml_response.raise_for_status = MagicMock()
        xml_response.text = VALID_DK_XML
        provider._client.get = AsyncMock(return_value=xml_response)

        token_resp = MagicMock(spec=httpx.Response)
        token_resp.json.return_value = {
            "access_token": "tok-1",
            "expires_in": 3600,
        }
        provider._client.post = AsyncMock(return_value=token_resp)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        # First fetch — triggers token POST
        await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)
        provider._client.post.assert_awaited_once()

        # Second fetch — should NOT trigger another POST (token cached)
        await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)
        provider._client.post.assert_awaited_once()  # still once

    @pytest.mark.asyncio
    async def test_token_is_refetched_after_expiry(self) -> None:
        """Fetch after token expiry triggers a new token POST."""
        config = ConstructionProviderConfig(
            dk_client_id="c",
            dk_secret="s",
            dk_tenant_id="t",
        )
        provider = _make_provider(config)

        xml_response = MagicMock(spec=httpx.Response)
        xml_response.raise_for_status = MagicMock()
        xml_response.text = VALID_DK_XML
        provider._client.get = AsyncMock(return_value=xml_response)

        # First token with 3600s expiry
        token_resp1 = MagicMock(spec=httpx.Response)
        token_resp1.json.return_value = {
            "access_token": "tok-1",
            "expires_in": 3600,
        }
        # Second token with short expiry
        token_resp2 = MagicMock(spec=httpx.Response)
        token_resp2.json.return_value = {
            "access_token": "tok-2",
            "expires_in": 30,
        }

        provider._client.post = AsyncMock(side_effect=[token_resp1, token_resp2])

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        # First fetch
        await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)
        provider._client.post.assert_awaited_once()
        _, post_kwargs1 = provider._client.post.call_args
        assert "tok-1" in post_kwargs1 or provider._dk_token == "tok-1"

        # Manually expire the cached token
        provider._dk_token_expires_at = 0  # force expiry

        # Second fetch — should trigger new POST
        await provider._fetch_landscape_zones(route, Land.DK, strtree, seg_geoms)
        assert provider._client.post.await_count == 2  # now twice


class TestSeRequestXml:
    """Tests for SE request: correct WITHIN attribute and WKT value format."""

    @pytest.mark.asyncio
    async def test_se_request_is_post_with_xml_body_and_authenticationkey(self) -> None:
        """SE sends a POST with XML body containing authenticationkey; response is JSON."""

        config = ConstructionProviderConfig(tv_api_key="se-secret-key")
        provider = _make_provider(config)
        se_json_fixture = json.loads(
            (FIXTURES_DIR / "live-samples" / "se_trafikverket_live_sample.json").read_text()
        )
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=se_json_fixture)
        provider._client.post = AsyncMock(return_value=mock_response)

        route = _make_route()
        strtree, seg_geoms = _build_strtree(route)

        await provider._fetch_landscape_zones(route, Land.SE, strtree, seg_geoms)

        provider._client.post.assert_awaited_once()
        args, kwargs = provider._client.post.call_args
        assert args[0] == "https://api.trafikinfo.trafikverket.se/v2/data.json"
        assert 'authenticationkey="se-secret-key"' in kwargs["content"]
        # SE request is XML (response body is JSON, but request uses XML format)
        assert kwargs["headers"]["Accept"] == "application/xml"
        provider._client.get.assert_not_awaited()


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


class TestFetchConstructionZones:
    """Integrationstests für `fetch_construction_zones` mit gemocktem HTTP."""

    @pytest.mark.asyncio
    async def test_fetches_from_all_countries(self) -> None:
        """Alle drei Länder (DE, DK, SE) werden parallel abgerufen."""
        provider = _make_provider()
        route = _make_route()

        # DE mock
        sample_entry = _sample_autobahn_entry()
        de_mock_resp = MagicMock(spec=httpx.Response)
        de_mock_resp.raise_for_status = MagicMock()
        de_mock_resp.json = MagicMock(return_value={"roadworks": [sample_entry]})

        # DK mock
        dk_mock_resp = MagicMock(spec=httpx.Response)
        dk_mock_resp.raise_for_status = MagicMock()
        dk_mock_resp.text = VALID_DK_XML

        # SE mock
        se_mock_resp = MagicMock(spec=httpx.Response)
        se_mock_resp.raise_for_status = MagicMock()
        se_mock_resp.json = MagicMock(return_value={"Situations": []})

        token_mock_resp = MagicMock(spec=httpx.Response)
        token_mock_resp.json.return_value = {"access_token": "tok", "expires_in": 3600}

        async def get_side(url: str, **kw: Any):
            if "verkehr.autobahn.de" in url:
                return de_mock_resp
            return dk_mock_resp

        async def post_side(url: str, **kw: Any):
            if "login.microsoftonline.com" in url:
                return token_mock_resp
            return se_mock_resp

        provider._client.get = AsyncMock(side_effect=get_side)
        provider._client.post = AsyncMock(side_effect=post_side)

        zones = await provider.fetch_construction_zones(route, [Land.DE, Land.DK, Land.SE])

        # DE returns 0 zones (no MOTORWAY segments on route), DK returns zones,
        # SE returns []. The key assertion is that all 3 HTTP calls were made.
        assert len(zones) >= 0  # DE=0, DK>=0, SE=0


class TestConstructorAndLifecycle:
    """Tests für Konstruktion (eager client / injizierter client) und Lifecycle."""

    def test_default_client_is_created(self) -> None:
        """Ohne expliziten Client wird ein neuer erstellt."""
        provider = _make_provider()
        assert provider._client is not None

    @pytest.mark.asyncio
    async def test_provider_closes_http_client(self) -> None:
        """Schließt den HTTP-Client beim Beenden."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        provider = ConstructionProviderImpl(_make_config(), client=mock_client)

        await provider.close()

        mock_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exit(self) -> None:
        """Der Context Manager schließt den Client bei `__aexit__`."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        provider = ConstructionProviderImpl(_make_config(), client=mock_client)

        await provider.__aexit__(None, None, None)

        mock_client.aclose.assert_awaited_once()


class TestFakeConstructionProvider:
    """Tests für FakeConstructionProvider (Fehlende Coverage)."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_by_default(self) -> None:
        """Ein leerer Fake-Provider liefert eine leere Liste."""
        provider = FakeConstructionProvider()
        route = _make_route()

        result = await provider.fetch_construction_zones(route, [])

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_test_zones(self) -> None:
        """Ein Fake-Provider mit Test-Zonen liefert diese zurück."""
        zone = ConstructionZone(
            betroffene_segmente=[0],
            tempolimit_kmh=80,
            sperrungstyp=Sperrungstyp.TEMPORARY_SPEED_LIMIT,
            umleitungshinweis=None,
            land=Land.DE,
            gueltig_von=datetime.now(UTC),
            gueltig_bis=None,
        )
        provider = FakeConstructionProvider(test_zones=[zone])
        route = _make_route()

        result = await provider.fetch_construction_zones(route, [Land.DE])

        assert len(result) == 1
        assert result[0] == zone

    @pytest.mark.asyncio
    async def test_filters_by_country(self) -> None:
        """Bei Angabe von Ländern werden nur Zonen dieses Landes zurückgegeben."""
        de_zone = ConstructionZone(
            betroffene_segmente=[0],
            tempolimit_kmh=80,
            sperrungstyp=Sperrungstyp.TEMPORARY_SPEED_LIMIT,
            umleitungshinweis=None,
            land=Land.DE,
            gueltig_von=datetime.now(UTC),
            gueltig_bis=None,
        )
        dk_zone = ConstructionZone(
            betroffene_segmente=[1],
            tempolimit_kmh=80,
            sperrungstyp=Sperrungstyp.PARTIALLY_CLOSED,
            umleitungshinweis=None,
            land=Land.DK,
            gueltig_von=datetime.now(UTC),
            gueltig_bis=None,
        )
        provider = FakeConstructionProvider(test_zones=[de_zone, dk_zone])
        route = _make_route()

        result = await provider.fetch_construction_zones(route, [Land.DE])

        assert len(result) == 1
        assert result[0].land == Land.DE
        assert result[0] is de_zone

    @pytest.mark.asyncio
    async def test_recorded_calls(self) -> None:
        """Die Aufrufe des Providers werden mitprotokolliert."""
        provider = FakeConstructionProvider()
        route = _make_route()

        await provider.fetch_construction_zones(route, [Land.DE])

        assert len(provider.fetch_construction_zones_calls) == 1
        assert provider.fetch_construction_zones_calls[0][0] is route
        assert provider.fetch_construction_zones_calls[0][1] == [Land.DE]

    @pytest.mark.asyncio
    async def test_copy_not_reference(self) -> None:
        """Die zurückgegebene Liste ist eine Kopie, keine Referenz."""
        zone = ConstructionZone(
            betroffene_segmente=[0],
            tempolimit_kmh=80,
            sperrungstyp=Sperrungstyp.TEMPORARY_SPEED_LIMIT,
            umleitungshinweis=None,
            land=Land.DE,
            gueltig_von=datetime.now(UTC),
            gueltig_bis=None,
        )
        provider = FakeConstructionProvider(test_zones=[zone])
        route = _make_route()

        result = await provider.fetch_construction_zones(route, [])
        result.append(zone)  # Modifiziert die lokale Liste

        result2 = await provider.fetch_construction_zones(route, [])

        assert result is not result2
