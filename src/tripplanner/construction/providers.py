"""Provider-Implementierungen für construction-Daten.

Enthält:
- ConstructionProviderConfig: Konfiguration für externe APIs
- ConstructionProviderImpl: Implementierung mit der Autobahn GmbH API (DE) und
  DATEX II Feeds (DK, SE)
- FakeConstructionProvider: Fake-Provider für Tests
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel
from shapely.geometry import LineString

if TYPE_CHECKING:
    import tripplanner.routing.models as routing_models

from tripplanner.construction.models import (
    ConstructionProvider,
    ConstructionZone,
    Land,
    Sperrungstyp,
)
from tripplanner.construction.parser import (
    ROADWORKS_TYPES,
    DATEXIIConstructionZoneInternal,
    parse_datexii_xml,
)
from tripplanner.geo import Coordinate, haversine_distance_m

logger = logging.getLogger(__name__)

# DATEX II feed endpoints for the DK/SE flow. DE is no longer a DATEX II
# country (superseded by the Autobahn GmbH open API below) per direct user
# instruction.
DATEXII_ENDPOINTS: dict[Land, str] = {
    Land.DK: "https://businessservice.dataudveksler.app.vd.dk/api/DateX2",
    Land.SE: "https://api.trafikinfo.trafikverket.se/v2/data.json",
}

# Public, unauthenticated JSON REST API operated by Die Autobahn GmbH des
# Bundes. Documented at https://autobahn.api.bund.dev, OpenAPI spec at
# https://autobahn.api.bund.dev/openapi.yaml. Verified live:
# GET {AUTOBAHN_BASE_URL}/A1/services/roadworks returns current roadworks.
AUTOBAHN_BASE_URL = "https://verkehr.autobahn.de/o/autobahn"

# Entries whose nearest route segment is farther than this are discarded as
# not actually on this route.
_DE_ROADWORKS_MAX_DISTANCE_M = 500.0

# The Autobahn GmbH API exposes no structured speed-limit field. 80 km/h is
# the standard real-world default speed limit at active German Autobahn
# roadworks absent more specific data (used for both derived Sperrungstyp
# values below, since ConstructionZone's validator requires tempolimit_kmh
# for both PARTIALLY_CLOSED and TEMPORARY_SPEED_LIMIT).
_DE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH = 80
# DK and SE DATEX II APIs expose no structured speed-limit field for roadwork
# impacts (delays element is often empty, and the impact structure lacks a
# numeric speed). 80 km/h is the standard real-world default speed limit at
# active roadworks absent more specific data, matching the DE default.
_DK_SE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH = 80

_AUTOBAHN_ID_PATTERN = re.compile(r"A\d+")


def _parse_wkt_point(wkt: str) -> tuple[float, float] | None:
    """Parse a WKT ``POINT (lon lat)`` string into (lat, lon).

    Returns ``None`` if the WKT cannot be parsed.
    """
    try:
        wkt = wkt.strip()
        if not wkt.upper().startswith("POINT"):
            return None
        # Remove "POINT " prefix and parentheses
        inner = wkt[5:].strip().strip("()")
        parts = inner.split()
        if len(parts) >= 2:
            lon, lat = float(parts[0]), float(parts[1])
            return (lat, lon)
    except (ValueError, IndexError):
        pass
    return None


def _parse_wkt_line(wkt: str) -> list[tuple[float, float]] | None:
    """Parse a WKT ``LINESTRING (lon lat, lon lat, ...)`` into [(lat, lon)].

    Returns ``None`` if no valid coordinates are found.
    """
    coords: list[tuple[float, float]] = []
    try:
        wkt = wkt.strip()
        if not wkt.upper().startswith("LINESTRING"):
            return None
        # Extract content inside parentheses
        inner = wkt[10:].strip().strip("()")
        pairs = inner.split(",")
        for pair in pairs:
            parts = pair.strip().split()
            if len(parts) >= 2:
                try:
                    lon, lat = float(parts[0]), float(parts[1])
                    coords.append((lat, lon))
                except ValueError:
                    continue
    except (ValueError, IndexError):
        pass
    return coords if coords else None


def _parse_trafikverket_situations(data: dict[str, Any]) -> list[DATEXIIConstructionZoneInternal]:
    """Map Trafikverket Situation/Deviation JSON to DATEXIIConstructionZoneInternal.

    Response shape: ``RESPONSE.RESULT[0].Situation[].Deviation[]``.
    Only Deviations whose ``MessageTypeValue`` is in ``ROADWORKS_TYPES``
    are kept (filtering out unrelated traffic messages).
    """
    zones: list[DATEXIIConstructionZoneInternal] = []

    response = data.get("RESPONSE", {})
    results: list[dict[str, Any]] = response.get("RESULT", [])

    for result in results:
        for situation in result.get("Situation", []):
            for deviation in situation.get("Deviation", []):
                msg_type_value: str = deviation.get("MessageTypeValue", "")

                # Filter: only roadworks-related types
                if msg_type_value not in ROADWORKS_TYPES:
                    continue

                start_time = deviation.get("StartTime")
                end_time = deviation.get("EndTime")
                message = deviation.get("Message")
                geometry = deviation.get("Geometry", {})

                # Coordinate extraction: prefer Line, fall back to Point
                line_wkt = (
                    geometry.get("Line", {}).get("WGS84")
                    if isinstance(geometry.get("Line"), dict)
                    else None
                )
                point_wkt = (
                    geometry.get("Point", {}).get("WGS84")
                    if isinstance(geometry.get("Point"), dict)
                    else None
                )

                koordinaten: list[tuple[float, float]] = []
                if line_wkt:
                    parsed = _parse_wkt_line(line_wkt)
                    if parsed:
                        koordinaten = parsed
                if not koordinaten and point_wkt:
                    pt = _parse_wkt_point(point_wkt)
                    if pt:
                        koordinaten = [pt]

                gueltig_von = (
                    datetime.fromisoformat(start_time) if start_time else datetime.now(UTC)
                )
                gueltig_bis = datetime.fromisoformat(end_time) if end_time else None

                zones.append(
                    DATEXIIConstructionZoneInternal(
                        sperrungstyp=msg_type_value,
                        gueltig_von=gueltig_von,
                        gueltig_bis=gueltig_bis,
                        koordinaten=koordinaten,
                        umleitungshinweis=message or None,
                        tempolimit_kmh=None,  # No structured speed field — default applied upstream
                    )
                )

    return zones


class ConstructionProviderConfig(BaseModel):
    """Konfiguration für den ConstructionProvider.

    DE benötigt keine Credentials (Autobahn GmbH API ist unauthentifiziert).
    DK nutzt OAuth2 client_credentials Flow über Azure AD (`dk_client_id`/
    `dk_secret`/`dk_tenant_id`), SE einen Trafikverket `authenticationkey`
    (`tv_api_key`).
    """

    dk_client_id: str | None = None
    dk_secret: str | None = None
    dk_tenant_id: str | None = None
    """Azure AD tenant ID for DK OAuth2 client_credentials flow.

    Obtained from the Dataudveksleren portal when associating the service
    account with the roadworks dataset.  Must be present (non-empty) for
    DK credential checks to pass.
    """
    dk_download_url: str | None = None
    """Per-dataset download URL for the DK DateX2 endpoint.

    Obtained from the Dataudveksleren portal.  Defaults to the standard
    DATEXII_ENDPOINTS[Land.DK] value when unset, since this repo has no
    verified alternative endpoint.
    """
    tv_api_key: str | None = None
    timeout_seconds: float = 30.0


class ConstructionProviderImpl(ConstructionProvider):
    """Implementierung des ConstructionProvider: Autobahn GmbH (DE), DATEX II (DK, SE)."""

    def __init__(
        self,
        config: ConstructionProviderConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            config: Provider configuration (credentials, timeout).
            client: Optional pre-opened httpx.AsyncClient for process-wide
                reuse (mirrors `OpenMeteoProvider.__init__`). When None, a new
                client is created eagerly so the provider also works without
                `async with`.
        """
        self._config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)

    async def __aenter__(self) -> ConstructionProviderImpl:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def _has_credentials(self, country: Land) -> bool:
        """Check whether the configured credentials suffice for `country`.

        DE always returns True (the Autobahn GmbH API takes no credentials).
        DK requires dk_client_id, dk_secret, AND dk_tenant_id.
        """
        if country == Land.DK:
            return (
                bool(self._config.dk_client_id)
                and bool(self._config.dk_secret)
                and bool(self._config.dk_tenant_id)
            )
        if country == Land.SE:
            return bool(self._config.tv_api_key)
        return True

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        countries: list[Land],
    ) -> list[ConstructionZone]:
        """Fetch roadworks along the route for the given countries.

        Countries missing required credentials (DK/SE) are skipped with a
        logged warning instead of sending a request that would fail with
        401/403 anyway.
        """
        all_zones: list[ConstructionZone] = []

        for country in countries:
            if not self._has_credentials(country):
                logger.warning("Construction API %s: missing credentials, skipping.", country)
                continue
            if country == Land.DE:
                zones = await self._fetch_de_roadworks(route)
            else:
                zones = await self._fetch_landscape_zones(route, country)
            all_zones.extend(zones)

        return all_zones

    async def _fetch_de_roadworks(
        self,
        route: routing_models.Route,
    ) -> list[ConstructionZone]:
        """Fetch and parse roadworks from the Autobahn GmbH open API for DE.

        A DE-specific fetch+parse path, separate from `_fetch_landscape_zones`'s
        DATEX II flow: the Autobahn API is JSON, unauthenticated, and queried
        per Autobahn ID rather than per country bounding box.
        """
        autobahn_ids = sorted(_extract_autobahn_ids(route))
        if not autobahn_ids:
            return []

        responses = await asyncio.gather(
            *(self._fetch_autobahn_roadworks(autobahn_id) for autobahn_id in autobahn_ids),
            return_exceptions=True,
        )

        zones: list[ConstructionZone] = []
        for autobahn_id, response in zip(autobahn_ids, responses, strict=True):
            if isinstance(response, BaseException):
                logger.warning(
                    "Construction API DE (%s): request failed: %s", autobahn_id, response
                )
                continue
            for entry in response:
                zone = _parse_autobahn_roadwork(entry, route)
                if zone is not None:
                    zones.append(zone)

        return zones

    async def _fetch_autobahn_roadworks(self, autobahn_id: str) -> list[dict[str, Any]]:
        """GET roadworks for a single Autobahn ID; no auth headers/query params."""
        response = await self._client.get(f"{AUTOBAHN_BASE_URL}/{autobahn_id}/services/roadworks")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        roadworks: list[dict[str, Any]] = data.get("roadworks", [])
        return roadworks

    async def _fetch_landscape_zones(
        self,
        route: routing_models.Route,
        land: Land,
    ) -> list[ConstructionZone]:
        """Abfrage und Parsing für ein DATEX-II-Land (DK, SE)."""
        endpoint = (
            (self._config.dk_download_url or DATEXII_ENDPOINTS[land])
            if land == Land.DK
            else DATEXII_ENDPOINTS[land]
        )

        try:
            if land == Land.DK:
                params = self._build_dk_params(route)
                token = self._get_dk_bearer_token()
                if token is None:
                    token = await self._refresh_dk_bearer_token()
                response = await self._client.get(
                    endpoint,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            else:
                xml_body = self._build_se_request_xml(route)
                response = await self._client.post(
                    endpoint,
                    content=xml_body,
                    headers={"Content-Type": "text/xml", "Accept": "application/xml"},
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                logger.error("Construction API %s: auth failed", land)
            else:
                logger.warning("Construction API %s: HTTP error: %s", land, exc)
            return []
        except httpx.TimeoutException:
            logger.warning("Construction API %s: request timed out", land)
            return []
        except Exception:
            logger.warning("Construction API %s: request failed", land)
            return []
        if land == Land.SE:
            construction_zones = _parse_trafikverket_situations(response.json())
        else:
            xml_content = response.text
            construction_zones = parse_datexii_xml(xml_content, land)

        return [
            ConstructionZone(
                betroffene_segmente=await self._map_to_segment_ids(zone, route),
                tempolimit_kmh=(
                    zone.tempolimit_kmh
                    if zone.tempolimit_kmh is not None
                    else _DK_SE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH
                ),
                sperrungstyp=self._map_closure_type(zone.sperrungstyp),
                umleitungshinweis=zone.umleitungshinweis,
                land=land,
                gueltig_von=zone.gueltig_von,
                gueltig_bis=zone.gueltig_bis,
            )
            for zone in construction_zones
        ]

    # Azure AD OAuth2 token endpoint for DK service accounts.
    _AZURE_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    # Safety margin (seconds) before the actual expiry to avoid using
    # a token that expires mid-request.
    _TOKEN_EXPIRY_SAFETY_MARGIN_S = 60

    def _get_dk_bearer_token(self) -> str | None:
        """Return the cached bearer token if still valid, else ``None``."""
        token = getattr(self, "_dk_token", None)
        expires_at = getattr(self, "_dk_token_expires_at", None)
        if (
            token is not None
            and expires_at is not None
            and time.time() < expires_at - self._TOKEN_EXPIRY_SAFETY_MARGIN_S
        ):
            return str(token)
        return None

    async def _refresh_dk_bearer_token(self) -> str:
        """POST to Azure AD to obtain a fresh OAuth2 bearer token.

        Uses the ``client_credentials`` grant with the configured
        ``dk_client_id``, ``dk_secret``, and ``dk_tenant_id``.
        Caches the result on the instance for subsequent calls.
        """
        tenant_id = self._config.dk_tenant_id or ""
        token_url = self._AZURE_TOKEN_URL.format(tenant_id=tenant_id)
        form_body = (
            f"client_id={self._config.dk_client_id}"
            f"&scope={self._config.dk_client_id}/.default"
            f"&client_secret={self._config.dk_secret}"
            "&grant_type=client_credentials"
        )
        resp = await self._client.post(
            token_url,
            content=form_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        access_token: str = data["access_token"]
        expires_in: int = data["expires_in"]
        self._dk_token = access_token
        self._dk_token_expires_at = time.time() + expires_in
        return access_token

    def _build_dk_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameter für Dataudveksleren (Denmark) DATEX II API."""
        coords = self._route_to_bounding_box(route)
        return {
            "coords": coords,
            "startDate": (datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"),
            "format": "datex2",
        }

    def _build_se_request_xml(self, route: routing_models.Route) -> str:
        """Build the Trafikverket v2 `data.json` POST body for roadwork Situations.

        Filters by a bounding-box ``WITHIN`` on
        ``Deviation.Geometry.Point.WGS84`` (the indexed point sub-geometry).
        Value is WKT-style: ``"{minLon} {minLat}, {maxLon} {maxLat}"``
        (longitude-first pairs, space between lon/lat within a pair, comma
        between the two corner pairs).
        """
        bbox = self._route_to_bounding_box(route)
        # bbox is "minLat,minLon,maxLat,maxLon"; reformat to lon-first WKT.
        min_lat, min_lon, max_lat, max_lon = bbox.split(",", 3)
        wkt_box = f"{min_lon} {min_lat}, {max_lon} {max_lat}"
        return (
            "<REQUEST>"
            f'<LOGIN authenticationkey="{self._config.tv_api_key}"/>'
            '<QUERY objecttype="Situation" schemaversion="1.6" namespace="road.trafficinfo">'
            f'<WITHIN name="Deviation.Geometry.Point.WGS84" shape="box" value="{wkt_box}"/>'
            "</FILTER>"
            "</QUERY></REQUEST>"
        )

    def _route_to_bounding_box(self, route: routing_models.Route) -> str:
        """Konvertiert Route zu Bounding Box für API-Abfrage."""
        coords: list[tuple[float, float]] = []
        for segment in route.segments:
            for lat, lon in segment.geometrie:
                coords.append((lat, lon))

        if not coords:
            return ""

        lats, lons = zip(*coords, strict=True)
        return f"{min(lats)},{min(lons)},{max(lats)},{max(lons)}"

    async def _map_to_segment_ids(
        self,
        zone: DATEXIIConstructionZoneInternal,
        route: routing_models.Route,
    ) -> list[int]:
        """Mapped DATEX II Geometrie auf Route-Segment-IDs."""
        zone_geom = self._zone_to_geometry(zone)

        betroffene_ids: list[int] = []
        for idx, segment in enumerate(route.segments):
            seg_geom = LineString(segment.geometrie)

            if zone_geom.intersects(seg_geom):
                betroffene_ids.append(idx)

        return betroffene_ids

    def _zone_to_geometry(
        self,
        zone: DATEXIIConstructionZoneInternal,
    ) -> LineString:
        """Konvertiert DATEX II Geometrie zu Shapely LineString."""
        coords = [(pt[1], pt[0]) for pt in zone.koordinaten]
        return LineString(coords)

    def _map_closure_type(self, xsi_type: str) -> Sperrungstyp:
        """Map a DATEX II xsi:type value to the Sperrungstyp enum."""
        type_map = {
            "fullyClosed": Sperrungstyp.FULLY_CLOSED,
            "partiallyClosed": Sperrungstyp.PARTIALLY_CLOSED,
            "laneClosed": Sperrungstyp.LANE_CLOSED,
            "temporarySpeedLimit": Sperrungstyp.TEMPORARY_SPEED_LIMIT,
            "reducedLanes": Sperrungstyp.REDUCED_LANES,
            "detrourRequired": Sperrungstyp.DETOUR_REQUIRED,
            "Roadworks": Sperrungstyp.PARTIALLY_CLOSED,
            "MaintenanceWorks": Sperrungstyp.PARTIALLY_CLOSED,
        }
        return type_map.get(xsi_type, Sperrungstyp.PARTIALLY_CLOSED)


def _extract_autobahn_ids(route: routing_models.Route) -> set[str]:
    """Extract distinct German Autobahn IDs (e.g. "A9") from motorway segments.

    GraphHopper does not always populate `strassenname` for motorway segments;
    segments with no resolvable Autobahn ID are silently skipped (a
    documented, safe under-approximation of roadwork coverage).
    """
    autobahn_ids: set[str] = set()
    for segment in route.segments:
        if segment.strassenklasse != "MOTORWAY" or not segment.strassenname:
            continue
        match = _AUTOBAHN_ID_PATTERN.search(segment.strassenname)
        if match:
            autobahn_ids.add(match.group())
    return autobahn_ids


def _nearest_segment_index(
    point: Coordinate, segments: list[routing_models.RouteSegment]
) -> tuple[int | None, float]:
    """Find the route segment nearest to `point` via haversine distance.

    Replicates `trip_input.api._segment_index_for_coordinate`'s haversine
    matching approach locally, rather than importing it: `construction` must
    not depend on `trip_input` (wrong dependency direction).
    """
    if not segments:
        return None, float("inf")
    best_idx, best_dist = 0, float("inf")
    for idx, segment in enumerate(segments):
        for vertex in (segment.geometrie[0], segment.geometrie[-1]):
            dist = haversine_distance_m(point, vertex)
            if dist < best_dist:
                best_dist, best_idx = dist, idx
    return best_idx, best_dist


def _parse_autobahn_timestamp(value: str) -> datetime:
    """Parse an Autobahn API ISO 8601 timestamp, tolerating a trailing 'Z'."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_autobahn_roadwork(
    entry: dict[str, Any], route: routing_models.Route
) -> ConstructionZone | None:
    """Map a single Autobahn GmbH `roadworks[]` JSON entry to a ConstructionZone.

    Returns None when the entry's coordinate cannot be matched to any route
    segment within `_DE_ROADWORKS_MAX_DISTANCE_M` (not actually on this route).
    """
    coordinate = entry.get("coordinate")
    if not coordinate:
        return None
    try:
        point: Coordinate = (float(coordinate["lat"]), float(coordinate["long"]))
    except (KeyError, TypeError, ValueError):
        return None

    segment_index, distance_m = _nearest_segment_index(point, route.segments)
    if segment_index is None or distance_m > _DE_ROADWORKS_MAX_DISTANCE_M:
        return None

    impact_symbols = entry.get("impact", {}).get("symbols", [])
    sperrungstyp = (
        Sperrungstyp.PARTIALLY_CLOSED
        if "CLOSED" in impact_symbols
        else Sperrungstyp.TEMPORARY_SPEED_LIMIT
    )

    start_timestamp = entry.get("startTimestamp")
    gueltig_von = (
        _parse_autobahn_timestamp(start_timestamp) if start_timestamp else datetime.now(UTC)
    )

    return ConstructionZone(
        betroffene_segmente=[segment_index],
        tempolimit_kmh=_DE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH,
        sperrungstyp=sperrungstyp,
        umleitungshinweis=None,
        land=Land.DE,
        gueltig_von=gueltig_von,
        gueltig_bis=None,
    )


class FakeConstructionProvider(ConstructionProvider):
    """Fake-Provider für Tests ohne externe API-Aufrufe."""

    def __init__(self, test_zones: list[ConstructionZone] | None = None) -> None:
        """Initialisiert den Fake-Provider mit optionalen Test-Zonen."""
        self.test_zones = test_zones or []
        self.fetch_construction_zones_calls: list[tuple[routing_models.Route, list[Land]]] = []

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        countries: list[Land],
    ) -> list[ConstructionZone]:
        """Liefert die konfigurierten Test-Zonen, optional gefiltert nach Land."""
        self.fetch_construction_zones_calls.append((route, countries))
        if countries:
            return [zone for zone in self.test_zones if zone.land in countries]
        return self.test_zones.copy()
