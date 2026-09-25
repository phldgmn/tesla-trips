"""DE construction-zone provider: Autobahn GmbH open API.

Historical default DE provider (per direct user instruction, superseding the
original DATEX II/mobilithek.info design — see `docs/plans/10-provider-
integration-wiring.md`). Superseded in turn by
`providers_de_datexii.DatexIIGermanyConstructionprovider` (NRW Mobilitaetsdaten
Mobilithek exporter), which is now the default DE provider used by
`ConstructionproviderImpl`. Kept here, unchanged in behaviour, so it can be
reinstated by passing it explicitly:

    ConstructionproviderImpl(config, de_provider=AutobahnConstructionprovider())
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from shapely import STRtree
from shapely.geometry import LineString

if TYPE_CHECKING:
    import tripplanner.routing.models as routing_models

from tripplanner.cache.store import TTLCache
from tripplanner.construction import matching
from tripplanner.construction.models import (
    DEFAULT_ROADWORKS_SPEED_LIMIT_KMH,
    ClosureType,
    ConstructionProvider,
    ConstructionZone,
    Land,
)
from tripplanner.geo import Coordinate

logger = logging.getLogger(__name__)

# Public, unauthenticated JSON REST API operated by Die Autobahn GmbH des
# Bundes. Documented at https://autobahn.api.bund.dev, OpenAPI spec at
# https://autobahn.api.bund.dev/openapi.yaml. Verified live:
# GET {AUTOBAHN_BASE_URL}/A1/services/roadworks returns current roadworks.
AUTOBAHN_BASE_URL = "https://verkehr.autobahn.de/o/autobahn"

# The Autobahn GmbH API exposes no structured speed-limit field. 80 km/h is
# the standard real-world default speed limit at active German Autobahn
# roadworks absent more specific data (used for both derived ClosureType
# values below, since ConstructionZone's validator requires tempolimit_kmh
# for both PARTIALLY_CLOSED and TEMPORARY_SPEED_LIMIT).
_DE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH = DEFAULT_ROADWORKS_SPEED_LIMIT_KMH

# Shared distance threshold for matching a roadwork's single point coordinate
# to the nearest route segment (see `matching.MAX_DISTANCE_M`).
_DE_ROADWORKS_MAX_DISTANCE_M = matching.MAX_DISTANCE_M

_AUTOBAHN_ID_PATTERN = re.compile(r"A\s*\d+")


def _extract_autobahn_ids(
    route: routing_models.Route,
) -> set[str]:
    """Extract distinct German Autobahn IDs (e.g. "A9") from motorway segments.

    Reads ``segment.street_ref`` (the GraphHopper ``street_ref`` path detail)
    which carries the OSM ``ref`` tag (e.g. "A 5") independently of
    ``segment.street_name`` (the OSM ``name`` tag, always ``None`` for most
    motorway segments).  This avoids the ~110-ID nationwide query that caused
    a 33 s regression in commit 6ece8a5.

    Args:
        route: The route to extract Autobahn IDs from.

    Returns:
        Set of unique Autobahn ID strings found on the route.
    """
    autobahn_ids: set[str] = set()
    for segment in route.segments:
        if segment.strassenklasse != "MOTORWAY" or not segment.street_ref:
            continue
        match = _AUTOBAHN_ID_PATTERN.search(segment.street_ref)
        if match:
            # The Autobahn GmbH API requires the compact form ("A5"), but
            # GraphHopper's street_ref detail may contain a space ("A 5"):
            # a space in the URL path silently returns an empty result set
            # instead of an error (live-verified: `.../A%205/...` -> HTTP 200,
            # `{"roadworks": []}`), so whitespace must be stripped here.
            autobahn_ids.add(re.sub(r"\s+", "", match.group()))
    return autobahn_ids


def _parse_autobahn_timestamp(value: str) -> datetime:
    """Parse an Autobahn API ISO 8601 timestamp, tolerating a trailing 'Z'."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_autobahn_roadwork(
    entry: dict[str, Any],
    route: routing_models.Route,
    strtree: STRtree | None = None,
    segment_geoms: list[LineString] | None = None,
) -> ConstructionZone | None:
    """Map a single Autobahn GmbH ``roadworks[]`` JSON entry to a ConstructionZone.

    Args:
        entry: A single roadworks entry from the Autobahn API response.
        route: The route to match against.
        strtree: Optional pre-built STRtree for fast nearest lookup.
        segment_geoms: Optional pre-built LineString list for route segments.

    Returns:
        A ``ConstructionZone`` or ``None`` if the entry is too far from the route.
    """
    coordinate = entry.get("coordinate")
    if not coordinate:
        return None
    try:
        point: Coordinate = (float(coordinate["lat"]), float(coordinate["long"]))
    except (KeyError, TypeError, ValueError):
        return None

    segment_index, distance_m = matching.nearest_segment_index(
        point, route.segments, strtree, segment_geoms
    )
    if segment_index is None or distance_m > _DE_ROADWORKS_MAX_DISTANCE_M:
        return None

    impact_symbols = entry.get("impact", {}).get("symbols", [])
    sperrungstyp = (
        ClosureType.PARTIALLY_CLOSED
        if "CLOSED" in impact_symbols
        else ClosureType.TEMPORARY_SPEED_LIMIT
    )

    start_timestamp = entry.get("startTimestamp")
    gueltig_von = (
        _parse_autobahn_timestamp(start_timestamp) if start_timestamp else datetime.now(UTC)
    )

    # Direction-aware matching is NOT POSSIBLE for DE roadworks:
    # the Autobahn GmbH API provides only a single Point coordinate
    # (no LineString geometry, no direction/carriageway field).
    # Proximity-only matching (500 m threshold) is the best available
    # heuristic.  This is a known data-source limitation.
    segment_length = (
        route.segments[segment_index].length_m if 0 <= segment_index < len(route.segments) else None
    )

    return ConstructionZone(
        betroffene_segmente=[segment_index],
        speed_limit_kmh=_DE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH,
        closure_type=sperrungstyp,
        umleitungshinweis=None,
        land=Land.DE,
        gueltig_von=gueltig_von,
        gueltig_bis=None,
        length_m=segment_length,
    )


class AutobahnConstructionProvider(ConstructionProvider):
    """DE-only construction-zone provider backed by the Autobahn GmbH open API.

    Superseded by `providers_de_datexii.DatexIIGermanyConstructionprovider`;
    kept for revert. Only ever returns zones for `Land.DE` — any other
    country in `countries` is ignored.
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
            cache_ttl_seconds: TTL in seconds for the persistent roadworks cache.
            cache_dir: Directory for the SQLite-backed cache database.
                Defaults to the project's default cache directory when None.
        """
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        cache_path = f"{cache_dir}/construction_cache.sqlite" if cache_dir else None
        self._cache = TTLCache(
            namespace="construction_zones_de_autobahn",
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
        """Fetch roadworks along the route from the Autobahn GmbH API.

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
        strtree, segment_geoms = matching.build_strtree(route.segments)
        return await self._fetch_de_roadworks(route, strtree, segment_geoms)

    async def _fetch_de_roadworks(
        self,
        route: routing_models.Route,
        strtree: STRtree,
        segment_geoms: list[LineString],
    ) -> list[ConstructionZone]:
        """Fetch and parse roadworks from the Autobahn GmbH open API for DE.

        Extracts Autobahn IDs from ``segment.street_ref`` (GraphHopper
        ``street_ref`` path detail) so only the handful of motorways actually
        traversed by this route are queried — no more ~110 nationwide IDs.
        Roadworks are then matched against route segments via the shared
        STRtree spatial index.

        Args:
            route: The route to match roadworks against.
            strtree: Pre-built STRtree for the route segments.
            segment_geoms: Pre-built LineString list for route segments.

        Returns:
            List of matched ``ConstructionZone`` objects.
        """
        # 1. Extract only the Autobahn IDs that this route actually uses.
        autobahn_ids = _extract_autobahn_ids(route)

        if not autobahn_ids:
            logger.debug("Construction API DE (Autobahn): no Autobahn IDs on route")
            return []

        # 2. Fetch roadworks for each identified Autobahn in parallel.
        responses = await asyncio.gather(
            *(self._fetch_autobahn_roadworks(aid) for aid in sorted(autobahn_ids)),
            return_exceptions=True,
        )

        # 3. Parse and match roadworks to route segments.
        zones: list[ConstructionZone] = []
        for autobahn_id, response in zip(sorted(autobahn_ids), responses, strict=True):
            if isinstance(response, BaseException):
                logger.warning(
                    "Construction API DE (Autobahn, %s): request failed: %s",
                    autobahn_id,
                    response,
                )
                continue
            for entry in response:
                zone = _parse_autobahn_roadwork(entry, route, strtree, segment_geoms)
                if zone is not None:
                    zones.append(zone)

        return zones

    async def _fetch_autobahn_roadworks(self, autobahn_id: str) -> list[dict[str, Any]]:
        """GET roadworks for a single Autobahn ID; no auth headers/query params.

        Cached per ``autobahn_id`` in the persistent TTL cache.
        """
        cache_key = f"autobahn:{autobahn_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Construction cache hit: %s", cache_key)
            return cached  # type: ignore[return-value]
        response = await self._client.get(f"{AUTOBAHN_BASE_URL}/{autobahn_id}/services/roadworks")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        roadworks: list[dict[str, Any]] = data.get("roadworks", [])
        self._cache.set(cache_key, roadworks)
        return roadworks
