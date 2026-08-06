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
  /** Menschenlesbare Adresse (Nominatim `display_name`). */
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

function parseSuggestion(entry: unknown): GeocodeSuggestion | null {
  if (!entry || typeof entry !== "object") return null;
  const latRaw = readStringField(entry, "lat");
  const lonRaw = readStringField(entry, "lon");
  const label = readStringField(entry, "display_name");
  if (latRaw === null || lonRaw === null || label === null) return null;
  const lat = Number(latRaw);
  const lon = Number(lonRaw);
  if (Number.isNaN(lat) || Number.isNaN(lon)) return null;
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

  const url = `${NOMINATIM_BASE}/search?format=jsonv2&addressdetails=0&limit=5&q=${encodeURIComponent(trimmed)}`;
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
  const url = `${NOMINATIM_BASE}/reverse?format=jsonv2&lat=${lat}&lon=${lon}`;
  try {
    const response = await fetch(url, {
      signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    const data: unknown = await response.json();
    if (!data || typeof data !== "object") return null;
    return readStringField(data, "display_name");
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    return null;
  }
}
