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
  /** Ladestand in Prozent */
  soc_pct: number;
  /** Zustand des Fahrzeugs */
  zustand: TripState;
  /** Geschwindigkeit in km/h */
  geschwindigkeit_kmh: number;
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
  /** Start-SoC in % */
  start_soc_pct: number;
  /** Ziel-SoC in % */
  ziel_soc_pct: number;
  /** Ladehalte (ein Eintrag pro tatsächlichem Halt, nicht pro Frame) */
  charging_stops: ChargingStop[];
  /** In der berechneten Route erkannte Fährverbindungen */
  erkannte_faehren: FaehrSegment[];
}

/** Ein Ladehalt (ChargingStop) aus dem Simulationsergebnis (`/trips`-Response). */
export interface ChargingStop {
  /** Name der Ladestation */
  name: string;
  /** Eindeutige ID der Ladestation (fuer Ladedauer-Vorgaben) */
  station_id: string;
  /** Position der Ladestation als [lat, lon] */
  position: [number, number];
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
