/** Baut die auf der Karte gezeichnete Routenlinie aus der vollen Streckengeometrie
 * plus echten, geroutete Abstechern zu Ladestationen (siehe `ChargingStop.detour_geometrie`,
 * `tripplanner.trip_input.api._step_lade_detours_routen`).
 *
 * Ohne diese Splice-Logik zeigt die Linie den Ladehalt entweder gar nicht (reine
 * `route_geometrie`, die GraphHopper ohne Ladestationen als Wegpunkte berechnet)
 * oder - mit der alten frame-basierten Linie - nur eine grobe Luftlinie zur
 * Station. `buildSplicedRoute()` fuegt die echte Abstecher-Geometrie an der
 * richtigen Stelle ein und liefert passend dazu SoC-Stuetzpunkte (inkl. eines
 * garantierten Ankunfts-/Abfahrts-Farbsprungs an jeder Station), die
 * `buildSocGradientExpression` (siehe Map.tsx) in eine MapLibre
 * `line-gradient`-Expression umwandelt.
 */

import { toLngLat, haversineDistanceM } from "./geo-utils";

/** Ein Ladehalt-Abstecher, wie ihn `buildSplicedRoute` zum Einfuegen braucht. */
export interface ChargingDetourInput {
  /** Position der Ladestation als [lat, lon] (Fallback-Ziel, falls `detourGeometrie` leer ist) */
  position: [number, number];
  /** Kumulierte Distanz entlang der Hauptroute, an der abgebogen wird */
  distanzM: number;
  /** Echte Hin-und-zurueck-Geometrie (leer = Fallback auf eine Luftlinie zur Station) */
  detourGeometrie: [number, number][];
  ankunftsSocPct: number;
  zielSocPct: number;
}

/** Ein SoC-Stuetzpunkt fuer `buildSocGradientExpression`, positioniert per
 * kumulierter Distanz entlang der GESPLICETEN Linie (inkl. Abstecher-Laenge). */
export interface RouteSample {
  distanzM: number;
  socPct: number;
  /** Ladehalt-Ankunft/-Abfahrt: wird beim Downsampling nie uebersprungen, damit
   *  der SoC-Sprung an der Ladestation sichtbar bleibt. */
  critical?: boolean;
}

export interface SplicedRoute {
  /** Fertige Koordinatenliste fuer die GeoJSON-LineString, in [lng, lat] */
  coordinates: [number, number][];
  /** Gesamtlaenge der gespliceten Linie in Metern (Hauptroute + alle Abstecher) */
  totalDistanceM: number;
  samples: RouteSample[];
}

/** Kumulierte Distanz (m) je Punkt einer (lat, lon)-Polyline; `result[0] === 0`. */
function cumulativeDistancesM(points: [number, number][]): number[] {
  const out: number[] = [0];
  for (let i = 1; i < points.length; i++) {
    out.push(out[i - 1] + haversineDistanceM(points[i - 1], points[i]));
  }
  return out;
}

export function buildSplicedRoute(
  routeGeometrie: [number, number][],
  chargingStops: ChargingDetourInput[],
  frameSamples: { distanzM: number; socPct: number }[],
): SplicedRoute {
  if (routeGeometrie.length === 0) {
    return { coordinates: [], totalDistanceM: 0, samples: [] };
  }

  const routeCum = cumulativeDistancesM(routeGeometrie);
  const sortedStops = [...chargingStops].sort(
    (a, b) => a.distanzM - b.distanzM,
  );
  const sortedFrames = [...frameSamples].sort(
    (a, b) => a.distanzM - b.distanzM,
  );

  const coordinates: [number, number][] = [];
  const samples: RouteSample[] = [];
  let stopIdx = 0;
  let frameIdx = 0;
  let detourOffset = 0;

  const emitFrameSamplesUpTo = (routeDistance: number) => {
    while (
      frameIdx < sortedFrames.length &&
      sortedFrames[frameIdx].distanzM <= routeDistance
    ) {
      samples.push({
        distanzM: sortedFrames[frameIdx].distanzM + detourOffset,
        socPct: sortedFrames[frameIdx].socPct,
      });
      frameIdx++;
    }
  };

  const spliceDetourAt = (
    branchPoint: [number, number],
    routeDistance: number,
    stop: ChargingDetourInput,
  ) => {
    const detour =
      stop.detourGeometrie.length >= 2
        ? stop.detourGeometrie
        : [branchPoint, stop.position, branchPoint];

    // Index innerhalb der Abstecher-Geometrie, der der Ladestation am
    // naechsten liegt - dort erfolgt der SoC-Farbsprung (Ankunft -> Ziel).
    let splitIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < detour.length; i++) {
      const dist = haversineDistanceM(detour[i], stop.position);
      if (dist < bestDist) {
        bestDist = dist;
        splitIdx = i;
      }
    }

    // `detour[0]` ist per Konstruktion bereits `branchPoint` (die Detour-
    // Route beginnt/endet dort) - nicht doppelt einfuegen.
    if (splitIdx === 0) {
      samples.push({
        distanzM: routeDistance + detourOffset,
        socPct: stop.ankunftsSocPct,
        critical: true,
      });
    }

    let prev = branchPoint;
    for (let i = 1; i < detour.length; i++) {
      const point = detour[i];
      detourOffset += haversineDistanceM(prev, point);
      coordinates.push(toLngLat(point));
      if (i === splitIdx) {
        samples.push({
          distanzM: routeDistance + detourOffset,
          socPct: stop.ankunftsSocPct,
          critical: true,
        });
      }
      prev = point;
    }
    samples.push({
      distanzM: routeDistance + detourOffset,
      socPct: stop.zielSocPct,
      critical: true,
    });
  };

  for (let i = 0; i < routeGeometrie.length; i++) {
    coordinates.push(toLngLat(routeGeometrie[i]));
    emitFrameSamplesUpTo(routeCum[i]);
    while (
      stopIdx < sortedStops.length &&
      sortedStops[stopIdx].distanzM <= routeCum[i]
    ) {
      spliceDetourAt(routeGeometrie[i], routeCum[i], sortedStops[stopIdx]);
      stopIdx++;
    }
  }

  // Nachzuegler: Frames/Ladehalte, deren Distanz ueber den letzten
  // Routenpunkt hinausgeht (sollte regulaer nicht vorkommen, Sicherheitsnetz
  // gegen Rundungsfehler an den Grenzen).
  emitFrameSamplesUpTo(Infinity);
  const lastRoutePoint = routeGeometrie[routeGeometrie.length - 1];
  const lastRouteDistance = routeCum[routeCum.length - 1];
  while (stopIdx < sortedStops.length) {
    spliceDetourAt(lastRoutePoint, lastRouteDistance, sortedStops[stopIdx]);
    stopIdx++;
  }

  return {
    coordinates,
    totalDistanceM: routeCum[routeCum.length - 1] + detourOffset,
    samples,
  };
}
