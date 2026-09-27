/** TypeScript interfaces for the frontend visualization.
 *
 * Hand-written to match the Python Pydantic models
 * in tripplanner.simulation.models and tripplanner.optimization.models.
 *
 * IMPORTANT: Coordinates in the backend are (lat, lon), MapLibre requires
 * them converted to [lng, lat] (see geo-utils.ts::toLngLat()).
 */

/** Vehicle state at a point in the simulation. */
export type TripState = "FAHREN" | "LADEN" | "PAUSE";

/** A single timepoint in the trip simulation. */
export interface SimulationFrame {
  /** Timepoint as ISO-8601 string */
  timestamp: string;
  /** Position as [lat, lon] tuple in WGS84 */
  position: [number, number];
  /** Cumulative distance from trip start along the route in meters */
  distanceM: number;
  /** Charge level in percent */
  socPct: number;
  /** Vehicle state */
  state: TripState;
  /** Speed in km/h */
  speedKmh: number;
  /** Assumed temperature in degrees Celsius for this route point,
   *  `null` if weather was not considered in the calculation
   *  (see `WeatherDetailLevel` "off"). */
  temperatureC: number | null;
  /** Assumed wind speed in m/s for this route point,
   *  `null` like `temperatureC`. */
  windSpeedMs: number | null;
  /** Assumed wind direction in degrees
   *  (0° = N, 90° = E), `null` like `temperatureC`. */
  windDirectionDeg: number | null;
  /** Assumed precipitation in mm/h for this route point,
   *  `null` like `temperatureC`. */
  precipitationMm: number | null;
}

/** Complete trip time series. */
export interface TripSimulationResult {
  /** List of simulation frames */
  frames: SimulationFrame[];
  /** Total distance in km */
  totalDistanceKm: number;
  /** Total driving time in minutes */
  totalDrivingTimeMin: number;
  /** Total charging time in minutes */
  totalChargingTimeMin: number;
  /** Forced wait time at waypoints without charging, in minutes (not in
   *  total_driving_time_min/total_charging_time_min) */
  totalWaitingTimeMin: number;
  /** Starting SoC in % */
  startSocPct: number;
  /** Target SoC in % */
  targetSocPct: number;
  /** Charging stops (one entry per actual stop, not per frame) */
  chargingStops: ChargingStop[];
  /** Waypoint stay durations (one entry per stay, not per frame) */
  waypointStops: WaypointStop[];
  /** Full route geometry as a list of [lat, lon] points
   *  (dense GraphHopper polyline, not reduced to `frames` - for a
   *  angle-accurate map display, see route-line.ts::buildSplicedRoute()) */
  routeGeometry: [number, number][];
  /** Ferry connections detected in the computed route */
  detectedFerries: FerrySegment[];
  /** Sum of estimated charging costs across all charging stops, grouped by
   *  currency (empty if no stop has cached pricing data; multiple entries if
   *  stops span several currencies, e.g. a DE-DK-SE trip) */
  totalChargingCost: ChargingCostByCurrency[];
  /** Number of charging stops excluded from `total_charging_cost` because no
   *  pricing data is cached yet for their station */
  chargingStopsMissingPricing: number;
  /** Construction zones/road closures along the route for map display (empty if none) */
  constructionZones: ConstructionZone[];
}

/** A single construction zone event merged into a ConstructionZone. */
export interface ConstructionZoneEvent {
  /** Type of closure: "fullyClosed" | "partiallyClosed" | "laneClosed" |
   *  "temporarySpeedLimit" | "reducedLanes" | "detrourRequired" */
  closureType: string;
  /** Reduced speed limit in km/h (null if no restriction) */
  speedLimitKmh: number | null;
  /** Free-text detour information (optional) */
  detourNotice: string | null;
  /** Country where the construction zone is located: "DE" | "DK" | "SE" */
  country: string;
  /** ISO-8601 start time of the construction zone */
  validFrom: string;
  /** ISO-8601 end time of the construction zone (null if indefinite) */
  validTo: string | null;
}

/** A construction zone/road closure along the route (`ConstructionZoneAPI`).
 *  Can merge multiple adjacent events. */
export interface ConstructionZone {
  /** Representative position of the construction zone as [lat, lon] */
  position: [number, number];
  /** List of all construction zone events merged into this marker position */
  events: ConstructionZoneEvent[];
  /** Length of the construction zone in meters (null if unknown) */
  lengthM: number | null;
}

/** Aggregated estimated charging cost in a single currency
 *  (`TripSimulationResult.total_charging_cost`). */
export interface ChargingCostByCurrency {
  /** ISO-4217 currency code */
  currency: string;
  /** Summed cost in `currency` */
  amount: number;
}

/** A charging stop (ChargingStop) from the simulation result (`/trips` response). */
export interface ChargingStop {
  /** Name of the charging station */
  name: string;
  /** Unique ID of the charging station (for charging duration presets) */
  stationId: string;
  /** Position of the charging station as [lat, lon] */
  position: [number, number];
  /** Cumulative distance along the route where the turn to the charging station occurs */
  distanceM: number;
  /** Real, routed geometry via GraphHopper from the route to the charging station
   *  and back as a list of [lat, lon] points (empty if not determinable
   *  - see route-line.ts::buildSplicedRoute()) */
  detourGeometry: [number, number][];
  /** Index in `route_geometrie` from which `detour_geometrie` replaces the main route
   *  (null, if `detour_geometrie` is empty) */
  routeIndexBefore: number | null;
  /** Index in `route_geometrie` up to which (inclusive) `detour_geometrie` replaces the
   *  main route (null, if `detour_geometrie` is empty) */
  routeIndexAfter: number | null;
  /** Index in `detour_geometrie` where the charging station is actually reached
   *  (null, if `detour_geometrie` is empty) */
  detourStationIndex: number | null;
  /** Arrival SoC in % */
  arrivalSocPct: number;
  /** Target SoC in % */
  targetSocPct: number;
  /** Charging duration in seconds */
  chargingDurationS: number;
  /** Energy amount charged in kWh during the charging stop */
  energyChargedKwh: number;
  /** ISO-8601 arrival time at the station */
  arrivalTime: string;
  /** ISO-8601 departure time from the station */
  departureTime: string;
  /** Applicable Tesla-owner rate per kWh at arrival time, from cached pricing
   *  data (null if no pricing data is cached yet for this station) */
  pricePerKwh: number | null;
  /** ISO-4217 currency of `price_per_kwh`/`estimated_cost` (null iff those are null) */
  currency: string | null;
  /** Estimated cost of this charging stop (`energie_geladen_kwh * price_per_kwh`),
   *  null if no pricing data is cached yet for this station */
  estimatedCost: number | null;
  /** ISO-8601 timestamp of the cached pricing data used for `price_per_kwh`,
   *  null if no pricing data has ever been scraped for this station */
  pricingUpdatedUtc: string | null;
}

/** A waypoint stay (WaypointStop) from the simulation result
 *  (`/trips` response) - forced wait time at a waypoint, optionally
 *  with charging via available on-site charging power. */
export interface WaypointStop {
  /** Position of the waypoint stop as [lat, lon] */
  position: [number, number];
  /** Cumulative distance along the route at this waypoint stop */
  distanceM: number;
  /** ISO-8601 arrival time at the waypoint stop */
  arrivalTime: string;
  /** ISO-8601 time of the (forced) departure */
  departureTime: string;
  /** Charging power used in kW, null if no charging occurred */
  chargingPowerKw: number | null;
  /** SoC at arrival in % */
  arrivalSocPct: number;
  /** SoC at departure in % */
  targetSocPct: number;
  /** Energy amount charged in kWh during the stay */
  energyChargedKwh: number;
}

/** A waypoint with optional stay duration. */
export interface Waypoint {
  /** Position of the waypoint as [lat, lon] */
  position: [number, number];
  /** Stay duration in seconds (null = no waypoint) */
  dwell_time_s: number | null;
}

/** A ferry connection detected in the computed route (`FerrySegmentAPI`). */
export interface FerrySegment {
  name: string;
  lengthM: number;
  bboxSw: [number, number];
  bboxNe: [number, number];
  /** Departure time specified by the user (ISO-8601), or null if unplanned. */
  departure: string | null;
  /** Arrival time specified by the user (ISO-8601), or null if unplanned. */
  arrival: string | null;
}
