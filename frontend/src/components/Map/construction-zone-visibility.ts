/** Mindestlaenge (Meter), ab der eine Baustelle bei einem gegebenen Zoom-Level
 * noch als Marker angezeigt wird. Je weiter herausgezoomt (kleinerer Zoom-Wert),
 * desto laenger/impactvoller muss eine Baustelle sein, um sichtbar zu bleiben -
 * verhindert, dass bei einer laengeren Route hunderte kleine Baustellen-Marker
 * die Karte zupflastern, waehrend beim Hineinzoomen (lokale Ansicht) auch
 * kurze Baustellen wieder auftauchen.
 *
 * Schwellen nach `minZoom` absteigend sortiert; der erste Eintrag, dessen
 * `minZoom` der aktuelle Zoom noch erreicht, bestimmt die Mindestlaenge. */
const ZOOM_MIN_LAENGE_THRESHOLDS: ReadonlyArray<{
  readonly minZoom: number;
  readonly minLaengeM: number;
}> = [
  { minZoom: 11, minLaengeM: 0 },
  { minZoom: 9, minLaengeM: 300 },
  { minZoom: 7, minLaengeM: 1000 },
  { minZoom: 5, minLaengeM: 5000 },
  { minZoom: 0, minLaengeM: 15000 },
];

/** Liefert die Mindestlaenge (Meter), die eine Baustelle bei `zoom` haben
 *  muss, um noch als Marker angezeigt zu werden (siehe
 *  `ZOOM_MIN_LAENGE_THRESHOLDS`). */
export function minConstructionZoneLaengeForZoom(zoom: number): number {
  for (const { minZoom, minLaengeM } of ZOOM_MIN_LAENGE_THRESHOLDS) {
    if (zoom >= minZoom) return minLaengeM;
  }
  // Unerreichbar, da der letzte Eintrag `minZoom: 0` jeden gueltigen
  // (nicht-negativen) Zoom-Wert abdeckt - Fallback nur fuer den
  // theoretischen Fall eines negativen Zoom-Werts.
  return ZOOM_MIN_LAENGE_THRESHOLDS[ZOOM_MIN_LAENGE_THRESHOLDS.length - 1]
    .minLaengeM;
}

/** Entscheidet, ob ein Baustellen-Marker bei gegebenem Zoom sichtbar sein
 *  soll. Baustellen ohne bekannte Laenge (`laengeM === null`) werden IMMER
 *  angezeigt, da ihr Impact nicht abschaetzbar ist und ein Verstecken sie
 *  faelschlich als "unwichtig" einstufen wuerde. */
export function isConstructionZoneVisibleAtZoom(
  laengeM: number | null,
  zoom: number,
): boolean {
  if (laengeM === null) return true;
  return laengeM >= minConstructionZoneLaengeForZoom(zoom);
}
