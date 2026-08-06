import { getDefaultDepartureIso } from "../utils/datetime-utils";

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
}

/** Erzeugt einen neuen, leeren Stopp. */
export function createEmptyStop(): Stop {
  return { id: crypto.randomUUID(), address: "", position: null };
}

/** Reifentyp – siehe `tripplanner.trip_input.models.VehicleProfile.reifentyp`. */
export type ReifenTyp =
  "standard" | "winter" | "low_rolling_resistance" | "performance";

/** Fahrzeugprofil-Payload (`VehicleProfile`). */
export interface VehicleProfileInput {
  masse_kg: number;
  cw_wert: number;
  stirnflaeche_m2: number;
  rollwiderstandsbeiwert: number;
  batteriekapazitaet_kwh: number;
  nebenverbraucher_baseline_kw: number;
  reifentyp: ReifenTyp;
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
   *  `null`. Das Backend leitet daraus die tatsächliche Wartezeit ab. */
  geplante_abfahrt: string | null;
}

/** Vollständiger Request-Body für `POST /trips` (`TripRequestAPI`). */
export interface TripRequestPayload {
  start: [number, number];
  ziel: [number, number];
  zwischenstopps: WaypointInput[];
  /** ISO-8601, z. B. "2026-08-15T08:30:00". */
  abfahrtszeit: string;
  fahrzeugprofil: VehicleProfileInput;
  praeferenzen: Record<string, unknown>;
  start_soc_pct: number;
  ziel_soc_pct: number;
}

/** Fehler beim Aufbau des Requests aus dem aktuellen Formularzustand
 *  (z. B. fehlende Positionen) – wird vom Formular vor dem Absenden per
 *  `validateStops()` abgefangen; dieser Typ dient als letzte Absicherung. */
export class TripRequestBuildError extends Error {}

/** Baut den vollständigen `TripRequestPayload` aus der Stopp-Liste und den
 *  übrigen Formularwerten. Wirft `TripRequestBuildError`, wenn Start/Ziel
 *  keine aufgelöste Position haben (sollte durch `validateStops()` im UI
 *  bereits verhindert sein). */
export function buildTripRequestPayload(args: {
  stops: Stop[];
  fahrzeugprofil: VehicleProfileInput;
  startSocPct: number;
  zielSocPct: number;
  praeferenzen?: Record<string, unknown>;
}): TripRequestPayload {
  const { stops } = args;
  if (stops.length < 2) {
    throw new TripRequestBuildError(
      "Mindestens zwei Stopps (Start und Ziel) sind erforderlich.",
    );
  }
  const start = stops[0];
  const ziel = stops[stops.length - 1];
  const zwischen = stops.slice(1, -1);

  if (!start.position) {
    throw new TripRequestBuildError(
      "Der Startpunkt hat keine aufgelöste Adresse.",
    );
  }
  if (!ziel.position) {
    throw new TripRequestBuildError(
      "Der Zielpunkt hat keine aufgelöste Adresse.",
    );
  }
  const unresolved = zwischen.find((s) => !s.position);
  if (unresolved) {
    throw new TripRequestBuildError(
      `Der Zwischenstopp "${unresolved.address || "(leer)"}" hat keine aufgelöste Adresse.`,
    );
  }

  return {
    start: start.position,
    ziel: ziel.position,
    zwischenstopps: zwischen.map((s) => ({
      koordinate: s.position as [number, number],
      aufenthaltsdauer_s: null,
      geplante_abfahrt: s.leaveAt ?? null,
    })),
    abfahrtszeit: start.leaveAt ?? getDefaultDepartureIso(),
    fahrzeugprofil: args.fahrzeugprofil,
    praeferenzen: args.praeferenzen ?? {},
    start_soc_pct: args.startSocPct,
    ziel_soc_pct: args.zielSocPct,
  };
}

/** Validiert die Stopp-Liste und gibt eine Liste von Fehlermeldungen zurück
 *  (leer = keine Fehler). Für Formular-Validierung VOR dem Absenden – die
 *  eigentliche Payload-Erzeugung (`buildTripRequestPayload`) prüft dieselben
 *  Invarianten nochmals defensiv. */
export function validateStops(stops: Stop[]): string[] {
  const errors: string[] = [];
  if (stops.length < 2) {
    errors.push("Mindestens zwei Stopps (Start und Ziel) sind erforderlich.");
    return errors;
  }
  stops.forEach((stop, idx) => {
    if (!stop.position) {
      const rolle =
        idx === 0
          ? "Start"
          : idx === stops.length - 1
            ? "Ziel"
            : `Zwischenstopp ${idx}`;
      errors.push(`${rolle}: Adresse noch nicht ausgewählt.`);
    }
  });
  return errors;
}
