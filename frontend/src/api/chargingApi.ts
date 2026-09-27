/** API client for the supercharger endpoints. */

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
  is_24_7: boolean;
  date_opened: string | null;
}

export interface SuperchargerPricingTier {
  tier_label: string;
  time_label: string | null;
  currency: string;
  amount: number;
  unit: "kWh" | "min";
  idle_fee_text: string | null;
}

export interface SuperchargerPricing {
  slug: string;
  tiers: SuperchargerPricingTier[];
  updated_utc: string | null;
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

/** Fetch all supercharger stations from the database. */
export async function fetchSuperchargers(
  country?: string,
): Promise<SuperchargerStation[]> {
  const params = country ? `?country=${encodeURIComponent(country)}` : "";
  const response = await fetch(`/api/superchargers${params}`);
  if (!response.ok) {
    throw new ChargingApiError(
      `Error fetching superchargers (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<SuperchargerStation[]>;
}

/** Fetch details for a single supercharger station. */
export async function fetchSuperchargerDetail(
  slug: string,
): Promise<SuperchargerStation> {
  const response = await fetch(
    `/api/superchargers/${encodeURIComponent(slug)}`,
  );
  if (!response.ok) {
    if (response.status === 404) {
      throw new ChargingApiError("Station not found", 404);
    }
    throw new ChargingApiError(
      `Error fetching station (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<SuperchargerStation>;
}

/** Refresh a supercharger station with fresh data from the Tesla API.
 *
 *  The fetch runs entirely server-side (backend uses system curl with
 *  browser TLS fingerprint to bypass the Akamai WAF block). A
 *  direct cross-origin fetch from the frontend is not an option: the
 *  Tesla API does not send an Access-Control-Allow-Origin header, causing the
 *  browser to reject reading the response regardless of WAF status.
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

/** Read cached pricing data for a station without fetching it again. */
export async function fetchSuperchargerPricing(
  slug: string,
): Promise<SuperchargerPricing> {
  const response = await fetch(
    `/api/superchargers/${encodeURIComponent(slug)}/pricing`,
  );
  if (!response.ok) {
    throw new ChargingApiError(
      `Error fetching pricing data (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<SuperchargerPricing>;
}

/** Scrapes current pricing data for a station from Tesla and stores it.
 *
 *  Server-side via `TeslaClient` like `refreshSupercharger`, to bypass
 *  the Akamai WAF block and missing CORS headers from the Tesla API.
 */
export async function refreshSuperchargerPricing(
  slug: string,
): Promise<SuperchargerPricing> {
  const response = await fetch(
    `/api/superchargers/${encodeURIComponent(slug)}/refresh-pricing`,
    { method: "POST" },
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new ChargingApiError(
      detail || `Error updating pricing (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<SuperchargerPricing>;
}
