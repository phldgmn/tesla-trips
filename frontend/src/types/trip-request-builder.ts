import { getDefaultDepartureIso } from "../utils/datetime-utils";

import type {
  FerryExclusion,
  FerryTimeWindow,
  ChargingDurationSpecification,
  Stop,
  TripRequestPayload,
  VehicleProfileInput,
  WeatherDetailLevel,
  HighwayPreferenceLevel,
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
  vehicleProfile: VehicleProfileInput;
  startSocPct: number;
  minArrivalSocPct: number;
  targetSocPct: number;
  preferences?: Record<string, unknown>;
  avoidAllFerries?: boolean;
  highwayPreference?: HighwayPreferenceLevel;
  avoidedFerries?: FerryExclusion[];
  ferryTimeWindows?: FerryTimeWindow[];
  chargingDurationSpecifications?: ChargingDurationSpecification[];
  weatherDetailLevel?: WeatherDetailLevel;
  minChargeDurationS: number;
  maxChargeSocPct?: number;
  considerConstructionSites?: boolean;
}): TripRequestPayload {
  const { stops } = args;
  if (stops.length < 2) {
    throw new TripRequestBuildError(
      "Mindestens zwei Stopps (Start und Ziel) sind erforderlich.",
    );
  }
  const start = stops[0];
  const destination = stops[stops.length - 1];
  const between = stops.slice(1, -1);

  if (!start.position) {
    throw new TripRequestBuildError(
      "Der Startpunkt hat keine aufgelöste Adresse.",
    );
  }
  if (!destination.position) {
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
    destination: destination.position,
    waypoints: between.map((s) => ({
      coordinate: s.position as [number, number],
      stayDurationS: null,
      plannedDeparture: s.leaveAt ?? null,
      chargingPowerKw: s.chargingPowerKw ?? null,
    })),
    departureTime: start.leaveAt ?? getDefaultDepartureIso(),
    vehicleProfile: args.vehicleProfile,
    preferences: args.preferences ?? {},
    startSocPct: args.startSocPct,
    targetSocPct: args.targetSocPct,
    minArrivalSocPct: args.minArrivalSocPct,
    avoidAllFerries: args.avoidAllFerries ?? false,
    highwayPreference: args.highwayPreference ?? "off",
    avoidedFerries: args.avoidedFerries ?? [],
    ferryTimeWindows: args.ferryTimeWindows ?? [],
    chargingDurationSpecifications: args.chargingDurationSpecifications ?? [],
    weatherDetailLevel: args.weatherDetailLevel ?? "high",
    minChargingTimeS: args.minChargeDurationS,
    maxChargeSocPct: args.maxChargeSocPct ?? 100,
    considerConstructionSites: args.considerConstructionSites ?? true,
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
      const role =
        idx === 0 ? "Start" : idx === stops.length - 1 ? "Ziel" : `Stop ${idx}`;
      errors.push(`${role}: Adresse noch nicht ausgewählt.`);
    }
  });
  return errors;
}
