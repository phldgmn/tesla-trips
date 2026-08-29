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
import type { RouteSample } from "./route-line";

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
  /** Zeitpunkt (ISO), an dem der Ladehalt tatsaechlich erreicht/verlassen wird -
   *  fuer den Routen-Hover-Tooltip (siehe `findNearestRouteSample` in
   *  `Map.tsx`), damit der Sprung von Ankunfts- zu Abfahrtszeit exakt am
   *  Ladehalt liegt statt am naechstgelegenen (raeumlich verwechselbaren)
   *  Fahr-Frame. Optional, da nicht jeder Aufrufer (z. B. reine SoC-
   *  Gradient-Tests) ihn benoetigt. */
  ankunftszeit?: string;
  abfahrtszeit?: string;
}

export interface SplicedRoute {
  /** Fertige Koordinatenliste fuer die GeoJSON-LineString, in [lng, lat] */
  coordinates: [number, number][];
  /** Gesamtlaenge der gespliceten Linie in Metern (Hauptroute + alle Abstecher) */
  totalDistanceM: number;
  samples: RouteSample[];
  /** Ein Eintrag pro Ladehalt, an der Stelle (Koordinaten-Index + kumulierte
   *  Distanz), an der die Ladestation tatsaechlich erreicht wird - Grundlage
   *  fuer `splitRouteIntoLegs()`. Sortiert nach `distanzM`. */
  legBoundaries: { coordinateIndex: number; distanzM: number }[];
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
  /** Kumulierte Distanz entlang der (ungespliceten) Hauptroute, an der
   *  tatsaechlich abgebogen/geladen wird (`ChargingDetourInput.distanzM`) -
   *  NICHT identisch mit `startIdx`/`endIdx`, die per `margin_m`-Puffer
   *  (siehe `_finde_klammerpunkte`) bis zu 3 km VOR/NACH diesem Punkt
   *  liegen. Trennt beim Einspleissen Vor-Ladehalt- von Nach-Ladehalt-
   *  Fahr-Frames, die beide noch innerhalb dieses Puffers liegen koennen -
   *  siehe `buildSplicedRoute`. */
  chargeDistanzM: number;
  ankunftsSocPct: number;
  zielSocPct: number;
  ankunftszeit?: string;
  abfahrtszeit?: string;
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
        chargeDistanzM: stop.distanzM,
        ankunftsSocPct: stop.ankunftsSocPct,
        zielSocPct: stop.zielSocPct,
        ankunftszeit: stop.ankunftszeit,
        abfahrtszeit: stop.abfahrtszeit,
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
      chargeDistanzM: stop.distanzM,
      ankunftsSocPct: stop.ankunftsSocPct,
      zielSocPct: stop.zielSocPct,
      ankunftszeit: stop.ankunftszeit,
      abfahrtszeit: stop.abfahrtszeit,
    };
  });
}

export function buildSplicedRoute(
  routeGeometrie: [number, number][],
  chargingStops: ChargingDetourInput[],
  frameSamples: { distanzM: number; socPct: number; zeitpunkt?: string }[],
): SplicedRoute {
  if (routeGeometrie.length === 0) {
    return {
      coordinates: [],
      totalDistanceM: 0,
      samples: [],
      legBoundaries: [],
    };
  }

  const routeCum = cumulativeDistancesM(routeGeometrie);
  const detours = resolveDetours(routeGeometrie, routeCum, chargingStops);
  const sortedFrames = [...frameSamples].sort(
    (a, b) => a.distanzM - b.distanzM,
  );

  const coordinates: [number, number][] = [];
  const samples: RouteSample[] = [];
  const legBoundaries: { coordinateIndex: number; distanzM: number }[] = [];
  let frameIdx = 0;
  let detourIdx = 0;
  // Kumulierte Differenz (gesplicete Distanz - urspruengliche Routendistanz)
  // an der aktuellen Position, durch bereits eingefuegte Abstecher.
  let offset = 0;
  let i = 0;

  const emitPlainFrame = (
    distanzM: number,
    socPct: number,
    zeitpunkt?: string,
  ) => {
    samples.push({ distanzM: distanzM + offset, socPct, zeitpunkt });
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

      // Index innerhalb der Detour-Geometrie, an dem die Ladestation
      // tatsaechlich erreicht wird - dort erfolgt der SoC-Farbsprung
      // (Ankunft -> Ziel). Bevorzugt der vom Backend exakt gelieferte Index
      // (siehe `ResolvedDetour.stationIndex`/`_step_lade_detours_routen`);
      // eine Naechster-Punkt-Suche waere bei Autobahnkreuzen mit nah
      // beieinander liegenden Rampen unzuverlaessig (findet ggf. eine andere,
      // geometrisch nahe aber tatsaechlich andere Rampe). Wird VOR der
      // Frame-Interpolation unten gebraucht, um Vor-/Nach-Ladehalt-Frames
      // richtig zu trennen.
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
      const stationDetourDistanzM = detourCum[splitIdx];

      // Erkennen, ob der Ladehalt am Abzweigpunkt (routeIndexVor) liegt.
      // In diesem Fall entspricht der gesamte ersetzte Hauptroutensegment
      // [rangeStartOriginal, rangeEndOriginal] dem kompletten Detour-Rundkurs
      // (Hin- + Rueckweg), nicht nur dem Rueckweg. Alle Frames in diesem
      // Bereich sind Phantom-Frames und werden uebersprungen.
      const BRANCH_POINT_THRESHOLD_M = 3000;
      const isChargeAtBranchPoint =
        Math.abs(detour.chargeDistanzM - rangeStartOriginal) <=
        BRANCH_POINT_THRESHOLD_M;

      // Frames, die (durch die Zeit-Diskretisierung der Simulation) noch VOR
      // dem tatsaechlichen Abzweigpunkt, aber bereits innerhalb des per
      // `margin_m`-Puffer ersetzten Bereichs liegen (siehe
      // `_finde_klammerpunkte`, bis zu 3 km VOR/NACH dem eigentlichen
      // Ladehalt), anteilig auf die Detour-Laenge legen statt sie fallen zu
      // lassen. WICHTIG: getrennt nach Vor-/Nach-Ladehalt anhand
      // `chargeDistanzM` (dem tatsaechlichen Abzweigpunkt) interpolieren -
      // eine einzige Interpolation ueber den GESAMTEN (bis zu 6 km breiten)
      // Puffer wuerde Vor-Ladehalt-Frames mit niedrigem SoC teils HINTER den
      // SoC-Sprung an der Station projizieren (sichtbar als falsche SoC-
      // Werte im Routen-Hover-Tooltip einige Kilometer nach dem Ladehalt).
      //
      // Klassifizierung per `zeitpunkt`: Frames mit zeitpunkt < ankunftszeit
      // sind Vor-Ladehalt-Frames (gehen auf den Hinweg). Frames mit
      // zeitpunkt > abfahrtszeit sind Nach-Ladehalt-Frames (gehen auf den
      // Rueckweg oder werden nach dem Detour verarbeitet). Frames ohne
      // zeitpunkt fallen fallback-maessig auf die distanzM-Klassifizierung
      // zurueck (<= chargeDistanzM = Hinweg, > chargeDistanzM = Rueckweg).
      //
      // Sonderfall: Ladehalt am Abzweigpunkt (isChargeAtBranchPoint).
      // Dann ist der gesamte Bereich [rangeStartOriginal, rangeEndOriginal]
      // Phantom-Bereich - alle Frames darin werden uebersprungen.
      const ankunftsZeit = detour.ankunftszeit
        ? new Date(detour.ankunftszeit).getTime()
        : null;
      const abfahrtsZeit = detour.abfahrtszeit
        ? new Date(detour.abfahrtszeit).getTime()
        : null;
      while (
        frameIdx < sortedFrames.length &&
        sortedFrames[frameIdx].distanzM <= rangeEndOriginal
      ) {
        const frame = sortedFrames[frameIdx];
        const frameZeit = frame.zeitpunkt
          ? new Date(frame.zeitpunkt).getTime()
          : null;

        // Sonderfall: Ladehalt am Abzweigpunkt -> gesamter Pufferbereich ist Phantom
        if (isChargeAtBranchPoint && frame.distanzM >= rangeStartOriginal) {
          // Frame im ersetzten Segment -> ueberspringen (Phantom-Frame)
          frameIdx++;
          continue;
        }

        // Nur Frames in der Naehe von chargeDistanzM (margin_m ~ 3 km) per zeitpunkt
        // klassifizieren. Weiter entfernte Frames werden rein per distanzM
        // einsortiert, da ihre zeitpunkt-Daten auf dem ersetzten Hauptroutensegment
        // nicht verlässlich sind (Phantom-Frames).
        const CLASSIFICATION_THRESHOLD_M = 5000;
        const distFromCharge = frame.distanzM - detour.chargeDistanzM;
        const useTimeClassification =
          frameZeit !== null &&
          Math.abs(distFromCharge) <= CLASSIFICATION_THRESHOLD_M;
        const isPreCharge = useTimeClassification
          ? frameZeit < (ankunftsZeit ?? Infinity)
          : frame.distanzM <= detour.chargeDistanzM;
        const isPostCharge = useTimeClassification
          ? frameZeit > (abfahrtsZeit ?? -Infinity)
          : frame.distanzM > detour.chargeDistanzM;
        // Schwelle für Nach-Ladehalt-Frames auf dem Rueckweg: nur Frames nah an
        // chargeDistanzM (innerhalb von ~5 km) werden auf den Rueckweg interpoliert;
        // weiter entfernte Frames sind Phantom-Frames und werden uebersprungen.
        // Schwelle für Nach-Ladehalt-Frames auf dem Rueckweg: nur Frames nah an
        // chargeDistanzM (innerhalb von ~5 km NACH dem Ladehalt) werden auf den
        // Rueckweg interpoliert; Frames VOR chargeDistanzM mit post-charging
        // zeitpunkt sind Phantom-Frames (Simulation lief auf Hauptroute weiter)
        // Schwelle fuer Nach-Ladehalt-Frames auf dem Rueckweg: nur Frames nah an
        // chargeDistanzM (innerhalb von ~5 km) werden auf den Rueckweg interpoliert;
        // weiter entfernte Frames sind Phantom-Frames und werden uebersprungen.
        const POST_CHARGE_THRESHOLD_M = 5000;
        const isPostChargeNear =
          isPostCharge &&
          distFromCharge > 0 && // exclude exact chargeDistanzM to avoid duplicate arrival sample
          distFromCharge <= POST_CHARGE_THRESHOLD_M;

        if (frame.distanzM < rangeStartOriginal) {
          // Frame liegt vor dem Pufferbereich -> normal emittieren, aber
          // Frames mit zeitpunkt NACH Ankunft (ankunftsZeit) sind
          // Phantom-Frames (Simulation lief auf Hauptroute weiter, waehrend
          // Auto schon abbiegt/laedt) und werden uebersprungen.
          const isAfterArrival =
            frameZeit !== null && ankunftsZeit !== null
              ? frameZeit > ankunftsZeit
              : false;
          if (isPostCharge || isAfterArrival) {
            frameIdx++;
          } else {
            emitPlainFrame(frame.distanzM, frame.socPct, frame.zeitpunkt);
            frameIdx++;
          }
        } else if (
          isPreCharge &&
          !isPostCharge &&
          frame.distanzM < detour.chargeDistanzM
        ) {
          // Pre-Ladehalt-Frame: alle auf den Start des Detours legen, damit die
          // vorherige, niedrige SoC-Spanne bis zur Station korrekt angezeigt wird.
          // Es wird KEINE Interpolation auf dem Hinweg vorgenommen, da das zu
          // falschen hoch-SoC-Segmenten direkt vor der Ladestation führen würde
          // (siehe Issue mit 44% SoC vor dem Charger).
          samples.push({
            distanzM: rangeStartOriginal + offset,
            socPct: frame.socPct,
            zeitpunkt: frame.zeitpunkt,
          });
          frameIdx++;
        } else if (isPostChargeNear) {
          // Nach-Ladehalt-Frame nahe am Ladehalt: auf Rueckweg interpolieren
          const inboundSpan = rangeEndOriginal - detour.chargeDistanzM;
          const frac =
            inboundSpan > 0
              ? (frame.distanzM - detour.chargeDistanzM) / inboundSpan
              : 0;
          samples.push({
            distanzM:
              rangeStartOriginal +
              offset +
              stationDetourDistanzM +
              frac * (detourLen - stationDetourDistanzM),
            socPct: frame.socPct,
            zeitpunkt: frame.zeitpunkt,
          });
          frameIdx++;
        } else if (isPostCharge) {
          // Post-Stop-Frame: entweder auf Rueckweg (echter Lade-Detour) oder direkt
          // emittieren (Stopover ohne echten Detour). Unterscheidung: Wenn der
          // Rueckweg sehr kurz ist (detourLen - stationDetourDistanzM < 100 m),
          // ist es kein echter Lade-Detour, sondern ein Stopover, und Frames
          // sollen direkt emittiert werden (siehe Issue mit 100% SoC nach Stop).
          const returnLegLen = detourLen - stationDetourDistanzM;
          if (returnLegLen < 100) {
            // Stopover ohne echten Rueckweg: Frame direkt emittieren
            emitPlainFrame(frame.distanzM, frame.socPct, frame.zeitpunkt);
            frameIdx++;
          } else {
            // echter Lade-Detour mit Rueckweg
            // Phantom-Frame auf dem ersetzten Hauptroutensegment. NICHT hier verarbeiten, sondern nach
            // dem Detour-Block mit korrektem Offset emittieren.
            // frameIdx NICHT inkrementieren, damit der Frame im naechsten
            // Schleifendurchlauf (nach dem Detour) wieder gesehen wird.
            break;
          }
        } else {
          // Frame ohne eindeutige Zeit-Zuordnung (kein zeitpunkt oder genau im
          // Ladezeitfenster) -> ueberspringen, da er keiner realen Fahrt
          // entspricht (Phantom-Frame auf Hauptroute zwischen Abzweig und
          // Wiedereinstieg).
          frameIdx++;
        }
      }
      coordinates.push(toLngLat(detour.detour[0]));
      const arrivalDistanzM =
        rangeStartOriginal + offset + stationDetourDistanzM;
      const emitChargeJump = (coordinateIndex: number) => {
        samples.push({
          distanzM: arrivalDistanzM,
          socPct: detour.ankunftsSocPct,
          zeitpunkt: detour.ankunftszeit,
          critical: true,
        });
        samples.push({
          distanzM: arrivalDistanzM + CHARGE_JUMP_EPSILON_M,
          socPct: detour.zielSocPct,
          zeitpunkt: detour.abfahrtszeit,
          critical: true,
        });
        legBoundaries.push({ coordinateIndex, distanzM: arrivalDistanzM });
      };
      if (splitIdx === 0) {
        emitChargeJump(coordinates.length - 1);
      }
      for (let k = 1; k < detour.detour.length; k++) {
        coordinates.push(toLngLat(detour.detour[k]));
        if (k === splitIdx) {
          emitChargeJump(coordinates.length - 1);
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
        sortedFrames[frameIdx].zeitpunkt,
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
      sortedFrames[frameIdx].zeitpunkt,
    );
    frameIdx++;
  }

  // `samples` wird NICHT zwangslaeufig in aufsteigender `distanzM`-Reihenfolge
  // befuellt: die Ankunfts-/Abfahrts-Stuetzpunkte eines Ladehalts (siehe
  // `emitChargeJump` oben) werden erst NACH den innerhalb des margin_m-
  // Puffers interpolierten Vor-/Nach-Ladehalt-Frames gepusht, liegen
  // distanzM-maessig aber DAZWISCHEN. Ohne diese Sortierung waere jede
  // binaere Suche ueber `samples` (siehe `findNearestRouteSample`) undefiniert.
  samples.sort((a, b) => a.distanzM - b.distanzM);

  return {
    coordinates,
    totalDistanceM: routeCum[routeCum.length - 1] + offset,
    samples,
    legBoundaries,
  };
}
