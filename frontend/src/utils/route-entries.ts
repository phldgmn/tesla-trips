/** Buildet eine chronologisch sortierte Liste von Route-Einträgen (Stopp,
 *  Ladehalt, Fähre, Fahrsegment) aus den Simulationsdaten. Wird für das
 *  vereinheitlichte Route-Feldset in TripPlannerForm.tsx verwendet.
 *
 *  Jeder Route-Eintrag hat ein `sortKey`-Feld, das zur sortierung herangezogen
 *  wird (null-Werte werden ans Ende sortiert, stabile Sortierung erhält
 *  ursprüngliche Reihenfolge bei gleichen/null Keys). Stopp/Ladehalt/Fähre
 *  tragen zusätzlich ein `timing`-Feld (Ankunft/Abfahrt einzeln, `sortKey`
 *  ist `timing.arrival ?? timing.departure`), aus dem TripPlannerForm.tsx
 *  die überhängenden Zeit-Badges und den Tageswechsel-Trenner ableitet.
 *  Zwischen je zwei chronologisch aufeinanderfolgenden Stopp/Ladehalt/Fähre-
 *  Einträgen wird - sofern zeitlich/räumlich sinnvoll ermittelbar - ein
 *  `Fahrsegment`-Eintrag eingefügt (gefahrene Strecke/Zeit dazwischen).
 */

import type { Stop } from "../types/trip-request";
import type { ChargingStop, FerrySegment, SimulationFrame } from "../types";
import type { FerryExclusion } from "../types/trip-request";
import type { PositionTiming } from "./timing-utils";
import {
  estimateWaypointTimings,
  estimatePositionTiming,
  cumulativeDistancesKm,
  calculateDrivingSegment,
} from "./timing-utils";
import { isDayChange } from "./datetime-utils";

/**
 * Vergleicht zwei FerryExclusion-Einträge auf inhaltliche Gleichheit.
 */
function sameFerryExclusion(a: FerryExclusion, b: FerryExclusion): boolean {
  return (
    a.name === b.name &&
    a.bboxSw[0] === b.bboxSw[0] &&
    a.bboxSw[1] === b.bboxSw[1] &&
    a.bboxNe[0] === b.bboxNe[0] &&
    a.bboxNe[1] === b.bboxNe[1]
  );
}

/** Mindestdauer (Minuten), ab der ein Fahrsegment eingefügt wird - unterhalb
 *  davon liegen zwei Einträge praktisch am selben Ort/Zeitpunkt (z. B. eine
 *  Ladestation direkt an einem Zwischenstopp), ein "0 min · 0,0 km"-Eintrag
 *  wäre nur Rauschen in der Timeline. */
const MIN_DRIVING_SEGMENT_DURATION_MIN = 1;

/** Route-Eintrag mit Zeitpunkt-Bezug (Stopp, Ladehalt oder Fähre) - alles
 *  außer den verbindenden `Fahrsegment`-Einträgen. */
export type PointEntry =
  | {
      art: "Stopp";
      sortKey: string | null;
      timing: PositionTiming;
      stop: Stop;
      stopIndex: number;
    }
  | {
      art: "Ladehalt";
      sortKey: string | null;
      timing: PositionTiming;
      chargingStop: ChargingStop;
    }
  | {
      art: "Fähre";
      sortKey: string | null;
      timing: PositionTiming;
      ferry: FerrySegment;
    };

/** Verbindender Eintrag zwischen zwei `PunktEintrag`en: gefahrene Strecke/
 *  Zeit zwischen deren `connectionTime`en. Trägt KEIN eigenes
 *  Tageswechsel-Flag - Aufrufer prüfen `istTageswechsel(vonIso, bisIso)`
 *  direkt, um Strecke/Zeit/beide Daten in einer Zeile zu kombinieren (siehe
 *  `DriveSegmentRow` in `timeline-rows.tsx`), statt separat einen
 *  `Tagestrenner` zu rendern. */
export interface DrivingSegmentEntry {
  art: "Fahrsegment";
  sortKey: string | null;
  vonIso: string;
  bisIso: string;
  distanceKm: number;
  durationMin: number;
}

/** Tageswechsel-Trenner zwischen zwei `PunktEintrag`en OHNE Fahrsegment
 *  dazwischen (z. B. Verbindung unterhalb der Mindestdauer) - mit
 *  Fahrsegment wird der Tageswechsel stattdessen in dessen Zeile
 *  kombiniert (siehe `FahrsegmentEintrag`-Docstring). Bewusst NICHT anhand
 *  von Ankunft/Abfahrt DESSELBEN Eintrags gebildet (das wäre ein
 *  Tageswechsel INNERHALB eines Aufenthalts, z. B. ein Ladehalt über
 *  Mitternacht - dafür zeigen die Zeit-Badges des jeweiligen Eintrags
 *  selbst das Datum an, siehe `TimeBadge`/`istTageswechsel`-Aufruf in
 *  `TripPlannerForm.tsx`). */
export interface DaySeparatorEntry {
  art: "Tagestrenner";
  sortKey: string | null;
  vonIso: string;
  bisIso: string;
}

export type RouteEntry = PointEntry | DrivingSegmentEntry | DaySeparatorEntry;

/** Zeitpunkt, an dem ein `PunktEintrag` mit einem angrenzenden Fahrsegment
 *  verbunden wird: "anfang" bevorzugt die Ankunft (Fahrsegment endet hier),
 *  "ende" bevorzugt die Abfahrt (Fahrsegment beginnt hier) - mit Fallback
 *  auf den jeweils anderen Zeitpunkt, falls nicht vorhanden (z. B. Start
 *  ohne Ankunft, Ziel ohne Abfahrt). */
function connectionTime(
  entry: PointEntry,
  side: "anfang" | "ende",
): string | null {
  return side === "anfang"
    ? (entry.timing.arrival ?? entry.timing.departure)
    : (entry.timing.departure ?? entry.timing.arrival);
}

/** Baut eine chronologisch sortierte Liste von Route-Einträgen.
 *
 * - Alle `stops` werden immer inkludiert (in Originalreihenfolge als Stabiltiebraker).
 * - `chargingStops` werden mit ihrer `ankunftszeit` als sortKey inkludiert.
 * - `detectedFerries`, die NICHT in `avoidedFerries` enthalten sind, werden mit
 *   ihrer erwarteten Ankunftszeit (BBox-Mitte) als sortKey inkludiert.
 * - Ferien in `avoidedFerries` werden EXKLUDET.
 * - Wenn `frames` undefined/leer ist, werden nur Stops zurückgegeben (sortKey: null).
 *
 * Die Sortierung ist aufsteigend nach `sortKey` (ISO-String-Vergleich via localeCompare),
 * wobei null-Werte ans Ende sortiert werden. Die native Array.sort() ist spec-stabil,
 * sodass Einträge mit gleichen/null Keys ihre Konstruktionsreihenfolge behalten.
 */
export function buildRouteEntries(args: {
  stops: Stop[];
  frames: SimulationFrame[] | undefined;
  chargingStops: ChargingStop[] | undefined;
  detectedFerries: FerrySegment[] | undefined;
  avoidedFerries: FerryExclusion[];
}): RouteEntry[] {
  const {
    stops,
    frames,
    chargingStops,
    detectedFerries,
    avoidedFerries: avoidedFerries,
  } = args;

  // Keine Simulation: nur Stops in Originalreihenfolge
  if (!frames || frames.length === 0) {
    return stops.map((stop, idx) => ({
      art: "Stopp" as const,
      sortKey: null,
      timing: {
        arrival: null,
        departure: null,
        arrivalSocPct: null,
        departureSocPct: null,
      },
      stop,
      stopIndex: idx,
    }));
  }

  // Stops: sortKey = Ankunftszeit (falls vorhanden, sonst Abfahrt)
  const waypointTimings = estimateWaypointTimings(frames, stops);
  const stopEntries: PointEntry[] = stops.map((stop, idx) => {
    const { arrival, departure, arrivalSocPct, departureSocPct } =
      waypointTimings[idx];
    const sortKey = arrival ?? departure ?? null;
    return {
      art: "Stopp" as const,
      sortKey,
      timing: { arrival, departure, arrivalSocPct, departureSocPct },
      stop,
      stopIndex: idx,
    };
  });

  // Ladehalte: sortKey = ankunftszeit
  const chargingStopEntries: PointEntry[] = (chargingStops ?? []).map(
    (chargingStop) => ({
      art: "Ladehalt" as const,
      sortKey: chargingStop.arrivalTime,
      timing: {
        arrival: chargingStop.arrivalTime,
        departure: chargingStop.departureTime,
        arrivalSocPct: chargingStop.arrivalSocPct,
        departureSocPct: chargingStop.targetSocPct,
      },
      chargingStop,
    }),
  );

  // Fähren (nur nicht-vermiedene): sortKey = Ankunftszeit (BBox-Mitte)
  const recognizedFerriesWithoutAvoided = detectedFerries?.filter(
    (ferry) =>
      !avoidedFerries.some((avoidedFerry) =>
        sameFerryExclusion(avoidedFerry, {
          name: ferry.name,
          bboxSw: ferry.bboxSw,
          bboxNe: ferry.bboxNe,
        }),
      ),
  );

  const ferryEntries: PointEntry[] = (
    recognizedFerriesWithoutAvoided ?? []
  ).map((ferry) => {
    // BBox-Mitte berechnen
    const bbox_sw = ferry.bboxSw;
    const bbox_ne = ferry.bboxNe;
    const bboxCenter: [number, number] = [
      (bbox_sw[0] + bbox_ne[0]) / 2,
      (bbox_sw[1] + bbox_ne[1]) / 2,
    ];
    const timing = estimatePositionTiming(bboxCenter, frames);
    const sortKey = timing.arrival ?? timing.departure ?? null;
    return {
      art: "Fähre" as const,
      sortKey,
      timing,
      ferry,
    };
  });

  // Alle Einträge kombinieren und sortieren
  const allEntries: PointEntry[] = [
    ...stopEntries,
    ...chargingStopEntries,
    ...ferryEntries,
  ];

  // Stabile Sortierung: null sortiert ans Ende, andernfalls ISO-String-Vergleich
  allEntries.sort((a, b) => {
    if (a.sortKey === null && b.sortKey === null) return 0;
    if (a.sortKey === null) return 1;
    if (b.sortKey === null) return -1;
    return a.sortKey.localeCompare(b.sortKey);
  });

  // Fahrsegmente zwischen je zwei aufeinanderfolgenden Einträgen einfügen
  // (gefahrene Strecke/Zeit dazwischen) - siehe `connectionTime`/
  // `berechneFahrsegment`. `cumulativeKm` einmalig für die gesamte Route
  // gebildet statt pro Segment neu (siehe `berechneFahrsegment`-Docstring).
  const cumulativeKm = cumulativeDistancesKm(frames);
  const result: RouteEntry[] = [];
  allEntries.forEach((entry, idx) => {
    result.push(entry);
    const next = allEntries[idx + 1];
    if (!next) return;

    const von = connectionTime(entry, "ende");
    const to = connectionTime(next, "anfang");
    if (von === null || to === null || von === to) return;

    const segment = calculateDrivingSegment(von, to, frames, cumulativeKm);
    const hasDrivingSegment =
      segment !== null &&
      segment.durationMin >= MIN_DRIVING_SEGMENT_DURATION_MIN;

    if (hasDrivingSegment) {
      result.push({
        art: "Fahrsegment" as const,
        sortKey: von,
        vonIso: von,
        bisIso: to,
        ...segment,
      });
      return;
    }

    if (isDayChange(von, to)) {
      result.push({
        art: "Tagestrenner" as const,
        sortKey: von,
        vonIso: von,
        bisIso: to,
      });
    }
  });

  return result;
}
