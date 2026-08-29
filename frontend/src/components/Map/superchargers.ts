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

/** Innere SVG-Pfade (Lucide-Icon-Set, stroke-basiert, 24x24-Viewbox) fuer
 *  die minimalistischen Icon-Buttons unten im Popover - je einer fuer
 *  Stationsdaten-, Preis- und Komplett-Refresh, damit die drei Aktionen auf
 *  einen Blick unterscheidbar bleiben. */
const ICON_INFO_PATHS =
  '<circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4"></path><path d="M12 8h.01"></path>';
const ICON_TAG_PATHS =
  '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"></path><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"></circle>';
const ICON_REFRESH_PATHS =
  '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"></path><path d="M21 3v5h-5"></path><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"></path><path d="M8 16H3v5"></path>';

/** Baut einen minimalistischen, quadratischen Icon-only-Button (kein
 *  sichtbarer Text - Zweck/Ziel wird ueber `title`/`aria-label` als Tooltip
 *  bzw. fuer Screenreader vermittelt). `disabled` graut den Button aus und
 *  unterbindet Klicks waehrend ein Refresh bereits laeuft. */
function buildIconButton(
  iconPaths: string,
  title: string,
  onClick: () => void,
  disabled: boolean,
): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.disabled = disabled;
  btn.style.cssText = `display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;padding:0;background:#f9fafb;color:#4b5563;border:1px solid #e5e7eb;border-radius:8px;cursor:${disabled ? "default" : "pointer"};opacity:${disabled ? "0.5" : "1"};transition:background-color .15s,color .15s,border-color .15s;`;
  btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${iconPaths}</svg>`;
  if (!disabled) {
    btn.onmouseenter = () => {
      btn.style.background = "#eff6ff";
      btn.style.color = "#2563eb";
      btn.style.borderColor = "#bfdbfe";
    };
    btn.onmouseleave = () => {
      btn.style.background = "#f9fafb";
      btn.style.color = "#4b5563";
      btn.style.borderColor = "#e5e7eb";
    };
    btn.onclick = onClick;
  }
  return btn;
}

/** Formatiert den Preis eines `SuperchargerPricingTier` ohne Zeitfenster,
 *  z. B. "0.42 EUR/kWh". */
function formatTierPrice(tier: SuperchargerPricing["tiers"][number]): string {
  return `${tier.amount.toFixed(2)} ${tier.currency}/${tier.unit}`;
}

/** Wandelt ein 12h-Zeitfenster-Label (z. B. "12:00 AM - 4:00 PM", wie von
 *  Tesla geliefert) in 24h-Notation um ("00:00 - 16:00"). Ersetzt jedes
 *  `H:MM AM/PM`-Vorkommen im String einzeln, damit Trennzeichen/Layout des
 *  Original-Labels unangetastet bleiben. Labels ohne AM/PM (bereits 24h
 *  oder unerwartetes Format) werden unveraendert zurueckgegeben. */
function formatTimeLabel24h(label: string): string {
  return label.replace(
    /(\d{1,2}):(\d{2})\s*(AM|PM)/gi,
    (_match, hourStr: string, minuteStr: string, meridiem: string) => {
      let hour = Number(hourStr) % 12;
      if (meridiem.toUpperCase() === "PM") {
        hour += 12;
      }
      return `${String(hour).padStart(2, "0")}:${minuteStr}`;
    },
  );
}

/** Gruppiert Preis-Tiers nach `tier_label`, Reihenfolge des ersten
 *  Auftretens bleibt erhalten (Backend liefert Tiers bereits gruppiert). */
function groupTiersByLabel(
  tiers: SuperchargerPricing["tiers"],
): Map<string, SuperchargerPricing["tiers"]> {
  const groups = new Map<string, SuperchargerPricing["tiers"]>();
  for (const tier of tiers) {
    const existing = groups.get(tier.tier_label);
    if (existing) {
      existing.push(tier);
    } else {
      groups.set(tier.tier_label, [tier]);
    }
  }
  return groups;
}

/** Baut ein DOM-Element mit der reinen Preistabelle fuer eine Station -
 *  ohne Aktions-Buttons, die liegen bei allen Aufrufern (generisches
 *  Supercharger-Popover `buildSuperchargerPopoverElement` sowie
 *  Ladehalt-Popup `popups.ts::buildChargingStopPopupElement`) einheitlich
 *  in einer gemeinsamen Icon-Button-Zeile am Popover-Ende, siehe
 *  `buildIconButton`. `emptyLabel` erlaubt kontextabhaengigen Text
 *  (Popover vs. Ladehalt).
 */
export function buildPricingSection(
  state: PricingRefreshState,
  options?: { emptyLabel?: string },
): HTMLElement {
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
      for (const [label, groupTiers] of groupTiersByLabel(
        loadedPricing.tiers,
      )) {
        // Der "Other EV"-Tier interessiert Tesla-Fahrer meist nicht direkt
        // (siehe `select_owner_rate_for_time` im Backend) - standardmaessig
        // eingeklappt via natives <details>/<summary>, um im Popover Platz
        // fuer die relevanteren Tiers zu sparen.
        const isOtherEv = /other\s*evs?/i.test(label);
        const group = document.createElement(isOtherEv ? "details" : "div");
        group.style.cssText = "margin-bottom:6px;";

        const heading = document.createElement(isOtherEv ? "summary" : "div");
        heading.style.cssText = isOtherEv
          ? "font-weight:600;font-size:12px;color:#111827;cursor:pointer;"
          : "font-weight:600;font-size:12px;color:#111827;margin-bottom:2px;";
        heading.textContent = label;
        group.appendChild(heading);

        if (groupTiers.length === 1 && !groupTiers[0].time_label) {
          const row = document.createElement("div");
          row.style.cssText =
            "display:flex;justify-content:flex-end;font-size:13px;font-weight:600;";
          row.textContent = formatTierPrice(groupTiers[0]);
          group.appendChild(row);
        } else {
          const table = document.createElement("table");
          table.style.cssText =
            "width:100%;border-collapse:collapse;font-size:12px;";
          for (const tier of groupTiers) {
            const tr = document.createElement("tr");
            const tdTime = document.createElement("td");
            tdTime.style.cssText =
              "padding:1px 8px 1px 0;color:#666;white-space:nowrap;";
            tdTime.textContent = tier.time_label
              ? formatTimeLabel24h(tier.time_label)
              : "Ganzt\u00e4gig";
            tr.appendChild(tdTime);
            const tdPrice = document.createElement("td");
            tdPrice.style.cssText =
              "padding:1px 0;text-align:right;font-weight:600;white-space:nowrap;";
            tdPrice.textContent = formatTierPrice(tier);
            tr.appendChild(tdPrice);
            table.appendChild(tr);
          }
          group.appendChild(table);
        }
        section.appendChild(group);
      }
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

  return section;
}

/** Baut den einzelnen Icon-Button "Preise aktualisieren", wiederverwendet
 *  im Ladehalt-Popup (`popups.ts::buildChargingStopPopupElement`), das nur
 *  ueber eine Preis-, aber keine Stationsdaten-Refresh-Aktion verfuegt. */
export function buildPricingRefreshIconButton(
  state: PricingRefreshState,
  onRefresh: () => void,
): HTMLButtonElement {
  return buildIconButton(
    ICON_TAG_PATHS,
    "Preis von Tesla abrufen",
    onRefresh,
    state.status === "loading",
  );
}

/** Baut ein DOM-Element fuer ein Supercharger-Popover mit minimalistischer,
 *  icon-only Aktionsleiste am unteren Rand (Stationsdaten / Preise / beides
 *  aktualisieren). */
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

  if (isRefreshing) {
    const spinner = document.createElement("div");
    spinner.style.cssText =
      "margin-top:6px;color:#666;font-style:italic;font-size:12px;";
    spinner.textContent = "Aktualisiere Stationsdaten\u2026";
    container.appendChild(spinner);
  }

  if (pricing && onRefreshPricing) {
    container.appendChild(buildPricingSection(pricing));
  }

  const pricingLoading = pricing?.status === "loading";
  const actions = document.createElement("div");
  actions.style.cssText =
    "display:flex;gap:6px;margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb;";
  actions.appendChild(
    buildIconButton(
      ICON_INFO_PATHS,
      "Stationsdaten von Tesla aktualisieren",
      onRefresh,
      isRefreshing,
    ),
  );
  if (onRefreshPricing) {
    actions.appendChild(
      buildIconButton(
        ICON_TAG_PATHS,
        "Preise von Tesla abrufen",
        onRefreshPricing,
        pricingLoading,
      ),
    );
    actions.appendChild(
      buildIconButton(
        ICON_REFRESH_PATHS,
        "Stationsdaten und Preise aktualisieren",
        () => {
          onRefresh();
          onRefreshPricing();
        },
        isRefreshing || pricingLoading,
      ),
    );
  }
  container.appendChild(actions);

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
