/** Adress-Geocoding über die öffentliche OpenStreetMap-Nominatim-API.
 *
 * Ersetzt manuelle Lat/Lon-Eingabe durch Adresssuche ("Vorwärts-Geocoding":
 * Adresstext → Koordinate) sowie Rückwärts-Auflösung eines Kartenklicks in
 * eine lesbare Adresse ("Reverse-Geocoding": Koordinate → Adresstext).
 *
 * Nominatim-Nutzungsrichtlinie (https://operations.osmfoundation.org/policies/nominatim/):
 * max. 1 Anfrage/Sekunde, aussagekräftiger Referer/User-Agent (der Browser
 * sendet den Referer automatisch). Aufrufer MÜSSEN Anfragen debouncen
 * (siehe `TripPlannerForm.tsx`) und laufende Anfragen bei neuer Eingabe
 * abbrechen (`AbortController`), um die Richtlinie einzuhalten.
 */

const NOMINATIM_BASE = "https://nominatim.openstreetmap.org";
const MIN_QUERY_LENGTH = 3;

/** Ein Adressvorschlag aus der Geocoding-Suche. */
export interface GeocodeSuggestion {
  /** Menschenlesbare Adresse (formatiert). */
  label: string;
  /** Aufgelöste Koordinate [lat, lon]. */
  position: [number, number];
}

/** Liest ein Feld aus einem unbekannten JSON-Wert, falls vorhanden und vom erwarteten Typ. */
function readStringField(value: object, field: string): string | null {
  if (field in value) {
    const raw = (value as Record<string, unknown>)[field];
    if (typeof raw === "string") return raw;
  }
  return null;
}

/**
 * Formatierungshilfe für Nominatim-Adressen mit addressdetails=1.
 * Liefert "Hauptstraße 8, 12345 Musterstadt, Germany" etc.
 */
export function formatDisplayAddress(entry: object, fallback: string): string {
  if (!entry || typeof entry !== "object") return fallback;
  const address = (entry as Record<string, unknown>).address;
  if (!address || typeof address !== "object") return fallback;

  const addr = address as Record<string, unknown>;
  const road = readStringField(addr, "road");
  const houseNumber = readStringField(addr, "house_number");
  const postcode = readStringField(addr, "postcode");

  // City fallback chain: city → town → village → municipality → county
  const city =
    readStringField(addr, "city") ??
    readStringField(addr, "town") ??
    readStringField(addr, "village") ??
    readStringField(addr, "municipality") ??
    readStringField(addr, "county");

  const country = readStringField(addr, "country");

  // streetLine: road alone, or "road house_number"
  const streetLine = road
    ? houseNumber
      ? `${road} ${houseNumber}`
      : road
    : undefined;

  // cityLine: "postcode city" (both optional, join with space if present)
  const cityLine =
    postcode && city ? `${postcode} ${city}` : (postcode ?? city ?? undefined);

  // Join non-undefined parts with ", "
  const parts = [streetLine, cityLine, country].filter(Boolean) as string[];
  return parts.length > 0 ? parts.join(", ") : fallback;
}

function parseSuggestion(entry: unknown): GeocodeSuggestion | null {
  if (!entry || typeof entry !== "object") return null;
  const latRaw = readStringField(entry, "lat");
  const lonRaw = readStringField(entry, "lon");
  const labelRaw = readStringField(entry, "display_name");
  if (latRaw === null || lonRaw === null || labelRaw === null) return null;
  const lat = Number(latRaw);
  const lon = Number(lonRaw);
  if (Number.isNaN(lat) || Number.isNaN(lon)) return null;

  // Use formatDisplayAddress for label, fallback to raw display_name
  const label = formatDisplayAddress(entry, labelRaw ?? "");
  if (label === "") return null; // formatDisplayAddress returned empty fallback

  return { label, position: [lat, lon] };
}

/** Sucht Adressen, die zu `query` passen. Liefert `[]` für zu kurze Anfragen
 *  oder wenn Nominatim nichts findet. Wirft bei Netzwerk-/HTTP-Fehlern
 *  (außer Abbruch durch `signal`). */
export async function searchAddress(
  query: string,
  signal?: AbortSignal,
): Promise<GeocodeSuggestion[]> {
  const trimmed = query.trim();
  if (trimmed.length < MIN_QUERY_LENGTH) return [];

  const url = `${NOMINATIM_BASE}/search?format=jsonv2&addressdetails=1&limit=5&q=${encodeURIComponent(trimmed)}`;
  const response = await fetch(url, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Adresssuche fehlgeschlagen (Status ${response.status})`);
  }
  const data: unknown = await response.json();
  if (!Array.isArray(data)) return [];

  const suggestions: GeocodeSuggestion[] = [];
  for (const entry of data) {
    const suggestion = parseSuggestion(entry);
    if (suggestion) suggestions.push(suggestion);
  }
  return suggestions;
}

/** Löst eine Koordinate in eine lesbare Adresse auf (z. B. nach Kartenklick/Drag).
 *  Liefert `null`, wenn keine Adresse gefunden wurde oder die Anfrage fehlschlägt
 *  (Aufrufer sollte dann auf die rohen Koordinaten als Anzeigetext zurückfallen). */
export async function reverseGeocode(
  position: [number, number],
  signal?: AbortSignal,
): Promise<string | null> {
  const [lat, lon] = position;
  const url = `${NOMINATIM_BASE}/reverse?format=jsonv2&addressdetails=1&lat=${lat}&lon=${lon}`;
  try {
    const response = await fetch(url, {
      signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    const data: unknown = await response.json();
    if (!data || typeof data !== "object") return null;
    const rawDisplayName = readStringField(data, "display_name");
    const formatted = formatDisplayAddress(data, rawDisplayName ?? "");
    // If no display_name AND formatDisplayAddress also found nothing, return null
    if (rawDisplayName === null && formatted === "") return null;
    return formatted;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    return null;
  }
}
