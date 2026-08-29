/** Reiner Zeitplan-Builder: baut aus einem Simulationsergebnis und den
 *  Nutzer-Stopps den vereinheitlichten, chronologischen Zeitplan.
 *
 *  Bewusst frei von React/JSX: die Logik ist eine reine, testbare Funktion
 *  (siehe die Komponente `TripSummary` für die Darstellung).
 */

import {
  estimateWaypointTimings,
  estimatePositionTiming,
  cumulativeDistancesKm,
  findNearestFrameIndex,
  berechneFahrsegment,
} from "./timing-utils";
import type { TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";

/** Kurzadresse: nur der erste Teil vor dem ersten Komma (Stadt oder Straße). */
function shortAddress(address: string): string {
  const comma = address.indexOf(",");
  return comma > 0 ? address.slice(0, comma) : address;
}

/** Gibt eine kurze Anzeigeadresse eines Stopps zurück: Kurzform der Adresse,
 *  sonst gerundete Koordinaten, sonst "(unbekannt)". */
function stopLabel(stop: Stop): string {
  if (stop.address) return shortAddress(stop.address);
  if (stop.position) {
    return `(${stop.position[0].toFixed(4)}, ${stop.position[1].toFixed(4)})`;
  }
  return "(unbekannt)";
}

/** Ein Eintrag im vereinheitlichten, chronologischen Zeitplan - entweder ein
 *  Nutzer-Stopp, ein Ladehalt oder eine terminierte Fähre. */
export interface TimePlanEntry {
  key: string;
  art: "Stopp" | "Ladehalt" | "Fähre";
  label: string;
  arrival: string | null;
  departure: string | null;
  /** Gefahrene Strecke seit dem vorherigen Eintrag in km, oder null, wenn der
   *  Zeitpunkt dieses oder des vorherigen Eintrags nicht ermittelbar war
   *  (insbesondere der erste Eintrag der Route). */
  distanceSinceLastKm: number | null;
  /** Fahrzeit seit dem vorherigen Eintrag in Minuten, gleiche
   *  Verfügbarkeitsbedingung wie `distanceSinceLastKm`. */
  durationSinceLastMin: number | null;
  /** SoC bei Ankunft in %, oder null (z. B. am Start, der keine Ankunft hat). */
  ankunftsSocPct: number | null;
  /** SoC bei Abfahrt in %, oder null (z. B. am Ziel, das keine Abfahrt hat). */
  abfahrtsSocPct: number | null;
  /** Während eines Ladehalts geladene Energie in kWh, sonst null. */
  energieGeladenKwh: number | null;
  /** Geschätzte Kosten dieses Ladehalts, oder null (kein Ladehalt oder keine
   *  gecachten Preisdaten für die Station vorhanden). */
  estimatedCost: number | null;
  /** ISO-4217-Währung von `estimatedCost`, oder null (siehe `estimatedCost`). */
  costCurrency: string | null;
}

/** Baut den vereinheitlichten, chronologischen Zeitplan aus Stopps, Ladehalten
 *  und erkannten Fähren, sortiert nach Ankunft (Stopps ohne Ankunft, z. B.
 *  der Start, werden nach ihrer Abfahrt einsortiert). Fähren ohne vom Nutzer
 *  vorgegebene Zeit erhalten eine geschätzte Ankunfts-/Abfahrtszeit anhand
 *  ihrer Bounding-Box-Mitte (siehe `estimatePositionTiming`), damit auch sie
 *  chronologisch einsortiert werden können (gleiches Vorgehen wie in
 *  `route-eintraege.ts` für die Routen-Hoverkarte).
 *
 * Ergänzt für jeden Eintrag (soweit ermittelbar) die seit dem vorherigen
 * Eintrag gefahrene Strecke/Zeit sowie den SoC bei Ankunft/Abfahrt, indem der
 * jeweils nächstgelegene Simulationsframe herangezogen wird (siehe
 * `findNearestFrameIndex`/`cumulativeDistancesKm`). Für Ladehalte stammen
 * SoC und geladene Energie direkt aus dem `ChargingStop` (exakt statt
 * geschätzt). */
export function buildTimePlan(
  result: TripSimulationResult,
  stops: Stop[],
): TimePlanEntry[] {
  const { frames } = result;
  const timings = estimateWaypointTimings(frames, stops);

  // Exakter Zwischenstopp-Aufenthalt (Ankunft/Abfahrt/SoC/geladene Energie
  // aus `result.waypoint_stops`, siehe `ZwischenstoppAufenthalt` im Backend)
  // statt der nur GESCHAETZTEN Werte aus `estimateWaypointTimings` (nächst-
  // gelegener Simulationsframe) - Koordinaten sind identisch, da `Stop.
  // position` unveraendert als `Waypoint.koordinate` an das Backend
  // durchgereicht wird.
  const stopEintraege: TimePlanEntry[] = stops.map((stop, i) => {
    const waypointStop = stop.position
      ? result.waypoint_stops.find(
          (w) =>
            w.position[0] === stop.position?.[0] &&
            w.position[1] === stop.position?.[1],
        )
      : undefined;
    return {
      key: `stopp-${stop.id}`,
      art: "Stopp",
      label: stopLabel(stop),
      arrival: waypointStop?.ankunftszeit ?? timings[i]?.arrival ?? null,
      departure: waypointStop?.abfahrtszeit ?? timings[i]?.departure ?? null,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      ankunftsSocPct: waypointStop?.ankunfts_soc_pct ?? null,
      abfahrtsSocPct: waypointStop?.ziel_soc_pct ?? null,
      energieGeladenKwh: waypointStop?.energie_geladen_kwh ?? null,
      estimatedCost: null,
      costCurrency: null,
    };
  });

  const ladehaltEintraege: TimePlanEntry[] = result.charging_stops.map(
    (stop) => ({
      key: `ladehalt-${stop.station_id}-${stop.ankunftszeit}`,
      art: "Ladehalt",
      label: stop.name,
      arrival: stop.ankunftszeit,
      departure: stop.abfahrtszeit,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      ankunftsSocPct: stop.ankunfts_soc_pct,
      abfahrtsSocPct: stop.ziel_soc_pct,
      energieGeladenKwh: stop.energie_geladen_kwh,
      estimatedCost: stop.estimated_cost,
      costCurrency: stop.currency,
    }),
  );

  const faehrEintraege: TimePlanEntry[] = result.erkannte_faehren.map(
    (f, idx) => {
      let arrival = f.abfahrt;
      let departure = f.ankunft;
      if (arrival === null || departure === null) {
        const bboxMitte: [number, number] = [
          (f.bbox_sw[0] + f.bbox_no[0]) / 2,
          (f.bbox_sw[1] + f.bbox_no[1]) / 2,
        ];
        const estimated = estimatePositionTiming(bboxMitte, frames);
        arrival = arrival ?? estimated.arrival;
        departure = departure ?? estimated.departure;
      }
      return {
        key: `faehre-${idx}-${f.name}`,
        art: "Fähre",
        label: f.name,
        arrival,
        departure,
        distanceSinceLastKm: null,
        durationSinceLastMin: null,
        ankunftsSocPct: null,
        abfahrtsSocPct: null,
        energieGeladenKwh: null,
        estimatedCost: null,
        costCurrency: null,
      };
    },
  );

  const sorted = [
    ...stopEintraege,
    ...ladehaltEintraege,
    ...faehrEintraege,
  ].sort((a, b) => {
    const timeA = a.arrival ?? a.departure ?? "";
    const timeB = b.arrival ?? b.departure ?? "";
    return timeA.localeCompare(timeB);
  });

  // Zweiter Durchlauf: Strecke/Zeit seit dem vorherigen Eintrag sowie (für
  // Stopp/Fähre) SoC bei Ankunft/Abfahrt anhand der nächstgelegenen
  // Simulationsframes ergänzen.
  const cumulative = cumulativeDistancesKm(frames);
  let prevExitIso: string | null = null;
  let prevExitIdx: number | null = null;

  for (const eintrag of sorted) {
    const arrivalIso = eintrag.arrival ?? eintrag.departure;
    const exitIso = eintrag.departure ?? eintrag.arrival;
    const arrivalIdx = arrivalIso
      ? findNearestFrameIndex(arrivalIso, frames)
      : null;
    const exitIdx = exitIso ? findNearestFrameIndex(exitIso, frames) : null;

    if (
      prevExitIdx !== null &&
      prevExitIso !== null &&
      arrivalIdx !== null &&
      arrivalIso !== null
    ) {
      const seg = berechneFahrsegment(
        prevExitIso,
        arrivalIso,
        frames,
        cumulative,
      );
      if (seg !== null) {
        eintrag.distanceSinceLastKm = seg.distanzKm;
        eintrag.durationSinceLastMin = seg.dauerMin;
      }
    }

    if (eintrag.art !== "Ladehalt") {
      // Exakte Werte (aus `result.waypoint_stops`, siehe oben) NICHT durch
      // die nur geschaetzte Frame-Naeherung ueberschreiben.
      if (eintrag.ankunftsSocPct === null) {
        eintrag.ankunftsSocPct =
          eintrag.arrival && arrivalIdx !== null
            ? frames[arrivalIdx].soc_pct
            : null;
      }
      if (eintrag.abfahrtsSocPct === null) {
        eintrag.abfahrtsSocPct =
          eintrag.departure && exitIdx !== null
            ? frames[exitIdx].soc_pct
            : null;
      }
    }

    prevExitIso = exitIso;
    prevExitIdx = exitIdx;
  }

  return sorted;
}
