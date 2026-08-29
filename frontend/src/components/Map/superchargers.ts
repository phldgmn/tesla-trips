import type { SuperchargerStation } from "../../api/chargingApi";

/** Baut ein DOM-Element fuer ein Supercharger-Popover mit Refresh-Button. */
export function buildSuperchargerPopoverElement(
  station: SuperchargerStation,
  isRefreshing: boolean,
  onRefresh: () => void,
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
