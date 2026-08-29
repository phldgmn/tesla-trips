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


def map_waypoints_to_segments(waypoints, segments, route) -> list[int]:
    """Ermittelt fuer jeden Waypoint den Segment-Index, an dem er liegt.

    Bevorzugt `route.via_point_indices` - vom Routing-Provider EXAKT
    gelieferte Segment-Indizes (bei GraphHopper aus der "reached via
    point"-Instruktion, sign=5; siehe `GraphHopperRoutingProvider.
    _map_path_to_route`) statt einer reinen Naechster-Punkt-Suche, die
    auf sich selbst kreuzenden/schleifenden Routen mehrdeutig waere
    (siehe `waypoint_to_segment`-Docstring). Faellt auf die monoton
    fortschreitende Naechster-Punkt-Suche zurueck, falls ein Provider
    keine (oder eine unpassende Anzahl) `via_point_indices` liefert
    (z. B. ein zukuenftiger/alternativer Provider ohne diese Information).

    Args:
        waypoints: Liste der Waypoints.
        segments: Liste aller Route-Segmente.
        route: Die Route mit optionalen `via_point_indices`.

    Returns:
        Segment-Index fuer jeden Waypoint in derselben Reihenfolge.
    """
    if len(route.via_point_indices) == len(waypoints):
        max_idx = len(segments) - 1
        return [min(idx, max_idx) for idx in route.via_point_indices]

    indices: list[int] = []
    next_search_start_idx = 0
    for wp in waypoints:
        seg_idx = waypoint_to_segment(wp, segments, next_search_start_idx)
        next_search_start_idx = seg_idx
        indices.append(seg_idx)
    return indices


def waypoint_to_segment(
    waypoint: "Waypoint",
    segments: list[RouteSegment],
    min_seg_idx: int = 0,
) -> int:
    """Ermittle das Segment, das einem Waypoint am nächsten liegt.

    Sucht nur ab `min_seg_idx` (Segmente vor dem vorherigen, in Fahrt-
    richtung bereits zugeordneten Waypoint werden ausgeschlossen).
    `RouteSegment`s sind einer pro GraphHopper-Polyline-Punktpaar (siehe
    `GraphHopperRoutingProvider._map_path_to_route`), also sehr
    feingranular - eine reine Distanzsuche ueber ALLE Segmente kann bei
    sich kreuzenden/parallel verlaufenden Strassen (z. B. eine Route, die
    nahe an einer bereits befahrenen Kreuzung vorbeikommt) faelschlich
    einen geometrisch nahen, aber entlang der Route weit entfernten
    Punkt treffen - sichtbar u. a. als falscher SoC-Gradient-Sprung weit
    vor/hinter dem tatsaechlichen Zwischenstopp auf der Karte. Da
    Zwischenstopps als GraphHopper-Via-Punkte in Anfragereihenfolge in
    die Route geroutet werden (siehe `GraphHopperRoutingProvider.
    berechne_route`), muessen sie auch entlang der Route in dieser
    Reihenfolge auftreten - ein monoton steigender Suchstart pro
    Waypoint erzwingt das.

    Args:
        waypoint: Der zu mappende Waypoint.
        segments: Liste aller Route-Segmente.
        min_seg_idx: Erster zu durchsuchender Segment-Index.

    Returns:
        Der Segment-Index des Waypoints.
    """
    wp_coord = waypoint.koordinate

    min_dist = float("inf")
    closest_seg_idx = min_seg_idx

    for idx in range(min_seg_idx, len(segments)):
        # Benutze den Segment-Startpunkt als Referenz
        seg_start = segments[idx].geometrie[0]
        dist = haversine_distance_m(wp_coord, seg_start)
        if dist < min_dist:
            min_dist = dist
            closest_seg_idx = idx

    return closest_seg_idx
