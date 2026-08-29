export type StopRole = "start" | "end" | "middle";

/** Bestimmt die Rolle eines Stopps anhand seines Index und der Gesamtzahl. */
export function stopRole(index: number, total: number): StopRole {
  if (index === 0) return "start";
  if (index === total - 1) return "end";
  return "middle";
}

/** Liefert die Markerfarbe (CSS-Hex) für eine StopRole. */
export function roleToMarkerColor(role: StopRole): string {
  switch (role) {
    case "start":
      return "#22c55e"; // grün
    case "end":
      return "#ef4444"; // rot
    case "middle":
      return "#3b82f6"; // blau
  }
}

/** Anzeige-Label für einen Stopp in Popup/Header (Rolle als Fallback). */
export function roleToLabel(role: StopRole): string {
  switch (role) {
    case "start":
      return "Start";
    case "end":
      return "Ziel";
    case "middle":
      return "Stop";
  }
}

/** Kürzel für die Markerdarstellung: A (Start), B (Ziel), Punkt sonst. */
export function roleToMarkerGlyph(role: StopRole): string {
  switch (role) {
    case "start":
      return "A";
    case "end":
      return "B";
    case "middle":
      return "●";
  }
}

/** Erzeugt ein gestyltes DOM-Element für einen Stopp-Marker. Start/Ziel
 *  behalten ihr Buchstaben-Kürzel (A/B); Zwischenstopps bekommen ein
 *  Pin-Icon statt eines reinen Punkts, damit der Marker auch dann klar als
 *  Ort erkennbar bleibt, wenn er zusätzlich Aufenthalts-Details trägt (siehe
 *  `buildStopPopupHtml`). BEWUSST kein inline `position`/`z-index` hier -
 *  das durchkreuzt MapLibres eigene Transform-basierte Positionierung des
 *  Marker-Elements und liess Marker beim Zoomen von ihrer Koordinate
 *  abdriften (siehe `raiseStopMarkersToTop` fuer die stattdessen genutzte,
 *  rein DOM-Reihenfolge-basierte Stapelung ueber Baustellen-/Ladehalt-
 *  Markern). */
export function buildMarkerElement(role: StopRole): HTMLElement {
  const el = document.createElement("div");
  const color = roleToMarkerColor(role);
  const isMiddle = role === "middle";
  if (isMiddle) {
    el.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="#ffffff" style="pointer-events:none;">
      <path d="M12 2C8.14 2 5 5.14 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.86-3.14-7-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/>
    </svg>`;
  } else {
    el.textContent = roleToMarkerGlyph(role);
  }
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:28px",
    "height:28px",
    "border-radius:50%",
    `background-color:${color}`,
    "border:2px solid #ffffff",
    "color:#ffffff",
    "font-weight:700",
    "font-size:14px",
    "line-height:1",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:grab",
  ]
    .concat(isMiddle ? [] : ["font-family:system-ui,sans-serif"])
    .join(";");
  return el;
}

/** Erzeugt ein gestyltes DOM-Element fuer einen Ladehalt-Marker (Blitz-Symbol).
 *
 * Ein Marker pro tatsaechlichem Ladehalt (`TripSimulationResult.charging_stops`),
 * nicht pro Simulationsframe - eine Ladepause erzeugt sonst mehrere
 * `zustand === "LADEN"`-Frames, die andernfalls zu mehreren, entlang der
 * Strecke verteilten Markern statt eines einzigen an der Ladestation fuehren
 * wuerden.
 */
export function buildChargingStopMarkerElement(): HTMLElement {
  const el = document.createElement("div");
  el.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="#ffffff" style="pointer-events:none;">
    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
  </svg>`;
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:28px",
    "height:28px",
    "border-radius:50%",
    "background-color:#f59e0b",
    "border:2px solid #ffffff",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:pointer",
  ].join(";");
  return el;
}

/** Erzeugt ein gestyltes DOM-Element fuer einen Baustellen-Marker
 * (Warndreieck-Symbol), ein Eintrag pro `TripSimulationResult.construction_zones`.
 * Optisch bewusst kleiner und farblich abgesetzt von Ladehalt-Markern
 * (`buildChargingStopMarkerElement`), damit beide Markertypen auf einen
 * Blick unterscheidbar bleiben. */
export function buildConstructionZoneMarkerElement(): HTMLElement {
  const el = document.createElement("div");
  el.innerHTML = `<svg viewBox="0 0 24 24" width="12" height="12" fill="#1f2937" style="pointer-events:none;">
    <path d="M12 2L1 21h22L12 2zm0 5.5L18.5 19h-13L12 7.5z"/>
    <rect x="11" y="11" width="2" height="5" />
    <rect x="11" y="17" width="2" height="2" />
  </svg>`;
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:20px",
    "height:20px",
    "border-radius:50%",
    "background-color:#fbbf24",
    "border:2px solid #ffffff",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:pointer",
  ].join(";");
  return el;
}

/** Erzeugt Popup-HTML für eine ConstructionZone: ein Abschnitt pro Event.
 *
 *  Einzelne Events werden wie zuvor gerendert; bei mehreren Events (gemerged)
 *  erzeugt jeder Event einen eigenen, optisch abgesetzten Abschnitt mit
 *  "Baustelle N von M"-Überschrift.
 */
