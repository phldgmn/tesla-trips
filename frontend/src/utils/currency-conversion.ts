/** Currency conversion service using frankfurter.dev API.
 *
 * Fetches latest exchange rates and converts amounts to EUR.
 * Caches rates in localStorage with a 24h TTL to minimize API calls.
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
async function fetchLatestRates(): Promise<Record<string, number>> {
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
async function getRates(): Promise<Record<string, number>> {
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

/** Converts an amount from source currency to EUR.
 * Returns the original amount if currency is already EUR or conversion fails.
 */
export async function convertToEUR(
  amount: number,
  currency: string,
): Promise<number> {
  if (currency === "EUR") return amount;

  try {
    const rates = await getRates();
    const rate = rates[currency];
    if (rate == null) {
      console.error(
        `No exchange rate found for ${currency}, using original amount`,
      );
      return amount;
    }
    // rate is EUR -> quote (e.g., 1 EUR = 7.46 DKK)
    // So to convert DKK to EUR: amount / rate
    return amount / rate;
  } catch (error) {
    console.error(`Currency conversion failed for ${currency}:`, error);
    return amount; // Fallback to original amount
  }
}

/** Converts multiple currency amounts to EUR and returns the total.
 * Also returns the individual converted amounts for display.
 */
export async function convertAllToEUR(
  entries: Array<{ currency: string; amount: number }>,
): Promise<{
  totalEUR: number;
  breakdown: Array<{
    currency: string;
    originalAmount: number;
    eurAmount: number;
  }>;
}> {
  const breakdown = await Promise.all(
    entries.map(async (entry) => {
      const eurAmount = await convertToEUR(entry.amount, entry.currency);
      return {
        currency: entry.currency,
        originalAmount: entry.amount,
        eurAmount,
      };
    }),
  );
  const totalEUR = breakdown.reduce((sum, b) => sum + b.eurAmount, 0);
  return { totalEUR, breakdown };
}

/** Clears the cached exchange rates (useful for testing or manual refresh). */
export function clearRateCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY);
  } catch {
    // Ignore
  }
}
