/** Hilfsfunktionen zur Berechnung von Ankunfts-/Abfahrtszeiten aus Simulationsframes.
 *
 * Das Backend akzeptiert nur eine feste Abfahrtszeit am Start plus optionale
 * Mindestaufenthaltsdauern pro Zwischenstopp – keine frei wählbaren
 * Abfahrtszeitpunkte an Zwischenpunkten. Diese Datei rekonstruiert daher die
 * tatsächlichen Ankunfts-/Abfahrtszeiten, indem sie Simulationsframes den
 * Stopps (und, verallgemeinert, beliebigen Positionen wie Fährverbindungen)
 * zuordnet.
 *
 * Die Rolle jedes Stopps (Start/Zwischenstopp/Ziel) ergibt sich rein aus
 * seiner Position im Array.
 */

import type { SimulationFrame } from "../types";
import type { Stop } from "../types/trip-request";
import { haversineDistanceM } from "./geo-utils";

/** Zeit- und Ladestand-Informationen für eine Position (Ankunfts-/Abfahrts-
 *  Cluster). SoC wird direkt aus dem jeweiligen Grenz-Frame übernommen (kein
 *  eigenständiger Wert), damit Anzeige-Zeitpunkt und -SoC immer konsistent
 *  zum selben Simulationsframe gehören. */
export interface PositionTiming {
  /** ISO-Zeitstempel der Ankunft, oder null falls nicht ermittelbar. */
  arrival: string | null;
  /** ISO-Zeitstempel der Abfahrt, oder null falls nicht ermittelbar. */
  departure: string | null;
  /** Ladestand in % bei Ankunft, oder null falls nicht ermittelbar. */
  arrivalSocPct: number | null;
  /** Ladestand in % bei Abfahrt, oder null falls nicht ermittelbar. */
  departureSocPct: number | null;
}

/** Zeitinformationen für einen einzelnen Stopp. */
export interface WaypointTiming extends PositionTiming {
  /** ID des zugehörigen Stopps. */
  stopId: string;
}

/** Maximale Distanz in Metern, innerhalb der ein Frame noch als "am Stopp"
 *  gewertet wird. */
const CLUSTER_RADIUS_M = 2000;

/** Ermittelt für jeden Stopp die Ankunfts- und Abfahrtszeit anhand
 *  der Simulationsframes.
 *
 * @param frames  Zeitlich geordnete Liste von Simulationsframes.
 * @param stops   Liste der Stopps (Array-Position bestimmt die Rolle:
 *                [0] = Start, [n-1] = Ziel, dazwischen = Zwischenstopps).
 * @returns  Gleichlange Liste von {@link WaypointTiming} – ein Eintrag pro Stopp.
 */
export function estimateWaypointTimings(
  frames: SimulationFrame[],
  stops: Stop[],
): WaypointTiming[] {
  if (frames.length === 0) {
    return stops.map((s) => ({
      stopId: s.id,
      arrival: null,
      departure: null,
      arrivalSocPct: null,
      departureSocPct: null,
    }));
  }

  return stops.map((stop, idx) => {
    // Start (Index 0): Ankunft null, Abfahrt = erster Frame
    if (idx === 0) {
      return {
        stopId: stop.id,
        arrival: null,
        departure: frames[0].timestamp,
        arrivalSocPct: null,
        departureSocPct: frames[0].socPct,
      };
    }

    // Ziel (letzter Index): Ankunft = letzter Frame, Abfahrt null
    if (idx === stops.length - 1) {
      const lastFrame = frames[frames.length - 1];
      return {
        stopId: stop.id,
        arrival: lastFrame.timestamp,
        departure: null,
        arrivalSocPct: lastFrame.socPct,
        departureSocPct: null,
      };
    }

    // Zwischenstopp: Falls keine Position aufgelöst, null / null zurück
    if (stop.position === null) {
      return {
        stopId: stop.id,
        arrival: null,
        departure: null,
        arrivalSocPct: null,
        departureSocPct: null,
      };
    }

    return {
      stopId: stop.id,
      ...estimatePositionTiming(stop.position, frames),
    };
  });
}

/** Sucht zum gegebenen Punkt den Frame-Cluster (< CLUSTER_RADIUS_M) und gibt
 *  die Ankunfts-/Abfahrtszeit dieses Clusters zurück. Generalisierte Variante
 *  ohne Stopp-Bezug – wird auch zur chronologischen Einordnung unterminierter
 *  Fährverbindungen anhand ihrer Bounding-Box-Mitte verwendet (siehe
 *  `route-eintraege.ts`). */
export function estimatePositionTiming(
  position: [number, number],
  frames: SimulationFrame[],
): PositionTiming {
  if (frames.length === 0) {
    return {
      arrival: null,
      departure: null,
      arrivalSocPct: null,
      departureSocPct: null,
    };
  }

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
    return {
      arrival: null,
      departure: null,
      arrivalSocPct: null,
      departureSocPct: null,
    };
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
    arrival: frames[leftIdx].timestamp,
    departure: frames[rightIdx].timestamp,
    arrivalSocPct: frames[leftIdx].socPct,
    departureSocPct: frames[rightIdx].socPct,
  };
}

/** Berechnet für jeden Frame die kumulierte gefahrene Distanz in km seit
 *  Frame 0 (Haversine-Summe entlang der Frame-Positionen). Gleiche Länge
 *  wie `frames`; `result[0]` ist immer 0. */
export function cumulativeDistancesKm(frames: SimulationFrame[]): number[] {
  const out: number[] = frames.length > 0 ? [0] : [];
  for (let i = 1; i < frames.length; i++) {
    out.push(
      out[i - 1] +
        haversineDistanceM(frames[i - 1].position, frames[i].position) / 1000,
    );
  }
  return out;
}

/** Findet den Index des Frames, dessen Zeitstempel `iso` am nächsten liegt
 *  (kleinste absolute Differenz). Liefert `null` bei leerem `frames`-Array. */
export function findNearestFrameIndex(
  iso: string,
  frames: SimulationFrame[],
): number | null {
  if (frames.length === 0) return null;
  const target = new Date(iso).getTime();
  let bestIdx = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < frames.length; i++) {
    const diff = Math.abs(new Date(frames[i].timestamp).getTime() - target);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestIdx = i;
    }
  }
  return bestIdx;
}

/** Fahrzeit und -distanz zwischen zwei Zeitpunkten. */
export interface DrivingSegment {
  distanceKm: number;
  durationMin: number;
}

/** Berechnet Fahrzeit und -distanz zwischen zwei Zeitpunkten anhand der
 *  ihnen nächstgelegenen Simulationsframes - gleiches Vorgehen wie in
 *  `TripSummary.tsx` für "Strecke/Dauer seit letztem" (siehe
 *  `buildTimePlan`). `cumulativeKm` MUSS `cumulativeDistancesKm(frames)`
 *  sein - als Parameter statt intern neu berechnet, damit Aufrufer mit
 *  vielen Segmenten (siehe `route-eintraege.ts`) die Distanzsumme nur
 *  einmal für die gesamte Route bilden statt einmal pro Segment.
 *  Liefert `null`, wenn sich einer der beiden Zeitpunkte keinem Frame
 *  zuordnen lässt (z. B. leeres `frames`-Array). */
export function calculateDrivingSegment(
  vonIso: string,
  bisIso: string,
  frames: SimulationFrame[],
  cumulativeKm: number[],
): DrivingSegment | null {
  const fromIdx = findNearestFrameIndex(vonIso, frames);
  const toIdx = findNearestFrameIndex(bisIso, frames);
  if (fromIdx === null || toIdx === null) return null;
  return {
    distanceKm: Math.max(0, cumulativeKm[toIdx] - cumulativeKm[fromIdx]),
    durationMin: Math.max(
      0,
      (new Date(bisIso).getTime() - new Date(vonIso).getTime()) / 60000,
    ),
  };
}
