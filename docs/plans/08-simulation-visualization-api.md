# Plan: Simulation, Visualization/API Layer (Phase 7)

**Purpose of this document:** Joint implementation plan for three closely related components:

1. **simulation** (Backend, Phase 6→7) – Generation of the time series from Route+ChargingPlan+Energy/Weather data  
2. **visualization** (Frontend, Phase 7) – Graphical display of simulation results  
3. **trip_input / API Layer** (Phase 7) – FastAPI endpoint + CLI entry point, orchestrating the 11 data-flow steps  

The three modules will be implemented tightly coupled, as they strongly depend on each other and share the same contract (JSON-schema-based).

---

## 1. Purpose & Scope

### 1.1 `simulation` Module

**What it delivers:**

- Generate a discrete time series (`TripSimulationResult`) from `Route`, `ChargingPlan`, `SegmentEnergyResult`, and `WeatherSample`  
- Each time step contains: timestamp, position (interpolation between segment boundaries), current SoC, speed (km/h), state (DRIVING/CHARGING/PAUSE)  
- Time resolution: configurable (default: every 60 seconds), alternatively segment boundaries + charging stops as explicit event points  
- Position reconstruction via linear interpolation along the route geometry  
- SoC history: discharge (from `energy` module) and charging operations (from `battery` module, charging curve)

**Out of scope:**

- No real-time simulation (only pure reconstruction based on fixed route + charging plan)  
- No route or charging plan recalculation  
- No weather-ETA iterative resolution (completed by `optimization` module before simulation)

### 1.2 `visualization` Module (Frontend)

**What it delivers:**

- Map with route as GeoJSON LineString with SoC color gradient along the route  
- Markers for charging stops (Tesla Supercharger) and intermediate stops  
- Time/SoC history as a separate chart (line chart over time vs. SoC)  
- Interactive hover info windows per marker/segment  

**Out of scope:**

- No direct map data download strategy (uses OpenFreemap/standard style)  
- No animations (only static display)  
- No navigation (no simulator control, read-only mode only)  

### 1.3 `trip_input`/API Layer

**What it delivers:**

- `POST /trips` endpoint (FastAPI) takes `TripRequest`, returns `TripSimulationResult`  
- CLI entry point (`python -m tripplanner.cli trips …`) calling the same pipeline  
- Orchestration of all 11 steps from the data-flow section (including iterative ETA/weather loop from `optimization`)  
- No business logic, only chaining of already-implemented modules  

**Out of scope:**

- No authentication (local, non-public API endpoint)  
- No persistence (transient only)  
- No caching layer (to be added later if needed)  

---

## 2. Dependencies & Phase Assignment

| Module | Phase | Imported Models (exactly per register) |
| ------- | ------- | ------------------------------------------- |
| **simulation** | Phase 6→7 | `tripplanner.routing.models.Route`, `tripplanner.elevation.models.SegmentGradient`, `tripplanner.weather.models.WeatherSample`, `tripplanner.energy.models.SegmentEnergyResult`, `tripplanner.battery.models.SoCState`, `tripplanner.battery.models.ChargingCurve`, `tripplanner.charging_infrastructure.models.ChargingStation`, `tripplanner.optimization.models.ChargingPlan`, `tripplanner.optimization.models.ChargingStop` |
| **visualization** | Phase 7 | `tripplanner.simulation.models.TripSimulationResult`, `tripplanner.simulation.models.SimulationFrame`, `tripplanner.optimization.models.ChargingStop`, `tripplanner.trip_input.models.Waypoint` (for display only) |
| **trip_input/API** | Phase 7 | `tripplanner.trip_input.models.TripRequest`, `tripplanner.trip_input.models.VehicleProfile`, `tripplanner.trip_input.models.Waypoint`, `tripplanner.routing.models.Route`, `tripplanner.elevation.models.SegmentGradient`, `tripplanner.weather.models.WeatherSample`, `tripplanner.energy.models.SegmentEnergyResult`, `tripplanner.charging_infrastructure.models.ChargingStation`, `tripplanner.optimization.models.ChargingPlan`, `tripplanner.simulation.models.TripSimulationResult` |

**Note:** `trip_input.models` still needs to be implemented; the register is binding for this module — it exists only as a schema definition, no implementation.

---

## 3. Data Models (Python, Pydantic v2)

### 3.1 `simulation.models`

```python
from __future__ import annotations
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated
from pydantic import BaseModel, Field, model_validator

class TripState(str, Enum):
    FAHREN = "FAHREN"
    LADEN = "LADEN"
    PAUSE = "PAUSE"

class SimulationFrame(BaseModel):
    """A single time point in the trip simulation"""

    zeitpunkt: datetime
    position: tuple[
        float, float
    ]  # (lat, lon) – WGS84, consistent with the domain model (see convention below)
    soc_pct: Annotated[float, Field(ge=0.0, le=100.0)]
    zustand: TripState
    geschwindigkeit_kmh: Annotated[float, Field(ge=0.0)]

    @model_validator(mode="after")
    def validate_speed_state_consistency(self) -> SimulationFrame:
        if self.zustand == TripState.LADEN and self.geschwindigkeit_kmh > 0.5:
            raise ValueError("When charging, speed must be ≈ 0")
        if self.zustand == TripState.PAUSE and self.geschwindigkeit_kmh > 5.0:
            raise ValueError("During pause, speed should be very low")
        return self

class TripSimulationResult(BaseModel):
    """Complete time series of a trip"""

    frames: list[SimulationFrame]
    gesamt_distanz_km: float
    gesamt_fahrzeit_min: float
    gesamt_ladezeit_min: float
    start_soc_pct: float
    ziel_soc_pct: float
```

**Supplementary types (not listed in register, but required):**

- `tripplanner.trip_input.models`: `TripRequest` (see register), `Waypoint`, `VehicleProfile` (see register)  

**Important — Coordinate Convention:** `SimulationFrame.position` uses `(lat, lon)` like all domain models (see `docs/plans/01-routing.md`, Section 3). The GeoJSON order `(lon, lat)` is generated exclusively at the frontend rendering boundary (see Section 5.2), never in backend models.

---

## 4. Public Interface (Python)

### 4.1 `simulation.__init__.py` (public API)

```python
from tripplanner.simulation.models import SimulationFrame, TripSimulationResult
from tripplanner.simulation.simulate import simulate_trip

__all__ = ["SimulationFrame", "TripSimulationResult", "simulate_trip"]
```

### 4.2 `simulation/simulate.py` (core function)

```python
from datetime import datetime, timedelta
from typing import Optional
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.battery.models import SoCState, ChargingCurve, ChargingCurvePoint
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.optimization.models import ChargingPlan, ChargingStop
from tripplanner.simulation.models import SimulationFrame, TripState

def simulate_trip(
    route: Route,
    charging_plan: ChargingPlan,
    segment_energy: list[SegmentEnergyResult],
    weather_samples: list[WeatherSample],
    start_soc_pct: float,
    max_iterations: int = 3,
    convergence_threshold_minutes: float = 30.0,
    output_resolution_seconds: int = 60,
) -> TripSimulationResult:
    """
    Simulates the complete trip along the route, considering the charging plan.

    Args:
        route: Route with segments (fixed geometry from GraphHopper)
        charging_plan: Optimized charging plan from optimization module
        segment_energy: Energy consumption per segment
        weather_samples: Weather per query point
        start_soc_pct: Starting SoC in %
        max_iterations: Max. number of iterations for ETA-weather convergence
        convergence_threshold_minutes: Threshold in minutes for iteration refresh
        output_resolution_seconds: Time resolution of output (default: 60s)

    Returns:
        TripSimulationResult: Time series of frames (time, position, SoC, state, speed)
    """
    pass  # Implementation see Section 5
```

### 4.3 `tripplanner.cli` (CLI Entry Point)

**File:** `src/tripplanner/cli/main.py`

```python
import typer
from pathlib import Path
from typing import Optional
from tripplanner.trip_input.api import create_trip_simulation
from tripplanner.simulation.models import TripSimulationResult
import json

app = typer.Typer(help="Tesla Trip Planner – CLI for trip planning and simulation")

@app.command()
def trips(
    start: str = typer.Option(..., help="Start coordinate as 'lat,lon'"),
    ziel: str = typer.Option(..., help="Destination coordinate as 'lat,lon'"),
    zwischenstopps: Optional[list[str]] = typer.Option(
        None, help="Intermediate stops as 'lat,lon:duration_min'"
    ),
    abfahrtszeit: str = typer.Option(
        ..., help="Departure time in ISO format (e.g. '2026-08-15T08:30:00')"
    ),
    start_soc_pct: float = typer.Option(80.0, ge=0.0, le=100.0, help="Start SoC in percent"),
    ziel_soc_pct: float = typer.Option(20.0, ge=0.0, le=100.0, help="Destination SoC in percent"),
    vehicle_profile: str = typer.Option(
        "model3_standard", help="Name of vehicle profile from config"
    ),
    output_json: Optional[Path] = typer.Option(
        None, help="Path to JSON output (default: stdout)"
    ),
) -> None:
    """
    Calculates a trip and simulates it completely (including charging planning and ETA-weather iterative).
    """
    # Input conversion
    from datetime import datetime
    import math

    def parse_coord(s: str) -> tuple[float, float]:
        parts = s.split(",")
        if len(parts) != 2:
            raise ValueError(f"Invalid coordinate: {s}")
        return (
            float(parts[0]),
            float(parts[1]),
        )  # lat, lon — input format "lat,lon" preserves order

    def parse_waypoint(s: str) -> tuple[tuple[float, float], float | None]:
        if ":" in s:
            coord, dur = s.split(":")
            return parse_coord(coord), int(dur) * 60
        return parse_coord(s), None

    start_coord = parse_coord(start)
    ziel_coord = parse_coord(ziel)

    zwischen = []
    if zwischenstopps:
        for wp in zwischenstopps:
            coord, dur = parse_waypoint(wp)
            zwischen.append((coord, timedelta(seconds=dur) if dur else None))

    request = {
        "start": start_coord,
        "ziel": ziel_coord,
        "zwischenstopps": [
            {"koordinate": w[0], "aufenthaltsdauer_s": int(w[1].total_seconds()) if w[1] else None}
            for w in zwischen
        ],
        "abfahrtszeit": abfahrtszeit,
        "fahrzeugprofil": vehicle_profile,
        "praeferenzen": {},
    }

    result: TripSimulationResult = create_trip_simulation(request)

    output = {
        "gesamt_distanz_km": result.gesamt_distanz_km,
        "gesamt_fahrzeit_min": result.gesamt_fahrzeit_min,
        "gesamt_ladezeit_min": result.gesamt_ladezeit_min,
        "frames": [
            {
                "zeitpunkt": f.zeitpunkt.isoformat(),
                "position": list(f.position),
                "soc_pct": f.soc_pct,
                "zustand": f.zustand.value,
                "geschwindigkeit_kmh": f.geschwindigkeit_kmh,
            }
            for f in result.frames
        ],
    }

    if output_json:
        output_json.write_text(json.dumps(output, indent=2))
    else:
        print(json.dumps(output, indent=2))

if __name__ == "__main__":
    app()
```

### 4.4 `tripplanner.trip_input.api` (FastAPI Endpoint)

**File:** `src/tripplanner/trip_input/api.py`

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timedelta
from tripplanner.trip_input.models import TripRequest, Waypoint, VehicleProfile
from tripplanner.simulation.models import TripSimulationResult
import httpx
import asyncio

app = FastAPI(title="Tesla Trip Planner API", version="0.1.0")

# --- Request/Response models for API (identical to models, but explicit for API) ---
class TripRequestAPI(BaseModel):
    """API request for /trips endpoint"""

    start: tuple[float, float] = Field(..., description="Start coordinate (lat, lon)")
    ziel: tuple[float, float] = Field(..., description="Destination coordinate (lat, lon)")
    zwischenstopps: list[WaypointAPI] = Field(default=[], description="List of intermediate stops")
    abfahrtszeit: datetime = Field(..., description="ISO-8601 departure time")
    fahrzeugprofil: str = Field(..., description="Name of vehicle profile from configuration")
    praeferenzen: dict = Field(default_factory=dict, description="User preferences")

class WaypointAPI(BaseModel):
    koordinate: tuple[float, float] = Field(..., description="(lat, lon)")
    aufenthaltsdauer_s: Optional[int] = Field(
        None, ge=0, description="Minimum stay duration in seconds"
    )

class TripSimulationResultAPI(BaseModel):
    """API response for /trips endpoint"""

    gesamt_distanz_km: float = Field(..., description="Total distance in km")
    gesamt_fahrzeit_min: float = Field(..., description="Total driving time in minutes")
    gesamt_ladezeit_min: float = Field(..., description="Total charging time in minutes")
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Destination SoC in %")
    frames: list[FrameAPI]

class FrameAPI(BaseModel):
    zeitpunkt: datetime = Field(..., description="ISO-8601 timestamp")
    position: tuple[float, float] = Field(
        ...,
        description="(lat, lon), consistent with the internal domain model — conversion to GeoJSON (lon, lat) only occurs in the frontend",
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0)
    zustand: str = Field(..., description="'FAHREN', 'LADEN', or 'PAUSE'")
    geschwindigkeit_kmh: float = Field(..., ge=0.0)

# --- Core function (shared by API and CLI) ---
async def create_trip_simulation(request_dict: dict) -> TripSimulationResult:
    """
    Orchestrates the 11 data-flow steps:
    1. OSM routing (including intermediate stops as required waypoints)
    2. Extract elevation profile
    3. Divide route into segments
    4. Initial ETA per segment (rough estimate)
    5. Fetch weather data for rough ETAs
    6. Incorporate construction sites
    7. Calculate energy consumption per segment
    8. Determine optimal charging plan
    9. Update ETA with actual driving/charging time + iteration
    10. Simulate entire trip
    11. Prepare results

    Implementation see Section 5.
    """
    raise NotImplementedError("Orchestration must be implemented")

@app.post("/trips", response_model=TripSimulationResultAPI, status_code=201)
async def create_trip_endpoint(request: TripRequestAPI):
    """
    Creates a new trip simulation.
    """
    try:
        result = await create_trip_simulation(request.model_dump())
        return TripSimulationResultAPI(
            gesamt_distanz_km=result.gesamt_distanz_km,
            gesamt_fahrzeit_min=result.gesamt_fahrzeit_min,
            gesamt_ladezeit_min=result.gesamt_ladezeit_min,
            start_soc_pct=result.start_soc_pct,
            ziel_soc_pct=result.ziel_soc_pct,
            frames=[
                FrameAPI(
                    zeitpunkt=f.zeitpunkt,
                    position=f.position,
                    soc_pct=f.soc_pct,
                    zustand=f.zustand.value,
                    geschwindigkeit_kmh=f.geschwindigkeit_kmh,
                )
                for f in result.frames
            ],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {str(e)}")
```

---

## 5. External Integration / Algorithm Details

### 5.1 `simulation` Module: Algorithms

#### 5.1.1 Time Resolution & Interpolation

**Two approaches, uniformly controllable via flag (`output_resolution_seconds` vs. `segment_boundaries_only`):**

1. **Segment boundaries + charging stops (event-based):**
   - Each segment start/end + charging stop + intermediate stop becomes a frame  
   - Linear interpolation of position and SoC in between  

2. **Regular sampling rate (default: 60s):**
   - Start at `abfahrtszeit`  
   - For each interval `t .. t+60s`:
     - Determine current segment (via `position` + `distances_cumsum`)
     - If SoC ≥ 100% and no charging stop → "PAUSE" until charging stop  
     - If charging stop active → "CHARGING", SoC↑ per charging curve  
     - Else → "DRIVING", SoC↓ per segment energy  

**Helper function `interpolate_position(route: Route, distance: float) -> tuple[float, float]`:**

- `distances_cumsum = [0] + list(accumulate(r.gesamtlaenge_m for r in route.segments))`  
- Find segment `i` with `distances_cumsum[i] ≤ distance < distances_cumsum[i+1]`  
- Interpolate fraction `α = (distance - distances_cumsum[i]) / route.segments[i].laenge_m`  
- `coords = route.segments[i].geometrie` (LineString)  
- `lat = coords[0][0] + α * (coords[-1][0] - coords[0][0])`  
- `lon = coords[0][1] + α * (coords[-1][1] - coords[0][1])`  
- Return `(lat, lon)` — consistent with `RouteSegment.geometrie` and all domain models; conversion to `(lon, lat)` only occurs at GeoJSON generation in the frontend (Section 5.2)

#### 5.1.2 Charging Curve Interpolation

**`battery.ChargingCurve` + `ChargingCurvePoint`** (soc_pct, ladeleistung_kw):

```python
def interpolate_charging_power(curve: list[ChargingCurvePoint], soc: float) -> float:
    """Piecewise linear interpolation of charging power."""
    if soc <= curve[0].soc_pct:
        return curve[0].ladeleistung_kw
    if soc >= curve[-1].soc_pct:
        return curve[-1].ladeleistung_kw

    for i in range(len(curve) - 1):
        if curve[i].soc_pct <= soc <= curve[i + 1].soc_pct:
            alpha = (soc - curve[i].soc_pct) / (curve[i + 1].soc_pct - curve[i].soc_pct)
            return curve[i].ladeleistung_kw + alpha * (
                curve[i + 1].ladeleistung_kw - curve[i].ladeleistung_kw
            )
    raise RuntimeError("Unreachable")
```

#### 5.1.3 Calculate Charging Duration

```python
def compute_charge_duration(
    start_soc_pct: float,
    target_soc_pct: float,
    charging_curve: list[ChargingCurvePoint],
    nominal_power_kw: float,
    duration_seconds: int,
) -> float:
    """
    Calculates how much SoC is reached in `duration_seconds`.
    Conversely: How long until target SoC?
    """
    if target_soc_pct <= start_soc_pct:
        return 0.0

    # Energy requirement ∫ power(SoC) dSoC
    energy_kwh = 0.0
    for i in range(len(charging_curve) - 1):
        soc1, p1 = charging_curve[i].soc_pct, charging_curve[i].ladeleistung_kw
        soc2, p2 = charging_curve[i + 1].soc_pct, charging_curve[i + 1].ladeleistung_kw

        if start_soc_pct >= soc2 or target_soc_pct <= soc1:
            continue  # Interval not affected

        low = max(start_soc_pct, soc1)
        high = min(target_soc_pct, soc2)

        # Average charging power in segment
        p_avg = (p1 + p2) / 2.0
        energy_kwh += p_avg * (high - low) / 100.0  # (kW) * (SoC%-points) / 100

    # Duration (seconds) = Energy / (power * efficiency) – simplified: power * 0.95
    efficiency = 0.95
    required_seconds = (energy_kwh * 3600) / (nominal_power_kw * efficiency)

    return min(required_seconds, duration_seconds)  # max. as long as available
```

### 5.2 `visualization` Module: MapLibre GL JS Setup

#### 5.2.1 Project Structure (recommended)

```
frontend/
├── src/
│   ├── main.tsx                # Entry point, MapLibre Worker setup
│   ├── App.tsx
│   ├── components/
│   │   ├── Map.tsx             # MapLibre component (see example)
│   │   ├── RouteLine.tsx       # Line with line-gradient SoC color gradient
│   │   ├── StopMarkers.tsx     # Markers for charging stops + intermediate stops
│   │   └── SocChart.tsx        # Line chart over time vs. SoC (e.g. recharts)
│   └── types/
│       └── generated.ts        # Types generated from JSON schema (see below)
└── package.json
```

#### 5.2.2 MapLibre `line-gradient` Syntax

**Concrete expression (from research results):**

```javascript
'line-gradient': [
  'interpolate',
  ['linear'],                   // Interpolation type
  ['line-progress'],            // Input: 0 = start, 1 = end of line
  0,   '#ef4444',               // Start: red (low SoC)
  0.3, '#f59e0b',               // 30%: orange
  0.6, '#eab308',               // 60%: yellow
  1.0, '#22c55e'                // End: green (high SoC)
]
```

**Important:** The `line-gradient` expression **cannot directly** reference GeoJSON properties (`['get', 'soc_start']`).  
**Workaround**: The color stops are **pre-calculated** and attached to a LineString with properties.  
But: MapLibre GL JS allows **only line-progress** as input for `line-gradient`.  
**Solution**: We generate a **color lookup function** on the client side.

**Recommended implementation (via client logic, not MapLibre expression):**

1. Generate `frames` from `TripSimulationResult` → calculate `SoC` per km or per `line-progress`  
2. Create **a color interpolation function** in JavaScript:

```typescript
function socToColor(soc: number): string {
  // Color gradient: red → yellow → green
  if (soc <= 10) return '#ef4444';
  if (soc <= 30) return '#f97316';
  if (soc <= 50) return '#eab308';
  if (soc <= 70) return '#84cc16';
  return '#22c55e';
}

// Generate colors per frame
const colors = simulationResult.frames.map(f => socToColor(f.soc_pct));
```

1. Create a **GeoJSON LineString with `line-stops` pseudo-feature**  
   **Or simpler**: Create **multiple GeoJSON lines**, each with a single-color segment (see "Workaround" search).  
   **Practical**: Create **a single LineString GeoJSON**, calculate `line-progress` (0–1) for each frame, and create an array of colors.  
   MapLibre does **not support data-driven line-gradient** → we simulate it with **segmentation**:

```javascript
// We divide the route into 100 segments, each with its own layer
const segments = 100;
for (let i = 0; i < segments; i++) {
  const start = i / segments;
  const end = (i + 1) / segments;
  const soc = simulateSocAtProgress(start);
  const color = socToColor(soc);

  map.addLayer({
    id: `route-segment-${i}`,
    type: 'line',
    source: 'route-source',
    filter: ['all', ['==', '$type', 'LineString']],
    paint: {
      'line-color': color,
      'line-width': 4,
      'line-opacity': 0.9,
      'line-gradient': [
        'interpolate', ['linear'],
        ['line-progress'],
        start, color,
        end, color
      ]
    }
  });
}
```

**But that's inefficient! Better: use `line-dasharray` + `line-color` (no gradient)**  
→ **Final recommendation (from research)**: Since `line-gradient` **does not allow data-driven styling**, we instead use:

- **Single color per line** (no gradient), or  
- **Multiple layers** with `line-color`, per segment  
- **Or**: Use a **raster-based heatmap overlay** (not within scope)  

**Final decision (based on research):**  
> We use a **LineString GeoJSON with `line-color` per segment**, dividing the route into N segments (e.g. 100). Each segment gets a color based on the average SoC in that segment. MapLibre does not support data-driven `line-gradient`, so this is the pragmatic workaround.

#### 5.2.2b Coordinate Conversion at the Rendering Boundary

All backend models (API response, `SimulationFrame`, `ChargingStation`, `Waypoint`) deliver coordinates as `(lat, lon)` — identical to the convention of all Python domain models. MapLibre GL JS (`setLngLat`, GeoJSON `coordinates`) expects strictly `[lng, lat]` however. The conversion happens **exclusively** at this one location in the frontend, nowhere else:

```typescript
// frontend/src/utils/geo-utils.ts

/** Converts a backend coordinate (lat, lon) to MapLibre order [lng, lat]. */
export function toLngLat([lat, lon]: [number, number]): [number, number] {
  return [lon, lat];
}

/** Converts a list of backend coordinates for a GeoJSON LineString. */
export function routeToGeoJsonCoordinates(
  geometrie: [number, number][],
): [number, number][] {
  return geometrie.map(toLngLat);
}
```

Usage in route GeoJSON generation: `coordinates: routeToGeoJsonCoordinates(route.geometrie)`. Usage in markers: `.setLngLat(toLngLat(stop.standort.koordinate))`.

#### 5.2.3 Markers for Charging Stops + Intermediate Stops

```typescript
import { Marker } from 'maplibre-gl';
import { toLngLat } from './utils/geo-utils';

// Charging stops (Tesla Supercharger)
chargingStops.forEach(stop => {
  const el = document.createElement('div');
  el.className = 'marker-charger';
  el.innerHTML = '⚡'; // or SVG icon

  new Marker(el)
    .setLngLat(toLngLat(stop.standort.koordinate))  // API returns (lat, lon); toLngLat() converts to MapLibre [lng, lat]
    .setPopup(new Popup().setHTML(`<h3>${stop.standort.name}</h3><p>SoC: ${stop.ankunfts_soc_pct.toFixed(0)}% → ${stop.ziel_soc_pct.toFixed(0)}%</p>`))
    .addTo(map);
});

// Intermediate stops (optional stay)
waypoints.forEach((wp, i) => {
  if (wp.aufenthaltsdauer_s) {
    const el = document.createElement('div');
    el.className = 'marker-waypoint';
    el.innerHTML = '📍';

    new Marker(el)
      .setLngLat(toLngLat(wp.koordinate))  // API returns (lat, lon); toLngLat() converts to MapLibre [lng, lat]
      .setPopup(new Popup().setHTML(`<p>Intermediate stop ${i + 1}<br>Pause: ${wp.aufenthaltsdauer_s / 60} min</p>`))
      .addTo(map);
  }
});
```

### 5.3 JSON Schema Contract Generation (Backend → Frontend)

#### 5.3.1 Pydantic → JSON Schema

```python
# src/tripplanner/simulation/generate_schema.py
import json
from pathlib import Path
from tripplanner.simulation.models import TripSimulationResult, SimulationFrame
from tripplanner.optimization.models import ChargingStop

def generate_schema(output_dir: Path = Path("frontend/src/types")) -> None:
    """Generates JSON schema and converts to TypeScript."""
    output_dir.mkdir(exist_ok=True)

    # Schema for TripSimulationResult (including $defs)
    schema = TripSimulationResult.model_json_schema(by_alias=False, ref_template="#/$defs/{model}")

    # Save
    (output_dir / "simulation.schema.json").write_text(json.dumps(schema, indent=2))

    # Schema for ChargingStop (optional, for display)
    (output_dir / "charging.schema.json").write_text(
        json.dumps(ChargingStop.model_json_schema(by_alias=False), indent=2)
    )

if __name__ == "__main__":
    generate_schema()
```

#### 5.3.2 JSON Schema → TypeScript (CLI-based)

**Installation:**

```bash
npm install -g json-schema-to-typescript
# or locally
npm install json-schema-to-typescript
```

**Generation (after schema generation):**

```bash
json2ts -i frontend/src/types/simulation.schema.json -o frontend/src/types/generated.ts
```

**Output file `frontend/src/types/generated.ts` (excerpt):**

```typescript
export interface SimulationFrame {
  zeitpunkt: string;          // ISO 8601
  position: [number, number]; // [lat, lon] – convert with toLngLat() from geo-utils.ts before MapLibre rendering (see Section 5.2)
  soc_pct: number;
  zustand: 'FAHREN' | 'LADEN' | 'PAUSE';
  geschwindigkeit_kmh: number;
}

export interface TripSimulationResult {
  frames: SimulationFrame[];
  gesamt_distanz_km: number;
  gesamt_fahrzeit_min: number;
  gesamt_ladezeit_min: number;
  start_soc_pct: number;
  ziel_soc_pct: number;
}
```

**Integration into `package.json` script:**

```json
{
  "scripts": {
    "gen:types": "python -m tripplanner.simulation.generate_schema && json2ts -i frontend/src/types/simulation.schema.json -o frontend/src/types/generated.ts"
  }
}
```

---

## 6. Test Strategy

### 6.1 `simulation` Tests (Unit)

**Fixtures (in `tests/fixtures/simulation/`):**

- `route_segment_example.json`: 3 segments, geometry, lengths, gradients  
- `weather_samples_example.json`: 2 weather query points, temperature/wind  
- `energy_results_example.json`: Energy consumption per segment (kWh)  
- `charging_plan_example.json`: 2 charging stops, `ChargingStop` lists  
- `expected_frames_60s.json`: Expected frames with 60s resolution (reference)  

**Test cases (minimum 3, including edge cases):**

**Test 1: Simple simulation (no charging stops)**

```python
def test_simulate_trip_no_charging():
    # Given: Route with 3 segments (10km, 5km, 8km), 0% gradient, 120km/h limit
    route = Route(segments=[...], gesamtlaenge_m=23_000, geometrie=[...])
    energy = [SegmentEnergyResult(segment_index=0, energiebedarf_kwh=1.5, rekuperation_kwh=0.0),
              SegmentEnergyResult(segment_index=1, energiebedarf_kwh=0.8, ...),
              SegmentEnergyResult(segment_index=2, energiebedarf_kwh=1.2, ...)]
    plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=820)  # 13.7 min driving time

    # When: Simulation with start_soc_pct=80, output_resolution_seconds=60
    result = simulate_trip(route, plan, energy, [], start_soc_pct=80.0)

    # Then: 14 frames (840s / 60s + 1), SoC ends at ~65%, no CHARGING state
    assert len(result.frames) == 14
    assert result.frames[0].soc_pct == 80.0
    assert result.frames[-1].soc_pct < 70.0
    assert all(f.zustand == TripState.FAHREN for f in result.frames)
    assert result.gesamt_fahrzeit_min == pytest.approx(13.7, rel=0.01)
```

**Test 2: Simulation with one charging stop (convergence)**

```python
def test_simulate_trip_with_charging():
    # Given: Charging stop in the middle of the route, SoC drops to 20%, target SoC=40%
    # ChargingCurve: [10% → 150kW], [30% → 120kW], [50% → 100kW], [80% → 60kW]
    plan = ChargingPlan(
        ladehalte=[
            ChargingStop(
                station=...,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=40.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=...,
                abfahrtszeit=...,
            )
        ],
        gesamtreisezeit_s=1200,
    )

    result = simulate_trip(route, plan, energy, [], start_soc_pct=80.0)

    # Then: at least one CHARGING frame, total trip time > driving time
    assert any(f.zustand == TripState.LADEN for f in result.frames)
    assert result.gesamt_ladezeit_min > 20.0
    assert result.frames[-1].soc_pct == pytest.approx(40.0, abs=0.5)
```

**Test 3: Edge case – SoC over 100% (regeneration not possible)**

```python
def test_simulate_trip_soc_cap():
    # Given: Charging stop with target SoC=120% (invalid, but test validation)
    plan = ChargingPlan(
        ladehalte=[ChargingStop(..., ziel_soc_pct=120.0, ...)],
        gesamtreisezeit_s=1200
    )

    with pytest.raises(ValueError, match="SoC must not exceed 100%"):
        simulate_trip(route, plan, energy, [], start_soc_pct=80.0)
```

**Test boundary:**

- Unit tests: all above (fixed inputs, no mocks except external APIs)  
- Integration test (`@pytest.mark.integration`): Simulate real route from `routing` fixture, weather fixture, `optimization` output → complete pipeline

---

### 6.2 `visualization` Tests (E2E, Playwright)

**Test cases (browser-based, `tests/e2e/visualization/`):**

**Test 1: Route with color gradient is rendered**

```typescript
test('route-line renders with gradient color segments', async ({ page }) => {
  await page.goto('/?demo=true'); // Demo mode with predefined simulation

  // Wait for map initialization
  await page.waitForSelector('.maplibregl-map');

  // Check that route line exists (layer 'route-segment-0' to -N)
  const layers = await page.evaluate(() =>
    map.getStyle().layers.map((l: any) => l.id).filter((id: string) => id.startsWith('route-segment-'))
  );
  expect(layers.length).toBeGreaterThan(50); // at least 50 segments
});
```

**Test 2: Charging stop markers are displayed**

```typescript
test('charger markers appear on map', async ({ page }) => {
  await page.goto('/?demo=true');

  // Wait for marker container
  await page.waitForSelector('.marker-charger');

  const markers = await page.$$('.marker-charger');
  expect(markers.length).toBeGreaterThan(0);

  // Click on a marker → popup opens
  await markers[0].click();
  await page.waitForSelector('.maplibregl-popup');
});
```

**Test 3: SoC chart renders time series**

```typescript
test('soc chart displays time vs. SoC line', async ({ page }) => {
  await page.goto('/?demo=true');

  // Wait for SVG chart (recharts)
  await page.waitForSelector('.recharts-line');

  const path = await page.getAttribute('.recharts-line path', 'd');
  expect(path).not.toBe(null);
  expect(path!.length).toBeGreaterThan(100); // at least one curve
});
```

**Test boundary:**

- Unit tests (Jest/Vitest): Only `socToColor`, `interpolatePosition` → low effort  
- E2E (Playwright): Full application, test server, demo mode, compare snapshots  

---

### 6.3 `trip_input` Tests (Integration)

**Test cases:**

**Test 1: API endpoint returns correct JSON**

```python
def test_api_create_trip_endpoint(client: AsyncClient):
    # Given: Payload per test fixture
    payload = {
        "start": [8.6821, 50.1109],   // Frankfurt
        "ziel": [11.5820, 48.1351],   // Munich
        "zwischenstopps": [],
        "abfahrtszeit": "2026-08-15T08:00:00",
        "fahrzeugprofil": "model3_standard",
        "praeferenzen": {}
    }

    # When: POST /trips
    response = await client.post("/trips", json=payload)
    assert response.status_code == 201

    data = response.json()
    # Then: Response matches TripSimulationResultAPI
    assert "frames" in data
    assert len(data["frames"]) > 0
    assert data["gesamt_fahrzeit_min"] > 0
    assert data["gesamt_ladezeit_min"] >= 0
```

**Test 2: CLI outputs JSON to stdout (regression)**

```python
def test_cli_trips_output(capsys):
    # Given: Mocked API call (via subprocess)
    result = subprocess.run(
        ["python", "-m", "tripplanner.cli", "trips",
         "--start", "8.6821,50.1109",
         "--ziel", "11.5820,48.1351",
         "--abfahrtszeit", "2026-08-15T08:00:00"],
        capture_output=True, text=True
    )

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "frames" in data
    assert isinstance(data["frames"], list)
```

**Test boundary:**

- Unit: API schema validation (`TripRequestAPI.model_validate()`)  
- Integration: E2E with simulated backend modules (mocked, no real API calls)  

---

## 7. Task Checklist (TDD Order)

### 7.1 `simulation` Module

- [ ] **Task 1 (simulation/models.py):** Implement Pydantic models `SimulationFrame`, `TripSimulationResult`, `TripState` enum, add validators (SoC range, state-speed consistency)  
  - Acceptance: `pytest tests/simulation/test_models.py` runs, all validations tested

- [ ] **Task 2 (simulation/simulate.py – function skeleton):** Implement `simulate_trip()` function signature, docstring, doc-tests  
  - Focus: `pytest tests/simulation/test_simulate.py::test_function_signature` ✓

- [ ] **Task 3 (simulation/simulate.py – position interpolation):** Implement `interpolate_position()`, tests with 3 segments (straight, curved, 0-length)  
  - Acceptance criterion: `(lat, lon)` order (consistent with domain model), linear interpolation, segment index calculation; GeoJSON conversion `(lon, lat)` handled separately in frontend

- [ ] **Task 4 (simulation/simulate.py – energy consumption per time step):** `compute_energy_for_interval()` → divide segment energy into sub-intervals  
  - Focus: Linear interpolation of distance, energy proportionally

- [ ] **Task 5 (simulation/simulate.py – charging curve interpolation):** Implement `interpolate_charging_power()`, tests for edge cases (SoC < min, > max)  
  - Focus: Piecewise linear, input validation

- [ ] **Task 6 (simulation/simulate.py – full simulation):** Fully implement `simulate_trip()`, call all 11 steps  
  - Focus: Loop over time intervals, SoC update, charging state selection, frame generation

- [ ] **Task 7 (simulation/fixtures):** Create fixtures for 2 scenarios (without charging stop, with charging stop), create reference frames  
  - Focus: JSON files, manually validated

- [ ] **Task 8 (simulation/test_simulate.py):** Implement 3 test cases (without charging stop, with charging stop, SoC cap)  
  - Focus: `pytest --cov=tripplanner.simulate --cov-report=term-missing` → coverage ≥ 85%

---

### 7.2 `visualization` Frontend (React + TypeScript)

- [ ] **Task 9 (frontend/setup):** Initialize Vite + React + TypeScript project, install MapLibre GL JS (`?worker&url` setup!)  
  - Focus: `vite.config.ts`, `tsconfig.json`, `main.tsx` with worker URL

- [ ] **Task 10 (frontend/types/):** Generate JSON schema (`python -m tripplanner.simulation.generate_schema`), generate TypeScript types (`json2ts`)  
  - Focus: `npm run gen:types`, commit `generated.ts`

- [ ] **Task 11 (frontend/components/Map.tsx):** Minimal map component with OpenFreemap style  
  - Focus: `map.on('load')`, baseline setup

- [ ] **Task 12 (frontend/components/RouteLine.tsx):** Route line component with segmentation (no real gradient, but color segments)  
  - Focus: `map.addSource('route', { type: 'geojson', data: geojson })`, loop `route-segment-0..N` to generate layers, color per segment based on intermediate SoC values

- [ ] **Task 13 (frontend/components/StopMarkers.tsx):** Markers for charging stops + intermediate stops (icon + popup)  
  - Focus: `Marker().setLngLat().addTo(map)`, dynamic popup content

- [ ] **Task 14 (frontend/components/SocChart.tsx):** Time series (X: time, Y: SoC) as line chart (recharts)  
  - Focus: `data={frames.map(f => ({ time: f.zeitpunkt, soc: f.soc_pct }))}`, recharts `Line` component

- [ ] **Task 15 (frontend/App.tsx):** Compose components, demo mode (fixed simulation), API mode (fetch `/trips`)  
  - Focus: State management (Context or Zustand in `App`)

- [ ] **Task 16 (frontend/e2e/):** Implement Playwright tests (route line, markers, SoC chart)  
  - Focus: `npx playwright codegen`, compare snapshots

---

### 7.3 `trip_input`/API Layer

- [ ] **Task 17 (trip_input/api.py):** FastAPI app with `POST /trips` endpoint (request/response models)  
  - Focus: `TripRequestAPI`, `TripSimulationResultAPI` Pydantic models, status code 201

- [ ] **Task 18 (trip_input/api.py):** Implement `create_trip_simulation()` function (orchestration of 11 steps)  
  - Focus: Call `routing`, `elevation`, `weather`, `energy`, `optimization`, `simulation` modules in correct order

- [ ] **Task 19 (trip_input/api.py):** Error handling (HTTPException on errors)  
  - Focus: Try/catch, status 500, meaningful error message

- [ ] **Task 20 (cli/main.py):** Implement Typer CLI with `trips` command, parse input parameters (coordinates, intermediate stops, departure time)  
  - Focus: `typer.Option`, `parse_coord`, `parse_waypoint`

- [ ] **Task 21 (cli/main.py):** Call `create_trip_simulation(request_dict)`, JSON output (stdout or file)  
  - Focus: `json.dumps`, `output_json.write_text`

- [ ] **Task 22 (tests/test_api.py):** API integration test (`test_api_create_trip_endpoint`)  
  - Focus: `client = AsyncClient(app)`, `await client.post("/trips", json=payload)`

- [ ] **Task 23 (tests/test_cli.py):** CLI integration test (`test_cli_trips_output`)  
  - Focus: `subprocess.run`, `json.loads(result.stdout)`

- [ ] **Task 24 (docs/plans/08-simulation-visualization-api.md):** Complete this document (now).  
  - Focus: Research, structure, details, tests.

---

## 8. Risks & Open Technical Questions

### 8.1 Simulation

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Position interpolation along curved segments** | Positions too inaccurate for highly curved roads | Use `shapely` for geometry interpolation (better than linear) — **recommended addition**, if time permits |
| **Charging curve outside table bounds (extrapolation)** | Incorrect charging duration, SoC overshoot or undershoot | `ValueError` on extrapolation, default SoC=100% after charging stop |

### 8.2 Visualization

| Risk | Impact | Mitigation |
|------|--------|------------|
| **MapLibre GL JS does not support `line-gradient` data-driven** | No SoC color gradient along line, only color segments | Accept segmentation, document limitation, possible future migration to vector tiles with data-driven styling |

### 8.3 trip_input / API

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Iterative ETA/weather convergence missing in `create_trip_simulation()`** | Weather and ETA inconsistent, incorrect energy calculation | Implement exactly the 6-step loop from `02-architecture.md`, configurable threshold (`convergence_threshold_minutes`) |

### 8.4 Open Technical Questions (not covered by open points)

1. **Should the `simulation` module take over `tripplanner.weather.models.WeatherSample` completely or only Temperature/Wind?**  
   → **Decision:** Only take over those weather parameters that the `energy` module needs (initially only temperature, wind speed/direction), ignore others in `WeatherSample`. Future extension without breaking changes possible.

2. **How is the route resolved when intermediate stops are included?**  
   → **Decision:** `Route.segments` already contains all intermediate stop coordinates as segment boundaries. Simulation simply iterates over all segments. No extra handling needed.

3. **Should `TripSimulationResult` also contain `ChargingPlan` information (e.g., for traceability)?**  
   → **Decision:** No, `TripSimulationResult` is a pure time series (`frames`). `ChargingPlan` stays in the `optimization` module. If needed, an extension (`frames_with_plan: list[tuple[SimulationFrame, list[ChargingStop]]]`) can be introduced later.

4. **Should the CLI support a `--output-format` option (JSON, CSV, GeoJSON)?**  
   → **Decision:** No, limit scope to JSON (simplest interoperability). CSV/GeoJSON can be added later as a future extension via addon module (`tripplanner.export`).

5. **Should the API allow CORS (for external clients)?**  
   → **Decision:** No, blocked by default (`CORSMiddleware` not activated). Local development allows `--cors` flag for dev mode, not for production.

---

## 9. Summary

This plan defines the implementation of `simulation` (Phase 6→7), `visualization` (frontend, Phase 7), and `trip_input`/API (Phase 7) as closely interlinked components:

- **`simulation`** generates a time series from a fixed route and charging plan via discrete time steps (60s default) with linear position interpolation and SoC calculation (discharge from `energy`, charging curves from `battery`).  
- **`visualization`** uses Vite + TypeScript + MapLibre GL JS, uses JSON schema generation (`pydantic.model_json_schema()` → `json-schema-to-typescript`) for TS types, uses segmentation for route color gradient (workaround for missing data-driven `line-gradient`).  
- **`trip_input`/API** provides FastAPI endpoint (`POST /trips`) and Typer CLI (`python -m tripplanner.cli trips …`), orchestrates all 11 steps including iterative ETA/weather resolution.  
- **Tests** follow the TDD pattern (test first), coverage gate 85% (`simulation`), Playwright E2E (`visualization`), integration tests (`trip_input`).  
- **Risks** are identified and documented with mitigations (e.g., `line-gradient` limitation accepted, `convergence_threshold_minutes` configurable).

The plan is complete, ready for implementation, and covers all requirements from the documents `01-project-specifications.md`, `02-architecture.md`, `03-module-specifications.md`, `06-open-points-contradictions.md`.

---

**End of plan.**
