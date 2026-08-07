/** API-Client fuer die Supercharger-Endpunkte. */

export interface SuperchargerStation {
  slug: string;
  name: string;
  latitude: number;
  longitude: number;
  country: string;
  total_stalls: number;
  power_kilowatt: number;
  status: string;
  stalls_v2: number;
  stalls_v3: number;
  stalls_v3_ultra: number;
  stalls_v4: number;
  ist_24_7: boolean;
  date_opened: string | null;
}

class ChargingApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ChargingApiError";
  }
}

/** Holt alle Supercharger-Stationen aus der Datenbank. */
export async function fetchSuperchargers(
  country?: string,
): Promise<SuperchargerStation[]> {
  const params = country ? `?country=${encodeURIComponent(country)}` : "";
  const response = await fetch(`/api/superchargers${params}`);
  if (!response.ok) {
    throw new ChargingApiError(
      `Fehler beim Abruf der Supercharger (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<SuperchargerStation[]>;
}

/** Holt Details zu einer einzelnen Supercharger-Station. */
export async function fetchSuperchargerDetail(
  slug: string,
): Promise<SuperchargerStation> {
  const response = await fetch(
    `/api/superchargers/${encodeURIComponent(slug)}`,
  );
  if (!response.ok) {
    if (response.status === 404) {
      throw new ChargingApiError("Station nicht gefunden", 404);
    }
    throw new ChargingApiError(
      `Fehler beim Abruf der Station (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<SuperchargerStation>;
}

/** Aktualisiert eine Supercharger-Station mit frischen Daten von der Tesla API.
 *
 *  Der Abruf laeuft komplett server-seitig (Backend nutzt System-curl mit
 *  Browser-TLS-Fingerprint, um den Akamai-WAF-Block zu umgehen). Ein
 *  direkter Cross-Origin-Fetch aus dem Frontend ist keine Option: die
 *  Tesla-API liefert keinen Access-Control-Allow-Origin-Header, wodurch der
 *  Browser das Lesen der Antwort unabhaengig vom WAF-Status verweigert.
 */
export async function refreshSupercharger(
  slug: string,
): Promise<SuperchargerStation> {
  const response = await fetch(
    `/api/superchargers/${encodeURIComponent(slug)}/refresh`,
    { method: "POST" },
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new ChargingApiError(
      detail || `Fehler bei der Aktualisierung (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<SuperchargerStation>;
}
