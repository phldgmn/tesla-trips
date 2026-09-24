"""Shared route-geometry helpers for computing detour anchor points.

Used both by `trip_input.api._step_route_charging_detours` (post-hoc detour
geometry for the map) and `optimization.detour_routing.precompute_detour_costs`
(pre-search detour cost estimation) - both need the same "two distinct,
already-directional points on the main route, well clear of the actual
branch-off point" anchoring to avoid GraphHopper direction ambiguity.
"""

from __future__ import annotations

from tripplanner.routing.models import Route


def find_bracket_points(
    route: Route, segment_index: int, margin_m: float = 3000.0
) -> tuple[int, int]:
    """Finds two points on `route.geometrie` well BEFORE/AFTER `segment_index`.

    A detour request with `start == destination` (the same point) is direction-
    ambiguous for GraphHopper: the router snaps the point onto the nearest
    road WITHOUT knowing which way the trip actually goes, and can end up
    driving past the correct exit to the next one just to turn around. Two
    DIFFERENT points that already lie on the main route in the correct
    direction of travel (at least `margin_m` before/after the actual branch
    point) fix the direction unambiguously instead - no heading parameter
    needed. `margin_m` must be generous enough to include an actually-usable
    highway exit in both directions: with too tight a margin (empirically
    tested with 500 m), both bracket points often land BEFORE the next real
    exit, forcing GraphHopper into a multi-kilometer detour via the next
    exit and back - verified live against the project's GraphHopper server
    (detour/straight-line ratio dropped from up to 20x at 500 m to ~1.2-2x
    at 3000 m).

    Args:
        route: The main route.
        segment_index: The decision-point segment index to bracket.
        margin_m: Minimum distance (meters) each bracket point must be from
            `segment_index` along the route.

    Returns:
        `(vor_index, nach_index)`: indices into `route.geometrie`.
    """
    last_index = len(route.geometrie) - 1
    segment_index = min(segment_index, len(route.segments) - 1)

    vor_index = segment_index
    distanz_zurueck = 0.0
    while vor_index > 0 and distanz_zurueck < margin_m:
        vor_index -= 1
        distanz_zurueck += route.segments[vor_index].length_m

    nach_index = segment_index
    distanz_vor = 0.0
    while nach_index < last_index and distanz_vor < margin_m:
        distanz_vor += route.segments[nach_index].length_m
        nach_index += 1

    return vor_index, nach_index
