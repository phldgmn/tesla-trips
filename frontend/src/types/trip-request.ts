/** Vertragstypen für die Reiseplanung (Request-Seite).
 *
 * Spiegelt `TripRequestAPI`/`WaypointAPI`/`VehicleProfile` aus
 * `src/tripplanner/trip_input/api.py` bzw. `models.py`. Koordinaten sind
 * überall `[lat, lon]`, konsistent mit der projektweiten Konvention
 * (siehe `AGENTS.md`, Abschnitt "Koordinatenkonvention"). Die Umwandlung
 * nach MapLibre-`[lng, lat]` erfolgt ausschließlich über
 * `utils/geo-utils.ts::toLngLat()`.
 *
 * DATENMODELL: Die Reise besteht aus einer dynamischen, geordneten Liste
 * von `Stop`s (mindestens 2) – bewusst OHNE separate "Start"/"Ziel"/
 * "Zwischenstopp"-Typen. Der erste Eintrag ist die Abfahrt, der letzte das
 * Ziel, alles dazwischen ein Zwischenstopp – rein durch Position in der
 * Liste bestimmt, nicht durch ein `kind`-Feld. Stopps lassen sich frei
 * hinzufügen/entfernen/umsortieren (min. 2 müssen erhalten bleiben).
 *
 * ABFAHRTSZEIT: Es gibt keine globale "Abfahrt" mehr. Jeder Stopp außer dem
 * letzten kann optional einen geplanten Abfahrtszeitpunkt (`leaveAt`)
 * haben. Der erste Stopp übernimmt diese Rolle für die Reise insgesamt
 * (Backend-Feld `abfahrtszeit`); ist er nicht gesetzt, wird ein sinnvoller
 * Default (nächste volle Stunde) verwendet. Für Zwischenstopps wird
 * `leaveAt` als gewünschter frühester Abfahrtszeitpunkt an das Backend
 * durchgereicht (`geplante_abfahrt`), das daraus serverseitig die
 * tatsächlich nötige Wartezeit ableitet (abhängig von der berechneten
 * Ankunftszeit, die clientseitig nicht bekannt ist).
 */

/** Ein Stopp der Route. Rolle (Start/Zwischenstopp/Ziel) ergibt sich rein
 *  aus der Position im `stops`-Array, nicht aus diesem Objekt. */
export interface Stop {
  /** Stabile client-seitige ID (z. B. via crypto.randomUUID()). */
  id: string;
  /** Freitext-Adresse – vom Nutzer eingetippt oder vom Geocoder aufgelöst. */
  address: string;
  /** Aufgelöste Koordinate [lat, lon], oder `null` solange nicht aufgelöst. */
  position: [number, number] | null;
  /** Geplanter Abfahrtszeitpunkt an diesem Stopp (ISO, lokal, ohne
   *  Zeitzone). Nur für Stopps vor dem letzten sinnvoll. */
  leaveAt?: string;
  /** Vor Ort verfügbare Ladeleistung an diesem Zwischenstopp in kW (z. B.
   *  eine Wallbox am Übernachtungsziel), optional. Wird nur während einer
   *  durch `leaveAt` erzwungenen Wartezeit genutzt – ohne Wartezeit findet
   *  kein Ladevorgang statt. Nur für Stopps vor dem letzten sinnvoll. */
  chargingPowerKw?: number;
}

/** Reifentyp – siehe `tripplanner.trip_input.models.VehicleProfile.reifentyp`. */
export type TireType =
  "standard" | "winter" | "low_rolling_resistance" | "performance";

/** Wetter-Detailgrad – siehe `tripplanner.weather.models.WeatherDetailLevel`. */
export type WeatherDetailLevel = "off" | "low" | "medium" | "high";

/** Autobahnpräferenz-Stufe – siehe `tripplanner.trip_input.models.TripRequest.autobahn_praeferenz`.
 *  'off' = keine Präferenz, 'low'/'medium'/'high' = priority-Boost *1.1/*1.2/*1.3
 *  für road_class == MOTORWAY (Nudge, keine Erzwingung). */
export type HighwayPreferenceLevel = "off" | "low" | "medium" | "high";

/** Fahrzeugprofil-Payload (`VehicleProfile`). */
export interface VehicleProfileInput {
  masse_kg: number;
  cw_wert: number;
  stirnflaeche_m2: number;
  rollwiderstandsbeiwert: number;
  batteriekapazitaet_kwh: number;
  nebenverbraucher_baseline_kw: number;
  reifentyp: TireType;
  dachbox: boolean;
}

/** Named, editierbares Fahrzeugprofil-Preset für die Auswahl im Formular. */
export interface VehicleProfilePreset {
  id: string;
  label: string;
  profile: VehicleProfileInput;
}

/** Wegpunkt-Payload für `POST /trips` (`WaypointAPI`). */
export interface WaypointInput {
  koordinate: [number, number];
  /** Fixe Mindestaufenthaltsdauer in Sekunden. Wird vom Formular nicht mehr
   *  gesetzt (siehe `geplante_abfahrt`) – bleibt Teil des Vertrags, weil das
   *  Backend-Feld weiterhin existiert und optional befüllbar ist. */
  aufenthaltsdauer_s: number | null;
  /** Gewünschter Abfahrtszeitpunkt an diesem Stopp (ISO, lokal), oder
   *  `null`. Erzwingt serverseitig eine Mindestwartezeit bis dahin. */
  geplante_abfahrt: string | null;
  /** Vor Ort verfügbare Ladeleistung an diesem Zwischenstopp in kW, oder
   *  `null`. Wird nur während einer durch `geplante_abfahrt` erzwungenen
   *  Wartezeit genutzt. */
  ladeleistung_kw: number | null;
}

/** Eine (gepufferte) Bounding Box um eine erkannte Fährverbindung, zur Vermeidung
 *  in einer nachfolgenden Routenberechnung (`FaehrAusschlussAPI`). */
export interface FerryExclusion {
  name: string;
  bbox_sw: [number, number];
  bbox_ne: [number, number];
}

/** Eine vom Nutzer vorgegebene Abfahrts-/Ankunftszeit für eine zuvor erkannte
 *  Fährverbindung (`FaehrZeitfensterAPI`), zur Abstimmung mit dem tatsächlichen
 *  Fährfahrplan. */
export interface FerryTimeWindow {
  name: string;
  bbox_sw: [number, number];
  bbox_ne: [number, number];
  /** ISO-8601, lokal (ohne Zeitzone). */
  abfahrt: string;
  /** ISO-8601, lokal (ohne Zeitzone). */
  ankunft: string;
}

/** Eine vom Nutzer vorgegebene feste Ladedauer für eine bestimmte Ladestation
 *  (`LadedauerVorgabeAPI`), identifiziert über die stabile `station_id`. */
export interface ChargingDurationTarget {
  station_id: string;
  charging_duration_s: number;
}

/** Vollständiger Request-Body für `POST /trips` (`TripRequestAPI`). */
export interface TripRequestPayload {
  start: [number, number];
  destination: [number, number];
  waypoints: WaypointInput[];
  /** ISO-8601, z. B. "2026-08-15T08:30:00". */
  departureTime: string;
  vehicleProfile: VehicleProfileInput;
  preferences: Record<string, unknown>;
  start_soc_pct: number;
  target_soc_pct: number;
  min_charging_time_s: number;
  min_arrival_soc_pct: number;
  max_charge_soc_pct: number;
  avoid_all_ferries: boolean;
  /** Grad der Autobahnpräferenz bei der Berechnung: 'off' (keine Präferenz),
   *  'low'/'medium'/'high' (Nudge, keine Erzwingung; Toggle "Autobahn"). */
  highway_preference: HighwayPreferenceLevel;
  avoided_ferries: FerryExclusion[];
  ferry_time_windows: FerryTimeWindow[];
  charging_duration_specifications: ChargingDurationTarget[];
  /** Steuert die räumliche/zeitliche Auflösung der Wetterabfrage
   *  (siehe `TripPlannerForm`-Kontrolle "Routendetails"). `"off"` entspricht
   *  dem alten `wetter_beruecksichtigen: false`, `"high"` dem alten `true`. */
  weather_detail_level: WeatherDetailLevel;
  /** Falls false, wird der Baustellen-Provider für diese Berechnung
   *  übersprungen, um sie zu beschleunigen (Toggle "Baustellen"). */
  consider_construction_sites: boolean;
}

/** Re-exports payload-builder and validation logic. */
export {
  TripRequestBuildError,
  buildTripRequestPayload,
  validateStops,
  createEmptyStop,
} from "./trip-request-builder";
