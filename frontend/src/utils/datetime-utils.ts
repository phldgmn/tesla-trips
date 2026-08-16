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

/** Formatiert einen ISO-Zeitstempel als reine Uhrzeit (z. B. "19:17"), ohne
 *  Datum - für die überhängenden Zeit-Badges in `TripPlannerForm` (siehe
 *  Route-Timeline), die dank des Tageswechsel-Trenners (`istTageswechsel`)
 *  kein Datum mehr pro Eintrag benötigen. */
export function formatUhrzeit(iso: string | null): string {
  if (iso === null) return "unbekannt";
  try {
    return new Date(iso).toLocaleTimeString("de-DE", { timeStyle: "short" });
  } catch {
    return "ungültig";
  }
}

/** Formatiert einen ISO-Zeitstempel als knappes Datum mit Wochentag (z. B.
 *  "So., 16.08.") - für den Tageswechsel-Trenner in der Route-Timeline, der
 *  bewusst klein gehalten wird (siehe `istTageswechsel`). */
export function formatDatumKurz(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("de-DE", {
      weekday: "short",
      day: "2-digit",
      month: "2-digit",
    });
  } catch {
    return "";
  }
}

/** Extrahiert den Kalendertag-Schlüssel (YYYY-MM-DD) aus einem naiven,
 *  zeitzonenlosen ISO-Zeitstempel. Reines String-Slicing statt
 *  `Date`-Parsing, um Zeitzonen-Verschiebungen bei der Tagesgrenze
 *  auszuschließen (siehe Datei-Docstring: naive lokale ISO-Strings). */
export function isoDatumSchluessel(iso: string): string {
  return iso.slice(0, 10);
}

/** Prüft, ob zwischen zwei chronologisch aufeinanderfolgenden Zeitpunkten
 *  ein Kalendertag-Wechsel liegt - für den Tageswechsel-Trenner in der
 *  Route-Timeline (`TripPlannerForm`). `null` (kein bekannter Zeitpunkt)
 *  ergibt nie einen Tageswechsel, damit unbekannte Zeiten keine
 *  Trenner-Flut auslösen. */
export function istTageswechsel(
  vorherigeIso: string | null,
  aktuelleIso: string | null,
): boolean {
  if (vorherigeIso === null || aktuelleIso === null) return false;
  return isoDatumSchluessel(vorherigeIso) !== isoDatumSchluessel(aktuelleIso);
}
