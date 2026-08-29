import type {
  SuperchargerPricing,
  SuperchargerStation,
} from "../../api/chargingApi";

/** Zustand eines Preis-Refresh-Vorgangs fuer eine einzelne Station. */
export type PricingRefreshState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; pricing: SuperchargerPricing }
  | { status: "error"; message: string; pricing?: SuperchargerPricing };

/** Formatiert einen `SuperchargerPricingTier` als lesbare Preiszeile,
 *  z. B. "0.42 EUR/kWh" oder "0.39 EUR/kWh (16:00 - 20:00)". */
function formatPricingTier(tier: SuperchargerPricing["tiers"][number]): string {
  const base = `${tier.amount.toFixed(2)} ${tier.currency}/${tier.unit}`;
  return tier.time_label ? `${base} (${tier.time_label})` : base;
}

/** Baut ein DOM-Element mit Preistabelle + Refresh-Aktion fuer eine Station,
 *  wiederverwendet sowohl im generischen Supercharger-Popover
 *  (`buildSuperchargerPopoverElement`) als auch im Ladehalt-Popup einer
 *  Route (`popups.ts::buildChargingStopPopupElement`). `refreshLabel` und
 *  `emptyLabel` erlauben kontextabhaengigen Text (Popover vs. Ladehalt).
 */
export function buildPricingSection(
  state: PricingRefreshState,
  onRefresh: () => void,
  options?: { refreshLabel?: string; emptyLabel?: string },
): HTMLElement {
  const refreshLabel =
    options?.refreshLabel ?? "\u{1F504} Preise von Tesla abrufen";
  const emptyLabel = options?.emptyLabel ?? "Keine Preisdaten vorhanden.";

  const section = document.createElement("div");
  section.style.cssText =
    "margin-top:8px;padding-top:6px;border-top:1px solid #e5e7eb;";

  if (state.status === "loading") {
    const spinner = document.createElement("span");
    spinner.style.cssText = "color:#666;font-style:italic;";
    spinner.textContent = "Preise werden abgerufen\u2026";
    section.appendChild(spinner);
    return section;
  }

  const loadedPricing =
    state.status === "loaded"
      ? state.pricing
      : state.status === "error"
        ? state.pricing
        : undefined;

  if (state.status === "error") {
    const errorBanner = document.createElement("div");
    errorBanner.style.cssText =
      "color:#ef4444;font-size:12px;margin-bottom:6px;";
    errorBanner.textContent = `Fehler: ${state.message}`;
    section.appendChild(errorBanner);
  }

  if (loadedPricing) {
    if (loadedPricing.tiers.length > 0) {
      const table = document.createElement("table");
      table.style.cssText =
        "width:100%;border-collapse:collapse;margin-bottom:6px;";
      for (const tier of loadedPricing.tiers) {
        const tr = document.createElement("tr");
        const tdLabel = document.createElement("td");
        tdLabel.style.cssText = "padding:2px 4px;color:#666;";
        tdLabel.textContent = tier.tier_label;
        tr.appendChild(tdLabel);
        const tdValue = document.createElement("td");
        tdValue.style.cssText = "padding:2px 4px;text-align:right;";
        tdValue.textContent = formatPricingTier(tier);
        tr.appendChild(tdValue);
        table.appendChild(tr);
      }
      section.appendChild(table);
    } else {
      const empty = document.createElement("div");
      empty.style.cssText = "color:#666;font-size:12px;margin-bottom:6px;";
      empty.textContent = emptyLabel;
      section.appendChild(empty);
    }
    if (loadedPricing.updated_utc) {
      const updated = document.createElement("div");
      updated.style.cssText = "color:#999;font-size:11px;margin-bottom:6px;";
      updated.textContent = `Aktualisiert: ${new Date(loadedPricing.updated_utc).toLocaleString("de-DE")}`;
      section.appendChild(updated);
    }
  }

  const btn = document.createElement("button");
  btn.style.cssText =
    "padding:4px 12px;background:#2563eb;color:white;border:none;border-radius:4px;cursor:pointer;font-size:12px;";
  btn.textContent = refreshLabel;
  btn.onclick = onRefresh;
  section.appendChild(btn);

  return section;
}

/** Baut ein DOM-Element fuer ein Supercharger-Popover mit Refresh-Button. */
export function buildSuperchargerPopoverElement(
  station: SuperchargerStation,
  isRefreshing: boolean,
  onRefresh: () => void,
  pricing?: PricingRefreshState,
  onRefreshPricing?: () => void,
): HTMLElement {
  const statusColor =
    station.status === "OPEN"
      ? "#22c55e"
      : station.status === "TEMP_CLOSED"
        ? "#ef4444"
        : "#f59e0b";

  const container = document.createElement("div");
  container.style.cssText =
    "font-family:system-ui,sans-serif;font-size:13px;min-width:200px;";

  const title = document.createElement("strong");
  title.style.cssText = "font-size:14px;";
  title.textContent = station.name;
  container.appendChild(title);

  const statusRow = document.createElement("div");
  statusRow.style.cssText =
    "margin:6px 0 4px;display:flex;gap:6px;align-items:center;";
  const dot = document.createElement("span");
  dot.style.cssText = `display:inline-block;width:8px;height:8px;border-radius:50%;background:${statusColor};`;
  statusRow.appendChild(dot);
  const statusText = document.createElement("span");
  statusText.textContent = station.status;
  statusRow.appendChild(statusText);
  container.appendChild(statusRow);

  const table = document.createElement("table");
  table.style.cssText = "width:100%;border-collapse:collapse;";
  const rows: [string, string][] = [
    [
      "Stalls",
      `${station.total_stalls} (V2:${station.stalls_v2} V3:${station.stalls_v3} V4:${station.stalls_v4})`,
    ],
    ["Leistung", `${station.power_kilowatt} kW`],
    ["24/7", station.ist_24_7 ? "Ja" : "Nein"],
    ["Eroeffnet", station.date_opened || "Unbekannt"],
  ];
  for (const [label, value] of rows) {
    const tr = document.createElement("tr");
    const tdLabel = document.createElement("td");
    tdLabel.style.cssText = "padding:2px 4px;color:#666;";
    tdLabel.textContent = label;
    tr.appendChild(tdLabel);
    const tdValue = document.createElement("td");
    tdValue.style.cssText = "padding:2px 4px;text-align:right;";
    tdValue.textContent = value;
    tr.appendChild(tdValue);
    table.appendChild(tr);
  }
  container.appendChild(table);

  const btnRow = document.createElement("div");
  btnRow.style.cssText = "margin-top:6px;";
  if (isRefreshing) {
    const spinner = document.createElement("span");
    spinner.style.cssText = "color:#666;font-style:italic;";
    spinner.textContent = "Aktualisiere…";
    btnRow.appendChild(spinner);
  } else {
    const btn = document.createElement("button");
    btn.style.cssText =
      "padding:4px 12px;background:#2563eb;color:white;border:none;border-radius:4px;cursor:pointer;font-size:12px;";
    btn.textContent = "\u{1F504} Von Tesla aktualisieren";
    btn.onclick = onRefresh;
    btnRow.appendChild(btn);
  }
  container.appendChild(btnRow);

  if (pricing && onRefreshPricing) {
    container.appendChild(
      buildPricingSection(pricing, onRefreshPricing, {
        refreshLabel:
          pricing.status === "loaded" && pricing.pricing.tiers.length > 0
            ? "\u{1F504} Preise erneut abrufen"
            : "\u{1F504} Preise von Tesla abrufen",
      }),
    );
  }

  return container;
}

/** Layer-IDs für das geclusterte Supercharger-Overlay (siehe
 * `addSuperchargerLayers`).
 */
export const SUPERCHARGER_LAYER_IDS = [
  "supercharger-clusters",
  "supercharger-cluster-count",
  "supercharger-unclustered",
] as const;

/** Baut die GeoJSON-FeatureCollection für das Supercharger-Overlay aus den
 * geladenen Stationen. `slug` in den Feature-Properties verweist beim
 * Klick auf die volle Stationsdaten in `superchargerStationsRef`.
 */
export function buildSuperchargerGeoJson(
  stations: SuperchargerStation[],
): GeoJSON.FeatureCollection<GeoJSON.Point, { slug: string }> {
  return {
    type: "FeatureCollection",
    features: stations.map((station) => ({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [station.longitude, station.latitude],
      },
      properties: { slug: station.slug },
    })),
  };
}
