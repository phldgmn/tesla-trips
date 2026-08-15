/** Gemeinsame Datum/Zeit-Hilfsfunktionen für die Reiseplanung.
 *
 * Alle Zeitstempel sind naive lokale ISO-8601-Strings ohne Zeitzone
 * (`YYYY-MM-DDTHH:mm:ss`), konsistent mit der Backend-Konvention
 * (`datetime.fromisoformat` in `src/tripplanner/trip_input/cli.py`).
 */

/** Kombiniert ein Datums-String (YYYY-MM-DD) und Zeit-String (HH:mm) zu ISO-8601 ohne Zeitzone. */
export function combineDateTimeToIso(date: string, time: string): string {
  return `${date}T${time}:00`;
}

/**
 * Extrahiert Datum (YYYY-MM-DD) und Zeit (HH:mm) aus einem ISO-String ohne Zeitzone.
 * Erwartet Format: "YYYY-MM-DDTHH:mm:ss" (Sekunden optional).
 */
export function splitIsoToDateTime(iso: string): {
  date: string;
  time: string;
} {
  const [date, time] = iso.split("T");
  return { date, time: (time ?? "00:00").slice(0, 5) };
}

/**
 * Erzeugt einen Standard-ISO-String für "heute, nächste volle Stunde".
 * Beispiel: 2026-08-15T09:00:00
 */
export function getDefaultDepartureIso(): string {
  const now = new Date();
  const date = now.toISOString().split("T")[0]; // YYYY-MM-DD
  const nextHour = now.getHours() + 1;
  const time = `${String(nextHour % 24).padStart(2, "0")}:00`;
  return combineDateTimeToIso(date, time);
}

/** Formatiert einen ISO-Zeitstempel für die deutsche Locale, oder "unbekannt"/
 *  "ungültig" bei fehlendem/ungültigem Wert. Gemeinsam genutzt von
 *  `TripSummary` (Zeitplan) und `TripPlannerForm` (Route/Fähren/Ladehalte). */
export function formatZeitpunkt(iso: string | null): string {
  if (iso === null) return "unbekannt";
  try {
    return new Date(iso).toLocaleString("de-DE", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return "ungültig";
  }
}
