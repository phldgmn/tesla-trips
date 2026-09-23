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
  calculateDrivingSegment,
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
  const stopEntries: TimePlanEntry[] = stops.map((stop, i) => {
    const waypointStop = stop.position
      ? result.waypointStops.find(
          (w) =>
            w.position[0] === stop.position?.[0] &&
            w.position[1] === stop.position?.[1],
        )
      : undefined;
    return {
      key: `stopp-${stop.id}`,
      art: "Stopp",
      label: stopLabel(stop),
      arrival: waypointStop?.arrivalTime ?? timings[i]?.arrival ?? null,
      departure: waypointStop?.departureTime ?? timings[i]?.departure ?? null,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      ankunftsSocPct: waypointStop?.arrivalSocPct ?? null,
      abfahrtsSocPct: waypointStop?.targetSocPct ?? null,
      energieGeladenKwh: waypointStop?.energyChargedKwh ?? null,
      estimatedCost: null,
      costCurrency: null,
    };
  });

  const chargingStopEntries: TimePlanEntry[] = result.chargingStops.map(
    (stop) => ({
      key: `ladehalt-${stop.stationId}-${stop.arrivalTime}`,
      art: "Ladehalt",
      label: stop.name,
      arrival: stop.arrivalTime,
      departure: stop.departureTime,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      ankunftsSocPct: stop.arrivalSocPct,
      abfahrtsSocPct: stop.targetSocPct,
      energieGeladenKwh: stop.energyChargedKwh,
      estimatedCost: stop.estimatedCost,
      costCurrency: stop.currency,
    }),
  );

  const ferryEntries: TimePlanEntry[] = result.detectedFerries.map((f, idx) => {
    let arrival = f.abfahrt;
    let departure = f.ankunft;
    if (arrival === null || departure === null) {
      const bboxCenter: [number, number] = [
        (f.bboxSw[0] + f.bboxNe[0]) / 2,
        (f.bboxSw[1] + f.bboxNe[1]) / 2,
      ];
      const estimated = estimatePositionTiming(bboxCenter, frames);
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
  });

  const sorted = [...stopEntries, ...chargingStopEntries, ...ferryEntries].sort(
    (a, b) => {
      const timeA = a.arrival ?? a.departure ?? "";
      const timeB = b.arrival ?? b.departure ?? "";
      return timeA.localeCompare(timeB);
    },
  );

  // Zweiter Durchlauf: Strecke/Zeit seit dem vorherigen Eintrag sowie (für
  // Stopp/Fähre) SoC bei Ankunft/Abfahrt anhand der nächstgelegenen
  // Simulationsframes ergänzen.
  const cumulative = cumulativeDistancesKm(frames);
  let prevExitIso: string | null = null;
  let prevExitIdx: number | null = null;

  for (const entry of sorted) {
    const arrivalIso = entry.arrival ?? entry.departure;
    const exitIso = entry.departure ?? entry.arrival;
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
      const seg = calculateDrivingSegment(
        prevExitIso,
        arrivalIso,
        frames,
        cumulative,
      );
      if (seg !== null) {
        entry.distanceSinceLastKm = seg.distanceKm;
        entry.durationSinceLastMin = seg.durationMin;
      }
    }

    if (entry.art !== "Ladehalt") {
      // Exakte Werte (aus `result.waypoint_stops`, siehe oben) NICHT durch
      // die nur geschaetzte Frame-Naeherung ueberschreiben.
      if (entry.ankunftsSocPct === null) {
        entry.ankunftsSocPct =
          entry.arrival && arrivalIdx !== null
            ? frames[arrivalIdx].socPct
            : null;
      }
      if (entry.abfahrtsSocPct === null) {
        entry.abfahrtsSocPct =
          entry.departure && exitIdx !== null ? frames[exitIdx].socPct : null;
      }
    }

    prevExitIso = exitIso;
    prevExitIdx = exitIdx;
  }

  return sorted;
}
