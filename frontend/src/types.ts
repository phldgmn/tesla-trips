/** TypeScript-Interfaces für das Visualization-Frontend.
 *
 * Manuell geschrieben, passend zu den Python-Pydantic-Modellen
 * in tripplanner.simulation.models und tripplanner.optimization.models.
 *
 * WICHTIG: Koordinaten im Backend sind (lat, lon), für MapLibre müssen
 * sie in [lng, lat] konvertiert werden (siehe geo-utils.ts::toLngLat()).
 */

/** Zustand des Fahrzeugs zu einem Zeitpunkt in der Simulation. */
export type TripState = "FAHREN" | "LADEN" | "PAUSE";

/** Ein einzelner Zeitpunkt in der Reisesimulation. */
export interface SimulationFrame {
  /** Zeitpunkt als ISO-8601 String */
  timestamp: string;
  /** Position als [lat, lon] Tuple in WGS84 */
  position: [number, number];
  /** Kumulierte Distanz vom Reisebeginn entlang der Route in Metern */
  distanceM: number;
  /** Ladestand in Prozent */
  socPct: number;
  /** Zustand des Fahrzeugs */
  state: TripState;
  /** Geschwindigkeit in km/h */
  speedKmh: number;
  /** Fuer diesen Streckenpunkt angenommene Temperatur in Grad Celsius,
   *  `null` wenn Wetter bei der Berechnung nicht beruecksichtigt wurde
   *  (siehe `WeatherDetailLevel` "off"). */
  temperatureC: number | null;
  /** Fuer diesen Streckenpunkt angenommene Windgeschwindigkeit in m/s,
   *  `null` wie `temperatur_c`. */
  windSpeedMs: number | null;
  /** Fuer diesen Streckenpunkt angenommene Windrichtung in Grad
   *  (0° = N, 90° = O), `null` wie `temperatur_c`. */
  windDirectionDeg: number | null;
  /** Fuer diesen Streckenpunkt angenommener Niederschlag in mm/h,
   *  `null` wie `temperatur_c`. */
  precipitationMm: number | null;
}

/** Vollständige Zeitreihe einer Reise. */
export interface TripSimulationResult {
  /** Liste der Simulationsframes */
  frames: SimulationFrame[];
  /** Gesamtdistanz in km */
  totalDistanceKm: number;
  /** Gesamtfahrzeit in Minuten */
  totalDrivingTimeMin: number;
  /** Gesamtladezeit in Minuten */
  totalChargingTimeMin: number;
  /** Erzwungene Wartezeit an Zwischenstopps OHNE Ladung, in Minuten (nicht in
   *  gesamt_fahrzeit_min/gesamt_ladezeit_min enthalten) */
  totalWaitingTimeMin: number;
  /** Start-SoC in % */
  startSocPct: number;
  /** Ziel-SoC in % */
  targetSocPct: number;
  /** Ladehalte (ein Eintrag pro tatsächlichem Halt, nicht pro Frame) */
  chargingStops: ChargingStop[];
  /** Zwischenstopp-Aufenthalte (ein Eintrag pro Aufenthalt, nicht pro Frame) */
  waypointStops: WaypointStop[];
  /** Vollstaendige Streckengeometrie der Route als Liste von [lat, lon]-Punkten
   *  (dichte GraphHopper-Polyline, nicht auf `frames` reduziert - fuer eine
   *  winkeltreue Kartendarstellung, siehe route-line.ts::buildSplicedRoute()) */
  routeGeometry: [number, number][];
  /** In der berechneten Route erkannte Fährverbindungen */
  detectedFerries: FerrySegment[];
  /** Sum of estimated charging costs across all charging stops, grouped by
   *  currency (empty if no stop has cached pricing data; multiple entries if
   *  stops span several currencies, e.g. a DE-DK-SE trip) */
  totalChargingCost: ChargingCostByCurrency[];
  /** Number of charging stops excluded from `total_charging_cost` because no
   *  pricing data is cached yet for their station */
  chargingStopsMissingPricing: number;
  /** Baustellen/Sperrungen entlang der Route fuer die Kartendarstellung (leer, falls keine) */
  constructionZones: ConstructionZone[];
}

/** Ein einzelnes Baustellen-Ereignis, das zu einer ConstructionZone gemergt wurde. */
export interface ConstructionZoneEvent {
  /** Art der Sperrung: "fullyClosed" | "partiallyClosed" | "laneClosed" |
   *  "temporarySpeedLimit" | "reducedLanes" | "detrourRequired" */
  closureType: string;
  /** Reduziertes Tempolimit in km/h (null wenn keine Beschraenkung) */
  speedLimitKmh: number | null;
  /** Freitext-Information zur Umleitung (optional) */
  detourNotice: string | null;
  /** Land, in dem die Baustelle liegt: "DE" | "DK" | "SE" */
  country: string;
  /** ISO-8601 Startzeitpunkt der Baustelle */
  validFrom: string;
  /** ISO-8601 Endzeitpunkt der Baustelle (null wenn unbestimmt) */
  validTo: string | null;
}

/** Eine Baustelle/Sperrung entlang der Route (`ConstructionZoneAPI`).
 *  Kann mehrere naeheinanderliegende Ereignisse zusammenfassen. */
export interface ConstructionZone {
  /** Repraesentative Position der Baustelle als [lat, lon] */
  position: [number, number];
  /** Liste aller Baustellen-Ereignisse, die zu dieser Marker-Position gemergt wurden */
  events: ConstructionZoneEvent[];
  /** Laenge der Baustelle in Metern (null wenn nicht bekannt) */
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

/** Ein Ladehalt (ChargingStop) aus dem Simulationsergebnis (`/trips`-Response). */
export interface ChargingStop {
  /** Name der Ladestation */
  name: string;
  /** Eindeutige ID der Ladestation (fuer Ladedauer-Vorgaben) */
  stationId: string;
  /** Position der Ladestation als [lat, lon] */
  position: [number, number];
  /** Kumulierte Distanz entlang der Route, an der zur Ladestation abgebogen wird */
  distanceM: number;
  /** Echte, ueber GraphHopper geroutete Geometrie von der Route zur Ladestation
   *  und zurueck als Liste von [lat, lon]-Punkten (leer, falls nicht ermittelbar
   *  - siehe route-line.ts::buildSplicedRoute()) */
  detourGeometry: [number, number][];
  /** Index in `route_geometrie`, ab dem `detour_geometrie` die Hauptroute ersetzt
   *  (null, falls `detour_geometrie` leer ist) */
  routeIndexBefore: number | null;
  /** Index in `route_geometrie`, bis zu dem (inklusive) `detour_geometrie` die
   *  Hauptroute ersetzt (null, falls `detour_geometrie` leer ist) */
  routeIndexAfter: number | null;
  /** Index in `detour_geometrie`, an dem die Ladestation tatsaechlich erreicht
   *  wird (null, falls `detour_geometrie` leer ist) */
  detourStationIndex: number | null;
  /** Ankunfts-SoC in % */
  arrivalSocPct: number;
  /** Ziel-SoC in % */
  targetSocPct: number;
  /** Ladedauer in Sekunden */
  chargingDurationS: number;
  /** Waehrend des Ladehalts geladene Energiemenge in kWh */
  energyChargedKwh: number;
  /** ISO-8601 Ankunftszeitpunkt an der Station */
  arrivalTime: string;
  /** ISO-8601 Abfahrtszeitpunkt von der Station */
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

/** Ein Zwischenstopp-Aufenthalt (WaypointStop) aus dem Simulationsergebnis
 *  (`/trips`-Response) - erzwungene Wartezeit an einem Zwischenstopp, optional
 *  mit Ladung ueber eine vor Ort verfuegbare Ladeleistung. */
export interface WaypointStop {
  /** Position des Zwischenstopps als [lat, lon] */
  position: [number, number];
  /** Kumulierte Distanz entlang der Route bei diesem Zwischenstopp */
  distanceM: number;
  /** ISO-8601 Ankunftszeitpunkt am Zwischenstopp */
  arrivalTime: string;
  /** ISO-8601 Zeitpunkt der (erzwungenen) Abfahrt */
  departureTime: string;
  /** Genutzte Ladeleistung in kW, null falls nicht geladen wurde */
  ladeleistungKw: number | null;
  /** SoC bei Ankunft in % */
  arrivalSocPct: number;
  /** SoC bei Abfahrt in % */
  targetSocPct: number;
  /** Waehrend des Aufenthalts geladene Energiemenge in kWh */
  energyChargedKwh: number;
}

/** Ein Zwischenstopp (Waypoint) mit optionaler Aufenthaltsdauer. */
export interface Waypoint {
  /** Position des Zwischenstopps als [lat, lon] */
  position: [number, number];
  /** Aufenthaltsdauer in Sekunden (null = kein Zwischenstopp) */
  dwell_time_s: number | null;
}

/** Eine in der berechneten Route erkannte Fährverbindung (`FaehrSegmentAPI`). */
export interface FerrySegment {
  name: string;
  lengthM: number;
  bboxSw: [number, number];
  bboxNe: [number, number];
  /** Vom Nutzer vorgegebene Abfahrtszeit (ISO-8601), oder null falls ungeplant. */
  abfahrt: string | null;
  /** Vom Nutzer vorgegebene Ankunftszeit (ISO-8601), oder null falls ungeplant. */
  ankunft: string | null;
}
