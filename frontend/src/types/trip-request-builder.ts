import { getDefaultDepartureIso } from "../utils/datetime-utils";

import type {
  FerryExclusion,
  FaehrZeitfenster,
  LadedauerVorgabe,
  Stop,
  TripRequestPayload,
  VehicleProfileInput,
  WeatherDetailLevel,
} from "./trip-request";

/** Fehler beim Aufbau des Requests aus dem aktuellen Formularzustand
 *  (z. B. fehlende Positionen) – wird vom Formular vor dem Absenden per
 *  `validateStops()` abgefangen; dieser Typ dient als letzte Absicherung. */
export class TripRequestBuildError extends Error {}

/** Erzeugt einen neuen, leeren Stopp. */
export function createEmptyStop(): Stop {
  return { id: crypto.randomUUID(), address: "", position: null };
}

/** Baut den vollständigen `TripRequestPayload` aus der Stopp-Liste und den
 *  übrigen Formularwerten. Wirft `TripRequestBuildError`, wenn Start/Ziel
 *  keine aufgelöste Position haben (sollte durch `validateStops()` im UI
 *  bereits verhindert sein). */
export function buildTripRequestPayload(args: {
  stops: Stop[];
  fahrzeugprofil: VehicleProfileInput;
  startSocPct: number;
  mindestAnkunftsSocPct: number;
  zielSocPct: number;
  praeferenzen?: Record<string, unknown>;
  alleFaehrenVermeiden?: boolean;
  vermiedeneFaehren?: FerryExclusion[];
  faehrZeitfenster?: FaehrZeitfenster[];
  ladedauerVorgaben?: LadedauerVorgabe[];
  wetterDetailgrad?: WeatherDetailLevel;
  mindestLadezeitS: number;
  maxLadeSocPct?: number;
  baustellenBeruecksichtigen?: boolean;
}): TripRequestPayload {
  const { stops } = args;
  if (stops.length < 2) {
    throw new TripRequestBuildError(
      "Mindestens zwei Stopps (Start und Ziel) sind erforderlich.",
    );
  }
  const start = stops[0];
  const ziel = stops[stops.length - 1];
  const between = stops.slice(1, -1);

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
  const unresolved = between.find((s) => !s.position);
  if (unresolved) {
    throw new TripRequestBuildError(
      `Der Zwischenstopp "${unresolved.address || "(leer)"}" hat keine aufgelöste Adresse.`,
    );
  }

  return {
    start: start.position,
    ziel: ziel.position,
    zwischenstopps: between.map((s) => ({
      koordinate: s.position as [number, number],
      aufenthaltsdauer_s: null,
      geplante_abfahrt: s.leaveAt ?? null,
      ladeleistung_kw: s.chargingPowerKw ?? null,
    })),
    abfahrtszeit: start.leaveAt ?? getDefaultDepartureIso(),
    fahrzeugprofil: args.fahrzeugprofil,
    praeferenzen: args.praeferenzen ?? {},
    start_soc_pct: args.startSocPct,
    ziel_soc_pct: args.zielSocPct,
    mindest_ankunfts_soc_pct: args.mindestAnkunftsSocPct,
    alle_faehren_vermeiden: args.alleFaehrenVermeiden ?? false,
    vermiedene_faehren: args.vermiedeneFaehren ?? [],
    faehr_zeitfenster: args.faehrZeitfenster ?? [],
    ladedauer_vorgaben: args.ladedauerVorgaben ?? [],
    wetter_detailgrad: args.wetterDetailgrad ?? "high",
    mindest_ladezeit_s: args.mindestLadezeitS,
    max_lade_soc_pct: args.maxLadeSocPct ?? 100,
    baustellen_beruecksichtigen: args.baustellenBeruecksichtigen ?? true,
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
        idx === 0 ? "Start" : idx === stops.length - 1 ? "Ziel" : `Stop ${idx}`;
      errors.push(`${rolle}: Adresse noch nicht ausgewählt.`);
    }
  });
  return errors;
}
