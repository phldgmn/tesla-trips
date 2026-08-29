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
  zeitpunkt: string;
  /** Position als [lat, lon] Tuple in WGS84 */
  position: [number, number];
  /** Kumulierte Distanz vom Reisebeginn entlang der Route in Metern */
  distanz_m: number;
  /** Ladestand in Prozent */
  soc_pct: number;
  /** Zustand des Fahrzeugs */
  zustand: TripState;
  /** Geschwindigkeit in km/h */
  geschwindigkeit_kmh: number;
  /** Fuer diesen Streckenpunkt angenommene Temperatur in Grad Celsius,
   *  `null` wenn Wetter bei der Berechnung nicht beruecksichtigt wurde
   *  (siehe `WeatherDetailLevel` "off"). */
  temperatur_c: number | null;
  /** Fuer diesen Streckenpunkt angenommene Windgeschwindigkeit in m/s,
   *  `null` wie `temperatur_c`. */
  windgeschwindigkeit_ms: number | null;
  /** Fuer diesen Streckenpunkt angenommener Niederschlag in mm/h,
   *  `null` wie `temperatur_c`. */
  niederschlag_mm: number | null;
}

/** Vollständige Zeitreihe einer Reise. */
export interface TripSimulationResult {
  /** Liste der Simulationsframes */
  frames: SimulationFrame[];
  /** Gesamtdistanz in km */
  gesamt_distanz_km: number;
  /** Gesamtfahrzeit in Minuten */
  gesamt_fahrzeit_min: number;
  /** Gesamtladezeit in Minuten */
  gesamt_ladezeit_min: number;
  /** Erzwungene Wartezeit an Zwischenstopps OHNE Ladung, in Minuten (nicht in
   *  gesamt_fahrzeit_min/gesamt_ladezeit_min enthalten) */
  gesamt_wartezeit_min: number;
  /** Start-SoC in % */
  start_soc_pct: number;
  /** Ziel-SoC in % */
  ziel_soc_pct: number;
  /** Ladehalte (ein Eintrag pro tatsächlichem Halt, nicht pro Frame) */
  charging_stops: ChargingStop[];
  /** Zwischenstopp-Aufenthalte (ein Eintrag pro Aufenthalt, nicht pro Frame) */
  waypoint_stops: WaypointStop[];
  /** Vollstaendige Streckengeometrie der Route als Liste von [lat, lon]-Punkten
   *  (dichte GraphHopper-Polyline, nicht auf `frames` reduziert - fuer eine
   *  winkeltreue Kartendarstellung, siehe route-line.ts::buildSplicedRoute()) */
  route_geometrie: [number, number][];
  /** In der berechneten Route erkannte Fährverbindungen */
  erkannte_faehren: FaehrSegment[];
  /** Sum of estimated charging costs across all charging stops, grouped by
   *  currency (empty if no stop has cached pricing data; multiple entries if
   *  stops span several currencies, e.g. a DE-DK-SE trip) */
  total_charging_cost: ChargingCostByCurrency[];
  /** Number of charging stops excluded from `total_charging_cost` because no
   *  pricing data is cached yet for their station */
  charging_stops_missing_pricing: number;
  /** Baustellen/Sperrungen entlang der Route fuer die Kartendarstellung (leer, falls keine) */
  construction_zones: ConstructionZone[];
}

/** Ein einzelnes Baustellen-Ereignis, das zu einer ConstructionZone gemergt wurde. */
export interface ConstructionZoneEvent {
  /** Art der Sperrung: "fullyClosed" | "partiallyClosed" | "laneClosed" |
   *  "temporarySpeedLimit" | "reducedLanes" | "detrourRequired" */
  sperrungstyp: string;
  /** Reduziertes Tempolimit in km/h (null wenn keine Beschraenkung) */
  tempolimit_kmh: number | null;
  /** Freitext-Information zur Umleitung (optional) */
  umleitungshinweis: string | null;
  /** Land, in dem die Baustelle liegt: "DE" | "DK" | "SE" */
  land: string;
  /** ISO-8601 Startzeitpunkt der Baustelle */
  gueltig_von: string;
  /** ISO-8601 Endzeitpunkt der Baustelle (null wenn unbestimmt) */
  gueltig_bis: string | null;
}

/** Eine Baustelle/Sperrung entlang der Route (`ConstructionZoneAPI`).
 *  Kann mehrere naeheinanderliegende Ereignisse zusammenfassen. */
export interface ConstructionZone {
  /** Repraesentative Position der Baustelle als [lat, lon] */
  position: [number, number];
  /** Liste aller Baustellen-Ereignisse, die zu dieser Marker-Position gemergt wurden */
  events: ConstructionZoneEvent[];
  /** Laenge der Baustelle in Metern (null wenn nicht bekannt) */
  laenge_m: number | null;
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
  station_id: string;
  /** Position der Ladestation als [lat, lon] */
  position: [number, number];
  /** Kumulierte Distanz entlang der Route, an der zur Ladestation abgebogen wird */
  distanz_m: number;
  /** Echte, ueber GraphHopper geroutete Geometrie von der Route zur Ladestation
   *  und zurueck als Liste von [lat, lon]-Punkten (leer, falls nicht ermittelbar
   *  - siehe route-line.ts::buildSplicedRoute()) */
  detour_geometrie: [number, number][];
  /** Index in `route_geometrie`, ab dem `detour_geometrie` die Hauptroute ersetzt
   *  (null, falls `detour_geometrie` leer ist) */
  route_index_vor: number | null;
  /** Index in `route_geometrie`, bis zu dem (inklusive) `detour_geometrie` die
   *  Hauptroute ersetzt (null, falls `detour_geometrie` leer ist) */
  route_index_nach: number | null;
  /** Index in `detour_geometrie`, an dem die Ladestation tatsaechlich erreicht
   *  wird (null, falls `detour_geometrie` leer ist) */
  detour_station_index: number | null;
  /** Ankunfts-SoC in % */
  ankunfts_soc_pct: number;
  /** Ziel-SoC in % */
  ziel_soc_pct: number;
  /** Ladedauer in Sekunden */
  ladedauer_s: number;
  /** Waehrend des Ladehalts geladene Energiemenge in kWh */
  energie_geladen_kwh: number;
  /** ISO-8601 Ankunftszeitpunkt an der Station */
  ankunftszeit: string;
  /** ISO-8601 Abfahrtszeitpunkt von der Station */
  abfahrtszeit: string;
  /** Applicable Tesla-owner rate per kWh at arrival time, from cached pricing
   *  data (null if no pricing data is cached yet for this station) */
  price_per_kwh: number | null;
  /** ISO-4217 currency of `price_per_kwh`/`estimated_cost` (null iff those are null) */
  currency: string | null;
  /** Estimated cost of this charging stop (`energie_geladen_kwh * price_per_kwh`),
   *  null if no pricing data is cached yet for this station */
  estimated_cost: number | null;
  /** ISO-8601 timestamp of the cached pricing data used for `price_per_kwh`,
   *  null if no pricing data has ever been scraped for this station */
  pricing_updated_utc: string | null;
}

/** Ein Zwischenstopp-Aufenthalt (WaypointStop) aus dem Simulationsergebnis
 *  (`/trips`-Response) - erzwungene Wartezeit an einem Zwischenstopp, optional
 *  mit Ladung ueber eine vor Ort verfuegbare Ladeleistung. */
export interface WaypointStop {
  /** Position des Zwischenstopps als [lat, lon] */
  position: [number, number];
  /** Kumulierte Distanz entlang der Route bei diesem Zwischenstopp */
  distanz_m: number;
  /** ISO-8601 Ankunftszeitpunkt am Zwischenstopp */
  ankunftszeit: string;
  /** ISO-8601 Zeitpunkt der (erzwungenen) Abfahrt */
  abfahrtszeit: string;
  /** Genutzte Ladeleistung in kW, null falls nicht geladen wurde */
  ladeleistung_kw: number | null;
  /** SoC bei Ankunft in % */
  ankunfts_soc_pct: number;
  /** SoC bei Abfahrt in % */
  ziel_soc_pct: number;
  /** Waehrend des Aufenthalts geladene Energiemenge in kWh */
  energie_geladen_kwh: number;
}

/** Ein Zwischenstopp (Waypoint) mit optionaler Aufenthaltsdauer. */
export interface Waypoint {
  /** Position des Zwischenstopps als [lat, lon] */
  position: [number, number];
  /** Aufenthaltsdauer in Sekunden (null = kein Zwischenstopp) */
  aufenthaltsdauer_s: number | null;
}

/** Eine in der berechneten Route erkannte Fährverbindung (`FaehrSegmentAPI`). */
export interface FaehrSegment {
  name: string;
  laenge_m: number;
  bbox_sw: [number, number];
  bbox_no: [number, number];
  /** Vom Nutzer vorgegebene Abfahrtszeit (ISO-8601), oder null falls ungeplant. */
  abfahrt: string | null;
  /** Vom Nutzer vorgegebene Ankunftszeit (ISO-8601), oder null falls ungeplant. */
  ankunft: string | null;
}
