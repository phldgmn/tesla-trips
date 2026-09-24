/** Geodätische Utility-Funktionen für das Visualization-Frontend.
 *
 * WICHTIG: Alle Backend-Modelle liefern Koordinaten als (lat, lon).
 * MapLibre GL JS erwartet jedoch [lng, lat] für GeoJSON und setLngLat().
 * Diese Datei enthält die EXAKT EINE Konvertierungsfunktion an der
 * Rendering-Grenze – nirgends sonst im Frontend wird die Reihenfolge
 * manipuliert.
 */

/** Konvertiert eine Backend-Koordinate (lat, lon) in MapLibre-Reihenfolge [lng, lat]. */
export function toLngLat([lat, lon]: [number, number]): [number, number] {
  return [lon, lat];
}

/** Berechnet die Distanz zwischen zwei Punkten auf der Erde (Haversine-Formel).
 *
 * @param a Erster Punkt als [lat, lon]
 * @param b Zweiter Punkt als [lat, lon]
 * @returns Distanz in Metern
 */
export function haversineDistanceM(
  a: [number, number],
  b: [number, number],
): number {
  const R = 6371_000; // Erdradius in Metern
  const [lat1, lon1] = a;
  const [lat2, lon2] = b;

  const φ1 = (lat1 * Math.PI) / 180;
  const φ2 = (lat2 * Math.PI) / 180;
  const Δφ = ((lat2 - lat1) * Math.PI) / 180;
  const Δλ = ((lon2 - lon1) * Math.PI) / 180;

  const cosφ1 = Math.cos(φ1);
  const cosφ2 = Math.cos(φ2);

  const aHav = Math.sin(Δφ / 2) ** 2 + cosφ1 * cosφ2 * Math.sin(Δλ / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(aHav), Math.sqrt(1 - aHav));

  return R * c;
}

/** Berechnet das Bearing (Vorwärtsazimut) von Punkt a zu Punkt b. */
export function bearingDeg(a: [number, number], b: [number, number]): number {
  const [lat1, lon1] = a;
  const [lat2, lon2] = b;

  const Δλ = ((lon2 - lon1) * Math.PI) / 180;
  const φ1 = (lat1 * Math.PI) / 180;
  const φ2 = (lat2 * Math.PI) / 180;

  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x =
    Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  const θ = Math.atan2(y, x);

  return ((θ * 180) / Math.PI + 360) % 360;
}

/** Interpoliert eine Position entlang einer Linie.
 *
 * @param points Array von Punkten als [lat, lon]
 * @param progress Wert zwischen 0 und 1 (0 = Start, 1 = Ende)
 * @returns Interpolierte Position als [lat, lon]
 */
export function interpolatePosition(
  points: [number, number][],
  progress: number,
): [number, number] {
  if (points.length === 0) {
    throw new Error("Points array ist leer");
  }
  if (points.length === 1) {
    return points[0];
  }

  // Normalize progress
  const p = Math.max(0, Math.min(1, progress));

  // Special case: progress = 1 should return the last point
  if (p === 1) {
    return points[points.length - 1];
  }

  // Calculate total segments and find the right segment
  const totalSegments = points.length - 1;
  const segmentProgress = p * totalSegments;
  const segmentIndex = Math.floor(segmentProgress);
  const segmentLocalProgress = segmentProgress - segmentIndex;

  // Clamp segmentIndex
  const i = Math.min(segmentIndex, totalSegments - 1);

  const [lat1, lon1] = points[i];
  const [lat2, lon2] = points[i + 1];

  // Linear interpolation
  const lat = lat1 + (lat2 - lat1) * segmentLocalProgress;
  const lon = lon1 + (lon2 - lon1) * segmentLocalProgress;

  return [lat, lon];
}
