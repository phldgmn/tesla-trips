/** Frankfurter API client and rate caching layer.
 *
 * Fetches exchange rates from frankfurter.dev, caches them in localStorage
 * with a 24h TTL.
 */

const CACHE_KEY = "frankfurter_rates";
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const FRANKFURTER_API = "https://api.frankfurter.dev/v2/rates";

interface CachedRates {
  rates: Record<string, number>;
  timestamp: number;
  base: string;
}

interface RateResponse {
  date: string;
  base: string;
  quote: string;
  rate: number;
}

/** Fetches latest exchange rates from frankfurter.dev API.
 * Returns rates with base=EUR (e.g., { USD: 1.08, DKK: 7.46, ... }).
 */
export async function fetchLatestRates(): Promise<Record<string, number>> {
  const response = await fetch(`${FRANKFURTER_API}?base=EUR`);
  if (!response.ok) {
    throw new Error(`Failed to fetch rates: ${response.status}`);
  }
  const data: RateResponse[] = await response.json();
  const rates: Record<string, number> = { EUR: 1 };
  for (const entry of data) {
    if (entry.quote !== entry.base) {
      rates[entry.quote] = entry.rate;
    }
  }
  return rates;
}

/** Gets cached rates or fetches fresh ones if cache is expired/missing. */
export async function getRates(): Promise<Record<string, number>> {
  try {
    const cached = localStorage.getItem(CACHE_KEY);
    if (cached) {
      const parsed: CachedRates = JSON.parse(cached);
      const age = Date.now() - parsed.timestamp;
      if (age < CACHE_TTL_MS && parsed.base === "EUR") {
        return parsed.rates;
      }
    }
  } catch {
    // Ignore cache errors, fall through to fetch
  }

  const rates = await fetchLatestRates();
  try {
    localStorage.setItem(
      CACHE_KEY,
      JSON.stringify({ rates, timestamp: Date.now(), base: "EUR" }),
    );
  } catch {
    // Ignore localStorage errors (e.g., private browsing)
  }
  return rates;
}

/** Clears the cached exchange rates (useful for testing or manual refresh). */
export function clearRateCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY);
  } catch {
    // Ignore
  }
}
