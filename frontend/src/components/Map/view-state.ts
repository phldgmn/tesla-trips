/** Von der Karte persistierter Kartenausschnitt (Mittelpunkt + Zoomstufe). */
export interface MapViewState {
  center: [number, number];
  zoom: number;
}

/** Ausschnitt beim allerersten Laden (kein `localStorage`-Wert vorhanden):
 * Welt-Ansicht, wie zuvor fest im `Map`-Konstruktor verdrahtet. */
export const DEFAULT_MAP_VIEW: MapViewState = { center: [0, 0], zoom: 2 };

/** Validiert einen aus `localStorage` wiederhergestellten Kartenausschnitt.
 * Schützt vor einem inkompatiblen/beschädigten Alt-Wert (z. B. nach
 * manueller Bearbeitung der DevTools oder einem künftigen Schema-Wechsel),
 * der sonst MapLibre beim Initialisieren mit NaN/Infinity abstürzen ließe. */
export function isValidMapViewState(value: unknown): value is MapViewState {
  if (typeof value !== "object" || value === null) return false;
  const { center, zoom } = value as Record<string, unknown>;
  return (
    Array.isArray(center) &&
    center.length === 2 &&
    center.every((c) => typeof c === "number" && Number.isFinite(c)) &&
    typeof zoom === "number" &&
    Number.isFinite(zoom)
  );
}
