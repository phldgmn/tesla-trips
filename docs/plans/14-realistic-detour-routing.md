# Realistic Detour Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the charging-stop optimizer's straight-line-times-fixed-factor detour cost model (`DETOUR_ROUTENFAKTOR = 1.6`, `DETOUR_GESCHWINDIGKEIT_KMH = 70.0` in `optimizer.py`) with real, GraphHopper-routed, elevation-aware, physics-based detour distance/time/energy per candidate charging station — computed once before the search runs, not guessed from a fixed multiplier.

**Architecture:** Add a precomputation step that, for every candidate charging station, routes a synthetic "there and back" detour (main route → station → main route) through the same `RoutingProvider` → `ElevationProvider` → `calculate_segment_consumption` pipeline already used for the main route, run concurrently (bounded concurrency) for all stations in one batch before the optimizer's state-graph search starts. The optimizer looks up the real cost per station instead of computing the heuristic, falling back to the heuristic only for a station where real routing failed. The precomputation runs once per trip (not once per ETA/weather-convergence iteration), which requires hoisting the charging-station fetch out of the iteration loop.

**Tech Stack:** Python 3.14, asyncio, existing `tripplanner.routing`/`tripplanner.elevation`/`tripplanner.energy` modules, Pydantic.

**Spec:** Direct user request (this conversation): the optimizer's charging-stop trade-off decisions (e.g. whether to detour to a station 1.3 km off-route) were shown to hinge on a fixed straight-line heuristic that can be wrong by enough to flip a close decision — verified by hand-computing the real GraphHopper-routed detour cost for a specific station (Malmö-Toftanäs, 1.3 km off-route) and finding it added ~221 s of real drive time versus what the heuristic would estimate. User wants this replaced with a correct, graph/road-based calculation.

## Global Constraints (from `AGENTS.md`)

- `uv run hk check --all` must pass; `uv run pytest -m "not integration"` must pass.
- Coverage threshold 85% for `src/tripplanner/` is not undercut.
- New data structures crossing module boundaries only through the owning module's `models.py` (Pydantic models only for cross-module data).
- Every public function keeps full type annotations (`mypy --strict`) and a Google-style docstring.
- **New comments and docstrings are written in English**, per AGENTS.md, even though most of the surrounding code in this repo uses German docstrings/comments (legacy convention, not to be extended). New **identifiers** (function/variable names) should continue the codebase's existing German domain-term style (`Ladehalt`, `Abstecher`, `Ladezeit`, …) for consistency with the functions they sit next to — do not introduce a second, English-named parallel vocabulary for the same concepts.
- No live external-API calls from unit tests — use `FakeRoutingProvider` / `FakeDataSource` / local fixtures, matching `tests/trip_input/test_charging_detours.py`.
- Coordinate convention: `(lat, lon)` everywhere.
- No new external dependencies — this plan only reuses existing routing/elevation/energy providers.
- Commit in small, self-contained steps; run the affected tests before each commit.

---

## 1. Root cause (what's being replaced)

`src/tripplanner/optimization/optimizer.py:49-59`:

```python
DETOUR_ROUTENFAKTOR: float = 1.6
DETOUR_GESCHWINDIGKEIT_KMH: float = 70.0
```

`NetworkXOptimizer._detour_kosten` (`optimizer.py:1023-1053`) turns the straight-line distance between a candidate charging station and its nearest route point (`offroute_distance_m`, computed by `_station_to_segment` via haversine) into a detour cost using only these two constants:

```python
strecke_m = offroute_distance_m * DETOUR_ROUTENFAKTOR
detour_geschwindigkeit_m_s = DETOUR_GESCHWINDIGKEIT_KMH * 1000.0 / 3600.0
zeit_s = strecke_m / detour_geschwindigkeit_m_s
energie_kwh = strecke_m * self._avg_verbrauch_kwh_pro_m
```

This is a single fixed multiplier applied to every candidate station everywhere in the world, regardless of the actual road network. It is not connected to GraphHopper at all during the search. The codebase already has a proven, real-routing solution to the *identical* geometric problem — `_step_route_charging_detours` in `src/tripplanner/trip_input/api.py:492-597` — but it only runs *after* the optimizer has already committed to a plan, purely to render detour geometry on the map. The search itself never sees real numbers.

**Evidence this matters:** hand-computing the real routed detour to Malmö-Toftanäs (1.3 km off-route) for a real trip found it costs **~221 s** round-trip at real road speeds — the current heuristic would estimate `1300 m × 1.6 / (70 km/h) ≈ 107 s`, roughly half. On a route where competing options are within ~20 s of each other, an error of this size is large enough to flip which station the optimizer picks.

---

## 2. Target architecture

```
create_trip_simulation()
  ├─ Step 1: route_calculate                     (unchanged)
  ├─ NEW: fetch_charging_stations                 (moved out of the iteration loop)
  ├─ NEW: precompute_detour_costs                 (new step, once per trip)
  ├─ loop (weather/ETA convergence, up to 3x):
  │    ├─ fetch_weather, construction_sites, calculate_segment_energy   (unchanged)
  │    └─ optimize_charging_plan
  │         └─ NetworkXOptimizer.optimize(..., detour_kosten=<precomputed dict>)
  │              └─ _detour_kosten(station_id, ...) looks up real cost,
  │                 falls back to the heuristic only if the station is missing
  │                 from the precomputed map (routing failed for it)
  └─ ...
```

New/moved files:

| File | Change |
|---|---|
| `src/tripplanner/routing/detour_geometry.py` | **New.** `_find_bracket_points` moved here (was private to `trip_input/api.py`), now a shared, public, pure function. |
| `src/tripplanner/optimization/station_mapping.py` | **New.** `map_station_to_segment` / `map_stations_to_segments` moved here (were private methods of `NetworkXOptimizer`), now shared, public, pure functions. `NetworkXOptimizer` delegates to them (behavior unchanged, verified by the existing test suite). |
| `src/tripplanner/optimization/detour_routing.py` | **New.** `precompute_detour_costs()` — the actual real-routing precomputation. |
| `src/tripplanner/optimization/models.py` | **Modify.** New `DetourKosten` Pydantic model. |
| `src/tripplanner/optimization/optimizer.py` | **Modify.** `_detour_kosten` takes a `station_id` + optional `detour_kosten` map; `optimize()` gains an optional `detour_kosten` parameter threaded through `_generate_graph` → `_add_charging_edges`. `_map_stations_to_segments`/`_station_to_segment`/`_haversine_distance` become thin delegators to the moved functions. |
| `src/tripplanner/optimization/__init__.py` | **Modify.** Export `DetourKosten`, `precompute_detour_costs`, `map_stations_to_segments`. |
| `src/tripplanner/trip_input/api.py` | **Modify.** `_step_route_charging_detours` imports `_find_bracket_points` from its new location instead of defining it. New `_step_8a_fetch_charging_stations` (station fetch, extracted verbatim from inside `_step_8_optimize_charging_plan`). `create_trip_simulation` calls station-fetch + `precompute_detour_costs` once before the loop; `_step_8_optimize_charging_plan` takes `charging_stations`/`detour_kosten` as parameters instead of fetching/computing them itself. |
| `tests/routing/test_detour_geometry.py` | **New.** Tests for the moved `_find_bracket_points` (mirrors its existing coverage in `test_charging_detours.py`, which keeps testing `_step_route_charging_detours` end-to-end). |
| `tests/optimization/test_station_mapping.py` | **New.** Tests for the moved station-mapping functions. |
| `tests/optimization/test_detour_routing.py` | **New.** Tests for `precompute_detour_costs` — real distance/time differs from the heuristic, concurrency fan-out, graceful degradation on routing failure. |
| `tests/optimization/test_optimization.py` | **Modify.** New tests for `_detour_kosten`'s real-vs-heuristic-fallback behavior. |
| `tests/trip_input/test_api.py` | **Modify.** New test: `charging_provider.get_stations_along_route` is called exactly once per `create_trip_simulation` call (not once per iteration). |

---

## Task 1: Move `_find_bracket_points` to a shared module

**Files:**

- Create: `src/tripplanner/routing/detour_geometry.py`
- Modify: `src/tripplanner/trip_input/api.py:448-489` (delete the function, import it instead)
- Test: `tests/routing/test_detour_geometry.py`

**Interfaces:**

- Produces: `find_bracket_points(route: Route, segment_index: int, margin_m: float = 3000.0) -> tuple[int, int]` — used by Task 3 (`precompute_detour_costs`) and by the existing `_step_route_charging_detours`.

- [ ] **Step 1: Create the new module with the moved function**

```python
"""Shared route-geometry helpers for computing detour anchor points.

Used both by `trip_input.api._step_route_charging_detours` (post-hoc detour
geometry for the map) and `optimization.detour_routing.precompute_detour_costs`
(pre-search detour cost estimation) — both need the same "two distinct,
already-directional points on the main route, well clear of the actual
branch-off point" anchoring to avoid GraphHopper direction ambiguity.
"""

from __future__ import annotations

from tripplanner.routing.models import Route


def find_bracket_points(
    route: Route, segment_index: int, margin_m: float = 3000.0
) -> tuple[int, int]:
    """Finds two points on `route.geometrie` well BEFORE/AFTER `segment_index`.

    A detour request with `start == ziel` (the same point) is direction-
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
        distanz_zurueck += route.segments[vor_index].laenge_m

    nach_index = segment_index
    distanz_vor = 0.0
    while nach_index < last_index and distanz_vor < margin_m:
        distanz_vor += route.segments[nach_index].laenge_m
        nach_index += 1

    return vor_index, nach_index
```

- [ ] **Step 2: Update `trip_input/api.py` to import instead of define**

In `src/tripplanner/trip_input/api.py`, delete the `_find_bracket_points` function body (lines 448-489) and replace every call site (`_find_bracket_points(...)`, there are 3 call sites inside `_step_route_charging_detours`) with `find_bracket_points(...)`. Add the import near the other `tripplanner.routing` imports:

```python
from tripplanner.routing.detour_geometry import find_bracket_points
```

- [ ] **Step 3: Write the test, moved verbatim from the existing coverage**

Check `tests/trip_input/test_charging_detours.py` for any test that directly exercises bracket-point selection (search for `bracket` or `_find_bracket_points`). If such direct-unit tests exist, move them into the new file below with updated imports; if only indirect coverage exists (via `_step_route_charging_detours`), write direct tests for the new pure function:

```python
"""Tests for `routing.detour_geometry.find_bracket_points`."""

from __future__ import annotations

from tripplanner.routing.detour_geometry import find_bracket_points
from tripplanner.routing.models import Route, RouteSegment


def _make_route(segment_length_m: float = 1000.0, count: int = 20) -> Route:
    segments = [
        RouteSegment(
            segment_index=i,
            geometrie=[(48.0 + i * 0.001, 11.0), (48.0 + (i + 1) * 0.001, 11.0)],
            laenge_m=segment_length_m,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )
        for i in range(count)
    ]
    return Route(
        segments=segments,
        gesamtlaenge_m=segment_length_m * count,
        geometrie=[(48.0 + i * 0.001, 11.0) for i in range(count + 1)],
    )


def test_bracket_points_span_at_least_margin_m() -> None:
    """With 1000 m segments and a 3000 m margin, brackets sit >= 3 segments away."""
    route = _make_route(segment_length_m=1000.0, count=20)

    vor_index, nach_index = find_bracket_points(route, segment_index=10, margin_m=3000.0)

    assert vor_index <= 7  # >= 3000m before segment 10 (3 segments of 1000m)
    assert nach_index >= 13  # >= 3000m after


def test_bracket_points_clamp_at_route_start() -> None:
    """A decision point near the route start clamps `vor_index` to 0, not negative."""
    route = _make_route(segment_length_m=1000.0, count=20)

    vor_index, _ = find_bracket_points(route, segment_index=1, margin_m=3000.0)

    assert vor_index == 0


def test_bracket_points_clamp_at_route_end() -> None:
    """A decision point near the route end clamps `nach_index` to the last point."""
    route = _make_route(segment_length_m=1000.0, count=20)

    _, nach_index = find_bracket_points(route, segment_index=19, margin_m=3000.0)

    assert nach_index == len(route.geometrie) - 1
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/routing/test_detour_geometry.py tests/trip_input/test_charging_detours.py -v`
Expected: all PASS (the moved function behaves identically — pure code motion, no logic change).

- [ ] **Step 5: Run full checks and commit**

Run: `uv run hk check --all && uv run pytest -m "not integration"`

```bash
git add src/tripplanner/routing/detour_geometry.py src/tripplanner/trip_input/api.py tests/routing/test_detour_geometry.py
git commit -m "refactor: move find_bracket_points to shared routing.detour_geometry module"
```

---

## Task 2: Extract station-to-segment mapping into a shared module

**Files:**

- Create: `src/tripplanner/optimization/station_mapping.py`
- Modify: `src/tripplanner/optimization/optimizer.py:318-371` (`_map_stations_to_segments`, `_station_to_segment`, `_haversine_distance` become delegators)
- Test: `tests/optimization/test_station_mapping.py`

**Interfaces:**

- Produces: `map_station_to_segment(station: ChargingStation, segments: list[RouteSegment]) -> tuple[int, float]`, `map_stations_to_segments(stations: list[ChargingStation], segments: list[RouteSegment]) -> dict[int, list[tuple[ChargingStation, float]]]` — used by Task 3 (needs `station_segments` *before* `optimizer.optimize()` runs) and by `NetworkXOptimizer` internally.

- [ ] **Step 1: Create the new module**

```python
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
```

- [ ] **Step 2: Make `NetworkXOptimizer` delegate to the shared functions**

In `src/tripplanner/optimization/optimizer.py`, add the import:

```python
from tripplanner.optimization.station_mapping import map_stations_to_segments
```

Replace the bodies of `_map_stations_to_segments` (`optimizer.py:318-336`) and `_station_to_segment` (`optimizer.py:338-355`) with delegators, and delete `_haversine_distance` (`optimizer.py:357-371`) entirely once nothing else in the file calls it (check with `grep -n "_haversine_distance" src/tripplanner/optimization/optimizer.py` before deleting):

```python
    def _map_stations_to_segments(
        self, stations: list[ChargingStation], segments: list[RouteSegment]
    ) -> dict[int, list[tuple[ChargingStation, float]]]:
        """Maps charging stations onto their nearest route segment.

        Delegates to `station_mapping.map_stations_to_segments` - see there
        for the shared implementation (also used by
        `optimization.detour_routing.precompute_detour_costs`, which needs
        the same mapping BEFORE `optimize()` runs).
        """
        return map_stations_to_segments(stations, segments)
```

Delete `_station_to_segment` and `_haversine_distance` if (and only if) `grep -n "_station_to_segment\|_haversine_distance" src/tripplanner/optimization/optimizer.py` shows no other call sites after removing `_map_stations_to_segments`'s old body. If `_haversine_distance` is used elsewhere in the file (e.g. by `_waypoint_to_segment`), keep it and only delegate `_map_stations_to_segments`/`_station_to_segment`.

- [ ] **Step 3: Write the test**

```python
"""Tests for `optimization.station_mapping`."""

from __future__ import annotations

from tripplanner.charging_infrastructure.models import ChargingStation, ConnectorType, StallType
from tripplanner.optimization.station_mapping import (
    map_station_to_segment,
    map_stations_to_segments,
)
from tripplanner.routing.models import RouteSegment


def _make_station(lat: float, lon: float, station_id: str = "s1") -> ChargingStation:
    return ChargingStation(
        station_id=station_id,
        name=station_id,
        coordinate=(lat, lon),
        stalls={StallType.V3: 4},
        max_ladeleistung_kw=250.0,
        connector_types=[ConnectorType.CCS2],
        country="DE",
    )


def _make_segments() -> list[RouteSegment]:
    return [
        RouteSegment(
            segment_index=0,
            geometrie=[(52.0, 13.0), (52.1, 13.0)],
            laenge_m=11_000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        ),
        RouteSegment(
            segment_index=1,
            geometrie=[(52.1, 13.0), (52.2, 13.0)],
            laenge_m=11_000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        ),
    ]


def test_map_station_to_segment_picks_nearest() -> None:
    segments = _make_segments()
    station = _make_station(52.19, 13.0)  # closest to segment 1's endpoint

    seg_idx, dist_m = map_station_to_segment(station, segments)

    assert seg_idx == 1
    assert dist_m < 2000.0


def test_map_stations_to_segments_groups_by_segment() -> None:
    segments = _make_segments()
    near_seg0 = _make_station(52.0, 13.0, station_id="a")
    near_seg1 = _make_station(52.2, 13.0, station_id="b")

    result = map_stations_to_segments([near_seg0, near_seg1], segments)

    assert result[0][0][0].station_id == "a"
    assert result[1][0][0].station_id == "b"
```

- [ ] **Step 4: Run tests, then the full optimizer suite to confirm zero behavior change**

Run: `uv run pytest tests/optimization/ -v`
Expected: all PASS, including the pre-existing `test_optimization.py` suite (this task is a pure refactor).

- [ ] **Step 5: Run full checks and commit**

```bash
git add src/tripplanner/optimization/station_mapping.py src/tripplanner/optimization/optimizer.py tests/optimization/test_station_mapping.py
git commit -m "refactor: extract station-to-segment mapping into shared station_mapping module"
```

---

## Task 3: Add the `DetourKosten` model

**Files:**

- Modify: `src/tripplanner/optimization/models.py`

**Interfaces:**

- Produces: `DetourKosten` — consumed by Task 4 (`precompute_detour_costs`) and Task 5 (`NetworkXOptimizer._detour_kosten`).

- [ ] **Step 1: Add the model**

Add near `OptimizationConstraints` in `src/tripplanner/optimization/models.py`:

```python
class DetourKosten(BaseModel):
    """Real, road-network-routed one-way detour cost for one charging station.

    Computed once, before the state-graph search runs, by
    `optimization.detour_routing.precompute_detour_costs` - replaces the
    straight-line heuristic (`DETOUR_ROUTENFAKTOR`/
    `DETOUR_GESCHWINDIGKEIT_KMH` in `optimizer.py`) with an actual
    GraphHopper-routed, elevation-aware detour computed the same way the
    main route's distance/time/energy are computed.

    Values represent the AVERAGE of the two directions of the round trip
    (main route -> station, station -> main route) - `NetworkXOptimizer`
    doubles this for the full round trip, mirroring the existing
    heuristic's calling convention (`_fuege_ladekante_hinzu`).
    """

    distanz_m: float = Field(ge=0.0, description="Average one-way routed distance in meters")
    zeit_s: float = Field(ge=0.0, description="Average one-way drive time in seconds")
    energie_kwh: float = Field(
        description=(
            "Average one-way energy consumption in kWh (may be negative if the "
            "detour road is net downhill, matching SegmentEnergyResult.energiebedarf_kwh "
            "sign convention)"
        )
    )
```

- [ ] **Step 2: Export it**

Add `DetourKosten` to the imports and `__all__` in `src/tripplanner/optimization/__init__.py`.

- [ ] **Step 3: Run checks and commit**

```bash
uv run mypy src && uv run pytest tests/optimization/ -v
git add src/tripplanner/optimization/models.py src/tripplanner/optimization/__init__.py
git commit -m "feat: add DetourKosten model for real routed detour costs"
```

---

## Task 4: Implement `precompute_detour_costs`

**Files:**

- Create: `src/tripplanner/optimization/detour_routing.py`
- Test: `tests/optimization/test_detour_routing.py`

**Interfaces:**

- Consumes: `find_bracket_points` (Task 1), `map_stations_to_segments`'s output shape (Task 2 — passed in already computed by the caller, not recomputed here), `RoutingProvider.berechne_route`, `ElevationProvider.get_elevation_profile`/`calculate_segment_gradients`, `energy.calculate_segment_consumption`.
- Produces: `precompute_detour_costs(...) -> dict[str, DetourKosten]` keyed by `station_id` — consumed by Task 6 (wiring into `trip_input/api.py`).

- [ ] **Step 1: Write the module**

```python
"""Precomputes real, road-network-routed detour costs for candidate charging
stations, once, before the state-graph search runs.

Mirrors `trip_input.api._step_route_charging_detours` (which does the same
two-leg routing for the FINAL chosen stops, for map display) but runs for
EVERY candidate station BEFORE the optimizer decides anything, feeding
`NetworkXOptimizer.optimize(detour_kosten=...)` so the search can weigh real
costs instead of the `DETOUR_ROUTENFAKTOR`/`DETOUR_GESCHWINDIGKEIT_KMH`
straight-line heuristic in `optimizer.py`.

Design notes:
- Weather is intentionally NOT re-fetched per detour: a short (typically
  < 5 km) detour's weather is well approximated by a neutral placeholder
  sample (same one used as the fallback in `trip_input.api._step_7_
  calculate_segment_energy` when no real weather sample is available for a
  segment) - re-querying a weather provider per candidate station would add
  dozens to hundreds of extra external API calls for no material accuracy
  gain, and risks the rate-limit issues already seen with weather providers
  in this project.
- Construction zones are intentionally NOT applied to detour segments for
  the same reason (short local roads, negligible probability/impact, and
  avoids re-running construction-zone matching per station).
- Stations within `ON_ROUTE_THRESHOLD_M` of the main route are treated as
  free (matching the existing heuristic's `offroute_distance_m <= 0.0`
  special case) and are not routed at all - saves a routing call per
  on-route station, and a "there and back" GraphHopper request for a near-
  zero-distance detour is not well-defined output-wise.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.elevation.elevation import ElevationProvider
from tripplanner.energy.energy import calculate_segment_consumption
from tripplanner.energy.models import VehicleEnergyParameters
from tripplanner.optimization.models import DetourKosten
from tripplanner.routing.detour_geometry import find_bracket_points
from tripplanner.routing.models import Route
from tripplanner.routing.providers import RoutingProvider
from tripplanner.trip_input.models import TripRequest, VehicleProfile
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents

logger = logging.getLogger(__name__)

ON_ROUTE_THRESHOLD_M: float = 100.0
"""Below this straight-line distance, a station is treated as effectively
on the route (zero detour cost) instead of being routed - matches
`NetworkXOptimizer._detour_kosten`'s `offroute_distance_m <= 0.0` free
case, widened slightly because GraphHopper snapping makes an exact 0.0
distance rare even for stations genuinely at a highway rest stop."""

_NEUTRAL_WEATHER_SAMPLE_KWARGS: dict[str, float] = {
    "temperatur_c": 20.0,
    "windgeschwindigkeit_ms": 5.0,
    "windrichtung_deg": 180.0,
    "niederschlag_mm": 0.0,
    "schneefall_cm": 0.0,
    "luftdruck_hpa": 1013.25,
    "luftfeuchtigkeit_pct": 60.0,
    "globalstrahlung_wm2": 400.0,
    "bewoelkung_pct": 20.0,
}
"""Same neutral placeholder values used as the missing-weather fallback in
`trip_input.api._step_7_calculate_segment_energy` - kept in sync
deliberately (both represent "no real weather signal available")."""


def _make_energy_params(vehicle_profile: VehicleProfile) -> VehicleEnergyParameters:
    """Builds `VehicleEnergyParameters` from a `VehicleProfile`.

    Duplicates the conversion in `trip_input.api._step_7_calculate_segment_
    energy` (that function is private and route-scoped, not reusable as-is)
    - kept as a single, obvious 8-line mapping rather than adding a shared
    helper for a conversion this small.
    """
    return VehicleEnergyParameters(
        masse_kg=vehicle_profile.masse_kg,
        cw_wert=vehicle_profile.cw_wert,
        stirnflaeche_m2=vehicle_profile.stirnflaeche_m2,
        rollwiderstandsbeiwert=vehicle_profile.rollwiderstandsbeiwert,
        batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
        nebenverbraucher_baseline_kw=vehicle_profile.nebenverbraucher_baseline_kw,
        reifentyp=vehicle_profile.reifentyp,
        dachbox=vehicle_profile.dachbox,
    )


async def _route_leg_kosten(
    leg_route: Route,
    elevation_provider: ElevationProvider,
    energy_params: VehicleEnergyParameters,
    abfahrtszeit: datetime,
) -> tuple[float, float, float]:
    """Computes (distanz_m, zeit_s, energie_kwh) for one already-routed leg.

    Reuses the exact same elevation -> gradient -> energy pipeline used for
    the main route (`_step_2_extract_elevation_profile` /
    `ElevationProvider.calculate_segment_gradients` /
    `calculate_segment_consumption`), applied to the leg's own segments.
    """
    if not leg_route.segments:
        return 0.0, 0.0, 0.0

    elevation_points = await elevation_provider.get_elevation_profile(leg_route)
    gradients = elevation_provider.calculate_segment_gradients(elevation_points, leg_route)

    distanz_m = 0.0
    zeit_s = 0.0
    energie_kwh = 0.0
    for idx, segment in enumerate(leg_route.segments):
        gradient = gradients[idx] if idx < len(gradients) else None
        wetter = WeatherSample(
            koordinate=segment.geometrie[0],
            zeitpunkt=abfahrtszeit,
            **_NEUTRAL_WEATHER_SAMPLE_KWARGS,
        )
        wind = WindComponents(
            segment_index=segment.segment_index, gegenwind_ms=0.0, seitenwind_ms=0.0
        )
        ergebnis = calculate_segment_consumption(
            segment=segment,
            gradient=gradient,  # type: ignore[arg-type]
            wetter=wetter,
            wind=wind,
            fahrzeug_params=energy_params,
            baustellen=None,
        )
        distanz_m += segment.laenge_m
        zeit_s += ergebnis.fahrzeit_s
        energie_kwh += ergebnis.energiebedarf_kwh

    return distanz_m, zeit_s, energie_kwh


async def precompute_detour_costs(
    routing_provider: RoutingProvider,
    elevation_provider: ElevationProvider,
    vehicle_profile: VehicleProfile,
    route: Route,
    station_segments: dict[int, list[tuple[ChargingStation, float]]],
    abfahrtszeit: datetime,
    max_concurrent_requests: int = 20,
) -> dict[str, DetourKosten]:
    """Computes real, road-network-routed detour costs for every candidate station.

    Routes a "there and back" detour (main route -> station -> main route)
    for each station whose straight-line distance from the route exceeds
    `ON_ROUTE_THRESHOLD_M`, through the same routing/elevation/energy
    pipeline used for the main route, all concurrently (bounded by
    `max_concurrent_requests`). A station whose routing fails (timeout, no
    route found, HTTP error) is simply OMITTED from the result - callers
    (`NetworkXOptimizer._detour_kosten`) fall back to the straight-line
    heuristic for that one station rather than failing the whole trip.

    Args:
        routing_provider: Same provider used for the main route.
        elevation_provider: Same provider used for the main route.
        vehicle_profile: The trip's vehicle profile.
        route: The already-computed main route (for bracket-point anchoring).
        station_segments: Output of `station_mapping.map_stations_to_segments`
            - maps each candidate station to its nearest main-route segment
            index (needed to place the detour's bracket points).
        abfahrtszeit: Trip departure time (used for the placeholder weather
            sample's timestamp field only - see module docstring on why
            detours don't fetch real weather).
        max_concurrent_requests: Upper bound on simultaneous routing calls
            in flight, to avoid overwhelming the routing server.

    Returns:
        Mapping from `station_id` to `DetourKosten` for every station that
        was successfully routed. Stations within `ON_ROUTE_THRESHOLD_M` and
        stations whose routing failed are absent from the result.
    """
    energy_params = _make_energy_params(vehicle_profile)
    semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def _kosten_fuer_station(
        station: ChargingStation, seg_idx: int
    ) -> tuple[str, DetourKosten] | None:
        vor_index, nach_index = find_bracket_points(route, seg_idx)
        hinweg_anfrage = TripRequest(
            start=route.geometrie[vor_index],
            ziel=station.coordinate,
            abfahrtszeit=abfahrtszeit,
            fahrzeugprofil=vehicle_profile,
        )
        rueckweg_anfrage = TripRequest(
            start=station.coordinate,
            ziel=route.geometrie[nach_index],
            abfahrtszeit=abfahrtszeit,
            fahrzeugprofil=vehicle_profile,
        )
        try:
            async with semaphore:
                hinweg_route, rueckweg_route = await asyncio.gather(
                    routing_provider.berechne_route(hinweg_anfrage),
                    routing_provider.berechne_route(rueckweg_anfrage),
                )
            hinweg_distanz, hinweg_zeit, hinweg_energie = await _route_leg_kosten(
                hinweg_route, elevation_provider, energy_params, abfahrtszeit
            )
            rueckweg_distanz, rueckweg_zeit, rueckweg_energie = await _route_leg_kosten(
                rueckweg_route, elevation_provider, energy_params, abfahrtszeit
            )
        except Exception:
            logger.warning(
                "Detour routing failed for station %s (%s) - falling back to the "
                "straight-line heuristic for this station.",
                station.station_id,
                station.name,
                exc_info=True,
            )
            return None

        return station.station_id, DetourKosten(
            distanz_m=(hinweg_distanz + rueckweg_distanz) / 2.0,
            zeit_s=(hinweg_zeit + rueckweg_zeit) / 2.0,
            energie_kwh=(hinweg_energie + rueckweg_energie) / 2.0,
        )

    tasks = [
        _kosten_fuer_station(station, seg_idx)
        for seg_idx, stations in station_segments.items()
        for station, offroute_distance_m in stations
        if offroute_distance_m > ON_ROUTE_THRESHOLD_M
    ]
    if not tasks:
        return {}

    results = await asyncio.gather(*tasks)
    return {station_id: kosten for r in results if r is not None for station_id, kosten in [r]}
```

- [ ] **Step 2: Write the failing tests first**

```python
"""Tests for `optimization.detour_routing.precompute_detour_costs`."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from tripplanner.charging_infrastructure.models import ChargingStation, ConnectorType, StallType
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.optimization.detour_routing import ON_ROUTE_THRESHOLD_M, precompute_detour_costs
from tripplanner.optimization.station_mapping import map_stations_to_segments
from tripplanner.routing import FakeRoutingProvider
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import TripRequest, VehicleProfile

VEHICLE_PROFILE = VehicleProfile(
    masse_kg=1800.0,
    cw_wert=0.23,
    stirnflaeche_m2=2.2,
    rollwiderstandsbeiwert=0.01,
    batteriekapazitaet_kwh=60.0,
    nebenverbraucher_baseline_kw=0.34,
    reifentyp="standard",
    dachbox=False,
)


def _make_route() -> Route:
    coords = [(48.0, 11.0), (48.5, 10.0), (49.0, 9.0)]
    segments = [
        RouteSegment(
            segment_index=i,
            geometrie=[coords[i], coords[i + 1]],
            laenge_m=50_000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )
        for i in range(len(coords) - 1)
    ]
    return Route(segments=segments, gesamtlaenge_m=100_000.0, geometrie=coords)


def _make_station(lat: float, lon: float, station_id: str = "s1") -> ChargingStation:
    return ChargingStation(
        station_id=station_id,
        name=station_id,
        coordinate=(lat, lon),
        stalls={StallType.V3: 4},
        max_ladeleistung_kw=250.0,
        connector_types=[ConnectorType.CCS2],
        country="DE",
    )


@pytest.mark.asyncio
async def test_precompute_returns_real_cost_per_station() -> None:
    route = _make_route()
    # ~5.5 km off-route (well above ON_ROUTE_THRESHOLD_M)
    station = _make_station(48.55, 10.05)
    station_segments = map_stations_to_segments([station], route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    result = await precompute_detour_costs(
        routing_provider=FakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert station.station_id in result
    assert result[station.station_id].distanz_m > 0.0
    assert result[station.station_id].zeit_s > 0.0


@pytest.mark.asyncio
async def test_precompute_skips_on_route_stations() -> None:
    route = _make_route()
    station = _make_station(48.0, 11.0)  # exactly on route
    station_segments = map_stations_to_segments([station], route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    result = await precompute_detour_costs(
        routing_provider=FakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert station.station_id not in result


@pytest.mark.asyncio
async def test_precompute_fans_out_concurrently() -> None:
    """N stations -> wall time close to ONE routing round trip, not N."""
    route = _make_route()
    stations = [_make_station(48.55, 10.0 + i * 0.01, station_id=f"s{i}") for i in range(10)]
    station_segments = map_stations_to_segments(stations, route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    class DelayedFakeRoutingProvider(FakeRoutingProvider):
        async def berechne_route(self, anfrage: TripRequest) -> Route:
            await asyncio.sleep(0.05)
            return await super().berechne_route(anfrage)

    start = asyncio.get_event_loop().time()
    result = await precompute_detour_costs(
        routing_provider=DelayedFakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        max_concurrent_requests=20,
    )
    elapsed = asyncio.get_event_loop().time() - start

    assert len(result) == 10
    # Sequential would be 10 stations * 2 legs * 0.05s = 1.0s; concurrent
    # should be close to 1 round trip (0.05s) plus overhead.
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_precompute_omits_station_on_routing_failure() -> None:
    route = _make_route()
    failing_station = _make_station(48.55, 10.05, station_id="fails")
    ok_station = _make_station(48.55, 10.06, station_id="ok")
    station_segments = map_stations_to_segments([failing_station, ok_station], route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    class PartiallyFailingRoutingProvider(FakeRoutingProvider):
        async def berechne_route(self, anfrage: TripRequest) -> Route:
            if anfrage.ziel == failing_station.coordinate or anfrage.start == failing_station.coordinate:
                raise RuntimeError("simulated routing failure")
            return await super().berechne_route(anfrage)

    result = await precompute_detour_costs(
        routing_provider=PartiallyFailingRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert "fails" not in result
    assert "ok" in result


@pytest.mark.asyncio
async def test_precompute_empty_station_segments_returns_empty_dict() -> None:
    route = _make_route()
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    result = await precompute_detour_costs(
        routing_provider=FakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments={},
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert result == {}
```

`FakeDataSource()` (imported from `tripplanner.elevation.providers`) takes no required constructor arguments, confirmed by its existing use in `tests/elevation/test_providers.py` and `tests/trip_input/test_api.py` — the fixture construction above is correct as written.

- [ ] **Step 3: Run tests, verify failures are import errors (module doesn't exist yet), then re-run after Step 1's module is in place**

Run: `uv run pytest tests/optimization/test_detour_routing.py -v`
Expected (before Step 1 exists): FAIL with `ModuleNotFoundError`.
After Step 1: PASS.

- [ ] **Step 4: Run mypy strictly on the new module**

Run: `uv run mypy src/tripplanner/optimization/detour_routing.py`
Expected: no errors. The only intentional `# type: ignore[arg-type]` is on `gradient=gradient` in `_route_leg_kosten`, mirroring the EXISTING identical pattern in `trip_input/api.py:306` (`calculate_segment_consumption`'s `gradient` parameter is typed stricter than a list-indexed `SegmentGradient | None` value can statically guarantee — the `None` case only occurs if `gradients` came up short, which does not happen in practice since `calculate_segment_gradients` always returns one entry per segment). `wind` is a real `WindComponents` instance (not `None`), so it needs no type-ignore.

- [ ] **Step 5: Run full checks and commit**

```bash
uv run hk check --all && uv run pytest tests/optimization/test_detour_routing.py -v
git add src/tripplanner/optimization/detour_routing.py tests/optimization/test_detour_routing.py
git commit -m "feat: add precompute_detour_costs for real routed charging-stop detours"
```

---

## Task 5: Wire real detour costs into `NetworkXOptimizer`

**Files:**

- Modify: `src/tripplanner/optimization/optimizer.py` (`_detour_kosten` at `optimizer.py:1023-1053`, `_add_charging_edges`, `_fuege_ladekante_hinzu`, `_generate_graph`, `optimize`)
- Modify: `src/tripplanner/optimization/models.py` (`OptimizerInterface.optimize` protocol)
- Test: `tests/optimization/test_optimization.py`

**Interfaces:**

- Consumes: `DetourKosten` (Task 3).
- Produces: `NetworkXOptimizer.optimize(..., detour_kosten: dict[str, DetourKosten] | None = None)` — consumed by Task 6.

- [ ] **Step 1: Change `_detour_kosten`'s signature to look up real costs first**

Current (`optimizer.py:1023-1053`):

```python
    def _detour_kosten(
        self,
        offroute_distance_m: float,
        vehicle_profile: VehicleProfile,
    ) -> tuple[float, float]:
        """Schätzt Zeit (s) und SoC-Verbrauch (%) für die einfache Strecke.
        ...
        """
        if offroute_distance_m <= 0.0:
            return 0.0, 0.0

        strecke_m = offroute_distance_m * DETOUR_ROUTENFAKTOR
        detour_geschwindigkeit_m_s = DETOUR_GESCHWINDIGKEIT_KMH * 1000.0 / 3600.0
        zeit_s = strecke_m / detour_geschwindigkeit_m_s
        energie_kwh = strecke_m * self._avg_verbrauch_kwh_pro_m
        soc_pct = self._calc_soc_verbrauch_pct(energie_kwh, vehicle_profile)
        return zeit_s, soc_pct
```

New:

```python
    def _detour_kosten(
        self,
        station_id: str,
        offroute_distance_m: float,
        vehicle_profile: VehicleProfile,
        detour_kosten: dict[str, DetourKosten] | None,
    ) -> tuple[float, float]:
        """Estimates one-way detour time (s) and SoC cost (%) for a station.

        Uses the REAL, GraphHopper-routed cost from `detour_kosten` (see
        `optimization.detour_routing.precompute_detour_costs`) whenever the
        station is present there. Falls back to the straight-line heuristic
        (`DETOUR_ROUTENFAKTOR`/`DETOUR_GESCHWINDIGKEIT_KMH`) only when
        `detour_kosten` is `None` (caller didn't precompute - e.g. some
        tests) or the station is missing from it (real routing failed for
        this specific station, see `precompute_detour_costs`'s docstring).

        `offroute_distance_m` is the straight-line distance (see
        `station_mapping.map_station_to_segment`); it is only used by the
        fallback heuristic.
        """
        if detour_kosten is not None and station_id in detour_kosten:
            kosten = detour_kosten[station_id]
            soc_pct = self._calc_soc_verbrauch_pct(kosten.energie_kwh, vehicle_profile)
            return kosten.zeit_s, soc_pct

        if offroute_distance_m <= 0.0:
            return 0.0, 0.0

        strecke_m = offroute_distance_m * DETOUR_ROUTENFAKTOR
        detour_geschwindigkeit_m_s = DETOUR_GESCHWINDIGKEIT_KMH * 1000.0 / 3600.0
        zeit_s = strecke_m / detour_geschwindigkeit_m_s
        energie_kwh = strecke_m * self._avg_verbrauch_kwh_pro_m
        soc_pct = self._calc_soc_verbrauch_pct(energie_kwh, vehicle_profile)
        return zeit_s, soc_pct
```

Add `from tripplanner.optimization.models import DetourKosten` to the existing `from tripplanner.optimization.models import (...)` import block if `models` isn't already imported that way in `optimizer.py` (check current imports; `OptimizationConstraints` etc. are imported from `tripplanner.optimization.models` already based on the surrounding code, so add `DetourKosten` to that same import list).

- [ ] **Step 2: Thread `detour_kosten` through `_add_charging_edges`**

`_add_charging_edges` currently calls `self._detour_kosten(offroute_distance_m=..., vehicle_profile=...)` once per candidate station (find this call site via `grep -n "_detour_kosten(" src/tripplanner/optimization/optimizer.py`). Add a `detour_kosten: dict[str, DetourKosten] | None` parameter to `_add_charging_edges`'s signature and pass it through:

```python
        detour_zeit_s, detour_soc_pct = self._detour_kosten(
            station_id=station.station_id,
            offroute_distance_m=offroute_distance_m,
            vehicle_profile=vehicle_profile,
            detour_kosten=detour_kosten,
        )
```

- [ ] **Step 3: Thread `detour_kosten` through `_generate_graph` and `optimize`**

Add `detour_kosten: dict[str, DetourKosten] | None = None` as the last parameter of `optimize()` (`optimizer.py:83-98`) and `_generate_graph` (find its signature via `grep -n "def _generate_graph" src/tripplanner/optimization/optimizer.py`), passing it down to the `_add_charging_edges` call site inside `_generate_graph`.

- [ ] **Step 4: Update `OptimizerInterface` and `ORToolsOptimizer`**

In `src/tripplanner/optimization/models.py`, add `detour_kosten: dict[str, DetourKosten] | None = None` as the last parameter of `OptimizerInterface.optimize` (`models.py:154-169`), and update its docstring with one sentence: `detour_kosten` (optional, real routed detour costs per station, see `optimization.detour_routing.precompute_detour_costs`) is looked up before falling back to the straight-line heuristic when computing off-route detour cost.

In `src/tripplanner/optimization/optimizer.py`, add the identical parameter to `ORToolsOptimizer.optimize` (the `NotImplementedError` stub).

- [ ] **Step 5: Update existing tests that call `_detour_kosten`/`_add_charging_edges` directly**

Search: `grep -rn "_detour_kosten(\|_add_charging_edges(" tests/optimization/test_optimization.py`

For each direct call, add `station_id=station.station_id` (or the relevant station's id) and `detour_kosten=None` to keep exercising the heuristic fallback path (preserves existing test intent unchanged).

- [ ] **Step 6: Add new tests for the real-vs-heuristic lookup**

Append to `tests/optimization/test_optimization.py`:

```python
class TestDetourKostenNutztRealeRoutingDatenWennVorhanden:
    """Tests for `_detour_kosten`'s real-vs-heuristic fallback logic (see
    `optimization.detour_routing`)."""

    def test_nutzt_reale_kosten_wenn_station_in_map(self) -> None:
        optimizer = create_networkx_optimizer()
        real_kosten = DetourKosten(distanz_m=2000.0, zeit_s=180.0, energie_kwh=0.4)

        zeit_s, soc_pct = optimizer._detour_kosten(
            station_id="real-station",
            offroute_distance_m=999_999.0,  # would give a wildly different heuristic result
            vehicle_profile=VehicleProfile(
                masse_kg=1800.0,
                cw_wert=0.23,
                stirnflaeche_m2=2.2,
                rollwiderstandsbeiwert=0.01,
                batteriekapazitaet_kwh=60.0,
                nebenverbraucher_baseline_kw=0.34,
                reifentyp="standard",
                dachbox=False,
            ),
            detour_kosten={"real-station": real_kosten},
        )

        assert zeit_s == 180.0

    def test_faellt_auf_heuristik_zurueck_wenn_station_fehlt(self) -> None:
        optimizer = create_networkx_optimizer()
        vehicle_profile = VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        )
        optimizer._avg_verbrauch_kwh_pro_m = 0.0002  # set as optimize() normally would

        zeit_s, _ = optimizer._detour_kosten(
            station_id="missing-station",
            offroute_distance_m=1000.0,
            vehicle_profile=vehicle_profile,
            detour_kosten={"other-station": DetourKosten(distanz_m=1.0, zeit_s=1.0, energie_kwh=0.0)},
        )

        # Heuristic: 1000m * 1.6 / (70 km/h) = ~82.3s
        assert zeit_s == pytest.approx(82.3, abs=0.5)

    def test_faellt_auf_heuristik_zurueck_wenn_detour_kosten_none(self) -> None:
        optimizer = create_networkx_optimizer()
        vehicle_profile = VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        )
        optimizer._avg_verbrauch_kwh_pro_m = 0.0002

        zeit_s, _ = optimizer._detour_kosten(
            station_id="any-station",
            offroute_distance_m=1000.0,
            vehicle_profile=vehicle_profile,
            detour_kosten=None,
        )

        assert zeit_s == pytest.approx(82.3, abs=0.5)
```

Add `from tripplanner.optimization.models import DetourKosten` to the test file's imports.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/optimization/test_optimization.py -v`
Expected: all PASS, including the new `TestDetourKostenNutztRealeRoutingDatenWennVorhanden` class.

- [ ] **Step 8: Run full checks and commit**

```bash
uv run hk check --all && uv run pytest -m "not integration"
git add src/tripplanner/optimization/optimizer.py src/tripplanner/optimization/models.py tests/optimization/test_optimization.py
git commit -m "feat: NetworkXOptimizer looks up real detour costs before falling back to the heuristic"
```

---

## Task 6: Wire the precomputation into `create_trip_simulation`

**Files:**

- Modify: `src/tripplanner/trip_input/api.py` (`_step_8_optimize_charging_plan`, `create_trip_simulation`)
- Test: `tests/trip_input/test_api.py`

**Interfaces:**

- Consumes: `precompute_detour_costs` (Task 4), `map_stations_to_segments` (Task 2), `NetworkXOptimizer.optimize(..., detour_kosten=...)` (Task 5).

- [ ] **Step 1: Extract the station-fetch block into its own function**

In `src/tripplanner/trip_input/api.py`, extract lines 357-389 (the `charging_provider.get_stations_along_route` call plus dedup loop, currently inside `_step_8_optimize_charging_plan`) into a new top-level function placed just before `_step_8_optimize_charging_plan`:

```python
async def _step_8a_fetch_charging_stations(
    charging_provider: ChargingStationProvider | None,
    route: Route,
) -> list[ChargingStation]:
    """Step 8a: Fetch and deduplicate candidate charging stations along the route.

    Extracted out of `_step_8_optimize_charging_plan` so `create_trip_
    simulation` can call it ONCE (before the weather/ETA convergence loop)
    instead of once per iteration - the route never changes between
    iterations, so the station list never changes either, and this call
    also feeds `optimization.detour_routing.precompute_detour_costs`, which
    must not be redone per iteration (it's a batch of routing/elevation
    calls, not free).

    Search radius deliberately 25 km, not the road width (1-2 km): on long-
    distance trips through regions with sparser Supercharger coverage
    (e.g. rural Sweden routes off the E4/E6), the nearest Supercharger
    regularly sits 10-25 km off the GraphHopper-chosen road - too tight a
    radius delivers ZERO candidates for an entire segment there, which
    makes `NetworkXOptimizer.optimize()` incorrectly raise "no reachable
    target node found" even though the route IS drivable with a realistic
    charging detour (repro: Gummersbach -> Hagfors kommun, Sweden - the only
    candidate in the gap, Ulricehamn, sits ~25 km off the route). 25 km is
    deliberately generous (also covers the 20 km distant Jönköping
    Supercharger) while still small enough not to needlessly bloat the
    state-graph size (see docs/plans/07-optimization.md).

    `get_stations_along_route()` maps stations per (fine-grained) route
    segment - with very short segments (e.g. one segment per GraphHopper
    polyline point pair, often < 200 m), the same physical station usually
    falls within the search radius of several consecutive segments and
    shows up repeatedly. Without deduplication by `station_id`, the
    optimizer would model the same station at many neighboring segment
    indices as separate charging opportunities, needlessly bloating the
    state graph (see docs/plans/07-optimization.md) and slowing the search.
    """
    provider = charging_provider or FakeChargingStationProvider()
    stations_dict = await provider.get_stations_along_route(route, search_radius_km=25.0)

    charging_stations: list[ChargingStation] = []
    seen_station_ids: set[str] = set()
    for station_list in stations_dict.values():
        for station in station_list:
            if station.station_id in seen_station_ids:
                continue
            seen_station_ids.add(station.station_id)
            charging_stations.append(station)

    return charging_stations
```

- [ ] **Step 2: Shrink `_step_8_optimize_charging_plan` to take stations/detour-costs as parameters**

Replace `_step_8_optimize_charging_plan`'s body (`api.py:318-409`) with:

```python
async def _step_8_optimize_charging_plan(  # noqa: PLR0913, PLR0917
    route: Route,
    segment_energy: list[SegmentEnergyResult],
    vehicle_profile: VehicleProfile,
    start_soc_pct: float,
    ziel_soc_pct: float,
    construction_zones: list[ConstructionZone],
    abfahrtszeit: datetime,
    elevation_provider: ElevationProvider,
    elevation_points: list[ElevationPoint],
    charging_stations: list[ChargingStation],
    detour_kosten: dict[str, DetourKosten],
    zwischenstopps: list[Waypoint] | None = None,
    ladedauer_vorgaben: dict[str, int] | None = None,
    faehr_zeitfenster: dict[int, tuple[int, datetime, datetime]] | None = None,
    mindest_ankunfts_soc_pct: float = 5.0,
    mindest_ladezeit_s: int = 600,
) -> ChargingPlan:
    """Step 8: Determine the optimal charging plan.

    Uses `create_networkx_optimizer()` as the prototype optimizer.
    `charging_stations` and `detour_kosten` are precomputed ONCE by
    `create_trip_simulation` (see `_step_8a_fetch_charging_stations` and
    `optimization.detour_routing.precompute_detour_costs`) - not fetched or
    computed here, so they stay identical (and are only computed once)
    across every weather/ETA-convergence iteration.
    """
    optimizer = create_networkx_optimizer()

    constraints = OptimizationConstraints(
        ziel_soc_pct=ziel_soc_pct,
        max_ladezeit_s=3600,
        mindest_ankunfts_soc_pct=mindest_ankunfts_soc_pct,
        mindest_ladezeit_s=mindest_ladezeit_s,
    )

    waypoints = list(zwischenstopps) if zwischenstopps else []

    gradients = elevation_provider.calculate_segment_gradients(elevation_points, route)

    return optimizer.optimize(
        route=route,
        segments=route.segments,
        gradients=gradients,
        energy_results=segment_energy,
        charging_stations=charging_stations,
        waypoints=waypoints,
        vehicle_profile=vehicle_profile,
        constraints=constraints,
        start_soc_pct=start_soc_pct,
        abfahrtszeit=abfahrtszeit,
        ladedauer_vorgaben=ladedauer_vorgaben,
        faehr_zeitfenster=faehr_zeitfenster,
        detour_kosten=detour_kosten,
    )
```

(The large multi-paragraph comment about the 25 km search radius and station deduplication moved to `_step_8a_fetch_charging_stations` in Step 1 — do not duplicate it here.)

- [ ] **Step 3: Wire the new steps into `create_trip_simulation`**

In `create_trip_simulation` (`api.py`, find via `grep -n "async def create_trip_simulation" src/tripplanner/trip_input/api.py`), after the existing `route_calculate` step and BEFORE the `for iteration in range(max_iterations):` loop, add:

```python
    # 8a. Fetch candidate charging stations and precompute their real,
    # routed detour costs ONCE - both depend only on `route`, which is
    # already final at this point, and must not be redone per iteration
    # (station fetch is cheap but repeated 1-3x for nothing; detour
    # precomputation is a batch of routing/elevation calls and must not be
    # repeated at all).
    with _log_step("fetch_charging_stations"):
        charging_stations = await _step_8a_fetch_charging_stations(charging_provider, route)

    with _log_step("precompute_detour_costs"):
        station_segments = map_stations_to_segments(charging_stations, route.segments)
        detour_kosten = await precompute_detour_costs(
            routing_provider=routing_provider or FakeRoutingProvider(),
            elevation_provider=elevation_provider,
            vehicle_profile=request.fahrzeugprofil,
            route=route,
            station_segments=station_segments,
            abfahrtszeit=request.abfahrtszeit,
        )
```

Then update the existing `charging_plan = await _step_8_optimize_charging_plan(...)` call inside the loop to pass `charging_stations=charging_stations` and `detour_kosten=detour_kosten` instead of `charging_provider=charging_provider`, and remove the now-unused `charging_provider` parameter from that call site (the provider is still used elsewhere in `create_trip_simulation`, e.g. by `_attach_charging_pricing` — do not remove the `charging_provider` parameter from `create_trip_simulation` itself, only from the `_step_8_optimize_charging_plan` call).

Add the new imports near the top of `api.py`:

```python
from tripplanner.optimization.detour_routing import precompute_detour_costs
from tripplanner.optimization.models import DetourKosten
from tripplanner.optimization.station_mapping import map_stations_to_segments
```

- [ ] **Step 4: Write the test proving stations/detour-costs are fetched exactly once**

Append to `tests/trip_input/test_api.py`:

```python
@pytest.mark.asyncio
async def test_create_trip_simulation_fetches_charging_stations_once_not_per_iteration(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """`get_stations_along_route` must be called exactly once per
    `create_trip_simulation()` call, regardless of `max_iterations` - the
    route (and therefore the station list) never changes between
    convergence iterations, and re-fetching would also redo the expensive
    detour-cost precomputation for nothing."""
    call_count = 0
    original = fake_charging_provider_berlin_munich.get_stations_along_route

    async def counting_get_stations_along_route(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        return await original(*args, **kwargs)

    fake_charging_provider_berlin_munich.get_stations_along_route = (  # type: ignore[method-assign]
        counting_get_stations_along_route
    )

    await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        max_iterations=3,
    )

    assert call_count == 1
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/trip_input/test_api.py -v -k "fetches_charging_stations_once or optimize_charging_plan or complete_run"`
Expected: all PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `uv run pytest -m "not integration"`
Expected: all PASS except the pre-existing, unrelated `TestChargerScrapePricing::test_all_failures_exits_nonzero` failure (confirm it's still the only failure and its message is unchanged from before this plan's changes).

- [ ] **Step 7: Run full checks and commit**

```bash
uv run hk check --all
git add src/tripplanner/trip_input/api.py tests/trip_input/test_api.py
git commit -m "feat: precompute real detour costs once per trip, not once per iteration"
```

---

## Task 7: Wire real detour costs into the FastAPI endpoint and CLI paths

**Files:**

- Verify only: `src/tripplanner/trip_input/api.py` (`create_trip_endpoint`), `src/tripplanner/trip_input/cli.py`

Both `create_trip_endpoint` and the CLI's `trips` command already call `create_trip_simulation(...)` (unchanged call signature — Task 6 only changed `create_trip_simulation`'s internals, not its own parameters). No changes should be needed here.

- [ ] **Step 1: Confirm no call-site changes are needed**

Run: `grep -n "create_trip_simulation(" src/tripplanner/trip_input/api.py src/tripplanner/trip_input/cli.py`

Confirm every call site passes only parameters that already existed before this plan (`routing_provider`, `charging_provider`, `weather_provider`, `construction_provider`, `elevation_provider`, `start_soc_pct`, `destination_soc_pct`, `mindest_ankunfts_soc_pct`, `mindest_ladezeit_s`, `ferry_observer`, `route_observer`). If so, no edits needed.

- [ ] **Step 2: Run the FastAPI and CLI test suites end to end**

Run: `uv run pytest tests/trip_input/test_api.py tests/trip_input/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 3: Manual smoke test against the running local stack**

With the local GraphHopper server and backend already running (per this conversation, `localhost:8989` and `localhost:3000`), replay the exact request from this conversation:

```bash
curl -s -X POST http://localhost:3000/api/trips \
  -H "Content-Type: application/json" \
  -d '{"start":[51.0451814,7.5571072],"ziel":[60.1079036,13.4509775],"zwischenstopps":[],"abfahrtszeit":"2026-08-17T23:00:00","fahrzeugprofil":{"masse_kg":1800,"cw_wert":0.23,"stirnflaeche_m2":2.2,"rollwiderstandsbeiwert":0.01,"batteriekapazitaet_kwh":60,"nebenverbraucher_baseline_kw":0.34,"reifentyp":"standard","dachbox":false},"praeferenzen":{},"start_soc_pct":100,"ziel_soc_pct":5,"mindest_ankunfts_soc_pct":5,"alle_faehren_vermeiden":true,"vermiedene_faehren":[],"faehr_zeitfenster":[],"ladedauer_vorgaben":[],"wetter_beruecksichtigen":false,"mindest_ladezeit_s":360,"baustellen_beruecksichtigen":false}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(s['name'], round(s['ankunfts_soc_pct'],1),'->',round(s['ziel_soc_pct'],1), s['ladedauer_s']) for s in d['charging_stops']]"
```

Compare the resulting charging-stop sequence and durations against the sequence recorded earlier in this conversation (Holdorf, Stuckenborstel, Kaltenkirchen, Busdorf, Rødekro, Slagelse, Køge 29.8%→80.0%/707s, Lagan, Kristinehamn). Record whether Køge's decision (or any other stop) changes now that real detour costs are used — this is expected evidence of the fix working, not a regression, if it changes.

Also check backend logs for a new `precompute_detour_costs` pipeline-step timing line and confirm it completes in a reasonable time (single-digit seconds; if it takes substantially longer, see Task 8's note on elevation batching).

- [ ] **Step 4: Commit if any fixes were needed, otherwise mark this task done with no commit**

---

## Task 8: Performance verification and (conditional) elevation batching

**Files:**

- Conditional modify: `src/tripplanner/optimization/detour_routing.py`

`ElevationProvider.get_elevation_profile()` issues one `data_source.get_elevations_batch()` call per invocation (`src/tripplanner/elevation/elevation.py:84-85`). Task 4's `precompute_detour_costs` calls it once per detour leg (2 per station), so a route with ~150 candidate stations issues ~300 separate elevation batch calls instead of one big one. Whether this matters depends on whether the underlying `DEMDataSourceProtocol` implementation keeps its raster tile(s) open/cached across calls on the same `ElevationProvider` instance (likely, but must be verified — do not assume).

- [ ] **Step 1: Measure real wall-clock time added by detour precomputation**

Using the manual smoke test from Task 7 Step 3 (or the backend's own `_log_step("precompute_detour_costs")` timing from Task 6 Step 3), record the `precompute_detour_costs` step duration for the Gummersbach→Hagfors-scale request (the route from this conversation, ~150+ candidate stations expected given its length and Germany's dense Supercharger coverage).

- [ ] **Step 2: If the measured time exceeds ~10 seconds, batch the elevation calls**

If Step 1 shows `precompute_detour_costs` taking materially longer than the GraphHopper routing portion alone, batch all detour-leg coordinates into a single `data_source.get_elevations_batch()` call instead of one per leg. Replace `_route_leg_kosten`'s per-leg `elevation_provider.get_elevation_profile(leg_route)` call with a two-pass approach in `precompute_detour_costs`:

1. First pass: for every leg, build its coordinate list using the exact same rule `ElevationProvider.get_elevation_profile` uses (first segment's start point, then every segment's end point — see `elevation.py:69-79`), and record `(start_offset, count)` into one combined coordinate list.
2. One call: `all_elevations = await elevation_provider.data_source.get_elevations_batch(all_coordinates)`.
3. Second pass: slice `all_elevations` back per leg using the recorded offsets, build `ElevationPoint` objects, and call `elevation_provider.calculate_segment_gradients(leg_elevation_points, leg_route)` per leg (this part stays synchronous and per-leg — it's pure computation, not I/O).

Do not implement this speculatively — only if Step 1's measurement shows it's needed. If the existing per-leg calls are already fast (tile caching working as expected), this task is done with no code change; record the measured timing in the commit message or a code comment on `precompute_detour_costs` instead.

- [ ] **Step 3: If Step 2 was needed, add a regression test**

Add a test to `tests/optimization/test_detour_routing.py` asserting `elevation_provider.data_source.get_elevations_batch` (spy/counting wrapper, matching the pattern in Task 4's `test_precompute_fans_out_concurrently`) is called exactly once for a `precompute_detour_costs` run with multiple stations, not once per leg.

- [ ] **Step 4: Run full checks and commit (only if Step 2 changed anything)**

```bash
uv run hk check --all && uv run pytest tests/optimization/ -v
git add src/tripplanner/optimization/detour_routing.py tests/optimization/test_detour_routing.py
git commit -m "perf: batch elevation lookups across all detour legs into one call"
```

---

## Task 9: Final full-suite verification

- [ ] **Step 1: Run the complete backend check**

```bash
uv run hk check --all
```

Expected: all green except the pre-existing, unrelated `charger scrape-pricing` lint/type findings if any (none expected from this plan's files).

- [ ] **Step 2: Run the complete backend test suite with coverage**

```bash
uv run pytest -m "not integration"
```

Expected: all PASS except the pre-existing `TestChargerScrapePricing::test_all_failures_exits_nonzero` failure (unrelated to this plan — confirm its failure message is byte-for-byte identical to before this plan's changes, proving nothing here touched it). Coverage threshold (85% for `src/tripplanner/`) must still be met — the new `detour_routing.py`/`station_mapping.py`/`detour_geometry.py` modules need their own tests (Tasks 1, 2, 4) to keep it that way; if `hk check` reports a coverage regression, add the missing test cases before considering this plan complete.

- [ ] **Step 3: Confirm no OR-Tools stub regression**

```bash
uv run pytest tests/optimization/test_optimization.py -k "ortools" -v
```

Expected: PASS — `ORToolsOptimizer.optimize` still raises `NotImplementedError`, now with the extra `detour_kosten` parameter accepted (Task 5 Step 4) but otherwise unchanged.

- [ ] **Step 4: Final commit / PR**

If all prior tasks were committed individually, this step is just a final `uv run hk check --all && uv run pytest -m "not integration"` confirmation with no new commit. Otherwise commit any stragglers, then open the PR with a summary linking back to this plan file and to the specific conversation evidence (Malmö/Køge detour miscalculation) that motivated it.
