"""Provider-Implementierungen für construction-Daten.

Enthält:
- ConstructionProviderConfig: Konfiguration für externe APIs
- ConstructionProviderImpl: Orchestriert DE/DK/SE. DK/SE werden hier direkt
  über DATEX II Feeds implementiert; DE wird an einen injizierbaren
  `ConstructionProvider` delegiert (Default: `DatexIIGermanyConstructionProvider`
  in `providers_de_datexii.py`; `AutobahnConstructionProvider` in
  `providers_de_autobahn.py` bleibt für ein Revert verfügbar).
- FakeConstructionProvider: Fake-Provider für Tests
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel
from shapely import STRtree
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    import tripplanner.routing.models as routing_models

from tripplanner.cache.store import TTLCache
from tripplanner.construction import matching
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
from tripplanner.construction.providers_de_datexii import DatexIIGermanyConstructionProvider
from tripplanner.geo import geodesic_length_m

logger = logging.getLogger(__name__)

# DATEX II feed endpoints for the DK/SE flow. DE is handled by a separate,
# injectable provider (see module docstring), not a DATEX II endpoint here.
DATEXII_ENDPOINTS: dict[Land, str] = {
    Land.DK: "https://businessservice.dataudveksler.app.vd.dk/api/DateX2",
    Land.SE: "https://api.trafikinfo.trafikverket.se/v2/data.json",
}

# SE AffectedDirectionValue that indicates both directions (no directional filter).
_SE_BOTH_DIRECTIONS_VALUES = frozenset(("BothDirections", "Båda riktningarna"))

# DK and SE DATEX II APIs expose no structured speed-limit field for roadwork
# impacts (delays element is often empty, and the impact structure lacks a
# numeric speed). 80 km/h is the standard real-world default speed limit at
# active roadworks absent more specific data.
_DK_SE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH = 80


# ---------------------------------------------------------------------------
# WKT helpers (unchanged from original)
# ---------------------------------------------------------------------------


def _parse_wkt_point(wkt: str) -> tuple[float, float] | None:
    """Parse a WKT ``POINT (lon lat)`` string into (lat, lon).

    Returns ``None`` if the WKT cannot be parsed.
    """
    try:
        wkt = wkt.strip()
        if not wkt.upper().startswith("POINT"):
            return None
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


# ---------------------------------------------------------------------------
# Trafikverket situation parser (unchanged from original)
# ---------------------------------------------------------------------------


def _parse_trafikverket_situations(
    data: dict[str, Any],
) -> list[DATEXIIConstructionZoneInternal]:
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

                if msg_type_value not in ROADWORKS_TYPES:
                    continue

                start_time = deviation.get("StartTime")
                end_time = deviation.get("EndTime")
                message = deviation.get("Message")
                geometry = deviation.get("Geometry", {})

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
                        tempolimit_kmh=None,
                        affected_direction_value=deviation.get("AffectedDirectionValue"),
                    )
                )

    return zones


# ---------------------------------------------------------------------------
# Provider config & protocol implementation
# ---------------------------------------------------------------------------


class ConstructionProviderConfig(BaseModel):
    """Konfiguration für den ConstructionProviderImpl (DK/SE DATEX-II-Pfad).

    DE benötigt keine Konfiguration hier (siehe `de_provider`-Parameter von
    `ConstructionProviderImpl.__init__`). DK nutzt OAuth2 client_credentials
    Flow über Azure AD (`dk_client_id`/`dk_secret`/`dk_tenant_id`), SE einen
    Trafikverket `authenticationkey` (`tv_api_key`).
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
    cache_ttl_seconds: float = 3600.0
    """TTL in seconds for the persistent cache."""
    cache_dir: str | None = None
    """Directory for the SQLite-backed cache database. Defaults to ``.cache``.

    If ``None``, the cache database is placed in the project's default cache
    directory (``.cache/construction_cache.sqlite``).
    """


class ConstructionProviderImpl(ConstructionProvider):
    """Implementierung des ConstructionProvider: DE (delegiert), DATEX II (DK, SE).

    DE-Baustellendaten werden an einen injizierbaren `ConstructionProvider`
    delegiert (`de_provider`), standardmäßig
    `providers_de_datexii.DatexIIGermanyConstructionProvider` (NRW
    Mobilitätsdaten DATEX II Export). `providers_de_autobahn.
    AutobahnConstructionProvider` bleibt für ein Revert verfügbar.
    """

    def __init__(
        self,
        config: ConstructionProviderConfig,
        client: httpx.AsyncClient | None = None,
        de_provider: ConstructionProvider | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            config: Provider configuration (credentials, timeout) for the
                DK/SE DATEX II paths.
            client: Optional pre-opened httpx.AsyncClient for process-wide
                reuse (mirrors `OpenMeteoProvider.__init__`). When None, a new
                client is created eagerly so the provider also works without
                `async with`. Also passed to the default `de_provider`.
            de_provider: Provider used for `Land.DE`. Defaults to
                `DatexIIGermanyConstructionProvider` (NRW Mobilitätsdaten
                DATEX II export). Pass `AutobahnConstructionProvider()` to
                revert to the Autobahn GmbH open API.
        """
        self._config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
        cache_path = f"{config.cache_dir}/construction_cache.sqlite" if config.cache_dir else None
        self._cache = TTLCache(
            namespace="construction_zones",
            ttl_seconds=config.cache_ttl_seconds,
            db_path=cache_path,
        )
        self._de_provider: ConstructionProvider = de_provider or DatexIIGermanyConstructionProvider(
            client=self._client,
            cache_ttl_seconds=config.cache_ttl_seconds,
            cache_dir=config.cache_dir,
        )

    async def __aenter__(self) -> ConstructionProviderImpl:
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def _has_credentials(self, country: Land) -> bool:
        """Check whether the configured credentials suffice for `country`.

        DE is always True (delegated to `self._de_provider`, which manages
        its own credential requirements, if any). DK requires dk_client_id,
        dk_secret, AND dk_tenant_id.
        """
        if country == Land.DK:
            return bool(
                self._config.dk_client_id and self._config.dk_secret and self._config.dk_tenant_id
            )
        if country == Land.SE:
            return bool(self._config.tv_api_key)
        return True

    @staticmethod
    def _build_strtree(
        segments: list[routing_models.RouteSegment],
    ) -> tuple[STRtree, list[LineString]]:
        """Build an STRtree spatial index from route segments **once**.

        Thin wrapper around `matching.build_strtree` kept as a method for
        backward-compatible test access.
        """
        return matching.build_strtree(segments)

    @staticmethod
    def _match_geometry_to_segments(
        zone_geom: BaseGeometry,
        strtree: STRtree,
        segment_geoms: list[LineString],
        max_distance_m: float = matching.MAX_DISTANCE_M,
    ) -> list[int]:
        """Match a zone geometry to route segments via STRtree + distance threshold.

        Thin wrapper around `matching.match_geometry_to_segments` kept as a
        method for backward-compatible test access.
        """
        return matching.match_geometry_to_segments(
            zone_geom, strtree, segment_geoms, max_distance_m
        )

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        countries: list[Land],
    ) -> list[ConstructionZone]:
        """Fetch roadworks along the route for the given countries.

        Countries missing required credentials (DK/SE) are skipped with a
        logged warning instead of sending a request that would fail with
        401/403 anyway.

        All configured countries are fetched **concurrently** via
        ``asyncio.gather``.  A single STRtree is built once and shared
        across all matching calls within this invocation.

        Args:
            route: The route to match construction zones against.
            countries: List of countries to fetch construction data for.

        Returns:
            List of ``ConstructionZone`` objects matched to route segments.
        """
        # DE is delegated to `self._de_provider`, which builds and uses its
        # own spatial index; the STRtree built here is only needed by DK/SE,
        # so it's skipped entirely for DE-only requests.
        strtree: STRtree | None = None
        segment_geoms: list[LineString] | None = None
        if any(c in (Land.DK, Land.SE) for c in countries):
            strtree, segment_geoms = self._build_strtree(route.segments)

        tasks: list[tuple[Land, Any]] = []
        for country in countries:
            if not self._has_credentials(country):
                logger.warning(
                    "Construction API %s: missing credentials, skipping.",
                    country,
                )
                continue
            if country == Land.DE:
                tasks.append(
                    (country, self._de_provider.fetch_construction_zones(route, [country]))
                )
            else:
                assert strtree is not None
                assert segment_geoms is not None
                tasks.append(
                    (country, self._fetch_landscape_zones(route, country, strtree, segment_geoms))
                )

        results = await asyncio.gather(
            *(t[1] for t in tasks),
            return_exceptions=True,
        )

        all_zones: list[ConstructionZone] = []
        for (country, _), result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Construction API %s: request failed: %s",
                    country,
                    result,
                )
                continue
            all_zones.extend(result)

        return all_zones

    @staticmethod
    def _build_landscape_cache_key(land: Land, route: routing_models.Route) -> str:
        """Build a coarse-grained cache key for DK/SE DATEX II queries.

        Rounds bounding-box coordinates to 0.5 degree grid so nearby or
        overlapping routes share the same cache entry.  The key format is
        ``{land}:{min_lat},{min_lon},{max_lat},{max_lon}``.
        """
        COARSE = 0.5

        def _round(v: float) -> float:
            return round(v / COARSE) * COARSE

        all_coords: list[tuple[float, float]] = []
        for seg in route.segments:
            all_coords.extend(seg.geometrie)
        if not all_coords:
            return f"{land}:0,0,0,0"
        lats = [c[0] for c in all_coords]
        lons = [c[1] for c in all_coords]
        min_lat = _round(min(lats))
        min_lon = _round(min(lons))
        max_lat = _round(max(lats))
        max_lon = _round(max(lons))
        return f"{land}:{min_lat},{min_lon},{max_lat},{max_lon}"

    async def _fetch_landscape_zones(
        self,
        route: routing_models.Route,
        land: Land,
        strtree: STRtree,
        segment_geoms: list[LineString],
    ) -> list[ConstructionZone]:
        """Abfrage und Parsing f�r ein DATEX-II-Land (DK, SE).

        Cached per country + coarse bounding-box so nearby/similar routes
        hit the same cache entry.
        """
        cache_key = self._build_landscape_cache_key(land, route)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Construction cache hit: %s", cache_key)
            return [ConstructionZone.model_validate(item) for item in cached]  # type: ignore[attr-defined]

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
                    headers={
                        "Content-Type": "text/xml",
                        "Accept": "application/xml",
                    },
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

        result = []
        for zone in construction_zones:
            segment_ids = self._match_zones_to_segment_ids(
                zone,
                strtree,
                segment_geoms,
                route.segments,
            )
            if len(zone.koordinaten) >= 2:
                laenge_m: float | None = geodesic_length_m(zone.koordinaten)
            elif segment_ids:
                laenge_m = sum(
                    route.segments[i].laenge_m for i in segment_ids if 0 <= i < len(route.segments)
                )
            else:
                laenge_m = None
            result.append(
                ConstructionZone(
                    betroffene_segmente=segment_ids,
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
                    laenge_m=laenge_m,
                )
            )
        self._cache.set(cache_key, [z.model_dump(mode="json") for z in result])
        return result

    _AZURE_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
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

        Args:
            None.

        Returns:
            The access token string.
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
        """Build the Trafikverket v2 ``data.json`` POST body for Situations.

        Args:
            route: The route to build the bounding box from.

        Returns:
            XML body for the Trafikverket API request.
        """
        bbox = self._route_to_bounding_box(route)
        min_lat, min_lon, max_lat, max_lon = bbox.split(",", 3)
        wkt_box = f"{min_lon} {min_lat}, {max_lon} {max_lat}"
        return (
            "<REQUEST>"
            f'<LOGIN authenticationkey="{self._config.tv_api_key}"/>'
            '<QUERY objecttype="Situation" schemaversion="1.6" '
            'namespace="road.trafficinfo">'
            "<FILTER>"
            f'<WITHIN name="Deviation.Geometry.Point.WGS84" shape="box" '
            f'value="{wkt_box}"/>'
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

    def _match_zones_to_segment_ids(
        self,
        zone: DATEXIIConstructionZoneInternal,
        strtree: STRtree,
        segment_geoms: list[LineString],
        route_segments: list[routing_models.RouteSegment] | None = None,
    ) -> list[int]:
        """Map a DATEX II zone to route segment IDs via distance and direction.

        Thin wrapper around `matching.match_zones_to_segment_ids` (with SE's
        "both directions" tokens) kept as a method for backward-compatible
        test access.
        """
        return matching.match_zones_to_segment_ids(
            zone,
            strtree,
            segment_geoms,
            route_segments,
            _SE_BOTH_DIRECTIONS_VALUES,
        )

    def _zone_to_geometry(self, zone: DATEXIIConstructionZoneInternal) -> BaseGeometry:
        """Convert DATEX II coordinates to a Shapely geometry.

        Thin wrapper around `matching.zone_to_geometry` kept as a method for
        backward-compatible test access.
        """
        return matching.zone_to_geometry(zone)

    def _map_closure_type(self, xsi_type: str) -> Sperrungstyp:
        """Map a DATEX II xsi:type value to the Sperrungstyp enum.

        Thin wrapper around `matching.map_closure_type` kept as a method for
        backward-compatible test access.
        """
        return matching.map_closure_type(xsi_type)

    def _filter_opposite_direction(
        self,
        zone: DATEXIIConstructionZoneInternal,
        segment_indices: list[int],
        route_segments: list[routing_models.RouteSegment],
    ) -> list[int]:
        """Filter out segment matches that are on the opposite carriageway.

        Thin wrapper around `matching.filter_opposite_direction` (with SE's
        "both directions" tokens) kept as a method for backward-compatible
        test access.
        """
        return matching.filter_opposite_direction(
            zone,
            segment_indices,
            route_segments,
            _SE_BOTH_DIRECTIONS_VALUES,
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
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Liefert die konfigurierten Test-Zonen, optional gefiltert nach Land."""
        self.fetch_construction_zones_calls.append((route, laender))
        if laender:
            return [zone for zone in self.test_zones if zone.land in laender]
        return self.test_zones.copy()
