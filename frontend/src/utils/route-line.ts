/** Baut die auf der Karte gezeichnete Routenlinie aus der vollen Streckengeometrie
 * plus echten, geroutete Abstechern zu Ladestationen (siehe `ChargingStop.detour_geometrie`,
 * `tripplanner.trip_input.api._step_lade_detours_routen`).
 *
 * Ohne diese Splice-Logik zeigt die Linie den Ladehalt entweder gar nicht (reine
 * `route_geometrie`, die GraphHopper ohne Ladestationen als Wegpunkte berechnet)
 * oder - mit der alten frame-basierten Linie - nur eine grobe Luftlinie zur
 * Station. `buildSplicedRoute()` ERSETZT den Streckenabschnitt zwischen den vom
 * Backend gelieferten Klammerpunkten (`route_index_vor`/`route_index_nach`,
 * siehe `_finde_klammerpunkte`) durch die echte Abstecher-Geometrie - anhand
 * fester Indizes, nicht per Distanz-/Naechster-Punkt-Suche, da Backend und
 * Frontend exakt dasselbe `route_geometrie`-Array erhalten und die Indizes
 * daher 1:1 gueltig sind. Liefert passend dazu SoC-Stuetzpunkte (inkl. eines
 * garantierten Ankunfts-/Abfahrts-Farbsprungs an jeder Station), die
 * `buildSocGradientExpression` (siehe Map.tsx) in eine MapLibre
 * `line-gradient`-Expression umwandelt.
 */

import { toLngLat, haversineDistanceM } from "./geo-utils";

/** Minimaler Distanz-Abstand (m) zwischen dem Ankunfts- und dem
 * Abfahrts-Stuetzpunkt an einer Ladestation. Das Laden selbst dauert in der
 * Kartendarstellung "keine" Fahrstrecke - der SoC-Sprung soll direkt an der
 * Station sichtbar sein statt ueber die gesamte Rueckfahrt der Detour-Schleife
 * verschmiert zu werden (siehe `buildSocGradientExpression`). Der Wert muss nur
 * gross genug sein, damit beide Stuetzpunkte nach der `line-progress`-Normierung
 * unterscheidbare Fortschrittswerte erhalten (siehe dortiger `progress <=
 * lastProgress`-Dedup).
 */
const CHARGE_JUMP_EPSILON_M = 0.5;

/** Ein Ladehalt-Abstecher, wie ihn `buildSplicedRoute` zum Einfuegen braucht. */
export interface ChargingDetourInput {
  /** Position der Ladestation als [lat, lon] (Ziel des Fallback-Abstechers,
   *  falls `detourGeometrie` leer ist; auch fuer den SoC-Ankunfts-/Abfahrts-
   *  Split innerhalb einer echten Detour-Geometrie genutzt) */
  position: [number, number];
  /** Kumulierte Distanz entlang der Hauptroute, an der abgebogen wird -
   *  bestimmt nur die REIHENFOLGE der Ladehalte, nicht die Spleiss-Position
   *  (dafuer werden `routeIndexVor`/`routeIndexNach` genutzt) */
  distanzM: number;
  /** Echte, ueber GraphHopper geroutete Geometrie von `routeIndexVor` ueber die
   *  Station zu `routeIndexNach` (leer = Fallback auf eine Luftlinie zur Station) */
  detourGeometrie: [number, number][];
  /** Index in `detourGeometrie`, an dem die Ladestation tatsaechlich erreicht
   *  wird - vom Backend exakt ermittelt (zwei separat geroutete Hin-/Rueckweg-
   *  Beine statt eines Via-Punkt-Requests, siehe `_step_lade_detours_routen`),
   *  statt es hier per Naechster-Punkt-Heuristik zu schaetzen, die bei
   *  Autobahnkreuzen mit nah beieinander liegenden Rampen fehlschlagen kann
   *  (null, falls `detourGeometrie` leer ist) */
  stationIndex: number | null;
  /** Index in `routeGeometrie`, ab dem `detourGeometrie` die Hauptroute ersetzt
   *  (null, falls `detourGeometrie` leer ist) */
  routeIndexVor: number | null;
  /** Index in `routeGeometrie`, bis zu dem (inklusive) `detourGeometrie` die
   *  Hauptroute ersetzt (null, falls `detourGeometrie` leer ist) */
  routeIndexNach: number | null;
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

/** Naechster Index in einer nach `routeCum` aufsteigend sortierten Distanzliste
 * per Binaersuche - fuer den Fallback-Fall (kein echtes Detour-Routing
 * verfuegbar), in dem kein Index vom Backend vorliegt. */
function nearestIndexByDistance(
  routeCum: number[],
  targetDistanceM: number,
): number {
  let lo = 0;
  let hi = routeCum.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (routeCum[mid] < targetDistanceM) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

/** Ein auf konkrete `routeGeometrie`-Indizes aufgeloester Ladehalt-Abstecher,
 * bereit zum Einspleissen. */
interface ResolvedDetour {
  /** Index in `routeGeometrie`, ab dem ersetzt wird (inklusive) */
  startIdx: number;
  /** Index in `routeGeometrie`, bis zu dem ersetzt wird (inklusive) */
  endIdx: number;
  /** Ersetzende Geometrie; `detour[0]`/`detour[detour.length-1]` liegen nahe
   *  bei `routeGeometrie[startIdx]`/`routeGeometrie[endIdx]` */
  detour: [number, number][];
  /** Index in `detour`, an dem die Ladestation tatsaechlich erreicht wird -
   *  vom Backend exakt ermittelt, oder `null` um auf eine Naechster-Punkt-Suche
   *  zurueckzufallen (siehe `buildSplicedRoute`) */
  stationIndex: number | null;
  stationPosition: [number, number];
  ankunftsSocPct: number;
  zielSocPct: number;
}

function resolveDetours(
  routeGeometrie: [number, number][],
  routeCum: number[],
  stops: ChargingDetourInput[],
): ResolvedDetour[] {
  const sorted = [...stops].sort((a, b) => a.distanzM - b.distanzM);
  return sorted.map((stop) => {
    if (
      stop.routeIndexVor !== null &&
      stop.routeIndexNach !== null &&
      stop.detourGeometrie.length >= 2
    ) {
      return {
        startIdx: stop.routeIndexVor,
        endIdx: stop.routeIndexNach,
        detour: stop.detourGeometrie,
        stationIndex: stop.stationIndex,
        stationPosition: stop.position,
        ankunftsSocPct: stop.ankunftsSocPct,
        zielSocPct: stop.zielSocPct,
      };
    }
    // Fallback: keine geroutete Geometrie verfuegbar (siehe
    // `_step_lade_detours_routen`) - einzelner Punkt als Luftlinien-Abstecher.
    const idx = nearestIndexByDistance(routeCum, stop.distanzM);
    return {
      startIdx: idx,
      endIdx: idx,
      detour: [routeGeometrie[idx], stop.position, routeGeometrie[idx]],
      // Der mittlere Punkt IST die Station - hier bekannt, keine Suche noetig.
      stationIndex: 1,
      stationPosition: stop.position,
      ankunftsSocPct: stop.ankunftsSocPct,
      zielSocPct: stop.zielSocPct,
    };
  });
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
  const detours = resolveDetours(routeGeometrie, routeCum, chargingStops);
  const sortedFrames = [...frameSamples].sort(
    (a, b) => a.distanzM - b.distanzM,
  );

  const coordinates: [number, number][] = [];
  const samples: RouteSample[] = [];
  let frameIdx = 0;
  let detourIdx = 0;
  // Kumulierte Differenz (gesplicete Distanz - urspruengliche Routendistanz)
  // an der aktuellen Position, durch bereits eingefuegte Abstecher.
  let offset = 0;
  let i = 0;

  const emitPlainFrame = (distanzM: number, socPct: number) => {
    samples.push({ distanzM: distanzM + offset, socPct });
  };

  while (i < routeGeometrie.length) {
    // Ueberlappende/bereits ueberholte Detours (sollte bei realistischem
    // Ladehalt-Abstand nicht vorkommen) sicherheitshalber ueberspringen,
    // statt die Kartendarstellung zu verlieren.
    while (detourIdx < detours.length && detours[detourIdx].startIdx < i) {
      detourIdx++;
    }

    const detour = detours[detourIdx];
    if (detour && detour.startIdx === i) {
      const rangeStartOriginal = routeCum[detour.startIdx];
      const rangeEndOriginal = routeCum[detour.endIdx];
      const detourCum = cumulativeDistancesM(detour.detour);
      const detourLen = detourCum[detourCum.length - 1];

      // Frames, die (durch die Zeit-Diskretisierung der Simulation) noch VOR
      // dem tatsaechlichen Abzweigpunkt, aber bereits innerhalb des ersetzten
      // Bereichs liegen, anteilig auf die Detour-Laenge legen statt sie
      // fallen zu lassen.
      while (
        frameIdx < sortedFrames.length &&
        sortedFrames[frameIdx].distanzM <= rangeEndOriginal
      ) {
        const frame = sortedFrames[frameIdx];
        if (frame.distanzM < rangeStartOriginal) {
          emitPlainFrame(frame.distanzM, frame.socPct);
        } else {
          const span = rangeEndOriginal - rangeStartOriginal;
          const frac =
            span > 0 ? (frame.distanzM - rangeStartOriginal) / span : 0;
          samples.push({
            distanzM: rangeStartOriginal + offset + frac * detourLen,
            socPct: frame.socPct,
          });
        }
        frameIdx++;
      }

      // Index innerhalb der Detour-Geometrie, an dem die Ladestation
      // tatsaechlich erreicht wird - dort erfolgt der SoC-Farbsprung
      // (Ankunft -> Ziel). Bevorzugt der vom Backend exakt gelieferte Index
      // (siehe `ResolvedDetour.stationIndex`/`_step_lade_detours_routen`);
      // eine Naechster-Punkt-Suche waere bei Autobahnkreuzen mit nah
      // beieinander liegenden Rampen unzuverlaessig (findet ggf. eine andere,
      // geometrisch nahe aber tatsaechlich andere Rampe).
      let splitIdx =
        detour.stationIndex !== null &&
        detour.stationIndex >= 0 &&
        detour.stationIndex < detour.detour.length
          ? detour.stationIndex
          : -1;
      if (splitIdx === -1) {
        let bestDist = Infinity;
        splitIdx = 0;
        for (let k = 0; k < detour.detour.length; k++) {
          const dist = haversineDistanceM(
            detour.detour[k],
            detour.stationPosition,
          );
          if (dist < bestDist) {
            bestDist = dist;
            splitIdx = k;
          }
        }
      }

      coordinates.push(toLngLat(detour.detour[0]));
      const arrivalDistanzM = rangeStartOriginal + offset + detourCum[splitIdx];
      const emitChargeJump = () => {
        samples.push({
          distanzM: arrivalDistanzM,
          socPct: detour.ankunftsSocPct,
          critical: true,
        });
        samples.push({
          distanzM: arrivalDistanzM + CHARGE_JUMP_EPSILON_M,
          socPct: detour.zielSocPct,
          critical: true,
        });
      };
      if (splitIdx === 0) {
        emitChargeJump();
      }
      for (let k = 1; k < detour.detour.length; k++) {
        coordinates.push(toLngLat(detour.detour[k]));
        if (k === splitIdx) {
          emitChargeJump();
        }
      }

      offset += detourLen - (rangeEndOriginal - rangeStartOriginal);
      i = detour.endIdx + 1;
      detourIdx++;
      continue;
    }

    coordinates.push(toLngLat(routeGeometrie[i]));
    while (
      frameIdx < sortedFrames.length &&
      sortedFrames[frameIdx].distanzM <= routeCum[i]
    ) {
      emitPlainFrame(
        sortedFrames[frameIdx].distanzM,
        sortedFrames[frameIdx].socPct,
      );
      frameIdx++;
    }
    i++;
  }

  // Sicherheitsnetz: Frames knapp jenseits des letzten Routenpunkts (z. B.
  // durch Rundung) noch aufnehmen statt zu verlieren.
  while (frameIdx < sortedFrames.length) {
    emitPlainFrame(
      sortedFrames[frameIdx].distanzM,
      sortedFrames[frameIdx].socPct,
    );
    frameIdx++;
  }

  return {
    coordinates,
    totalDistanceM: routeCum[routeCum.length - 1] + offset,
    samples,
  };
}
