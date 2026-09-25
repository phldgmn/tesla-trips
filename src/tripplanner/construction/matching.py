"""Shared route-matching helpers for construction-zone providers.

Country-agnostic geometry/spatial-index logic used by every construction
provider (DE, DK, SE) to map a parsed construction zone onto the route
segments it affects. Extracted from `ConstructionproviderImpl` so DE-specific
providers (`providers_de_autobahn.py`, `providers_de_datexii.py`) can reuse
the exact same matching semantics without duplicating them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    import tripplanner.routing.models as routing_models

from tripplanner.construction.models import ClosureType
from tripplanner.construction.parser import DATEXIIConstructionZoneInternal
from tripplanner.geo import Coordinate, bearing_deg, haversine_distance_m

# Direction-aware matching constants.
# The zone's own bearing (start->end of its geometry) is compared against
# the matched route segment's `bearing_deg`; the raw absolute difference is
# folded into the [0°, 180°] range (`angular_diff`), then a match is
# excluded if `angular_diff` exceeds this threshold — i.e. the zone's
# direction is roughly reversed relative to the route's direction of travel
# (opposite carriageway on a divided highway). 100° leaves headroom for
# normal route curvature (which causes smaller bearing drift) while still
# catching true opposite-direction traffic.
DIRECTION_OPPOSITE_LOW = 100.0

# Shared across DE, DK, and SE matching paths.
MAX_DISTANCE_M = 500.0


def build_strtree(
    segments: list[routing_models.RouteSegment],
) -> tuple[STRtree, list[LineString]]:
    """Build an STRtree spatial index from route segments **once**.

    segment geometries are in ``(lon, lat)`` order for Shapely,
    converting from the ``(lat, lon)`` order used in
    ``Routesegment.geometrie``.

    Args:
        segments: Route segments to index.

    Returns:
        Tuple of ``(STRtree, list[LineString])``.
    """
    geoms: list[LineString] = [
        LineString((pt[1], pt[0]) for pt in seg.geometrie) for seg in segments
    ]
    return STRtree(geoms), geoms


def match_geometry_to_segments(
    zone_geom: BaseGeometry,
    strtree: STRtree,
    segment_geoms: list[LineString],
    max_distance_m: float = MAX_DISTANCE_M,
) -> list[int]:
    """Match a zone geometry to route segments via STRtree + distance threshold.

    Uses ``dwithin`` for an O(log n) candidate query with a distance
    threshold.  This finds segments within *max_distance_m* (converted
    to degrees) of the zone, including zones that are near but whose
    bounding boxes don't overlap.

    Args:
        zone_geom: The zone geometry (Point or LineString).
        strtree: The pre-built STRtree from route segment geometries.
        segment_geoms: The list of ``LineString`` objects for each route segment.
        max_distance_m: Maximum distance in metres for a match.

    Returns:
        List of segment indices that match within the distance threshold.
    """
    if not segment_geoms:
        return []

    distance_deg = max_distance_m / 111_320.0
    try:
        indices = list(strtree.query(zone_geom, predicate="dwithin", distance=distance_deg))
    except (AttributeError, IndexError):
        indices = list(range(len(segment_geoms)))

    return [int(idx) for idx in indices]


def nearest_segment_index(
    point: Coordinate,
    segments: list[routing_models.RouteSegment],
    strtree: STRtree | None = None,
    segment_geoms: list[LineString] | None = None,
) -> tuple[int | None, float]:
    """Find the route segment nearest to `point` via STRtree or haversine.

    Args:
        point: The ``(lat, lon)`` coordinate to match.
        segments: The route segments to search.
        strtree: Optional pre-built STRtree for fast candidate retrieval.
        segment_geoms: Optional pre-built LineString list matching *segments*.

    Returns:
        ``(best_index, best_distance_m)`` or ``(None, inf)`` if no match.
    """
    if not segments:
        return None, float("inf")

    if strtree is not None and segment_geoms is not None:
        point_geom = Point((point[1], point[0]))
        try:
            indices = list(strtree.nearest(point_geom))
            candidate_indices = [int(idx) for idx in indices]
        except (AttributeError, IndexError, ValueError, TypeError):
            candidate_indices = []
        if not candidate_indices:
            candidate_indices = list(range(len(segments)))

        best_idx: int | None = None
        best_dist = float("inf")
        for idx in candidate_indices:
            if idx < 0 or idx >= len(segments):
                continue
            dist = haversine_distance_m(point, segments[idx].geometrie[0])
            if dist < best_dist:
                best_dist, best_idx = dist, idx
        return best_idx, best_dist

    best_idx, best_dist = 0, float("inf")
    for idx, segment in enumerate(segments):
        for vertex in (segment.geometrie[0], segment.geometrie[-1]):
            dist = haversine_distance_m(point, vertex)
            if dist < best_dist:
                best_dist, best_idx = dist, idx
    return best_idx, best_dist


def zone_to_geometry(zone: DATEXIIConstructionZoneInternal) -> BaseGeometry:
    """Convert DATEX II coordinates to a Shapely geometry.

    DATEX II coordinates are in ``(lat, lon)`` order (as stored in
    ``zone.koordinaten``); this method converts to ``(lon, lat)`` for
    Shapely.

    Args:
        zone: The DATEX II zone internal record.

    Returns:
        A Shapely geometry in ``(lon, lat)`` order.
    """
    coords = [(pt[1], pt[0]) for pt in zone.koordinaten]
    if len(coords) == 1:
        return Point(coords[0])
    return LineString(coords)


def map_closure_type(xsi_type: str) -> ClosureType:
    """Map a DATEX II xsi:type value to the ClosureType enum."""
    type_map = {
        "fullyClosed": ClosureType.FULLY_CLOSED,
        "partiallyClosed": ClosureType.PARTIALLY_CLOSED,
        "laneClosed": ClosureType.LANE_CLOSED,
        "temporarySpeedLimit": ClosureType.TEMPORARY_SPEED_LIMIT,
        "reducedLanes": ClosureType.REDUCED_LANES,
        "detrourRequired": ClosureType.DETOUR_REQUIRED,
        "Roadworks": ClosureType.PARTIALLY_CLOSED,
        "MaintenanceWorks": ClosureType.PARTIALLY_CLOSED,
        "ConstructionWorks": ClosureType.PARTIALLY_CLOSED,
        "RoadOrCarriagewayOrLaneManagement": ClosureType.PARTIALLY_CLOSED,
    }
    return type_map.get(xsi_type, ClosureType.PARTIALLY_CLOSED)


def filter_opposite_direction(
    zone: DATEXIIConstructionZoneInternal,
    segment_indices: list[int],
    route_segments: list[routing_models.RouteSegment],
    both_directions_values: frozenset[str] = frozenset(),
) -> list[int]:
    """Filter out segment matches that are on the opposite carriageway.

    For zones with LineString geometry, computes the zone's bearing from
    its first and last coordinates and compares it against each matched
    route segment's ``bearing_deg``.  segments whose bearing differs by
    more than 100° (after folding the raw difference into the
    [0°, 180°] range — i.e. roughly 180° apart, opposite direction on a
    divided highway) are excluded.

    If ``zone.affected_direction_value`` is set to anything other than a
    value in *both_directions_values*, the source direction is trusted and
    the geometry-bearing heuristic is skipped (used by SE's Trafikverket
    ``AffectedDirectionValue`` field; other sources pass an empty set here).

    Args:
        zone: The DATEX II zone with coordinate and direction metadata.
        segment_indices: segment indices already matched by distance.
        route_segments: Full route segments for bearing lookup.
        both_directions_values: Source-specific tokens meaning "both
            directions" (no directional filter should be applied).

    Returns:
        filterd list of segment indices, excluding opposite-direction matches.
    """
    zone_coords = zone.koordinaten
    zone_start, zone_end = zone_coords[0], zone_coords[-1]
    zone_bearing = bearing_deg(zone_start, zone_end)

    # Source-specific: if the source explicitly states a single direction,
    # trust that over geometry-bearing heuristics.
    affected_dir = zone.affected_direction_value
    if affected_dir and affected_dir not in both_directions_values:
        # Source states a specific direction — keep all distance matches.
        return segment_indices

    # Geometry-bearing heuristic: exclude opposite-direction segments.
    filtered: list[int] = []
    for idx in segment_indices:
        if idx < 0 or idx >= len(route_segments):
            continue
        seg = route_segments[idx]
        seg_bearing = seg.bearing_deg

        # Compute minimum angular difference on a circle.
        angular_diff = abs(zone_bearing - seg_bearing) % 360.0
        if angular_diff > 180.0:
            angular_diff = 360.0 - angular_diff

        # Exclude if bearings are roughly opposite (>100° apart).
        if angular_diff > DIRECTION_OPPOSITE_LOW:
            continue

        filtered.append(idx)

    return filtered


def match_zones_to_segment_ids(  # noqa: PLR0913, PLR0917
    zone: DATEXIIConstructionZoneInternal,
    strtree: STRtree,
    segment_geoms: list[LineString],
    route_segments: list[routing_models.RouteSegment] | None = None,
    both_directions_values: frozenset[str] = frozenset(),
    max_distance_m: float = MAX_DISTANCE_M,
) -> list[int]:
    """Map a DATEX II zone to route segment IDs via distance and direction.

    Uses the pre-built STRtree for O(log n) candidate retrieval, then
    filters with precise distance measurement.  For zones with LineString
    geometry, additionally filters out segments on the opposite
    carriageway (bearing diff greater than 100°, folded to the
    [0°, 180°] range) unless the source explicitly states both directions.

    Args:
        zone: The DATEX II zone to match.
        strtree: Shared STRtree spatial index.
        segment_geoms: Shared list of route segment geometries.
        route_segments: Optional route segments for bearing lookup
            (required for direction filtering).
        both_directions_values: Source-specific "both directions" tokens
            (see `filter_opposite_direction`).
        max_distance_m: Maximum distance in metres for a match.

    Returns:
        List of segment indices within *max_distance_m* of the zone
        and matching the zone's direction of travel.
    """
    zone_geom = zone_to_geometry(zone)
    indices = match_geometry_to_segments(zone_geom, strtree, segment_geoms, max_distance_m)

    # Direction-aware filtering for zones with LineString geometry.
    if len(zone.koordinaten) >= 2 and route_segments is not None and indices:
        indices = filter_opposite_direction(
            zone,
            indices,
            route_segments,
            both_directions_values,
        )

    return indices
