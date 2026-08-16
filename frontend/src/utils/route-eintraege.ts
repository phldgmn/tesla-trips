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
import type { ChargingStop, FaehrSegment, SimulationFrame } from "../types";
import type { FerryExclusion } from "../types/trip-request";
import type { PositionTiming } from "./timing-utils";
import {
  estimateWaypointTimings,
  estimatePositionTiming,
  cumulativeDistancesKm,
  berechneFahrsegment,
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

/** Mindestdauer (Minuten), ab der ein Fahrsegment eingefügt wird - unterhalb
 *  davon liegen zwei Einträge praktisch am selben Ort/Zeitpunkt (z. B. eine
 *  Ladestation direkt an einem Zwischenstopp), ein "0 min · 0,0 km"-Eintrag
 *  wäre nur Rauschen in der Timeline. */
const MIN_FAHRSEGMENT_DAUER_MIN = 1;

/** Route-Eintrag mit Zeitpunkt-Bezug (Stopp, Ladehalt oder Fähre) - alles
 *  außer den verbindenden `Fahrsegment`-Einträgen. */
export type PunktEintrag =
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
      faehre: FaehrSegment;
    };

/** Verbindender Eintrag zwischen zwei `PunktEintrag`en: gefahrene Strecke/
 *  Zeit zwischen deren `verbindungsZeitpunkt`en. */
export interface FahrsegmentEintrag {
  art: "Fahrsegment";
  sortKey: string | null;
  vonIso: string;
  bisIso: string;
  distanzKm: number;
  dauerMin: number;
}

export type RouteEintrag = PunktEintrag | FahrsegmentEintrag;

/** Zeitpunkt, an dem ein `PunktEintrag` mit einem angrenzenden Fahrsegment
 *  verbunden wird: "anfang" bevorzugt die Ankunft (Fahrsegment endet hier),
 *  "ende" bevorzugt die Abfahrt (Fahrsegment beginnt hier) - mit Fallback
 *  auf den jeweils anderen Zeitpunkt, falls nicht vorhanden (z. B. Start
 *  ohne Ankunft, Ziel ohne Abfahrt). */
function verbindungsZeitpunkt(
  eintrag: PunktEintrag,
  seite: "anfang" | "ende",
): string | null {
  return seite === "anfang"
    ? (eintrag.timing.arrival ?? eintrag.timing.departure)
    : (eintrag.timing.departure ?? eintrag.timing.arrival);
}

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
  const stopEintraege: PunktEintrag[] = stops.map((stop, idx) => {
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
  const ladehaltEintraege: PunktEintrag[] = (chargingStops ?? []).map(
    (chargingStop) => ({
      art: "Ladehalt" as const,
      sortKey: chargingStop.ankunftszeit,
      timing: {
        arrival: chargingStop.ankunftszeit,
        departure: chargingStop.abfahrtszeit,
        arrivalSocPct: chargingStop.ankunfts_soc_pct,
        departureSocPct: chargingStop.ziel_soc_pct,
      },
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

  const faehreEintraege: PunktEintrag[] = (
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
      timing,
      faehre,
    };
  });

  // Alle Einträge kombinieren und sortieren
  const alleEintraege: PunktEintrag[] = [
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

  // Fahrsegmente zwischen je zwei aufeinanderfolgenden Einträgen einfügen
  // (gefahrene Strecke/Zeit dazwischen) - siehe `verbindungsZeitpunkt`/
  // `berechneFahrsegment`. `cumulativeKm` einmalig für die gesamte Route
  // gebildet statt pro Segment neu (siehe `berechneFahrsegment`-Docstring).
  const cumulativeKm = cumulativeDistancesKm(frames);
  const ergebnis: RouteEintrag[] = [];
  alleEintraege.forEach((eintrag, idx) => {
    ergebnis.push(eintrag);
    const naechster = alleEintraege[idx + 1];
    if (!naechster) return;

    const von = verbindungsZeitpunkt(eintrag, "ende");
    const bis = verbindungsZeitpunkt(naechster, "anfang");
    if (von === null || bis === null || von === bis) return;

    const segment = berechneFahrsegment(von, bis, frames, cumulativeKm);
    if (!segment || segment.dauerMin < MIN_FAHRSEGMENT_DAUER_MIN) return;

    ergebnis.push({
      art: "Fahrsegment" as const,
      sortKey: von,
      vonIso: von,
      bisIso: bis,
      ...segment,
    });
  });

  return ergebnis;
}
