"""Tests for `routing.detour_geometry.find_bracket_points`."""

from __future__ import annotations

from tripplanner.routing.detour_geometry import find_bracket_points
from tripplanner.routing.models import Route, RouteSegment


def test_find_edges_walk_at_least_margin_in_both_directions() -> None:
    """`find_bracket_points` returns two points that together lie at least
    `margin_m` BEFORE AND AFTER the branch-off point - fixes the direction of
    travel for detour routing (see `_step_route_charging_detours`).
    """
    # 10 equally long 100m segments along a meridian (11 points).
    geometrie = [(52.0 + i * 0.0009, 13.0) for i in range(11)]
    segments = [
        RouteSegment(
            segment_index=i,
            geometrie=[geometrie[i], geometrie[i + 1]],
            length_m=100.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )
        for i in range(10)
    ]
    route = Route(segments=segments, gesamtlaenge_m=1000.0, geometrie=geometrie)

    vor_index, nach_index = find_bracket_points(route, segment_index=5, margin_m=250.0)

    assert vor_index < 5 < nach_index
    distanz_zurueck = sum(seg.length_m for seg in segments[vor_index:5])
    distanz_vor = sum(seg.length_m for seg in segments[5:nach_index])
    assert distanz_zurueck >= 250.0
    assert distanz_vor >= 250.0


def test_bracket_points_clamped_at_route_edges() -> None:
    """Near the start/end of the route the bracket points are clamped to the
    actual edge instead of throwing an index error.
    """
    geometrie = [(52.0 + i * 0.0009, 13.0) for i in range(5)]
    segments = [
        RouteSegment(
            segment_index=i,
            geometrie=[geometrie[i], geometrie[i + 1]],
            length_m=100.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )
        for i in range(4)
    ]
    route = Route(segments=segments, gesamtlaenge_m=400.0, geometrie=geometrie)

    vor_index, nach_index = find_bracket_points(route, segment_index=1, margin_m=10_000.0)

    assert vor_index == 0
    assert nach_index == len(geometrie) - 1
