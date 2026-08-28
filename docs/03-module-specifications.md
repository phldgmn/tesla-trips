# Module Specifications

Each module is a self-contained Python package under `src/tripplanner/<module_name>/` with its own test suite under `tests/<module_name>/`. Data structures are defined as Pydantic models in `src/tripplanner/<module_name>/models.py` and are consumed by other modules exclusively through those models.

---

## 1. `routing`

**Purpose:** Calculate road routes between the origin, intermediate stops, and destination.

**Inputs:** Origin coordinate, destination coordinate, ordered list of intermediate-stop coordinates, vehicle profile.

**Outputs:** `Route` (list of `RouteSegment`s: geometry, length, road class, speed limit, raw elevation data if provided by GraphHopper). Ferry connections are detected using `routing.faehren.erkenne_faehren()`; they can be avoided globally (`TripRequest.alle_faehren_vermeiden`) or selectively for individual, previously detected ferries (`vermiedene_faehren`) — both paths operate through the GraphHopper `custom_model`.

**Dependencies:** GraphHopper HTTP client (`graphhopper_client.py`). No direct access to raw OSM data outside this module.

**Testability:** GraphHopper responses are stored as test fixtures (recorded JSON responses); no live requests in unit tests. An integration test against a local GraphHopper instance is marked separately (`@pytest.mark.integration`).

---

## 2. `elevation`

**Purpose:** Provide an elevation profile for a given route.

**Inputs:** List of coordinates (from `routing` segments).

**Outputs:** Elevation for each coordinate, with gradient/slope derived for each segment.

**Dependencies:** `CopernicusDEMDataSource` from the public AWS Open Data S3 bucket `copernicus-dem-30m` (Cloud Optimized GeoTIFF, no AWS access key required), accessed via `rasterio` with GDAL `/vsicurl/` for HTTP range requests — no local tile downloads are required; only the tile windows actually needed for a route are loaded.

**Testability:** Small synthetic test tile in the test fixtures; no need to keep actual global DEM data available during test runs.

---

## 3. `weather`

**Purpose:** Provide weather conditions for route points at specified times.

**Inputs:** List of `(coordinate, timestamp)` pairs.

**Outputs:** One `WeatherSample` per point: temperature, wind speed, wind direction, precipitation, snowfall, atmospheric pressure, humidity, solar radiation, cloud cover.

**Dependencies:** Open-Meteo HTTP client.

**Special requirement:** Must support the iterative querying described in `02-architecture.md` — i.e. provide a method for re-querying previously queried points with updated timestamps.

**Testability:** HTTP client is hidden behind an interface (`WeatherProvider`) and replaced with a fake in tests.

---

## 4. `wind`

**Purpose:** Calculate the effective longitudinal and lateral wind components from wind speed/direction and the direction of travel.

**Inputs:** `WeatherSample`, segment bearing.

**Outputs:** Headwind/tailwind component (m/s, signed), crosswind component.

**Dependencies:** None — pure calculation logic, and therefore fully unit-testable with simple numerical values.

---

## 5. `construction` (Roadworks)

**Purpose:** Provide active construction zones, road closures, and speed limits along the route.

**Inputs:** Route geometry, country filter (DE, DK, SE).

**Outputs:** List of `ConstructionZone`s: affected road section (`betroffene_segmente`), affected-stretch length (`laenge_m`, derived — see below), speed limit, closure type, diversion information (if available).

**Dependencies:** DATEX II feeds from the respective countries, using a shared parser (one parser for all countries because they use the same standard). DE's default provider is a DATEX II feed too (NRW Mobilitätsdaten Mobilithek exporter, long- and short-duration Autobahn Arbeitsstellen); the earlier Autobahn GmbH open API provider (point coordinates only, no DATEX II) is kept available for revert (`providers_de_autobahn.py`), injectable via `ConstructionProviderImpl(de_provider=...)`.

**Direction-of-travel matching:** A zone/roadwork is only applied to a route segment if it is both spatially close (within 500 m) *and* applicable in the route's direction of travel — not merely on the same road. For zones with LineString geometry (DK/SE, and DE's default NRW DATEX II provider), the zone's own bearing (start→end of its coordinates) is compared against the matched `RouteSegment.bearing_deg`; a match is excluded when the angular difference (folded into `[0°, 180°]`) exceeds 100°, i.e. the zone runs roughly opposite to the route (opposite carriageway on a divided highway). For SE zones, an explicit `AffectedDirectionValue` other than "both directions" is trusted over the geometry heuristic. **Known limitation:** the legacy Autobahn GmbH API provider exposes only a single point coordinate — no direction filtering is possible there; matching stays proximity-only for that provider.

**Length derivation (`laenge_m`):** For zones with LineString geometry (DK/SE, and DE's default NRW DATEX II provider), computed directly as the geodesic length of that geometry (`tripplanner.geo.geodesic_length_m`). For point-only zones (legacy Autobahn GmbH provider), derived from the length of the matched route segment(s) (`RouteSegment.laenge_m`), since no length data exists in that source. `None` when neither is computable.

**Testability:** Example DATEX II XML files as fixtures.

---

## 6. `energy`

**Purpose:** Calculate energy consumption per segment using a physical model.

**Inputs:** Segment (length, gradient, road class/surface, speed limit/roadwork speed limit), `WeatherSample`, wind components, vehicle parameters (mass including payload, cW value, frontal area, rolling-resistance coefficient, auxiliary-load baseline, tire type).

**Outputs:** Energy demand (kWh) per segment, taking regenerative braking into account (only during downhill travel/deceleration and physically plausibly constrained).

**Design requirement:** Model parameters (cW, rolling resistance, etc.) must be passed in as a separate, replaceable configuration/object rather than hard-coded — this is a prerequisite for later calibration using real driving data (no dedicated calibration feature is included in the current scope, but this separation must exist from the beginning).

**Testability:** Purely deterministic calculation, highly suitable for unit testing against known reference values (e.g. "100 km/h, level road, no wind → X kWh/100 km").

---

## 7. `battery` (Charging Curve/Battery State)

**Purpose:** Model SoC progression during driving (discharge) and charging sessions (charging curve).

**Inputs:** Current SoC, energy demand (from `energy`), battery temperature (simplified model, potentially derived from outside temperature), charger power.

**Outputs:** SoC after each driving segment; SoC-over-time curve during a charging session (to determine how long charging is required to reach a given target SoC).

**Testability:** Charging curve implemented as a parameterized function (e.g. piecewise linear or lookup table by SoC range), testable against reference values.

---

## 8. `charging_infrastructure`

**Purpose:** Provide available Tesla Superchargers along or near the route.

**Inputs:** Route geometry, search radius.

**Outputs:** List of `ChargingStation`s: coordinates, number of stalls, maximum charging power, location name.

**Dependencies:** Tesla's own charging-location data (official source; see the research task in `06-open-points-contradictions.md` regarding the specific data source and licensing question).

**Design note:** Access must be encapsulated behind a `ChargingStationProvider` interface so that the data source can be replaced, even if only a single provider is currently implemented.

---

## 9. `optimization`

**Purpose:** Determine the optimal charging plan for the entire route.

**Inputs:** Route with segments, energy demand per segment, available charging stations, intermediate stops (including dwell time), departure time, initial SoC, target SoC, constraints (minimum SoC, etc.).

**Outputs:** `ChargingPlan`: ordered list of charging stops with arrival SoC, target SoC, estimated charging duration, and total travel time.

**Dependencies:** OR-Tools and/or NetworkX behind a shared `Optimizer` interface so that the concrete library can be replaced.

**Testability:** Small synthetic scenarios (few segments, few charging stations) with manually verifiable optimal results as regression tests.

---

## 10. `simulation`

**Purpose:** Generate a complete time/SoC simulation of the trip from the route and charging plan (for visualization and validation).

**Inputs:** Route, `ChargingPlan`, energy and weather data for each segment.

**Outputs:** Time series (timestamp, position, SoC, current speed/state: driving/charging/pause).

---

## 11. `visualization`

**Purpose:** Graphically display the route, charging stops, intermediate stops, and SoC progression.

**Technology:** TypeScript, MapLibre GL JS or Leaflet. Consumes the output of `simulation` through a JSON interface (clear contract, e.g. via JSON Schema generated from the Pydantic models).

---

## 12. `trip_input` / CLI or API Layer

**Purpose:** Accept origin, destination, intermediate stops, departure time, vehicle parameters, and preferences and pass them to the pipeline. As a concrete example, ferry avoidance is implemented through two typed fields (`alle_faehren_vermeiden` / `vermiedene_faehren`), independently of the generic `praeferenzen` dictionary.

**Form (current scope):** CLI and/or simple local API (e.g. FastAPI) as a thin layer over the pipeline described in `02-architecture.md`, with no independent optimization logic.
