"""Shared logic for mapping charging stations onto the nearest route segment.

Extracted from `NetworkXOptimizer` so `optimization.detour_routing.
precompute_detour_costs` can compute the same `(segment_index,
offroute_distance_m)` mapping BEFORE `NetworkXOptimizer.optimize()` runs -
the state-graph search needs it internally too, and the detour
precomputation must happen strictly earlier (it feeds `optimize()`'s
`detour_kosten` parameter). `NetworkXOptimizer._map_stations_to_segments`
delegates to `map_stations_to_segments` below so there is exactly one
implementation.
"""

from __future__ import annotations

from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.geo import haversine_distance_m
from tripplanner.routing.models import RouteSegment


def map_station_to_segment(
    station: ChargingStation, segments: list[RouteSegment]
) -> tuple[int, float]:
    """Finds the route segment nearest to `station` and the distance to it.

    Args:
        station: The charging station to place on the route.
        segments: All raw route segments (each segment's `geometrie` is
            checked point by point).

    Returns:
        `(closest_seg_idx, min_dist_m)`: the index of the nearest segment
        and the straight-line (haversine) distance in meters from `station`
        to the nearest point in that segment's geometry.
    """
    station_coord = station.coordinate

    min_dist = float("inf")
    closest_seg_idx = 0

    for idx, seg in enumerate(segments):
        for coord in seg.geometrie:
            dist = haversine_distance_m(station_coord, coord)
            if dist < min_dist:
                min_dist = dist
                closest_seg_idx = idx

    return closest_seg_idx, min_dist


def map_stations_to_segments(
    stations: list[ChargingStation], segments: list[RouteSegment]
) -> dict[int, list[tuple[ChargingStation, float]]]:
    """Maps every station onto its nearest route segment.

    Each entry also carries the straight-line distance (meters) between the
    station and the nearest route point - the basis for detour cost
    estimation, whether via the heuristic fallback in
    `NetworkXOptimizer._detour_kosten` or the real routed cost from
    `optimization.detour_routing.precompute_detour_costs`.

    Args:
        stations: Candidate charging stations along the route.
        segments: All raw route segments.

    Returns:
        Mapping from segment index to the list of `(station,
        offroute_distance_m)` pairs whose nearest segment is that index.
    """
    station_map: dict[int, list[tuple[ChargingStation, float]]] = {}

    for station in stations:
        best_seg_idx, offroute_distance_m = map_station_to_segment(station, segments)
        station_map.setdefault(best_seg_idx, []).append((station, offroute_distance_m))

    return station_map
