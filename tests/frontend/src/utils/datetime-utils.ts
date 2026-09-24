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
export function formatTimestamp(iso: string | null): string {
  if (iso === null) return "–";
  try {
    return new Date(iso).toLocaleString("de-DE", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return "–";
  }
}

/** Formatiert einen ISO-Zeitstempel als reine Uhrzeit (z. B. "19:17"), ohne
 *  Datum - für die überhängenden Zeit-Badges in `TripPlannerForm` (siehe
 *  Route-Timeline), die dank des Tageswechsel-Trenners (`istTageswechsel`)
 *  kein Datum mehr pro Eintrag benötigen. */
export function formatTime(iso: string | null): string {
  if (iso === null) return "–";
  try {
    return new Date(iso).toLocaleTimeString("de-DE", { timeStyle: "short" });
  } catch {
    return "–";
  }
}

/** Formatiert einen ISO-Zeitstempel als knappes Datum mit Wochentag (z. B.
 *  "So., 16.08.") - für den Tageswechsel-Trenner in der Route-Timeline, der
 *  bewusst klein gehalten wird (siehe `istTageswechsel`). */
export function formatShortDate(iso: string | null): string {
  try {
    if (iso === null) return "–";
    return new Date(iso).toLocaleDateString("de-DE", {
      weekday: "short",
      day: "2-digit",
      month: "2-digit",
    });
  } catch {
    return "–";
  }
}

/** Formatiert einen ISO-Zeitstempel als knappes Datum OHNE Wochentag (z. B.
 *  "18.08.") - kompakter als `formatDatumKurz`, für die Datums-Ergänzung an
 *  einem Zeit-Badge, wenn Ankunft und Abfahrt DESSELBEN Eintrags auf
 *  unterschiedliche Kalendertage fallen (siehe `istTageswechsel`). Steht
 *  dort inline neben Uhrzeit und SoC, ein voller Wochentag wäre zu lang. */
export function formatDayMonth(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("de-DE", {
      day: "2-digit",
      month: "2-digit",
    });
  } catch {
    return "";
  }
}

/** Formatiert einen ISO-Zeitstempel als knappes Datum+Uhrzeit (z. B.
 *  "29.08. 17:15") - fuer den Routen-Hover-Tooltip (`buildRouteHoverText`
 *  in `popups.ts`), der als knapper Cursor-Tooltip absichtlich kein volles
 *  Datum (`formatZeitpunkt`) traegt. `null` (kein bekannter Zeitpunkt am
 *  Streckenpunkt) ergibt "unbekannt", analog zu `formatUhrzeit`. */
export function formatShortDateTime(iso: string | null): string {
  if (iso === null) return "unbekannt";
  const date = formatDayMonth(iso);
  const time = formatTime(iso);
  return date ? `${date} ${time}` : time;
}

/** Extrahiert den Kalendertag-Schlüssel (YYYY-MM-DD) aus einem naiven,
 *  zeitzonenlosen ISO-Zeitstempel. Reines String-Slicing statt
 *  `Date`-Parsing, um Zeitzonen-Verschiebungen bei der Tagesgrenze
 *  auszuschließen (siehe Datei-Docstring: naive lokale ISO-Strings). */
export function isoDateKey(iso: string): string {
  return iso.slice(0, 10);
}

/** Prüft, ob zwischen zwei chronologisch aufeinanderfolgenden Zeitpunkten
 *  ein Kalendertag-Wechsel liegt - für den Tageswechsel-Trenner in der
 *  Route-Timeline (`TripPlannerForm`). `null` (kein bekannter Zeitpunkt)
 *  ergibt nie einen Tageswechsel, damit unbekannte Zeiten keine
 *  Trenner-Flut auslösen. */
export function isDayChange(
  previousIso: string | null,
  currentIso: string | null,
): boolean {
  if (previousIso === null || currentIso === null) return false;
  return isoDateKey(previousIso) !== isoDateKey(currentIso);
}
