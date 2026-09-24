# Project Specification: Personalized Tesla Trip Planner

## Objective

Develop a highly personalized trip planner for a Tesla Model 3. The focus is not on fast, standard navigation, but on a realistic simulation of energy consumption and optimal charging planning.

Unlike existing solutions (e.g. ABRP), the system shall:

* account for the individual driving style,
* be based on a physically grounded consumption model that can later be calibrated using the vehicle's own driving data,
* take extensive personal preferences into account,
* operate entirely locally, without dependence on proprietary routing services.

The innovation does not lie in route calculation itself, but in optimizing the overall trip: energy consumption, charging behavior, environmental conditions, and personal preferences are combined into a realistic, individualized trip plan.

---

## Core Architecture

The project is divided into largely independent modules (see `02-architecture.md`).

### Routing

Calculate one or more sensible road routes between the origin and destination. This component initially has no knowledge of battery state, charging plans, or energy consumption.

**Preferred: GraphHopper**

* local operation with OSM data
* mature routing quality, well documented, extensible
* support for turn costs, elevation profiles, vehicle profiles, and custom weightings
* clean API

**Alternative: Valhalla** — good support for different vehicle types, flexible cost functions, and also suitable for local operation. Remains a viable alternative, although GraphHopper is currently easier to extend.

**Not recommended: OSRM** — extremely fast, but insufficiently flexible for custom cost functions and vehicle models.

### OSM Data

Road infrastructure is stored entirely locally (offline, reproducible, no API limits). Required data includes: road class, speed limits, curves, intersections, turn costs, road type, surface, tunnels/bridges.

### Elevation Model

Sources: Copernicus DEM or SRTM. Imported locally once. Used to calculate gradients, descents, regenerative braking, and potential energy.

---

## Energy Consumption

Physically modeled, modular, and designed to be calibrated against real vehicle data later.

**Core components:** rolling resistance, aerodynamic drag, elevation energy, regenerative braking, auxiliary loads, battery behavior.

**Influencing factors:** vehicle speed, outside temperature, wind, precipitation, snowfall, road surface, vehicle load, roof box, winter tires.

---

## Weather Model

Weather is evaluated segment-by-segment along the route (not for every individual meter of road) — e.g. every 20–50 km or every 20–30 minutes of driving time. For each point, conditions are retrieved for the expected time of passage.

**Source: Open-Meteo** — free of charge, no API key required, multiple weather models, historical data, hourly resolution.

**Required variables:** temperature, wind speed, wind direction, precipitation, snowfall, atmospheric pressure, humidity, solar radiation, cloud cover.

### Wind Model

The critical factor is the projection of wind direction onto the direction of travel (headwind/tailwind/crosswind). Headwinds on motorways can increase consumption by significantly more than 10%.

---

## Departure Time and Time Planning

The user specifies a **departure time**. All time-dependent variables (weather, charging-station opening hours where relevant) are calculated relative to the expected passage time at each segment.

Because the expected passage time itself depends on energy consumption and the charging plan (which in turn depend on weather), time estimation is performed **iteratively**: an initial rough ETA estimate is used for the first weather query; energy consumption and the charging plan are then calculated; the ETA for each segment is updated; and, where the deviation is significant, the weather query is refined (see `02-architecture.md`, section "Iterative Time/Weather Resolution").

## Intermediate Stops

The user can define **one or more mandatory intermediate stops** (waypoints) between the origin and destination that the route must pass in the specified order. An intermediate stop is conceptually independent of a charging stop:

* An intermediate stop may have an optional dwell time (e.g. "30-minute break at location X").
* Intermediate stops are passed as hard constraints to the routing and optimization layers.
* Whether charging takes place at an intermediate stop is determined independently by the charging planner (an intermediate stop is not automatically a charging location, but it may be combined with one if a Tesla Supercharger is available there).

---

## Charging Planning

The central optimization step. The route does not determine the charging plan — the combination of route, battery state, charging curve, weather, elevation profile, departure time, and intermediate stops determines the optimal charging plan.

**Optimization objectives:** minimize charging time, minimize total travel time, minimize safety margins where possible, and maximize robustness.

The system explicitly does **not** charge to 100% by default. Instead, charging windows such as 8→42%, 12→55%, or 15→37% are optimized depending on the overall situation.

### Charging Curves

Model the charging curve based on: battery type, current SoC, battery temperature, outside temperature, degradation, and charger power. The model can later be calibrated using the vehicle's own driving and charging data.

### Charging Infrastructure

**Only Tesla Superchargers** are considered as charging infrastructure. Other providers (OpenChargeMap, Chargemap, etc.) are explicitly outside the data model.

**Required information per location:** charging power (stall type, e.g. V3/V4), number of stalls, connector type, location/coordinates, reliability/status (where available from the data source).

This restriction also simplifies the handling of opening hours (Tesla Superchargers are generally accessible 24/7), making a separate opening-hours logic for charging locations largely unnecessary.

---

## Roadworks

The relevant aspects are not travel time itself, but speed limits, road closures, and diversions.

**Source: DATEX II** — European standard, available for Germany, Denmark, and Sweden (the countries relevant to current routes). A single parser is sufficient because all countries use the same standard.

## Traffic

Live traffic is explicitly **not part of this project** (high cost, proprietary data, limited added value for energy optimization).

## Calibratability of the Consumption Model

The physical consumption model is designed from the outset so that it can **later** be calibrated using real Tesla driving data (e.g. adjusting model parameters for rolling resistance, aerodynamic drag coefficient, and auxiliary-load baseline). This is an architectural requirement for the energy module (clear separation between model structure and parameters), not a separate project phase within the current implementation scope.

---

## Trip Optimization

Optimization takes place after route calculation, using the road network determined by GraphHopper (see `02-architecture.md` for the two-phase model).

**State space:** current position, current SoC, current time (for weather/ETA consistency).

**Cost function:** driving time + charging time + detour + safety margin (+ optional penalty costs for constraint violations).

**Constraints:**

* SoC must not fall below the minimum value
* desired SoC at the destination
* maximum leg length
* mandatory intermediate stops in the specified order (including optional dwell time)
* fixed departure time as the initial condition

### Optimization Libraries

* **Google OR-Tools** — powerful for optimization problems with constraints.
* **NetworkX** — suitable for the initial prototype.
* **rustworkx** — an option for larger graphs later.

The specific library choice is secondary; the value lies in the definition of the state space and cost function.

---

## Data Flow

1. Calculate OSM route (including intermediate stops as mandatory waypoints).
2. Extract elevation profile.
3. Divide the route into segments.
4. Estimate an initial ETA for each segment based on the departure time and a rough speed assumption.
5. Retrieve weather data along the route for the initial ETAs.
6. Incorporate roadworks along the route (speed limits/closures/diversions).
7. Calculate energy consumption for each segment (including weather, elevation profile, and roadwork-related speed limits).
8. Determine the optimal charging plan (Tesla Superchargers only, taking intermediate stops into account).
9. Update the ETA for each segment using actual driving/charging times; if the deviation is significant, refine the weather query (step 5).
10. Simulate the complete trip.
11. Visualize the results.

---

## Modular Structure

Independent modules: routing, elevation model, weather, energy consumption, battery model, charging curves, charging infrastructure (Tesla), roadworks, optimization, visualization. Individual components can be replaced or improved later (see `03-module-specifications.md`).

---

## Current Architectural Decisions

| Component          | Decision                                                                              |
| ------------------ | ------------------------------------------------------------------------------------- |
| Routing            | GraphHopper                                                                           |
| Map data           | OpenStreetMap                                                                         |
| Elevation model    | Copernicus DEM or SRTM                                                                |
| Weather            | Open-Meteo                                                                            |
| Roadworks          | DATEX II                                                                              |
| Charging locations | Tesla Superchargers (exclusively)                                                     |
| Consumption        | Custom physical model, calibratable                                                   |
| Optimization       | Custom state-based search algorithm (A*/Dijkstra) with freely definable cost function |
| Visualization      | Custom map interface (MapLibre GL JS or Leaflet)                                      |

**Design principle:** Existing software is used wherever it reliably solves an already-solved problem (routing, maps, weather, elevation models, roadworks). The actual added value lies exclusively in the custom optimization and simulation logic.
