/** Mindestlaenge (Meter), ab der eine Baustelle bei einem gegebenen Zoom-Level
 * noch als Marker angezeigt wird. Je weiter herausgezoomt (kleinerer Zoom-Wert),
 * desto laenger/impactvoller muss eine Baustelle sein, um sichtbar zu bleiben -
 * verhindert, dass bei einer laengeren Route hunderte kleine Baustellen-Marker
 * die Karte zupflastern, waehrend beim Hineinzoomen (lokale Ansicht) auch
 * kurze Baustellen wieder auftauchen.
 *
 * Schwellen nach `minZoom` absteigend sortiert; der erste Eintrag, dessen
 * `minZoom` der aktuelle Zoom noch erreicht, bestimmt die Mindestlaenge. */
const ZOOM_MIN_LENGTH_THRESHOLDS: ReadonlyArray<{
  readonly minZoom: number;
  readonly minLengthM: number;
}> = [
  { minZoom: 11, minLengthM: 0 },
  { minZoom: 9, minLengthM: 300 },
  { minZoom: 7, minLengthM: 1000 },
  { minZoom: 5, minLengthM: 5000 },
  { minZoom: 0, minLengthM: 15000 },
];

/** Return the minimum length (meters) a construction zone at `zoom` must
 *  have to still be displayed as a marker (see
 *  `ZOOM_MIN_LENGTH_THRESHOLDS`). */
export function minConstructionZoneLengthForZoom(zoom: number): number {
  for (const {
    minZoom,
    minLengthM: minLengthM,
  } of ZOOM_MIN_LENGTH_THRESHOLDS) {
    if (zoom >= minZoom) return minLengthM;
  }
  // Unerreichbar, da der letzte Eintrag `minZoom: 0` jeden gueltigen
  // (nicht-negativen) Zoom-Wert abdeckt - Fallback nur fuer den
  // theoretischen Fall eines negativen Zoom-Werts.
  return ZOOM_MIN_LENGTH_THRESHOLDS[ZOOM_MIN_LENGTH_THRESHOLDS.length - 1]
    .minLengthM;
}

/** Decide whether a construction zone marker should be visible at a given zoom.
 *  Construction zones without a known length (`lengthM === null`) are ALWAYS
 *  displayed since their impact is not assessable and hiding them
 *  would falsely classify them as "unimportant". */
export function isConstructionZoneVisibleAtZoom(
  lengthM: number | null,
  zoom: number,
): boolean {
  if (lengthM === null) return true;
  return lengthM >= minConstructionZoneLengthForZoom(zoom);
}
