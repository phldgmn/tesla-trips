"""Detection of ferry connections in a computed `Route`.

There is no static ferry registry: ferry sections are detected solely from the
`road_environment`/`street_name` fields that `GraphHopperRoutingProvider`
extracts per segment from the GraphHopper path details
`road_environment`/`street_name` (see
`docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md`).
"""

from __future__ import annotations

from tripplanner.routing.models import Coordinate, FerrySegment, Route, RouteSegment

FERRY_BUFFER_DEG: float = 0.005
"""Buffer (in decimal degrees, about 500 m at DE/DK/SE latitudes) around the exact
segment geometry of a detected ferry, so the GraphHopper custom-model area built
from it reliably covers the whole ferry line."""

UNNAMED_FERRY = "Unnamed ferry"


def detect_ferries(route: Route) -> list[FerrySegment]:
    """Group consecutive ferry segments of a route into `FerrySegment` entries.

    Makes one linear pass over `route.segments` and merges consecutive segments
    with `road_environment == "FERRY"` into one `FerrySegment` each (name from the
    first non-empty `street_name` of the run, otherwise "Unnamed ferry"; length
    as the sum of `length_m`; bounding box from all `geometrie` coordinates,
    buffered by `FERRY_BUFFER_DEG`).

    Args:
        route: An already computed route (e.g. from `RoutingProvider.berechne_route()`).

    Returns:
        Detected ferry connections in driving order. Empty if the route has no
        ferry segments or `road_environment` was unavailable (e.g.
        `FakeRoutingProvider` routes).
    """
    ferries: list[FerrySegment] = []
    current_run: list[RouteSegment] = []

    def finish_run() -> None:
        if current_run:
            ferries.append(run_to_ferry_segment(current_run))

    for segment in route.segments:
        if segment.road_environment == "FERRY":
            current_run.append(segment)
        else:
            finish_run()
            current_run = []
    finish_run()

    return ferries


def run_to_ferry_segment(run: list[RouteSegment]) -> FerrySegment:
    """Build a `FerrySegment` from a contiguous run of ferry `RouteSegment`s."""
    name = next((s.street_name for s in run if s.street_name), None) or UNNAMED_FERRY
    length_m = sum(s.length_m for s in run)

    coordinates: list[Coordinate] = [coord for s in run for coord in s.geometrie]
    lats = [c[0] for c in coordinates]
    lons = [c[1] for c in coordinates]

    return FerrySegment(
        name=name,
        length_m=length_m,
        bbox_sw=(min(lats) - FERRY_BUFFER_DEG, min(lons) - FERRY_BUFFER_DEG),
        bbox_ne=(max(lats) + FERRY_BUFFER_DEG, max(lons) + FERRY_BUFFER_DEG),
        segment_index_start=run[0].segment_index,
        # Exclusive (like Python slices) so `route.segments[start:end]` is exactly
        # the ferry run - used by `optimization` to skip a user-fixed ferry
        # crossing in one jump during the state-graph search (see optimizer.py).
        segment_index_end=run[-1].segment_index + 1,
    )
