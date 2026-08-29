/** Zweck: Geometrie-/Projektions-Helfer der gespliceten Routenlinie sowie
 * Barrell-Re-Export der Splice- und Leg-Logik (siehe `route-splice.ts`,
 * `route-legs.ts`). Enthaelt den Anker-Typ `RouteSample` und die
 * Distanz-zu-Stuetzpunkt-Abbildung fuer den Routen-Hover-Tooltip.
 */
import { haversineDistanceM } from "./geo-utils";
export * from "./route-splice";
export * from "./route-legs";

/** Ein SoC-Stuetzpunkt fuer `buildSocGradientExpression`, positioniert per
 * kumulierter Distanz entlang der GESPLICETEN Linie (inkl. Abstecher-Laenge). */
export interface RouteSample {
  distanzM: number;
  socPct: number;
  /** Zeitpunkt (ISO) dieses Stuetzpunkts - fuer den Routen-Hover-Tooltip
   *  (siehe `findNearestRouteSample`/`buildRouteHoverText` in `Map.tsx`).
   *  Optional, da nicht jeder Aufrufer (z. B. reine SoC-Gradient-Tests) ihn
   *  benoetigt. */
  zeitpunkt?: string;
  /** Ladehalt-Ankunft/-Abfahrt: wird beim Downsampling nie uebersprungen, damit
   *  der SoC-Sprung an der Ladestation sichtbar bleibt. */
  critical?: boolean;
}

/** Meter pro Breitengrad - konstant genug fuer die kurzen Segmentabstaende
 *  einer dichten GraphHopper-Polyline (siehe `projectDistanceAlongLineM`). */
const METERS_PER_DEGREE_LAT = 111_320;

/** Projiziert `point` (lng, lat) auf die naechstgelegene Stelle der
 * gesplicete Linie `coordinates` (ebenfalls lng, lat) und liefert die
 * kumulierte Distanz (m) entlang der Linie bis zu dieser Projektion.
 *
 * Nutzt eine lokale ebene Naeherung (mit cos(lat)-Korrektur fuer die
 * Laengengrad-Verzerrung) statt einer Grosskreis-Projektion - fuer die
 * kurzen Segmentabstaende einer dichten GraphHopper-Polyline ausreichend
 * genau und deutlich einfacher als eine Grosskreis-Projektion pro Segment.
 *
 * Im Gegensatz zu einer reinen raeumlichen Naechster-Punkt-Suche ueber
 * `SimulationFrame.position` (die die Streckenreihenfolge ignoriert) nutzt
 * diese Funktion die tatsaechlich gezeichnete (gesplicete) Linie und liefert
 * die Distanz IN DEREN Reihenfolge. Das vermeidet Verwechslungen an Stellen,
 * an denen sich die Route raeumlich nahekommt (z. B. eine Autobahnabfahrt
 * nahe einem Ladehalt-Abstecher), obwohl die Punkte streckenmaessig weit
 * auseinanderliegen - der Bug, der zuvor dazu fuehrte, dass der Routen-
 * Hover-Tooltip einige Kilometer hinter einem Ladehalt noch die Vor-Lade-
 * Werte (Uhrzeit/SoC) anzeigte, weil der naechstgelegene `SimulationFrame`
 * raeumlich (nicht streckenmaessig) bestimmt wurde.
 *
 * Ein Ladehalt-Abstecher routet Hin- und Rueckweg oft als zwei SEPARAT
 * geroutete Beine ueber dieselbe (einzige) Zufahrtsstrasse zur Station
 * (siehe `_step_lade_detours_routen`) - Hin- und Rueckweg-Koordinaten sind
 * auf diesem gemeinsamen Stueck dann PIXELGENAU identisch. Bei einem
 * echten Gleichstand (Abstandsunterschied unterhalb `TIE_EPSILON_SQ_M2`)
 * gewinnt daher bewusst das SPAETER in `coordinates` liegende Segment: es
 * entspricht dem Rueckweg (nach dem Ladehalt), der beim Rendern zuletzt
 * gezeichnet wird und auf der Karte sichtbar obenauf liegt (siehe
 * `line-gradient`/`socToColor` - visuell die gruen eingefaerbte Haelfte).
 */
export function projectDistanceAlongLineM(
  coordinates: [number, number][],
  point: [number, number],
): number {
  if (coordinates.length === 0) return 0;
  // Zwei Kandidaten mit einem Abstandsunterschied unterhalb dieser Schwelle
  // (~5 m) gelten als raeumlicher Gleichstand.
  const TIE_EPSILON_SQ_M2 = 25;

  let cumulative = 0;
  let bestDistSq = Infinity;
  let bestCumulative = 0;
  for (let i = 0; i < coordinates.length - 1; i++) {
    const a = coordinates[i];
    const b = coordinates[i + 1];
    // Grosskreis-Laenge DIESES Segments (haversine, unabhaengig vom
    // Abfragepunkt) - damit sich Rundungsfehler ueber eine lange, viele
    // Breitengrade umspannende Route NICHT aufsummieren. Ein `cumulative`,
    // das stattdessen mit einer am Abfragepunkt verankerten cos(lat)-
    // Naeherung fuer JEDES Segment (auch weit entfernte) akkumuliert wurde,
    // driftete bei einer ~1500 km-Reise um mehrere Kilometer ab -
    // Ursache eines gemeldeten Bugs, bei dem der Routen-Hover-Tooltip nach
    // manchen Ladehalten weiterhin die Vor-Lade-Werte zeigte.
    const segLen = haversineDistanceM([a[1], a[0]], [b[1], b[0]]);

    // Lokale ebene Projektion NUR zur Bestimmung des naechstgelegenen
    // Segments und der Position `t` darauf - verankert an Segmentpunkt `a`
    // (nicht am Abfragepunkt), daher fuer diesen kurzen Abstand ausreichend
    // genau, unabhaengig davon, wie weit `point` selbst entfernt liegt.
    const cosLat = Math.cos((((a[1] + b[1]) / 2) * Math.PI) / 180);
    const toLocal = (p: [number, number]): [number, number] => [
      (p[0] - a[0]) * METERS_PER_DEGREE_LAT * cosLat,
      (p[1] - a[1]) * METERS_PER_DEGREE_LAT,
    ];
    const [bx, by] = toLocal(b);
    const [px, py] = toLocal(point);
    const segLenSq = bx * bx + by * by;
    let t = segLenSq > 0 ? (px * bx + py * by) / segLenSq : 0;
    t = Math.max(0, Math.min(1, t));
    const projX = t * bx;
    const projY = t * by;
    const dx = px - projX;
    const dy = py - projY;
    const distSq = dx * dx + dy * dy;
    const candidateCumulative = cumulative + t * segLen;

    if (
      distSq < bestDistSq - TIE_EPSILON_SQ_M2 ||
      (distSq <= bestDistSq + TIE_EPSILON_SQ_M2 &&
        candidateCumulative > bestCumulative)
    ) {
      bestDistSq = Math.min(bestDistSq, distSq);
      bestCumulative = candidateCumulative;
    }
    cumulative += segLen;
  }
  return bestCumulative;
}

/** Findet den `RouteSample`, dessen `distanzM` (kumulierte Distanz entlang
 * der gesplicete Linie) am naechsten an `distanzM` liegt - per Binaersuche,
 * da `samples` nach `distanzM` aufsteigend sortiert ist (siehe
 * `buildSplicedRoute`). Genutzt zusammen mit `projectDistanceAlongLineM` fuer
 * den Routen-Hover-Tooltip: die Mausposition wird auf die gezeichnete Linie
 * projiziert, die resultierende Distanz-entlang-der-Linie dann hier auf den
 * naechstgelegenen Stuetzpunkt (mit Zeitpunkt + SoC) abgebildet. */
export function findNearestRouteSample(
  samples: RouteSample[],
  distanzM: number,
): RouteSample | undefined {
  if (samples.length === 0) return undefined;
  let lo = 0;
  let hi = samples.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].distanzM < distanzM) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  if (
    lo > 0 &&
    Math.abs(samples[lo - 1].distanzM - distanzM) <
      Math.abs(samples[lo].distanzM - distanzM)
  ) {
    return samples[lo - 1];
  }
  return samples[lo];
}
