/** Currency conversion service using frankfurter.dev API.
 *
 * Pure conversion logic, delegating rate fetches to `./frankfurter`.
 */

import { getRates } from "./frankfurter";

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
export { clearRateCache } from "./frankfurter";
