"""DE construction-zone provider: NRW Mobilitaetsdaten DATEX II export.

Default DE provider, superseding `providers_de_autobahn.AutobahnConstructionprovider`
per direct user instruction. Consumes the two DATEX II Situation Publication
feeds published by the NRW Mobilitaetsdaten "systemadapter-mobilithek-exporter"
(aggregated nationwide Autobahn Arbeitsstellen data, not NRW-only despite the
supplier identifier), covering long-duration ("ld") and short-duration ("kd")
roadworks separately:

- https://www.mobilitaetsdaten.nrw/api/systemadapter-mobilithek-exporter/arbeitsstellen-ld-autobahn.xml
- https://www.mobilitaetsdaten.nrw/api/systemadapter-mobilithek-exporter/arbeitsstellen-kd-autobahn.xml

Both feeds are unauthenticated GETs returning a `d2LogicalModel`/
`payloadPublication` document with many `situation`/`situationRecord`
elements; unlike the Autobahn GmbH provider, geometry is a full LineString
(`groupOfLocations` → `posList`), enabling the same direction-aware matching
used for DK/SE (see `matching.filter_opposite_direction`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
from shapely import STRtree
from shapely.geometry import LineString

if TYPE_CHECKING:
    import tripplanner.routing.models as routing_models

from tripplanner.cache.store import TTLCache
from tripplanner.construction import matching
from tripplanner.construction.models import (
    DEFAULT_ROADWORKS_SPEED_LIMIT_KMH,
    ConstructionProvider,
    ConstructionZone,
    Land,
)
from tripplanner.construction.parser import DATEXIIConstructionZoneInternal, parse_datexii_xml
from tripplanner.geo import geodesic_length_m

logger = logging.getLogger(__name__)

# Public, unauthenticated DATEX II Situation Publication feeds operated by
# NRW Mobilitaetsdaten (Mobilithek exporter). "ld" = long-duration, "kd" =
# short-duration ("kurze duration") Autobahn roadworks; both are fetched and
# combined since neither alone covers all active roadworks.
NRW_ARBEITSSTELLEN_LD_URL = (
    "https://www.mobilitaetsdaten.nrw/api/systemadapter-mobilithek-exporter/"
    "arbeitsstellen-ld-autobahn.xml"
)
NRW_ARBEITSSTELLEN_KD_URL = (
    "https://www.mobilitaetsdaten.nrw/api/systemadapter-mobilithek-exporter/"
    "arbeitsstellen-kd-autobahn.xml"
)

# Neither feed exposes a structured speed-limit field for roadwork impacts
# (no `delayBand` observed in live data). 80 km/h is the standard real-world
# default speed limit at active Autobahn roadworks absent more specific
# data, matching the DK/SE and legacy Autobahn-GmbH defaults.
_DE_DATEXII_DEFAULT_SPEED_LIMIT_KMH = DEFAULT_ROADWORKS_SPEED_LIMIT_KMH


class DatexIIGermanyConstructionProvider(ConstructionProvider):
    """Default DE construction-zone provider: NRW Mobilitaetsdaten DATEX II export.

    DE-only — any other country in `laender` is ignored. Fetches and caches
    the long- and short-duration Autobahn Arbeitsstellen feeds, parses them
    with the shared DATEX II parser, and matches zones to route segments via
    the shared STRtree/direction-aware matching used for DK/SE.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        cache_ttl_seconds: float = 3600.0,
        cache_dir: str | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            client: Optional pre-opened httpx.AsyncClient for process-wide
                reuse. When None, a new client is created eagerly so the
                provider also works without `async with`.
            timeout_seconds: HTTP request timeout, used only when *client* is None.
            cache_ttl_seconds: TTL in seconds for the persistent feed cache.
            cache_dir: Directory for the SQLite-backed cache database.
                Defaults to the project's default cache directory when None.
        """
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        cache_path = f"{cache_dir}/construction_cache.sqlite" if cache_dir else None
        self._cache = TTLCache(
            namespace="construction_zones_de_datexii",
            ttl_seconds=cache_ttl_seconds,
            db_path=cache_path,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Fetch and match Autobahn roadworks along the route from the NRW feeds.

        Args:
            route: The route to match roadworks against.
            laender: Countries requested by the caller; this provider only
                ever returns `Land.DE` zones and returns `[]` immediately if
                `Land.DE` is not present.

        Returns:
            List of matched ``ConstructionZone`` objects.
        """
        if Land.DE not in laender:
            return []

        ld_xml, kd_xml = await asyncio.gather(
            self._fetch_feed(NRW_ARBEITSSTELLEN_LD_URL, "nrw_arbeitsstellen_ld"),
            self._fetch_feed(NRW_ARBEITSSTELLEN_KD_URL, "nrw_arbeitsstellen_kd"),
        )

        internal_zones = parse_datexii_xml(ld_xml, Land.DE) + parse_datexii_xml(kd_xml, Land.DE)

        strtree, segment_geoms = matching.build_strtree(route.segments)
        zones: list[ConstructionZone] = []
        for internal_zone in internal_zones:
            zone = self._match_zone(internal_zone, route, strtree, segment_geoms)
            if zone is not None:
                zones.append(zone)
        return zones

    async def _fetch_feed(self, url: str, cache_key: str) -> str:
        """GET a NRW DATEX II feed, cached as raw XML text in the persistent TTL cache."""
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Construction cache hit: %s", cache_key)
            return str(cached)
        response = await self._client.get(url)
        _ = response.raise_for_status()
        xml_content = response.text
        self._cache.set(cache_key, xml_content)
        return xml_content

    def _match_zone(
        self,
        zone: DATEXIIConstructionZoneInternal,
        route: routing_models.Route,
        strtree: STRtree,
        segment_geoms: list[LineString],
    ) -> ConstructionZone | None:
        """Match one parsed DATEX II zone to route segments, or return None."""
        segment_ids = matching.match_zones_to_segment_ids(
            zone,
            strtree,
            segment_geoms,
            route.segments,
        )
        if not segment_ids:
            return None

        if len(zone.koordinaten) >= 2:
            length_m: float | None = geodesic_length_m(zone.koordinaten)
        else:
            length_m = sum(
                route.segments[i].length_m for i in segment_ids if 0 <= i < len(route.segments)
            )

        return ConstructionZone(
            betroffene_segmente=segment_ids,
            speed_limit_kmh=(
                zone.speed_limit_kmh
                if zone.speed_limit_kmh is not None
                else _DE_DATEXII_DEFAULT_SPEED_LIMIT_KMH
            ),
            closure_type=matching.map_closure_type(zone.closure_type),
            umleitungshinweis=zone.umleitungshinweis,
            land=Land.DE,
            gueltig_von=zone.gueltig_von,
            gueltig_bis=zone.gueltig_bis,
            length_m=length_m,
        )
