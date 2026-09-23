/** Contract types for trip planning (request side).
 *
 * Mirrors `TripRequestAPI`/`WaypointAPI`/`VehicleProfileAPI` from
 * `src/tripplanner/trip_input/schemas/request.py`. Coordinates are always
 * `[lat, lon]`, following the project-wide convention (see `AGENTS.md`,
 * "Koordinatenkonvention"). Conversion to MapLibre `[lng, lat]` happens only
 * via `utils/geo-utils.ts::toLngLat()`.
 *
 * DATA MODEL: a trip is a dynamic, ordered list of `Stop`s (at least 2) –
 * deliberately WITHOUT separate start/destination/waypoint types. The first
 * entry is the departure, the last the destination, everything in between a
 * waypoint – determined purely by position in the list, not by a `kind`
 * field. Stops can be freely added/removed/reordered (at least 2 remain).
 *
 * DEPARTURE TIME: there is no global departure. Every stop except the last
 * may have a planned departure time (`leaveAt`). The first stop's value is
 * the trip departure (backend field `departureTime`); if unset, a sensible
 * default (next full hour) is used. For waypoints, `leaveAt` is sent as the
 * earliest desired departure (`plannedDeparture`); the server derives the
 * actual wait from the computed arrival time, which the client doesn't know.
 */

/** A stop on the route. Its role (start/waypoint/destination) follows purely
 *  from its position in the `stops` array, not from this object. */
export interface Stop {
  /** Stable client-side ID (e.g. via crypto.randomUUID()). */
  id: string;
  /** Free-text address, typed by the user or resolved by the geocoder. */
  address: string;
  /** Resolved coordinate [lat, lon], or `null` while unresolved. */
  position: [number, number] | null;
  /** Planned departure time at this stop (ISO, local, no time zone). Only
   *  meaningful for stops before the last one. */
  leaveAt?: string;
  /** Charging power available at this waypoint in kW (e.g. a wall box at the
   *  overnight stop), optional. Only used during a wait forced by `leaveAt` –
   *  without a wait there is no charging. Only meaningful for stops before
   *  the last one. */
  chargingPowerKw?: number;
}

/** Tire type – see `tripplanner.trip_input.models.VehicleProfile.tire_type`. */
export type TireType =
  "standard" | "winter" | "low_rolling_resistance" | "performance";

/** Weather detail level – see `tripplanner.weather.models.WeatherDetailLevel`. */
export type WeatherDetailLevel = "off" | "low" | "medium" | "high";

/** Highway preference level – see `tripplanner.trip_input.models.TripRequest.highway_preference`.
 *  'off' = no preference, 'low'/'medium'/'high' = priority boost *1.1/*1.2/*1.3
 *  for road_class == MOTORWAY (a nudge, not enforced). */
export type HighwayPreferenceLevel = "off" | "low" | "medium" | "high";

/** Vehicle profile payload (`VehicleProfileAPI`). */
export interface VehicleProfileInput {
  massKg: number;
  dragCoefficient: number;
  frontalAreaM2: number;
  rollingResistanceCoefficient: number;
  batteryCapacityKwh: number;
  auxiliaryBaselineKw: number;
  tireType: TireType;
  roofBox: boolean;
}

/** Named, editable vehicle profile preset for the form picker. */
export interface VehicleProfilePreset {
  id: string;
  label: string;
  profile: VehicleProfileInput;
}

/** Waypoint payload for `POST /trips` (`WaypointAPI`). */
export interface WaypointInput {
  coordinate: [number, number];
  /** Fixed minimum stay in seconds. No longer set by the form (see
   *  `plannedDeparture`), but still part of the contract because the backend
   *  field exists and is optional. */
  stayDurationS: number | null;
  /** Desired departure time at this stop (ISO, local), or `null`. The server
   *  enforces a minimum wait until then. */
  plannedDeparture: string | null;
  /** Charging power available at this waypoint in kW, or `null`. Only used
   *  during a wait forced by `plannedDeparture`. */
  chargingPowerKw: number | null;
}

/** A (buffered) bounding box around a detected ferry connection, to avoid it
 *  in a subsequent route computation (`FerryExclusionAPI`). */
export interface FerryExclusion {
  name: string;
  bboxSw: [number, number];
  bboxNe: [number, number];
}

/** A user-specified departure/arrival time for a previously detected ferry
 *  connection (`FerryTimeWindowAPI`), to match the actual timetable. */
export interface FerryTimeWindow {
  name: string;
  bboxSw: [number, number];
  bboxNe: [number, number];
  /** ISO-8601, local (no time zone). */
  departure: string;
  /** ISO-8601, local (no time zone). */
  arrival: string;
}

/** A user-specified fixed charging duration at a specific charging station
 *  (`ChargingDurationSpecificationAPI`), identified by its stable station ID. */
export interface ChargingDurationSpecification {
  stationId: string;
  chargingDurationS: number;
}

/** Full request body for `POST /trips` (`TripRequestAPI`). */
export interface TripRequestPayload {
  start: [number, number];
  destination: [number, number];
  waypoints: WaypointInput[];
  /** ISO-8601, e.g. "2026-08-15T08:30:00". */
  departureTime: string;
  vehicleProfile: VehicleProfileInput;
  preferences: Record<string, unknown>;
  startSocPct: number;
  targetSocPct: number;
  minChargingTimeS: number;
  minArrivalSocPct: number;
  maxChargeSocPct: number;
  avoidAllFerries: boolean;
  /** Highway preference: 'off' (none), 'low'/'medium'/'high' (a nudge, not
   *  enforced; "Autobahn" toggle). */
  highwayPreference: HighwayPreferenceLevel;
  avoidedFerries: FerryExclusion[];
  ferryTimeWindows: FerryTimeWindow[];
  chargingDurationSpecifications: ChargingDurationSpecification[];
  /** Spatial/temporal resolution of the weather lookup (see the
   *  "Routendetails" control in `TripPlannerForm`). `"off"` skips weather. */
  weatherDetailLevel: WeatherDetailLevel;
  /** If false, the construction-site provider is skipped to speed up
   *  planning ("Baustellen" toggle). */
  considerConstructionSites: boolean;
}

/** Re-exports payload-builder and validation logic. */
export {
  TripRequestBuildError,
  buildTripRequestPayload,
  validateStops,
  createEmptyStop,
} from "./trip-request-builder";
