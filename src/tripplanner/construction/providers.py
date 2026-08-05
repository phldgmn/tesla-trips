"""Provider-Implementierungen für construction-Daten.

Enthält:
- ConstructionProviderConfig: Konfiguration für externe APIs
- ConstructionProviderImpl: Implementierung mit DATEX II Feeds (DE, DK, SE)
- FakeConstructionProvider: Fake-Provider für Tests
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from httpx import AsyncClient, TimeoutException
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

DATEXII_ENDPOINTS: dict[Land, str] = {
    Land.DE: "https://www.mobilithek.info/datexii/rest/v2/situations",
    Land.DK: "https://businessservice.dataudveksler.app.vd.dk/api/DateX2",
    Land.SE: "https://api.trafikinfo.trafikverket.se/v1/trafficincidents",
}


class ConstructionProviderConfig(BaseModel):
    """Konfiguration für den ConstructionProvider."""

    mdm_username: str | None = None
    mdm_password: str | None = None
    dk_service_account: str | None = None
    dk_api_key: str | None = None
    tv_api_key: str
    timeout_seconds: float = 30.0


class ConstructionProviderImpl(ConstructionProvider):
    """Implementierung des ConstructionProvider mit DATEX II Feeds für DE, DK, SE."""

    def __init__(self, config: ConstructionProviderConfig) -> None:
        self._config = config
        self._client: AsyncClient | None = None

    async def __aenter__(self) -> ConstructionProviderImpl:
        self._client = AsyncClient(timeout=self._config.timeout_seconds)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Abfrage von Baustellen entlang der Route für die angegebenen Länder."""
        if not self._client:
            raise RuntimeError(
                "ConstructionProviderImpl must be used as async context manager.",
            )

        all_zones: list[ConstructionZone] = []

        for land in laender:
            zones = await self._fetch_landscape_zones(route, land)
            all_zones.extend(zones)

        return all_zones

    async def _fetch_landscape_zones(
        self,
        route: routing_models.Route,
        land: Land,
    ) -> list[ConstructionZone]:
        """Abfrage und Parsing für ein Land."""
        endpoint = DATEXII_ENDPOINTS[land]

        if land == Land.DE:
            params = self._build_de_params(route)
        elif land == Land.DK:
            params = self._build_dk_params(route)
        else:
            params = self._build_se_params(route)

        try:
            assert self._client is not None
            response = await self._client.get(endpoint, params=params)
            response.raise_for_status()
        except TimeoutException:
            return []
        except Exception:
            return []

        xml_content = response.text
        construction_zones = parse_datexii_xml(xml_content, land)

        return [
            ConstructionZone(
                betroffene_segmente=await self._map_to_segment_ids(zone, route),
                tempolimit_kmh=zone.tempolimit_kmh,
                sperrungstyp=self._map_sperrungstyp(zone.sperrungstyp),
                umleitungshinweis=zone.umleitungshinweis,
                land=land,
                gueltig_von=zone.gueltig_von,
                gueltig_bis=zone.gueltig_bis,
            )
            for zone in construction_zones
        ]

    def _build_de_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameter für MDM (Germany) DATEX II API."""
        coords = self._route_to_bounding_box(route)
        return {
            "query": "roadworks",
            "coords": coords,
            "validity": "active",
            "format": "xml",
        }

    def _build_dk_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameter für Dataudveksleren (Denmark) DATEX II API."""
        coords = self._route_to_bounding_box(route)
        return {
            "coords": coords,
            "startDate": (datetime.utcnow().isoformat() + "Z"),
            "format": "datex2",
        }

    def _build_se_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameter für Trafikverket (Sweden) API."""
        coords = self._route_to_bounding_box(route)
        return {
            "query": f"location geometry '{coords}' AND status 'active' AND type 'roadworks'",
            "key": self._config.tv_api_key,
        }

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

    def _map_sperrungstyp(self, xsi_type: str) -> Sperrungstyp:
        """Mappe DATEX II xsi:type zu Sperrungstyp enum."""
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


class FakeConstructionProvider(ConstructionProvider):
    """Fake-Provider für Tests ohne externe API-Aufrufe."""

    def __init__(self, test_zones: list[ConstructionZone] | None = None) -> None:
        self.test_zones = test_zones or []

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Gibt die vorkonfigurierten Test-Baustellen zurück."""
        if laender:
            return [z for z in self.test_zones if z.land in laender]
        return self.test_zones.copy()
