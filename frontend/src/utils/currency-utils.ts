/** Formatierung von Preisen/Waehrungen für die Kartendarstellung und
 *  Reisezusammenfassung (`Map.tsx`, `TripSummary.tsx`). */

/** Formatiert einen Preis in der angegebenen ISO-4217-Währung mit deutscher
 *  Locale (z. B. "10,00 €" oder "40,00 DKK"). */
export function formatCost(amount: number, currency: string): string {
  try {
    return amount.toLocaleString("de-DE", { style: "currency", currency });
  } catch {
    // Unbekannter/nicht standardkonformer Waehrungscode - roher Fallback
    // statt einer geworfenen Exception in der Render-Funktion.
    return `${amount.toLocaleString("de-DE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} ${currency}`;
  }
}

/** Formatiert den Preis eines Ladehalts, oder "–", falls keine Preisdaten
 *  für die Station gecacht sind (`ChargingStop.estimated_cost === null`).
 *  Behandelt `undefined` wie `null` - robust gegenüber Aufrufern, die die
 *  neueren, optionalen Preisfelder (noch) nicht mitliefern. */
export function formatCostOrDash(
  amount: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (amount == null || currency == null) return "–";
  return formatCost(amount, currency);
}
