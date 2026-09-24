# Implementation Plan: `optimization` Module (Phase 5)

## 1. Purpose & Scope

The `optimization` module calculates the optimal charging plan for a Tesla trip along a route already determined by GraphHopper. It solves a discretized state-space search problem that considers the following dimensions:

- **Position**: Segment index of the route (discretized, no recalculation of the road route)
- **State of Charge (SoC)**: Discretized into buckets (e.g., 1% steps, 0–100%)
- **Time**: Discretized into buckets (e.g., 15-minute steps, rounded to full quarter-hours)

The cost function minimizes total trip time (driving time + charging time) while respecting fixed constraints and optional penalty costs for constraint violations.

### Boundary

- **NO energy-optimal rerouting**: The route is fixed from Phase 1 (GraphHopper); the module only plans charging stops and speed profiles along the given route.
- **Waypoints are mandatory nodes**: As decided in `06-open-points-contradictions.md` (Variant a), waypoints are standalone waypoints with an optional minimum stay duration — they must be modeled in the state graph as explicit nodes with time locks.
- **No iteration within optimization**: Iterative ETA/weather convergence occurs at the higher-level orchestration layer (`trip_input`/API); the `optimization` module receives a consistent weather/ETA state as input.

### Out of Scope

- No creation/maintenance of the Tesla Supercharger database (provider interface envisioned, data source local/fixed)
- No personalized calibration of the consumption model (fixed defaults per the `vehicle_energy_parameters` Pydantic model)
- No real-time replanning for driving errors (simulation envisioned for the future, but no live adjustment)

---

## 2. Dependencies & Phase Assignment

- **Phase**: 5 (Core Optimization)
- **Phase Prerequisites**: Phase 4 (battery) must be complete (charging curve interface), Phase 3 (energy) must deliver segments with energy consumption.
- **Consumed Types** (exactly from canonical register):
  - `tripplanner.routing.models.Route`, `tripplanner.routing.models.RouteSegment`
  - `tripplanner.elevation.models.SegmentGradient`
  - `tripplanner.weather.models.WeatherSample` (via `tripplanner.wind.models.WindComponents`)
  - `tripplanner.construction.models.ConstructionZone`
  - `tripplanner.energy.models.SegmentEnergyResult`
  - `tripplanner.battery.models.SoCState`, `tripplanner.battery.models.ChargingCurve`
  - `tripplanner.charging_infrastructure.models.ChargingStation`
  - `tripplanner.trip_input.models.VehicleProfile`, `tripplanner.trip_input.models.TripRequest`, `tripplanner.trip_input.models.Waypoint`
  - `tripplanner.optimization.models.OptimizationConstraints`, `tripplanner.optimization.models.ChargingPlan`, `tripplanner.optimization.models.ChargingStop`

---

## 3. Data Models

### Pydantic Models (in `src/tripplanner/optimization/models.py`)

```python
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Literal
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
from tripplanner.trip_input.models import Waypoint, VehicleProfile
from tripplanner.routing.models import RouteSegment
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.elevation.models import SegmentGradient

class ChargingStop(BaseModel):
    """
    A charging stop with station, arrival and target SoC plus timing.
    """

    station: ChargingStation
    segment_index: Annotated[int, Field(ge=0)]
    ankunfts_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)]
    ziel_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)]
    geschaetzte_ladedauer_s: Annotated[int, Field(ge=0)]
    ankunftszeit: datetime
    abfahrtszeit: datetime

    @field_validator("ankunfts_soc_pct", "ziel_soc_pct")
    @classmethod
    def validate_soc_range(cls, v: float) -> float:
        if v < 0.0 or v > 100.0:
            raise PydanticCustomError(
                "soc_range_error",
                "SoC must be between 0.0 and 100.0, but is {value}",
                {"value": v},
            )
        return v

class OptimizationConstraints(BaseModel):
    """
    Hard constraints and safety parameters for optimization.
    """

    min_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)] = Field(
        default=15.0,
        description="Minimum permissible SoC (safety reserve)",
    )
    ziel_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)] = Field(
        default=80.0,
        description="desired SoC at destination",
    )
    max_etappenlaenge_km: Annotated[float, Field(gt=0.0)] = Field(
        default=500.0,
        description="maximum distance between charging stops (optional)",
    )
    sicherheitsreserve_pct: Annotated[float, Field(ge=0.0, le=20.0)] = Field(
        default=5.0,
        description="Reserve on target SoC (e.g., target SoC = 80%, reserve = 5% → actual target SoC = 75%)",
    )
    max_ladezeit_s: Annotated[int, Field(ge=600, le=7200)] = Field(
        default=3600,
        description="maximum duration of a single charging session (optional)",
    )

class ChargingPlan(BaseModel):
    """
    Optimization result: ordered list of charging stops + total trip time.
    """

    ladehalte: list[ChargingStop]
    gesamtreisezeit_s: Annotated[int, Field(ge=0)]
    min_zwischenstopp_ankunftszeit: dict[int, datetime] = Field(
        default_factory=dict,
        description="Minimum arrival time for waypoints (when not charging)",
    )

class StateNode(BaseModel):
    """
    Internal node in the state graph: (segment_index, soc_bucket, time_bucket).
    Not used as a Pydantic export model; serves only internal representation.
    """

    segment_index: int
    soc_pct: float  # discretized
    zeitpunkt: datetime

class OptimizerInterface:
    """
    Protocol/interface for interchangeability between NetworkX (prototype)
    and OR-Tools (production, later expansion).
    """

    def optimize(
        self,
        route: "Route",
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list["SegmentEnergyResult"],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """
        Optimization method provided by both backend implementations
        (NetworkXOptimizer, ORToolsOptimizer).
        """
        raise NotImplementedError
```

### Supplemental Types (not in register but internally required)

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class LadekurvenLookup:
    """
    Helper structure for charging curve interpolation: SoC degree → charging power (kW).
    """

    soc_pct: float
    ladeleistung_kw: float
```

---

## 4. Public Interface

### File Structure

```
src/tripplanner/optimization/
├── __init__.py        # re-exports OptimizerInterface + public models
├── models.py           # see above
├── optimizer.py        # Core logic: NetworkX and OR-Tools implementations
├── discretizer.py      # Discretization of SoC and time
└── validation.py       # Input data validation (route gaps, SoC range, etc.)
```

### Public API (`src/tripplanner/optimization/__init__.py`)

```python
from tripplanner.optimization.models import (
    ChargingStop,
    OptimizationConstraints,
    ChargingPlan,
    OptimizerInterface,
)
from tripplanner.optimization.optimizer import (
    create_networkx_optimizer,
    create_ortools_optimizer,
)

__all__ = [
    "ChargingStop",
    "OptimizationConstraints",
    "ChargingPlan",
    "OptimizerInterface",
    "create_networkx_optimizer",
    "create_ortools_optimizer",
]
```

### Public Functions (`optimizer.py`)

```python
from tripplanner.routing.models import Route
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.trip_input.models import Waypoint, VehicleProfile
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.optimization.models import (
    OptimizationConstraints,
    ChargingPlan,
    OptimizerInterface,
    LadekurvenLookup,
)

def create_networkx_optimizer(
    soc_step_pct: float = 1.0,
    time_step_min: int = 15,
) -> OptimizerInterface:
    """
    Factory function for the NetworkX-based prototype optimizer.
    """
    from tripplanner.optimization.optimizer import NetworkXOptimizer

    return NetworkXOptimizer(
        soc_step_pct=soc_step_pct,
        time_step_min=time_step_min,
    )

def create_ortools_optimizer(
    soc_step_pct: float = 1.0,
    time_step_min: int = 15,
    use_cp_sat: bool = True,
) -> OptimizerInterface:
    """
    Factory function for the OR-Tools-based optimizer (later version).
    use_cp_sat=True → CP-SAT Solver, False → Routing Solver.
    """
    from tripplanner.optimization.optimizer import ORToolsOptimizer

    return ORToolsOptimizer(
        soc_step_pct=soc_step_pct,
        time_step_min=time_step_min,
        use_cp_sat=use_cp_sat,
    )

class NetworkXOptimizer(OptimizerInterface):
    """A*/Dijkstra optimization with NetworkX (prototype)."""

    def __init__(
        self,
        soc_step_pct: float = 1.0,
        time_step_min: int = 15,
    ):
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min

    def optimize(
        self,
        route: Route,
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """
        Optimizes charging plan using a discretized state graph.
        A* search with heuristic = remaining distance / estimated travel speed.
        """
        # Implementation see Section 5
        pass

class ORToolsOptimizer(OptimizerInterface):
    """OR-Tools-based optimizer (CP-SAT or Routing Solver)."""

    def __init__(
        self,
        soc_step_pct: float = 1.0,
        time_step_min: int = 15,
        use_cp_sat: bool = True,
    ):
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self.use_cp_sat = use_cp_sat

    def optimize(
        self,
        route: Route,
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """
        Optimizes charging plan using constraint programming (CP-SAT) or
        routing solver (for larger instances).
        """
        # Implementation see Section 5
        pass
```

---

## 5. External Integration / Algorithm Details

### 5.1 State Space Discretization (`discretizer.py`)

**SoC discretization**: `soc_bucket = round(soc_pct / soc_step_pct)` → 0–100 in steps of e.g. 1% (101 buckets)

**Time discretization**: `zeit_bucket = round(zeitpunkt / timedelta(minutes=time_step_min)) * timedelta(minutes=time_step_min)` → rounded to full quarter-hour (15 min)

**Node hash**: `(segment_index, soc_bucket, zeit_bucket)` as dictionary key

### 5.2 Cost Function

```
Cost(Edge) = drive_time_segment + charge_time + detour_cost + constraint_penalty

with:
- drive_time_segment = segment_length_m / (speed_kmh * 1000/3600)
- charge_time = (target_soc_pct - arrival_soc_pct) / (charging_power_kw * 0.01) * battery_capacity_kwh * 3600
- detour_cost = 0 (since no rerouting alternatives, only charging stop planning)
- constraint_penalty = 0 (if constraint satisfied), else M (large constant, e.g. 10^6)
```

**Heuristic (A*)**: `h(n) = remaining_distance_m / (travel_speed_kmh * 1000/3600)`

**Travel speed**: set to default 110 km/h (motorway) for now; later adjustment via segment speed limits.

### 5.3 A*/Dijkstra Algorithm (NetworkX Implementation)

**Graph construction** (in `optimizer.py`):

```python
import networkx as nx

class NetworkXOptimizer(OptimizerInterface):
    def optimize(...):
        # 1. Create directed graph G = nx.DiGraph()
        G = nx.DiGraph()

        # 2. Create start node (segment_index=0, soc=start_soc, time=departure_time)
        start_node = (0, self._soc_to_bucket(start_soc_pct), self._zeit_to_bucket(abfahrtszeit))
        G.add_node(start_node, type="start", soc_pct=start_soc_pct, zeitpunkt=abfahrtszeit)

        # 3. Add nodes for all segments, SoC buckets, and time buckets
        #    (lazy: only generate reachable nodes)

        # 4. Add edges:
        #    - Drive edge: (seg, soc, time) → (seg+1, soc_drops, time+delta_t)
        #    - Charge edge: (seg, soc, time) → (seg, soc+charging, time+delta_tau)
        #    - Waypoint mandatory: (seg, soc, time) → (seg+1, soc, time+min_stay)

        # 5. Add charge edges to all available charging stations
        #    (only if station is within range or within segment interval)

        # 6. Run A* search:
        path = nx.astar_path(
            G,
            source=start_node,
            target=self._ziel_knoten,
            heuristic=lambda u, v: self._heuristik(u, v),
            weight="cost",
        )

        # 7. Extract ChargingStop objects from the path
        #    (detect charge edges and convert to ChargingStop)

        # 8. Calculate total trip time = last node time + remaining drive time
        #    (if destination not reached in last segment)

        return ChargingPlan(ladehalte=stops, gesamtreisezeit_s=total_time)
```

**Target node**: `(segment_index=len(segments)-1, soc_pct >= target_soc_pct, any time)`

**Heuristic function** (in `NetworkXOptimizer`):

```python
def _heuristik(self, u: tuple, v: tuple) -> float:
    """
    Admissible heuristic: time to destination under ideal conditions.
    """
    u_seg, u_soc, u_time = u
    v_seg, v_soc, v_time = v

    # Distance from v_seg to the end
    rest_distance = sum(seg.length_m for seg in self.segments[v_seg:])

    # Ideal speed (motorway, 110 km/h)
    v_ideal_mps = 110.0 * 1000 / 3600

    # Time duration
    rest_time_s = rest_distance / v_ideal_mps

    return rest_time_s
```

### 5.4 OR-Tools Implementation (CP-SAT vs. Routing Solver)

**CP-SAT (Constraint Programming SAT Solver)**: For small to medium instances (<50 charging stations). Modeled as integer linear optimization with boolean variables for "charge at station i".

**Routing Solver**: For large instances (>50 charging stations). Modeled as Vehicle Routing Problem with Time Windows (VRPTW) and additional constraints for SoC.

**Research findings** (see web search): CP-SAT is better suited for general integer problems and smaller instances; Routing Solver is specialized for routing problems with LNS heuristic. For the prototype and medium trips (approx. 300–600 km, ≤20 charging stations along the route), CP-SAT is a good fit. Later expansion with large datasets → Routing Solver.

**OR-Tools Model (CP-SAT Pseudocode)**:

```python
from ortools.sat.python import cp_model

def optimize(...):
    model = cp_model.CpModel()

    # Variables
    charge_at_station[i] = model.NewBoolVar(f"charge_{i}")
    arrival_soc[i] = model.NewIntVar(0, 100, f"arrival_soc_{i}")
    departure_soc[i] = model.NewIntVar(0, 100, f"departure_soc_{i}")
    charge_time[i] = model.NewIntVar(0, 3600, f"charge_time_{i}")
    arrival_time[i] = model.NewIntVar(int(departure_time.timestamp()), ..., f"arrival_{i}")

    # Constraints
    # 1. SoC consistency: arrival_soc[i+1] = departure_soc[i] - energy_consumption[i→i+1]
    # 2. Charge time calculation: charge_time[i] = (departure_soc[i] - arrival_soc[i]) * factor
    # 3. Minimum SoC: arrival_soc[i] >= min_soc_pct
    # 4. Waypoint time window: arrival_time[i] ≥ target_arrival_time[i]
    # 5. Target SoC: arrival_soc[last] >= target_soc_pct

    # Objective: minimize sum(charge_time) + sum(drive_time)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
    result = solver.Solve(model)

    if result == cp_model.OPTIMAL or result == cp_model.FEASIBLE:
        # Extract solution and build ChargingPlan
        pass
```

### 5.5 External APIs / Libraries

| Component | Library | Version | Usage |
| ----------- | ----------- | --------- | ------------ |
| NetworkX | `networkx` | ≥3.0 | A*/Dijkstra on DiGraph |
| OR-Tools | `ortools` | ≥9.10 | CP-SAT Solver (or Routing Solver) |
| Weather/Charging Curves | `tripplanner.weather`, `tripplanner.battery` | own modules | Data import via models |
| DEM Access | `rasterio` | ≥1.3 | not directly in optimization module, only via energy/battery |

**Dependencies in `pyproject.toml`**:

```toml
[project.dependencies]
networkx = "^3.0"
ortools = "^9.10"
rasterio = "^1.3"  # via elevation module, but listed here for completeness
```

---

## 6. Test Strategy

### Fixtures (in `tests/fixtures/optimization/`)

- `route_six_segments.json`: 6 segments, total length approx. 300 km, speed limits between 80–130 km/h
- `charging_stations_3.json`: 3 Superchargers along the route (Segment 1, 3, 5)
- `waypoints_1.json`: 1 waypoint (Segment 2, 15-min pause)
- `energy_segments_6.json`: Energy consumption per segment (calculated with reference vehicle)
- `charging_curve_v3.json`: V3 charging curve (10→80% in approx. 30 min, piezoelectric curve modeled)

### Example Test Cases (Unit Tests in `tests/optimization/test_optimizer.py`)

#### Test 1: Simple Route, No Charging Stop Needed (Given/When/Then)

```
Given: Route with 3 segments (100 km), starting SoC = 100%, target SoC = 60%, minimum SoC = 15%
When: Energy consumption per segment < 10% (total < 30% SoC consumption)
Then: No charging stop in the result, total trip time = drive time, arrival SoC ≥ target SoC
```

```python
def test_no_charging_stop_needed():
    # Setup: small route, low consumption
    route = _create_route(3, [100_000, 100_000, 100_000])  # 3 × 100 km
    gradients = _create_gradients(3, [0.0, 0.0, 0.0])
    energy = _create_energy([5.0, 5.0, 5.0])  # 15 kWh total
    stations = []  # no charging stations needed

    optimizer = create_networkx_optimizer()
    plan = optimizer.optimize(
        route=route,
        segments=route.segments,
        gradients=gradients,
        energy_results=energy,
        charging_stations=stations,
        waypoints=[],
        vehicle_profile=VehicleProfile(...),
        constraints=OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=60.0),
        start_soc_pct=100.0,
        abfahrtszeit=datetime(2025, 6, 15, 8, 0, 0),
    )

    # Assertions
    assert plan.ladehalte == []
    assert plan.gesamtreisezeit_s > 0
    # ... additional
```

#### Test 2: Route with Waypoint That Is Also a Charging Stop (Given/When/Then)

```
Given: Route with waypoint (Segment 2, 30-min pause), Supercharger on the same segment
When: Waypoint duration > time for charging session to target SoC
Then: A ChargingStop with station on Segment 2, arrival time ≤ waypoint time window
```

#### Test 3: Edge Case — Maximum Iteration, Convergence Reached (Integration Test)

```
Given: Iterative weather query (Phase 6/Orchestration) with 30-min threshold
When: Trip across multiple weather points, ETA deviation < 30 min after 1st iteration
Then: Optimization module receives consistent weather data, plan is stable (no change between iteration 1 and 2)
```

```python
@pytest.mark.integration
def test_convergence_small_eta_deviation():
    # Setup: large route (600 km), weather change along the way
    # → first iteration yields ETA differences < 30 min
    # → optimizer receives new weather, re-runs → plan unchanged
    pass
```

### Unit vs. Integration Test

| Test | Type | Marker | Description |
| ------ | ----- | -------- | -------------- |
| `test_no_charging_stop_needed` | Unit | — | Single case, all input data synthetic |
| `test_waypoint_with_charging_stop` | Unit | — | Combination of waypoint + charging stop |
| `test_edge_case_minimum_soc` | Unit | — | Starting SoC = minimum SoC + small delta → must charge |
| `test_convergence_small_eta_deviation` | Integration | `@pytest.mark.integration` | Orchestration layer with weather iteration |

---

## 7. Task Checklist

### Phase 7a – Model Definition & Validation

- [ ] Task 1: Create `src/tripplanner/optimization/models.py` — all Pydantic models (ChargingStop, OptimizationConstraints, ChargingPlan, OptimizerInterface) with Pydantic v2 syntax and custom validators (`PydanticCustomError` for SoC range).
- [ ] Task 2: Implement `src/tripplanner/optimization/validation.py` — functions for validating input data (`validate_route_has_no_gaps`, `validate_soc_range`, `validate_waypoints_ordered`).
- [ ] Task 3: Create `src/tripplanner/optimization/__init__.py` — re-exports public API.

### Phase 7b – NetworkX Prototype

- [ ] Task 4: Create `src/tripplanner/optimization/discretizer.py` — helper functions `_soc_to_bucket`, `_bucket_to_soc`, `_zeit_to_bucket`, `_bucket_to_time`.
- [ ] Task 5: Implement `src/tripplanner/optimization/optimizer.py` — `NetworkXOptimizer.optimize()` with graph construction, A* search, adding charge edges.
- [ ] Task 6: Implement `src/tripplanner/optimization/optimizer.py` — `_heuristik()` method (remaining distance / 110 km/h).

### Phase 7c – OR-Tools Backend (Preparation for later expansion)

- [ ] Task 7: Implement `src/tripplanner/optimization/optimizer.py` — `ORToolsOptimizer.optimize()` with CP-SAT model (variables, constraints, objective).
- [ ] Task 8: Implement `src/tripplanner/optimization/optimizer.py` — factory function `create_ortools_optimizer()` with `use_cp_sat` parameter.

### Phase 7d – Tests & Fixtures

- [ ] Task 9: Generate fixtures (`tests/fixtures/optimization/`): 6-segment route, 3 charging stations, 1 waypoint, energy and charging curve data.
- [ ] Task 10: Write `tests/optimization/test_optimizer.py` — 3 unit tests (no charging stop, waypoint=charging stop, minimum SoC edge case).
- [ ] Task 11: Write `tests/optimization/test_optimizer.py` — 1 integration test (`@pytest.mark.integration`) for convergence expectation.

### Phase 7e – Integration & Validation

- [ ] Task 12: Integrate `src/tripplanner/optimization/optimizer.py` — extend `tripplanner/trip_input/orchestration.py` to call `optimizer.optimize()` after energy calculation.
- [ ] Task 13: End-to-end test via CLI/API — trip from Berlin to Hamburg (approx. 260 km) with waypoint in Magdeburg (70 km), starting with 100% SoC, target SoC 80% → verify that the plan proposes 1–2 charging stops.
- [ ] Task 14: Coverage check → `pytest --cov=tripplanner.optimization --cov-report=term-missing --cov-fail-under=85`.

---

## 8. Risks & Open Technical Questions

### Risks

- **Scalability of NetworkX solution (resolved)**: The state graph was originally built per raw segment (segment index as a separate dimension), causing the state space to grow combinatorially with the number of charging stop options on very long routes (>1000 segments, e.g., one segment per GraphHopper polyline point pair) (trips >800 km took several minutes or hit a time limit). Drive edges now skip all raw segments between two decision points (charging station, waypoint, ferry boarding) in one jump in `_add_drive_edge`/`_generate_graph` (distance/energy via precomputed prefix sum, see `optimize()`), and the A* heuristic uses the same prefix sum instead of an O(n) recalculation per node. This reduces the state node count from O(raw segments × SoC buckets × time buckets) to O(decision points × SoC buckets × time buckets) — a 1450 km example with 12,000 raw segments/16 charging stations runs in <1 s instead of >90 s. OR-Tools (Phase 7c) remains the long-term planned expansion path for very many charging stations (>50).
- **Time discretization**: 15-minute steps can lead to suboptimal solutions (e.g., charging starting at 13:47 instead of 13:45). Finer discretization (5 min) → better solutions, but higher computational cost. Default set to 15 min; later adjustment via configuration parameter possible.
- **Incomplete charging curves**: The `ChargingCurve` validation in the battery module is not yet implemented → incorrect charging times possible. Fix: validate charging curve before optimization (monotonically increasing, no negative charging power).

### Open Technical Questions

- **Real vs. Ideal Speed**: Heuristic currently uses 110 km/h (motorway) as a fixed speed. Better heuristic: weighted average speed from speed limits of remaining segments. *(Later improvement, not required for MVP)*
- **Weather change during drive**: The current solution assumes constant weather per weather query point; no dynamic adjustment for sudden storms en route. *(Increased complexity; not currently planned)*
- **Charging curve temperature dependence**: Charging power depends on battery temperature, which in turn depends on outside temperature. Currently using an average charging curve. *(Optional for calibration in Phase 6, not part of MVP)*

### Future / not now (explicitly marked as "not in scope")

- **Multiple GraphHopper alternative routes**: Currently only one route is calculated; no energy-optimal selection of multiple routes. *(Later, non-invasive expansion path)*
- **Live replanning**: No dynamic plan adjustment during the drive. *(Would be a separate module with real-time feeds)*
- **Personalized calibration**: Fixed default parameters for `VehicleEnergyParameters`; no adjustment to personal driving data. *(Later expansion, not part of MVP)*

---

**End of the implementation plan for the `optimization` module.**
