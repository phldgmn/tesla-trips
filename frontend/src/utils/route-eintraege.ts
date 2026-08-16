/** Buildet eine chronologisch sortierte Liste von Route-Einträgen (Stopp,
 *  Ladehalt, Fähre) aus den Simulationsdaten. Wird für das vereinheitlichte
 *  Route-Feldset in TripPlannerForm.tsx verwendet.
 *
 *  Jeder Route-Eintrag hat ein `sortKey`-Feld, das zur sortierung herangezogen
 *  wird (null-Werte werden ans Ende sortiert, stabile Sortierung erhält
 *  ursprüngliche Reihenfolge bei gleichen/null Keys).
 */

import type { Stop } from "../types/trip-request";
import type { ChargingStop, FaehrSegment, SimulationFrame } from "../types";
import type { FerryExclusion } from "../types/trip-request";
import {
  estimateWaypointTimings,
  estimatePositionTiming,
} from "./timing-utils";

/**
 * Vergleicht zwei FaehrAusschluss-Einträge auf inhaltliche Gleichheit.
 */
function sameFerryExclusion(a: FerryExclusion, b: FerryExclusion): boolean {
  return (
    a.name === b.name &&
    a.bbox_sw[0] === b.bbox_sw[0] &&
    a.bbox_sw[1] === b.bbox_sw[1] &&
    a.bbox_no[0] === b.bbox_no[0] &&
    a.bbox_no[1] === b.bbox_no[1]
  );
}

export type RouteEintrag =
  | { art: "Stopp"; sortKey: string | null; stop: Stop; stopIndex: number }
  | { art: "Ladehalt"; sortKey: string | null; chargingStop: ChargingStop }
  | { art: "Fähre"; sortKey: string | null; faehre: FaehrSegment };

/** Baut eine chronologisch sortierte Liste von Route-Einträgen.
 *
 * - Alle `stops` werden immer inkludiert (in Originalreihenfolge als Stabiltiebraker).
 * - `chargingStops` werden mit ihrer `ankunftszeit` als sortKey inkludiert.
 * - `erkannteFaehren`, die NICHT in `vermiedeneFaehren` enthalten sind, werden mit
 *   ihrer erwarteten Ankunftszeit (BBox-Mitte) als sortKey inkludiert.
 * - Ferien in `vermiedeneFaehren` werden EXKLUDET.
 * - Wenn `frames` undefined/leer ist, werden nur Stops zurückgegeben (sortKey: null).
 *
 * Die Sortierung ist aufsteigend nach `sortKey` (ISO-String-Vergleich via localeCompare),
 * wobei null-Werte ans Ende sortiert werden. Die native Array.sort() ist spec-stabil,
 * sodass Einträge mit gleichen/null Keys ihre Konstruktionsreihenfolge behalten.
 */
export function buildRouteEintraege(args: {
  stops: Stop[];
  frames: SimulationFrame[] | undefined;
  chargingStops: ChargingStop[] | undefined;
  erkannteFaehren: FaehrSegment[] | undefined;
  vermiedeneFaehren: FerryExclusion[];
}): RouteEintrag[] {
  const { stops, frames, chargingStops, erkannteFaehren, vermiedeneFaehren } =
    args;

  // Keine Simulation: nur Stops in Originalreihenfolge
  if (!frames || frames.length === 0) {
    return stops.map((stop, idx) => ({
      art: "Stopp" as const,
      sortKey: null,
      stop,
      stopIndex: idx,
    }));
  }

  // Stops: sortKey = Ankunftszeit (falls vorhanden, sonst Abfahrt)
  const waypointTimings = estimateWaypointTimings(frames, stops);
  const stopEintraege: RouteEintrag[] = stops.map((stop, idx) => {
    const timing = waypointTimings[idx];
    const sortKey = timing.arrival ?? timing.departure ?? null;
    return {
      art: "Stopp" as const,
      sortKey,
      stop,
      stopIndex: idx,
    };
  });

  // Ladehalte: sortKey = ankunftszeit
  const ladehaltEintraege: RouteEintrag[] = (chargingStops ?? []).map(
    (chargingStop) => ({
      art: "Ladehalt" as const,
      sortKey: chargingStop.ankunftszeit,
      chargingStop,
    }),
  );

  // Fähren (nur nicht-vermiedene): sortKey = Ankunftszeit (BBox-Mitte)
  const recognizedFerriesWithoutAvoided = erkannteFaehren?.filter(
    (faehre) =>
      !vermiedeneFaehren.some((vermieden) =>
        sameFerryExclusion(vermieden, {
          name: faehre.name,
          bbox_sw: faehre.bbox_sw,
          bbox_no: faehre.bbox_no,
        }),
      ),
  );

  const faehreEintraege: RouteEintrag[] = (
    recognizedFerriesWithoutAvoided ?? []
  ).map((faehre) => {
    // BBox-Mitte berechnen
    const bbox_sw = faehre.bbox_sw;
    const bbox_no = faehre.bbox_no;
    const bboxCenter: [number, number] = [
      (bbox_sw[0] + bbox_no[0]) / 2,
      (bbox_sw[1] + bbox_no[1]) / 2,
    ];
    const timing = estimatePositionTiming(bboxCenter, frames);
    const sortKey = timing.arrival ?? timing.departure ?? null;
    return {
      art: "Fähre" as const,
      sortKey,
      faehre,
    };
  });

  // Alle Einträge kombinieren und sortieren
  const alleEintraege = [
    ...stopEintraege,
    ...ladehaltEintraege,
    ...faehreEintraege,
  ];

  // Stabile Sortierung: null sortiert ans Ende, andernfalls ISO-String-Vergleich
  alleEintraege.sort((a, b) => {
    if (a.sortKey === null && b.sortKey === null) return 0;
    if (a.sortKey === null) return 1;
    if (b.sortKey === null) return -1;
    return a.sortKey.localeCompare(b.sortKey);
  });

  return alleEintraege;
}
