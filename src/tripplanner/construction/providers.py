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

_AUTOBAHN_ID_PATTERN = re.compile(r"A\d+")


class ConstructionProviderConfig(BaseModel):
    """Konfiguration für den ConstructionProvider.

    DE benötigt keine Credentials (Autobahn GmbH API ist unauthentifiziert).
    DK nutzt HTTP Basic Auth (`dk_client_id`/`dk_secret`), SE einen
    Trafikverket `authenticationkey` (`tv_api_key`).
    """

    dk_client_id: str | None = None
    dk_secret: str | None = None
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
        """
        if country == Land.DK:
            return bool(self._config.dk_client_id) and bool(self._config.dk_secret)
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
        endpoint = DATEXII_ENDPOINTS[land]

        try:
            if land == Land.DK:
                params = self._build_dk_params(route)
                response = await self._client.get(
                    endpoint,
                    params=params,
                    auth=httpx.BasicAuth(
                        self._config.dk_client_id or "", self._config.dk_secret or ""
                    ),
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

        xml_content = response.text
        construction_zones = parse_datexii_xml(xml_content, land)

        return [
            ConstructionZone(
                betroffene_segmente=await self._map_to_segment_ids(zone, route),
                tempolimit_kmh=zone.tempolimit_kmh,
                sperrungstyp=self._map_closure_type(zone.sperrungstyp),
                umleitungshinweis=zone.umleitungshinweis,
                land=land,
                gueltig_von=zone.gueltig_von,
                gueltig_bis=zone.gueltig_bis,
            )
            for zone in construction_zones
        ]

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

        Assumption (best-documented v2 Situation/Deviation schema; not live-
        verified against Trafikverket's registration-gated docs): filters by a
        bounding-box `WITHIN` on `Deviation.Geometry.WGS84`.
        """
        bbox = self._route_to_bounding_box(route)
        return (
            "<REQUEST>"
            f'<LOGIN authenticationkey="{self._config.tv_api_key}"/>'
            '<QUERY objecttype="Situation" schemaversion="1.5">'
            f'<FILTER><WITHIN name="Deviation.Geometry.WGS84" shape="box" value="{bbox}"/></FILTER>'
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

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        countries: list[Land],
    ) -> list[ConstructionZone]:
        """Liefert die konfigurierten Test-Zonen, optional gefiltert nach Land."""
        if countries:
            return [zone for zone in self.test_zones if zone.land in countries]
        return self.test_zones.copy()
