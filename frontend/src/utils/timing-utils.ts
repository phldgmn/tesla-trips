/** Hilfsfunktionen zur Berechnung von Ankunfts-/Abfahrtszeiten aus Simulationsframes.
 *
 * Das Backend akzeptiert nur eine feste Abfahrtszeit am Start plus optionale
 * Mindestaufenthaltsdauern pro Zwischenstopp – keine frei wählbaren
 * Abfahrtszeitpunkte an Zwischenpunkten. Diese Datei rekonstruiert daher die
 * tatsächlichen Ankunfts-/Abfahrtszeiten, indem sie Simulationsframes den
 * Stopps zuordnet.
 *
 * Die Rolle jedes Stopps (Start/Zwischenstopp/Ziel) ergibt sich rein aus
 * seiner Position im Array.
 */

import type { TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";
import { haversineDistanceM } from "./geo-utils";

/** Zeitinformationen für einen einzelnen Stopp. */
export interface WaypointTiming {
  /** ID des zugehörigen Stopps. */
  stopId: string;
  /** ISO-Zeitstempel der Ankunft, oder null falls nicht ermittelbar. */
  arrival: string | null;
  /** ISO-Zeitstempel der Abfahrt, oder null falls nicht ermittelbar. */
  departure: string | null;
}

/** Maximale Distanz in Metern, innerhalb der ein Frame noch als "am Stopp"
 *  gewertet wird. */
const CLUSTER_RADIUS_M = 2000;

/** Ermittelt für jeden Stopp die Ankunfts- und Abfahrtszeit anhand
 *  der Simulationsframes.
 *
 * @param result  Simulationsergebnis mit einer zeitlich geordneten Liste von Frames.
 * @param stops   Liste der Stopps (Array-Position bestimmt die Rolle:
 *                [0] = Start, [n-1] = Ziel, dazwischen = Zwischenstopps).
 * @returns  Gleichlange Liste von {@link WaypointTiming} – ein Eintrag pro Stopp.
 */
export function estimateWaypointTimings(
  result: TripSimulationResult,
  stops: Stop[],
): WaypointTiming[] {
  const { frames } = result;

  if (frames.length === 0) {
    return stops.map((s) => ({
      stopId: s.id,
      arrival: null,
      departure: null,
    }));
  }

  return stops.map((stop, idx) => {
    // Start (Index 0): Ankunft null, Abfahrt = erster Frame
    if (idx === 0) {
      return {
        stopId: stop.id,
        arrival: null,
        departure: frames[0].zeitpunkt,
      };
    }

    // Ziel (letzter Index): Ankunft = letzter Frame, Abfahrt null
    if (idx === stops.length - 1) {
      return {
        stopId: stop.id,
        arrival: frames[frames.length - 1].zeitpunkt,
        departure: null,
      };
    }

    // Zwischenstopp: Falls keine Position aufgelöst, null / null zurück
    if (stop.position === null) {
      return { stopId: stop.id, arrival: null, departure: null };
    }

    return computeStopoverTiming(stop.position, frames, stop.id);
  });
}

/** Sucht zum gegebenen Punkt den Frame-Cluster (< CLUSTER_RADIUS_M) und gibt
 *  die Ankunfts-/Abfahrtszeit dieses Clusters zurück.
 */
function computeStopoverTiming(
  position: [number, number],
  frames: TripSimulationResult["frames"],
  stopId: string,
): WaypointTiming {
  let minDist = Infinity;
  let closestIdx = -1;

  for (let i = 0; i < frames.length; i++) {
    const d = haversineDistanceM(position, frames[i].position);
    if (d < minDist) {
      minDist = d;
      closestIdx = i;
    }
  }

  if (minDist > CLUSTER_RADIUS_M) {
    return { stopId, arrival: null, departure: null };
  }

  // Expandiere nach links und rechts, solange Frames innerhalb CLUSTER_RADIUS_M bleiben
  let leftIdx = closestIdx;
  while (leftIdx > 0) {
    const d = haversineDistanceM(position, frames[leftIdx - 1].position);
    if (d <= CLUSTER_RADIUS_M) {
      leftIdx--;
    } else {
      break;
    }
  }

  let rightIdx = closestIdx;
  while (rightIdx < frames.length - 1) {
    const d = haversineDistanceM(position, frames[rightIdx + 1].position);
    if (d <= CLUSTER_RADIUS_M) {
      rightIdx++;
    } else {
      break;
    }
  }

  return {
    stopId,
    arrival: frames[leftIdx].zeitpunkt,
    departure: frames[rightIdx].zeitpunkt,
  };
}
