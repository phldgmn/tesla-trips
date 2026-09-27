/** Pure time plan builder: builds from a simulation result and the
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

/** Return a short display address for a stop: short form of the address,
 *  otherwise rounded coordinates, otherwise "(unknown)". */
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
  /** Distance driven since the previous entry in km, or null if the
   *  Zeitpunkt dieses oder des vorherigen Eintrags nicht ermittelbar war
   *  (insbesondere der erste Eintrag der Route). */
  distanceSinceLastKm: number | null;
  /** Driving time since the previous entry in minutes, same
   *  Verfügbarkeitsbedingung wie `distanceSinceLastKm`. */
  durationSinceLastMin: number | null;
  /** SoC at arrival in %, or null (e.g. at the start which has no arrival). */
  arrivalSocPct: number | null;
  /** SoC at departure in %, or null (e.g. at the destination which has no departure). */
  departureSocPct: number | null;
  /** Energy charged in kWh during a charging stop, or null. */
  energyChargedKwh: number | null;
  /** Estimated cost of this charging stop, or null (no charging stop or no
   *  gecachten Preisdaten für die Station vorhanden). */
  estimatedCost: number | null;
  /** ISO-4217-Währung von `estimatedCost`, oder null (siehe `estimatedCost`). */
  costCurrency: string | null;
}

/** Build the unified, chronological time plan from stops, charging stops
 *  and detected ferries, sorted by arrival (stops without arrival, e.g.
 *  the start, are sorted by their departure). Ferries without a user-specified
 *  time get an estimated arrival/departure time based on
 *  their bounding box center (see `estimatePositionTiming`), so they can also
 *  be sorted chronologically (same approach as in
 *  `route-entries.ts` for the route hover card).
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
  // position` unveraendert als `Waypoint.coordinate` an das Backend
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
      arrivalSocPct: waypointStop?.arrivalSocPct ?? null,
      departureSocPct: waypointStop?.targetSocPct ?? null,
      energyChargedKwh: waypointStop?.energyChargedKwh ?? null,
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
      arrivalSocPct: stop.arrivalSocPct,
      departureSocPct: stop.targetSocPct,
      energyChargedKwh: stop.energyChargedKwh,
      estimatedCost: stop.estimatedCost,
      costCurrency: stop.currency,
    }),
  );

  const ferryEntries: TimePlanEntry[] = result.detectedFerries.map((f, idx) => {
    let arrival = f.departure;
    let departure = f.arrival;
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
      key: `ferry-${idx}-${f.name}`,
      art: "Fähre",
      label: f.name,
      arrival,
      departure,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      arrivalSocPct: null,
      departureSocPct: null,
      energyChargedKwh: null,
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
      if (entry.arrivalSocPct === null) {
        entry.arrivalSocPct =
          entry.arrival && arrivalIdx !== null
            ? frames[arrivalIdx].socPct
            : null;
      }
      if (entry.departureSocPct === null) {
        entry.departureSocPct =
          entry.departure && exitIdx !== null ? frames[exitIdx].socPct : null;
      }
    }

    prevExitIso = exitIso;
    prevExitIdx = exitIdx;
  }

  return sorted;
}
