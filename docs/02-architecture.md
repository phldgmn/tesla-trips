# System Architecture

## Overview

The system consists of two clearly separated phases, supplied by independent, replaceable modules:

```text
Phase 1 — Road Routing (GraphHopper)
   Origin, destination, intermediate stops  →  road route(s) (geometry + segments)

Phase 2 — Energy/Charging Optimization (custom logic)
   Route + elevation profile + weather + roadworks + charging infrastructure + departure time
        →  optimized charging plan + complete simulation
```

**Important:** Phase 1 has no knowledge of energy or charging information. Phase 2 does not modify the road route (no energy-optimal rerouting in the current implementation) — it exclusively plans charging stops and speed profiles on the route determined by GraphHopper. This separation is a deliberate simplification; see `06-open-points-contradictions.md` for a discussion of its implications.

## Component Diagram (Textual)

```text
                     ┌──────────────────────┐
                     │  Trip Input          │
                     │  Origin, destination,│
                     │  intermediate stops, │
                     │  departure time      │
                     └─────────┬────────────┘
                               │
                     ┌─────────▼────────────┐
                     │  Routing Module      │
                     │  (GraphHopper Client)│
                     └─────────┬────────────┘
                               │ Route + segments
              ┌────────────────┼─────────────────┐
              │                │                 │
    ┌─────────▼───────┐ ┌──────▼───────┐ ┌───────▼─────────┐
    │ Elevation Model │ │ ETA Estimator│ │ Roadworks Module│
    │ (DEM lookup)    │ │ (iterative)  │ │ (DATEX II)      │
    └─────────┬───────┘ └──────┬───────┘ └───────┬─────────┘
              │                │                 │
              │       ┌────────▼─────────┐       │
              │       │  Weather Module  │       │
              │       │  (Open-Meteo)    │       │
              │       └────────┬─────────┘       │
              │                │                 │
              └────────┬───────┴────────┬────────┘
                       │                │
              ┌────────▼────────────────▼──────────┐
              │      Energy Consumption Module     │
              │  (physical model, modular)         │
              └────────────────┬───────────────────┘
                                │ Energy demand per segment
              ┌─────────────────▼──────────────────┐
              │  Charging Infrastructure Module    │
              │  (Tesla) + Charging Curve Module   │
              └─────────────────┬──────────────────┘
                                │
              ┌─────────────────▼──────────────────┐
              │  Optimization Module               │
              │  (state-space search, OR-Tools/    │
              │   NetworkX)                        │
              └─────────────────┬──────────────────┘
                                │ Charging plan + complete simulation
              ┌─────────────────▼──────────────────┐
              │  Visualization (MapLibre/Leaflet)  │
              └────────────────────────────────────┘
```

## Iterative Time/Weather Resolution

Problem: The expected passage time at a segment depends on the preceding energy consumption and charging history, which in turn depends on the weather at earlier segments. There is therefore a dependency between ETA calculation and weather queries.

**Solution — two-stage calculation:**

1. **Initial rough forward estimate:** Estimate the ETA for each segment based on a reference speed (e.g. speed limit or historical average) without considering energy or charging effects.
2. **Weather query** for these rough ETAs (resolution: every 20–50 km or every 20–30 minutes of driving time).
3. **Energy and charging plan calculation** incorporating the retrieved weather data.
4. **ETA update** for each segment based on the actually calculated driving and charging times.
5. **Convergence check:** If the updated ETA at a weather query point differs from the originally queried time by more than a configurable threshold (e.g. 30 minutes), repeat the weather query for the affected points and rerun steps 3–4.
6. In practice, a single additional iteration is generally sufficient for day-scale trips; nevertheless, the maximum number of iterations should be configurable as a safety limit.

> **Implementation status:** Iterative time/weather resolution is fully implemented as of
> Phase E of `docs/plans/10-provider-integration-wiring.md`: `create_trip_simulation()` contains the convergence loop covering steps 4–9, with `max_iterations` and `convergence_threshold_minutes` as configurable parameters, and `refetch_weather` is used starting with the second iteration (see `src/tripplanner/trip_input/api.py:673-747`).

## Intermediate Stops as Constraints

Intermediate stops are passed to the routing module as ordered mandatory waypoints (GraphHopper natively supports multiple waypoints). For the optimization layer, they are modeled as additional nodes in the state graph with the following properties:

* fixed position, determined by the route
* optional minimum dwell time (constraint: arrival time + dwell time ≤ departure for the next segment)
* optionally combinable with a charging session if a Tesla Supercharger is available at the same location

## Technology Stack

| Layer                        | Technology                                                                                                                                  |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend / Optimization Logic | Python (uv as package/runtime manager)                                                                                                      |
| Routing Engine               | GraphHopper (Java, operated as a local service/container and accessed via HTTP API)                                                         |
| Map Data Pipeline            | OSM extracts (Osmium/osmconvert), stored locally                                                                                            |
| Geodata/Elevation            | Copernicus DEM/SRTM tiles, stored locally, accessed via rasterio                                                                            |
| Persistence                  | Local database for charging infrastructure, segments, and cache (e.g. SQLite/DuckDB for the prototype; PostGIS as a later option if needed) |
| Frontend/Visualization       | TypeScript, MapLibre GL JS or Leaflet                                                                                                       |
| Optimization                 | OR-Tools and/or NetworkX (interchangeable behind a common interface)                                                                        |

## Module Boundaries and Interfaces

Each module communicates exclusively through clearly typed data structures (e.g. Pydantic models in Python), never through implicit global state. Module-specific details are documented in `03-module-specifications.md`.

Core principle: Every module must be independently testable (unit tests without network access; external data sources are mocked through interfaces).
