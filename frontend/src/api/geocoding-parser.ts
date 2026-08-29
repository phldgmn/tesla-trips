/** Parsing-Hilfsfunktionen für Nominatim-JSON-Antworten.
 *
 * Diese Module sind rein funktional und frei von I/O-Code — sie können
 * ohne Netzwerkanbindung getestet werden. */

/** Liest ein Feld aus einem unbekannten JSON-Wert, falls vorhanden und vom erwarteten Typ. */
function readStringField(value: object, field: string): string | null {
  if (field in value) {
    const raw = (value as Record<string, unknown>)[field];
    if (typeof raw === "string") return raw;
  }
  return null;
}

/** Ein Adressvorschlag aus der Geocoding-Suche. */
export interface GeocodeSuggestion {
  /** Menschenlesbare Adresse (formatiert). */
  label: string;
  /** Aufgelöste Koordinate [lat, lon]. */
  position: [number, number];
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

/** Liest einen einzelnen Nominatim-Ergebniseintrag in einen `GeocodeSuggestion`. */
export function parseSuggestion(entry: unknown): GeocodeSuggestion | null {
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
