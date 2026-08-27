import { useEffect, useRef, useState } from "react";
import {
  Map,
  Marker,
  Popup,
  LngLatBounds,
  LayerSpecification,
  GeoJSONSource,
  setWorkerUrl,
  type StyleSpecification,
  type SourceSpecification,
  type MapMouseEvent,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre GL JS berechnet die Worker-URL zur Laufzeit relativ zu
// `import.meta.url` des eigenen Moduls (parst Vector-Tiles abseits des
// Main-Threads). Im Vite-Production-Build wird diese dynamisch berechnete
// URL von Rollup nicht erkannt/mitgebündelt - der Worker-Request würde ins
// Leere laufen (Karte bleibt dauerhaft im Ladezustand, kein `load`-Event,
// siehe `vite.config.ts`, `optimizeDeps.exclude` für den Dev-Server-seitigen
// Teil des gleichen Problems). Der `?worker&url`-Import lässt Vite die
// Worker-Datei als eigenständigen, korrekt referenzierten Chunk bündeln;
// `setWorkerUrl()` überschreibt MapLibres eigene (kaputte) Berechnung damit.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

import { toLngLat, haversineDistanceM } from "../utils/geo-utils";
import {
  buildSplicedRoute,
  splitRouteIntoLegs,
  projectDistanceAlongLineM,
  findNearestRouteSample,
  type RouteSample,
} from "../utils/route-line";
import { formatZeitpunkt } from "../utils/datetime-utils";
import { formatCostOrDash } from "../utils/currency-utils";
import { usePersistentState } from "../utils/persistent-state";
import {
  ChargingStop,
  ConstructionZone,
  ConstructionZoneEvent,
  TripSimulationResult,
  WaypointStop,
} from "../types";
import type { Stop } from "../types/trip-request";
import {
  fetchSuperchargers,
  refreshSupercharger,
  type SuperchargerStation,
} from "../api/chargingApi";
// Liberty-Style (OpenMapTiles-Schema) von openfreemap.org, einmalig
// vendored (siehe README.md). Sprite/Glyphs bleiben bei der CDN, nur die
// Vektor-Tile-Quelle wird unten auf den selbst gehosteten Tile-Server
// umgebogen (siehe buildBasemapStyle).
import libertyStyleRaw from "../assets/liberty-style.json";

setWorkerUrl(maplibreWorkerUrl);

// Lokaler Vektor-Tile-Server (docker-compose.yml, Service "tiles"; siehe
// scripts/build_basemap_tiles.sh, run.sh `start tiles`). Liefert PMTiles
// per `pmtiles serve` als ZXY/TileJSON-Endpunkt aus, gebaut aus demselben
// DE+DK+SE-OSM-Extrakt, den auch GraphHopper fuers Routing nutzt.
const TILES_BASE_URL = "http://localhost:8081";

/** Ersetzt in einem MapLibre-Style nur die `openmaptiles`-Vektor-Quelle
 * durch den selbst gehosteten Tile-Server; alle anderen Felder (Sprite,
 * Glyphs, Layer, weitere Quellen) bleiben unveraendert.
 */
export function buildBasemapStyle(
  baseStyle: StyleSpecification,
  tilesBaseUrl: string,
): StyleSpecification {
  return {
    ...baseStyle,
    sources: {
      ...baseStyle.sources,
      openmaptiles: {
        ...baseStyle.sources.openmaptiles,
        url: `${tilesBaseUrl}/basemap.json`,
      } as SourceSpecification,
    },
  };
}

const basemapStyle = buildBasemapStyle(
  libertyStyleRaw as unknown as StyleSpecification,
  TILES_BASE_URL,
);

// Farbpalette für den kontinuierlichen SoC-Verlauf: rot (≤5%) → orange (5-15%) → gelb (15-25%) → grün (25-75%) → blau (>75%).
const SOC_COLOR_STOPS: readonly [
  soc: number,
  r: number,
  g: number,
  b: number,
][] = [
  [0, 0xef, 0x44, 0x44], // 0% - red
  [5, 0xef, 0x44, 0x44], // 5% - red (red only for ≤5%)
  [15, 0xf9, 0x73, 0x16], // 15% - orange (orange for ≤15%)
  [25, 0xea, 0xb3, 0x08], // 25% - yellow
  [75, 0x22, 0xc5, 0x5e], // 75% - green
  [100, 0x3b, 0x82, 0xf6], // 100% - blue
];

function toHex(value: number): string {
  return Math.round(value).toString(16).padStart(2, "0");
}

/** Bildet einen SoC-Wert (0–100) linear auf eine Farbe entlang der Palette
 * `SOC_COLOR_STOPS` ab. Anders als eine Bucket-Funktion (feste Farbe je
 * Wertebereich) liefert dies für jeden SoC-Wert eine eigene, kontinuierlich
 * zwischen den Nachbar-Stützfarben interpolierte Farbe - Voraussetzung dafür,
 * dass der `line-gradient` in `buildSocGradientExpression` tatsächlich
 * stufenlos verläuft statt aus flachen Farbplateaus mit kurzen, abrupten
 * Übergängen an den alten Bucket-Grenzen zu bestehen (siehe dortiger
 * Docstring).
 */
export function socToColor(soc: number): string {
  const clamped = Math.min(100, Math.max(0, soc));
  let [socLo, rLo, gLo, bLo] = SOC_COLOR_STOPS[0];
  let [socHi, rHi, gHi, bHi] = SOC_COLOR_STOPS[SOC_COLOR_STOPS.length - 1];
  for (let i = 0; i < SOC_COLOR_STOPS.length - 1; i++) {
    if (
      clamped >= SOC_COLOR_STOPS[i][0] &&
      clamped <= SOC_COLOR_STOPS[i + 1][0]
    ) {
      [socLo, rLo, gLo, bLo] = SOC_COLOR_STOPS[i];
      [socHi, rHi, gHi, bHi] = SOC_COLOR_STOPS[i + 1];
      break;
    }
  }
  const t = socHi === socLo ? 0 : (clamped - socLo) / (socHi - socLo);
  const r = rLo + (rHi - rLo) * t;
  const g = gLo + (gHi - gLo) * t;
  const b = bLo + (bHi - bLo) * t;
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/** Baut die MapLibre `line-gradient`-Expression für den SoC-Farbverlauf entlang
 * der gesamten (gespliceten) Route aus einer einzigen Linie (statt vieler
 * einzelner Segment-Layer – siehe `MapVisualization`). `samples` kommt aus
 * `buildSplicedRoute()` und enthält sowohl Fahr-Stützpunkte (per `distanz_m`
 * positioniert) als auch `critical`-markierte Ladehalt-Ankunfts-/Abfahrts-
 * Stützpunkte, die beim Downsampling nie übersprungen werden - sonst wäre der
 * SoC-Sprung an einer Ladestation (niedriger Ankunfts- zu höherem Ziel-SoC)
 * nicht sichtbar. Alle Distanzwerte sind relativ zur gespliceten Linie
 * (inkl. Ladehalt-Abstecher), passend zu `line-progress`. Reguläre
 * Stützpunkte werden auf maximal `maxStops - critical.length` gleichmässig
 * heruntergesampelt (vermeidet riesige Expressions bei langen Trips mit
 * tausenden Frames); Stützpunkte mit identischer Distanz (z. B. während
 * eines Ladehalts) werden bereinigt, da `interpolate`-Stops strikt
 * aufsteigend sein müssen.
 */
export function buildSocGradientExpression(
  samples: RouteSample[],
  totalDistanceM: number,
  maxStops = 64,
): unknown[] {
  if (samples.length === 0 || totalDistanceM <= 0) {
    return [
      "interpolate",
      ["linear"],
      ["line-progress"],
      0,
      "#3b82f6",
      1,
      "#3b82f6",
    ];
  }

  const sorted = [...samples].sort((a, b) => a.distanzM - b.distanzM);
  const critical = sorted.filter((s) => s.critical);
  const regular = sorted.filter((s) => !s.critical);

  const budget = Math.max(2, maxStops - critical.length);
  const stride = Math.max(1, Math.ceil((regular.length - 1) / (budget - 1)));
  const sampledRegular: RouteSample[] = [];
  for (let i = 0; i < regular.length - 1; i += stride) {
    sampledRegular.push(regular[i]);
  }
  if (regular.length > 0) {
    sampledRegular.push(regular[regular.length - 1]);
  }

  const merged = [...sampledRegular, ...critical].sort(
    (a, b) => a.distanzM - b.distanzM,
  );

  const stops: (number | string)[] = [];
  let lastProgress = -1;
  for (const sample of merged) {
    const progress = Math.min(1, Math.max(0, sample.distanzM / totalDistanceM));
    if (progress <= lastProgress) continue;
    stops.push(progress, socToColor(sample.socPct));
    lastProgress = progress;
  }
  return ["interpolate", ["linear"], ["line-progress"], ...stops];
}

/** Rolle eines Stopps, abgeleitet aus seiner Position im Array. */
export type StopRole = "start" | "end" | "middle";

/** Bestimmt die Rolle eines Stopps anhand seines Index und der Gesamtzahl. */
export function stopRole(index: number, total: number): StopRole {
  if (index === 0) return "start";
  if (index === total - 1) return "end";
  return "middle";
}

/** Liefert die Markerfarbe (CSS-Hex) für eine StopRole. */
export function roleToMarkerColor(role: StopRole): string {
  switch (role) {
    case "start":
      return "#22c55e"; // grün
    case "end":
      return "#ef4444"; // rot
    case "middle":
      return "#3b82f6"; // blau
  }
}

/** Anzeige-Label für einen Stopp in Popup/Header (Rolle als Fallback). */
export function roleToLabel(role: StopRole): string {
  switch (role) {
    case "start":
      return "Start";
    case "end":
      return "Ziel";
    case "middle":
      return "Stop";
  }
}

/** Kürzel für die Markerdarstellung: A (Start), B (Ziel), Punkt sonst. */
export function roleToMarkerGlyph(role: StopRole): string {
  switch (role) {
    case "start":
      return "A";
    case "end":
      return "B";
    case "middle":
      return "●";
  }
}

/** Erzeugt ein gestyltes DOM-Element für einen Stopp-Marker. Start/Ziel
 *  behalten ihr Buchstaben-Kürzel (A/B); Zwischenstopps bekommen ein
 *  Pin-Icon statt eines reinen Punkts, damit der Marker auch dann klar als
 *  Ort erkennbar bleibt, wenn er zusätzlich Aufenthalts-Details trägt (siehe
 *  `buildStopPopupHtml`). BEWUSST kein inline `position`/`z-index` hier -
 *  das durchkreuzt MapLibres eigene Transform-basierte Positionierung des
 *  Marker-Elements und liess Marker beim Zoomen von ihrer Koordinate
 *  abdriften (siehe `raiseStopMarkersToTop` fuer die stattdessen genutzte,
 *  rein DOM-Reihenfolge-basierte Stapelung ueber Baustellen-/Ladehalt-
 *  Markern). */
export function buildMarkerElement(role: StopRole): HTMLElement {
  const el = document.createElement("div");
  const color = roleToMarkerColor(role);
  const isMiddle = role === "middle";
  if (isMiddle) {
    el.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="#ffffff" style="pointer-events:none;">
      <path d="M12 2C8.14 2 5 5.14 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.86-3.14-7-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/>
    </svg>`;
  } else {
    el.textContent = roleToMarkerGlyph(role);
  }
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:28px",
    "height:28px",
    "border-radius:50%",
    `background-color:${color}`,
    "border:2px solid #ffffff",
    "color:#ffffff",
    "font-weight:700",
    "font-size:14px",
    "line-height:1",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:grab",
  ]
    .concat(isMiddle ? [] : ["font-family:system-ui,sans-serif"])
    .join(";");
  return el;
}

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
const SUPERCHARGER_LAYER_IDS = [
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

/** Popup-Text für einen Stopp: Adresse falls vorhanden, sonst Rolle + gerundete Koordinaten. */
export function buildPopupText(stop: Stop, role: StopRole): string {
  if (stop.address) return stop.address;
  if (!stop.position) return roleToLabel(role);
  const [lat, lng] = stop.position;
  const r = (n: number) => Math.round(n * 1_000_000) / 1_000_000;
  return `${roleToLabel(role)} (${r(lat)}, ${r(lng)})`;
}

/** Erzeugt ein gestyltes DOM-Element fuer einen Ladehalt-Marker (Blitz-Symbol).
 *
 * Ein Marker pro tatsaechlichem Ladehalt (`TripSimulationResult.charging_stops`),
 * nicht pro Simulationsframe - eine Ladepause erzeugt sonst mehrere
 * `zustand === "LADEN"`-Frames, die andernfalls zu mehreren, entlang der
 * Strecke verteilten Markern statt eines einzigen an der Ladestation fuehren
 * wuerden.
 */
export function buildChargingStopMarkerElement(): HTMLElement {
  const el = document.createElement("div");
  el.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="#ffffff" style="pointer-events:none;">
    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
  </svg>`;
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:28px",
    "height:28px",
    "border-radius:50%",
    "background-color:#f59e0b",
    "border:2px solid #ffffff",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:pointer",
  ].join(";");
  return el;
}

/** Formatiert eine Dauer in Sekunden als "Xh Ymin" bzw. "Ymin". */
export function formatChargingDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}min` : `${minutes}min`;
}

/** Popup-HTML fuer einen Ladehalt: Name, Ankunfts-/Ziel-SoC samt Uhrzeit, Dauer, geladene Energie, Preis. */
export function buildChargingStopPopupHtml(stop: ChargingStop): string {
  const rows: [string, string][] = [
    ["Ankunft", `${stop.ankunfts_soc_pct.toFixed(0)}% SoC`],
    ["Ankunftszeit", formatZeitpunkt(stop.ankunftszeit)],
    ["Abfahrt", `${stop.ziel_soc_pct.toFixed(0)}% SoC`],
    ["Abfahrtszeit", formatZeitpunkt(stop.abfahrtszeit)],
    ["Dauer", formatChargingDuration(stop.ladedauer_s)],
    ["Geladen", `${stop.energie_geladen_kwh.toFixed(1)} kWh`],
    ["Preis", formatCostOrDash(stop.estimated_cost, stop.currency)],
  ];
  const rowsHtml = rows
    .map(
      ([label, value]) =>
        `<tr><td style="padding:2px 4px;color:#666;">${label}</td>` +
        `<td style="padding:2px 4px;text-align:right;">${value}</td></tr>`,
    )
    .join("");
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;">` +
    `<strong style="font-size:14px;">${stop.name}</strong>` +
    `<table style="width:100%;border-collapse:collapse;margin-top:4px;">${rowsHtml}</table>` +
    `</div>`
  );
}

/** Popup-HTML fuer einen Stopp-Marker: Adresse/Rolle als Titel, ergaenzt um
 *  Aufenthalts-Details (Ankunfts-/Abfahrts-SoC und -zeit, ggf. genutzte
 *  Ladeleistung/geladene Energie), sobald ein passender `WaypointStop` aus
 *  dem Simulationsergebnis vorliegt (erzwungene Wartezeit an diesem Stopp).
 *  Ersetzt zwei vormals getrennte Marker (editierbarer Adress-Marker +
 *  separater Detail-Marker) durch einen einzigen - sobald Details verfuegbar
 *  sind, werden sie direkt im selben Marker/Popup angezeigt statt einem
 *  zweiten, ueberlappenden Marker. */
export function buildStopPopupHtml(
  stop: Stop,
  role: StopRole,
  waypointStop: WaypointStop | null,
): string {
  const title = buildPopupText(stop, role);
  if (!waypointStop) {
    return `<div style="font-family:system-ui,sans-serif;font-size:13px;">${title}</div>`;
  }
  const rows: [string, string][] = [
    ["Ankunft", `${waypointStop.ankunfts_soc_pct.toFixed(0)}% SoC`],
    ["Ankunftszeit", formatZeitpunkt(waypointStop.ankunftszeit)],
    ["Abfahrt", `${waypointStop.ziel_soc_pct.toFixed(0)}% SoC`],
    ["Abfahrtszeit", formatZeitpunkt(waypointStop.abfahrtszeit)],
  ];
  if (waypointStop.ladeleistung_kw !== null) {
    rows.push([
      "Ladeleistung",
      `${waypointStop.ladeleistung_kw.toFixed(1)} kW`,
    ]);
    rows.push([
      "Geladen",
      `${waypointStop.energie_geladen_kwh.toFixed(1)} kWh`,
    ]);
  }
  const rowsHtml = rows
    .map(
      ([label, value]) =>
        `<tr><td style="padding:2px 4px;color:#666;">${label}</td>` +
        `<td style="padding:2px 4px;text-align:right;">${value}</td></tr>`,
    )
    .join("");
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;">` +
    `<strong style="font-size:14px;">${title}</strong>` +
    `<table style="width:100%;border-collapse:collapse;margin-top:4px;">${rowsHtml}</table>` +
    `</div>`
  );
}

/** Findet den `WaypointStop` (falls vorhanden), dessen Position mit `position`
 *  uebereinstimmt (innerhalb einer kleinen Toleranz gegen Fliesskomma-
 *  Rundung) - verknuepft einen editierbaren `Stop` mit seinen berechneten
 *  Aufenthalts-Details, da `WaypointStop` selbst keine `Stop.id` traegt. */
const WAYPOINT_STOP_MATCH_TOLERANCE_M = 25;

export function findWaypointStopAt(
  waypointStops: WaypointStop[],
  position: [number, number],
): WaypointStop | null {
  return (
    waypointStops.find(
      (w) =>
        haversineDistanceM(w.position, position) <=
        WAYPOINT_STOP_MATCH_TOLERANCE_M,
    ) ?? null
  );
}

/** Kurzes, deutsches Label je `Sperrungstyp`-Enum-Wert aus dem Backend
 *  (`tripplanner.construction.models.Sperrungstyp`). Unbekannte Werte
 *  (z. B. ein zukuenftiger Backend-Enum-Wert) fallen auf den Rohwert
 *  zurueck statt eine leere Zeile zu erzeugen. */
const SPERRUNGSTYP_LABELS: Record<string, string> = {
  fullyClosed: "Vollsperrung",
  partiallyClosed: "Teilsperrung",
  laneClosed: "Fahrspur gesperrt",
  temporarySpeedLimit: "Tempolimit",
  reducedLanes: "Fahrspuren reduziert",
  detrourRequired: "Umleitung erforderlich",
};

/** Erzeugt ein gestyltes DOM-Element fuer einen Baustellen-Marker
 * (Warndreieck-Symbol), ein Eintrag pro `TripSimulationResult.construction_zones`.
 * Optisch bewusst kleiner und farblich abgesetzt von Ladehalt-Markern
 * (`buildChargingStopMarkerElement`), damit beide Markertypen auf einen
 * Blick unterscheidbar bleiben. */
export function buildConstructionZoneMarkerElement(): HTMLElement {
  const el = document.createElement("div");
  el.innerHTML = `<svg viewBox="0 0 24 24" width="12" height="12" fill="#1f2937" style="pointer-events:none;">
    <path d="M12 2L1 21h22L12 2zm0 5.5L18.5 19h-13L12 7.5z"/>
    <rect x="11" y="11" width="2" height="5" />
    <rect x="11" y="17" width="2" height="2" />
  </svg>`;
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:20px",
    "height:20px",
    "border-radius:50%",
    "background-color:#fbbf24",
    "border:2px solid #ffffff",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:pointer",
  ].join(";");
  return el;
}

/** Erzeugt Popup-HTML für eine ConstructionZone: ein Abschnitt pro Event.
 *
 *  Einzelne Events werden wie zuvor gerendert; bei mehreren Events (gemerged)
 *  erzeugt jeder Event einen eigenen, optisch abgesetzten Abschnitt mit
 *  "Baustelle N von M"-Überschrift.
 */
export function buildConstructionZonePopupHtml(zone: ConstructionZone): string {
  function eventRowsHtml(event: ConstructionZoneEvent): string {
    const rows: [string, string][] = [];
    if (event.tempolimit_kmh !== null) {
      rows.push(["Tempolimit", `${event.tempolimit_kmh} km/h`]);
    }
    if (event.umleitungshinweis !== null) {
      rows.push(["Umleitung", event.umleitungshinweis]);
    }
    rows.push(["Land", event.land]);
    rows.push(["Gültig ab", formatZeitpunkt(event.gueltig_von)]);
    rows.push([
      "Gültig bis",
      event.gueltig_bis !== null
        ? formatZeitpunkt(event.gueltig_bis)
        : "unbestimmt",
    ]);
    return rows
      .map(
        ([label, value]) =>
          `<tr><td style="padding:2px 4px;color:#666;">${label}</td>` +
          `<td style="padding:2px 4px;text-align:right;">${value}</td></tr>`,
      )
      .join("");
  }

  const eventCount = zone.events.length;
  const sections = zone.events
    .map((event, index) => {
      const eventLabel =
        SPERRUNGSTYP_LABELS[event.sperrungstyp] ?? event.sperrungstyp;
      const heading =
        eventCount > 1
          ? `${eventLabel} (${index + 1} von ${eventCount})`
          : eventLabel;
      return (
        `<div style="margin-bottom:${index < eventCount - 1 ? "12px" : "0"};">` +
        (index < eventCount - 1
          ? `<hr style="border:none;border-top:1px solid #e5e7eb;margin:8px 0;">`
          : "") +
        `<strong style="font-size:14px;">${heading}</strong>` +
        `<table style="width:100%;border-collapse:collapse;margin-top:4px;">${eventRowsHtml(event)}</table>` +
        `</div>`
      );
    })
    .join("");
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;">` +
    `${sections}` +
    `</div>`
  );
}

/** Tooltip-Text fuer den Routen-Hover: Datum/Zeit und SoC am naechstgelegenen
 * Streckenpunkt. `sample` stammt aus `findNearestRouteSample()` ueber die
 * per `projectDistanceAlongLineM()` auf die gezeichnete (gesplicete) Linie
 * projizierte Mausposition - NICHT aus einer raeumlichen Naechster-Punkt-
 * Suche ueber `SimulationFrame.position`, die an Stellen, wo sich die Route
 * raeumlich (aber nicht streckenmaessig) annaehert, z. B. kurz nach einem
 * Ladehalt-Abstecher, den falschen (Vor-Lade-)Frame waehlen konnte. */
export function buildRouteHoverText(sample: RouteSample): string {
  return `${formatZeitpunkt(sample.zeitpunkt ?? null)} · ${sample.socPct.toFixed(0)}% SoC`;
}

/** Von der Karte persistierter Kartenausschnitt (Mittelpunkt + Zoomstufe). */
export interface MapViewState {
  center: [number, number];
  zoom: number;
}

/** Ausschnitt beim allerersten Laden (kein `localStorage`-Wert vorhanden):
 * Welt-Ansicht, wie zuvor fest im `Map`-Konstruktor verdrahtet. */
export const DEFAULT_MAP_VIEW: MapViewState = { center: [0, 0], zoom: 2 };

/** Validiert einen aus `localStorage` wiederhergestellten Kartenausschnitt.
 * Schützt vor einem inkompatiblen/beschädigten Alt-Wert (z. B. nach
 * manueller Bearbeitung der DevTools oder einem künftigen Schema-Wechsel),
 * der sonst MapLibre beim Initialisieren mit NaN/Infinity abstürzen ließe. */
export function isValidMapViewState(value: unknown): value is MapViewState {
  if (typeof value !== "object" || value === null) return false;
  const { center, zoom } = value as Record<string, unknown>;
  return (
    Array.isArray(center) &&
    center.length === 2 &&
    center.every((c) => typeof c === "number" && Number.isFinite(c)) &&
    typeof zoom === "number" &&
    Number.isFinite(zoom)
  );
}

interface MapProps {
  /** Simulationsergebnis (optional – entfällt im Planungsmodus). */
  simulationResult?: TripSimulationResult;
  /** Stopp-Liste (Start/Zwischenstopps/Ziel). Rolle ergibt sich aus dem Index. */
  stops?: Stop[];
  /** Wenn gesetzt: Kartenklick wählt Position für diesen Stopp (statt neuen Marker). */
  pickingStopId?: string | null;
  /** Callback bei Kartenklick während pickingStopId aktiv ist. */
  onPickPosition?: (stopId: string, position: [number, number]) => void;
  /** Callback beim Verschieben eines Stopp-Markers (dragend). */
  onStopMove?: (stopId: string, position: [number, number]) => void;
  /** Ob die Supercharger-Overlay auf der Karte sichtbar ist. */
  superchargerVisible?: boolean;
  /** Callback zum Umschalten der Supercharger-Sichtbarkeit. */
  onToggleSuperchargers?: () => void;
}

/** Map-Komponente für die Visualisierung der Simulationsergebnisse.
 *
 * Darstellung:
 * - Route als LineString mit Farbsegmenten basierend auf SoC (nur wenn
 *   `simulationResult` vorhanden)
 * - Marker für Ladehalte und Zwischenstopps (nur aus Simulationsergebnis)
 * - Bearbeitbare Stopp-Marker (Start/Zwischenstopp/Ziel) aus `stops` –
 *   draggable, mit Popup und dragend-Callback. Stopps ohne Position
 *   werden übersprungen.
 * - Kartenklick im Auswahlmodus (`pickingStopId`) liefert Koordinate zurück
 * - Supercharger-Overlay mit Ein-/Ausblend-Toggle, Klick → Popover,
 *   Refresh-Button (Browser ruft Tesla-API direkt auf)
 */
export function MapVisualization({
  simulationResult,
  stops: stopsProp,
  pickingStopId,
  onPickPosition,
  onStopMove,
  superchargerVisible,
  onToggleSuperchargers,
}: MapProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const [isMapLoaded, setIsMapLoaded] = useState(false);

  // Verwaltung der Stopp-Marker, keyed by stop.id
  // Record (kein typeof Map, um Konflikte mit MapLibre zu vermeiden)
  const markersRef = useRef<Record<string, Marker>>({});
  // Ladehalt-Marker (ein Eintrag pro tatsaechlichem Ladehalt aus
  // `simulationResult.charging_stops`), analog zu `markersRef` fuer Stopps.
  const chargingStopMarkersRef = useRef<Marker[]>([]);
  // Baustellen-Marker (ein Eintrag pro `simulationResult.construction_zones`),
  // analog zu `chargingStopMarkersRef`.
  const constructionZoneMarkersRef = useRef<Marker[]>([]);

  // Supercharger-Overlay
  const [superchargerStations, setSuperchargerStations] = useState<
    SuperchargerStation[]
  >([]);
  const [superchargerLoading, setSuperchargerLoading] = useState(false);
  const [superchargerError, setSuperchargerError] = useState<string | null>(
    null,
  );
  const [refreshingSlug, setRefreshingSlug] = useState<string | null>(null);
  const superchargerStationsRef = useRef<SuperchargerStation[]>([]);
  const activePopoverRef = useRef<Popup | null>(null);
  // Routen-Hover: zeigt Datum/Zeit + SoC des naechstgelegenen Streckenpunkts
  // (per Distanz-entlang-der-Linie, nicht raeumlich) in einem kleinen
  // Tooltip neben dem Cursor an.
  const [routeHoverInfo, setRouteHoverInfo] = useState<{
    x: number;
    y: number;
    sample: RouteSample;
  } | null>(null);
  const routeHoverHandlersRef = useRef<{
    move: (e: MapMouseEvent) => void;
    leave: () => void;
  } | null>(null);
  // Ids der pro-Leg SoC-Gradient-Sources (siehe `splitRouteIntoLegs`) - fuer
  // sauberes Entfernen in `clearSimulationLayers` beim naechsten Rebuild.
  const routeLegSourceIdsRef = useRef<string[]>([]);

  // Kartenausschnitt (Mittelpunkt + Zoom) wird in `localStorage` gespiegelt
  // und beim Neuladen wiederhergestellt, statt jedes Mal bei der
  // Welt-Ansicht zu starten. `initialMapViewRef` friert den beim Mount
  // wiederhergestellten Wert ein - der Map-Konstruktor braucht ihn nur
  // einmalig, spätere Änderungen laufen über `moveend` (s. u.).
  const [mapView, setMapView] = usePersistentState<MapViewState | null>(
    "map-view",
    null,
  );
  const initialMapViewRef = useRef<MapViewState>(
    mapView && isValidMapViewState(mapView) ? mapView : DEFAULT_MAP_VIEW,
  );

  useEffect(() => {
    // Map initialisieren
    if (!mapContainerRef.current || mapRef.current) return;

    mapRef.current = new Map({
      container: mapContainerRef.current,
      style: basemapStyle,
      center: initialMapViewRef.current.center,
      zoom: initialMapViewRef.current.zoom,
    });

    mapRef.current.on("load", () => {
      setIsMapLoaded(true);
    });
    mapRef.current.on("moveend", () => {
      const map = mapRef.current;
      if (!map) return;
      const center = map.getCenter();
      setMapView({ center: [center.lng, center.lat], zoom: map.getZoom() });
    });

    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `setMapView` (React-Setter, stabile Identität) wird bewusst nicht aufgenommen: der Effect soll nur beim Mount laufen, siehe `initialMapViewRef`.
  }, []);

  /** Entfernt alle Routen- und Marker-Sources/Layer aus der Karte. */
  function clearSimulationLayers(map: Map) {
    // Route (Basislinie, ein Source/Layer) + pro-Leg SoC-Gradient-Layer
    // (siehe `splitRouteIntoLegs` - eigene Texture-Aufloesung pro Leg statt
    // einer gemeinsamen 256-Texel-Texture ueber die gesamte Route).
    for (const sourceId of routeLegSourceIdsRef.current) {
      const layerId = `${sourceId}-gradient`;
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(sourceId)) map.removeSource(sourceId);
    }
    routeLegSourceIdsRef.current = [];
    if (map.getLayer("route")) map.removeLayer("route");
    if (map.getSource("route")) map.removeSource("route");
    // Hover-Handler der Haupt-Route abmelden (sonst haeufen sich beim
    // Neuaufbau der Route mehrere Listener mit veralteten `frames`-Closures)
    if (routeHoverHandlersRef.current) {
      map.off("mousemove", "route", routeHoverHandlersRef.current.move);
      map.off("mouseleave", "route", routeHoverHandlersRef.current.leave);
      routeHoverHandlersRef.current = null;
    }
    setRouteHoverInfo(null);
    // Ladehalte
    for (const marker of chargingStopMarkersRef.current) {
      marker.remove();
    }
    chargingStopMarkersRef.current = [];
    // Baustellen
    for (const marker of constructionZoneMarkersRef.current) {
      marker.remove();
    }
    constructionZoneMarkersRef.current = [];
  }

  /** Verschiebt jedes Stopp-Marker-Element (siehe `markersRef`) ans Ende
   *  seines DOM-Parents, damit Stopp-Marker IMMER ueber Baustellen-/
   *  Ladehalt-Markern liegen - rein per DOM-Reihenfolge (spaeter im DOM =
   *  oben im Paint-Stack), NICHT per inline `position`/`z-index` auf dem
   *  Marker-Element selbst (das durchkreuzt MapLibres eigene Transform-
   *  basierte Positionierung und liess Marker beim Zoomen von ihrer
   *  Koordinate abdriften). `appendChild` auf einem bereits eingehaengten
   *  Knoten verschiebt ihn nur um - Inline-Styles/Transform, die MapLibre
   *  selbst auf dem Element verwaltet, bleiben unangetastet.
   *
   *  Wird am Ende BEIDER Marker-Effekte aufgerufen (Simulationsergebnis- UND
   *  Stopp-Effekt), damit die Reihenfolge unabhaengig davon stimmt, welcher
   *  der beiden zuletzt gelaufen ist. */
  function raiseStopMarkersToTop() {
    for (const marker of Object.values(markersRef.current)) {
      const el = marker.getElement();
      el.parentNode?.appendChild(el);
    }
  }

  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;

    // IMMER zuerst alte Layer/Sources entfernen – egal ob wir in den
    // Planungsmodus wechseln (simulationResult = null) oder ein neues
    // Simulationsergebnis laden.
    clearSimulationLayers(map);

    // Wenn kein Simulationsergebnis vorhanden ist: hier aufhören.
    if (!simulationResult) {
      return;
    }

    // Route in GeoJSON konvertieren: volle GraphHopper-Geometrie (nicht die
    // zeitbasiert groben Simulationsframes) fuer eine winkeltreue Linie, die
    // dem tatsaechlichen Strassenverlauf folgt, GESPLICET mit echten,
    // geroutete Abstechern zu jedem Ladehalt (`ChargingStop.detour_geometrie`)
    // - sonst wuerde die Route nie die Ladestation verlassen (siehe
    // `buildSplicedRoute`). Liefert passend dazu SoC-Stuetzpunkte fuer
    // `buildSocGradientExpression`.
    const splicedRoute = buildSplicedRoute(
      simulationResult.route_geometrie,
      simulationResult.charging_stops.map((stop) => ({
        position: stop.position,
        distanzM: stop.distanz_m,
        detourGeometrie: stop.detour_geometrie,
        stationIndex: stop.detour_station_index,
        routeIndexVor: stop.route_index_vor,
        routeIndexNach: stop.route_index_nach,
        ankunftsSocPct: stop.ankunfts_soc_pct,
        zielSocPct: stop.ziel_soc_pct,
        ankunftszeit: stop.ankunftszeit,
        abfahrtszeit: stop.abfahrtszeit,
      })),
      // Frames + waypoint SoC-Spruenge fuer Gradient.
      // WICHTIG: LADEN/PAUSE-Frames durch SoC-Sprung ersetzen - der Gradient
      // soll den Sprung von Ankunfts- zu Ziel-SoC zeigen, nicht die Ladedauer.
      [
        ...simulationResult.frames
          .filter((f) => f.zustand === "FAHREN")
          .map((f) => ({
            distanzM: f.distanz_m,
            socPct: f.soc_pct,
            zeitpunkt: f.zeitpunkt,
          })),
        ...simulationResult.waypoint_stops.flatMap((stop) => [
          {
            distanzM: stop.distanz_m - 0.01,
            socPct: stop.ankunfts_soc_pct,
            zeitpunkt: stop.ankunftszeit,
            critical: true,
          },
          {
            distanzM: stop.distanz_m,
            socPct: stop.ziel_soc_pct,
            zeitpunkt: stop.ankunftszeit,
            critical: true,
          },
          {
            distanzM: stop.distanz_m + 0.5,
            socPct: stop.ziel_soc_pct,
            zeitpunkt: stop.abfahrtszeit,
            critical: true,
          },
        ]),
      ].sort((a, b) => a.distanzM - b.distanzM),
    );
    // Post-Processing fuer waypoint_stops: Korrigiere die SoC-Werte aller
    // FAHREN-Frames nach dem Stop. Die Backend-Berechnung verwendet u.U. die
    // falsche Baseline (Start-SoC statt waypoint.ziel_soc_pct) fuer die
    // ersten post-stop Frames. Korrektur: Alle post-stop FAHREN-Frames
    // auf waypoint.ziel_soc_pct setzen, damit der Gradient sofort den
    // korrekten Wert zeigt.
    // Pre-compute the spliced cumulative distance for each waypoint stop
    // Use the DEPARTURE sample (arrival + 0.5) for comparison, as that's
    // where the high SoC actually starts after the stop.
    // Pre-compute the spliced cumulative distance for each waypoint stop
    // Use stop.distanz_m directly since that's the waypoint position, not
    // splicedRoute.samples which might not align exactly with the stop.
    // Pre-compute the spliced cumulative distance for each waypoint stop
    // by finding the sample at or nearest to the stop (in spliced distance).
    const waypointStopDistances: Record<string, number> = {};
    for (const stop of simulationResult.waypoint_stops) {
      // Use position as the key since ID may be undefined
      const key =
        stop.position?.[0]?.toFixed(6) + "," + stop.position?.[1]?.toFixed(6);
      const matchingSample = splicedRoute.samples.find(
        (s) => Math.abs(s.distanzM - (stop.distanz_m + 0)) < 10,
      );
      if (matchingSample) {
        waypointStopDistances[key] = matchingSample.distanzM;
      } else {
        // Fallback: use the original distance
        waypointStopDistances[key] = stop.distanz_m + 0;
      }
    }
    const correctedSamples = splicedRoute.samples.map((sample) => {
      let correctedSocPct = sample.socPct;
      for (const stop of simulationResult.waypoint_stops) {
        const key =
          stop.position?.[0]?.toFixed(6) + "," + stop.position?.[1]?.toFixed(6);
        const stopDist = waypointStopDistances[key] ?? stop.distanz_m;
        if (stopDist !== undefined && sample.distanzM >= stopDist) {
          correctedSocPct = stop.ziel_soc_pct;
        }
      }
      return { ...sample, socPct: correctedSocPct, critical: true };
    });
    // Add waypoint stop sample at the stop position (high SoC).
    const waypointSamples = simulationResult.waypoint_stops.map((stop) => {
      const key =
        stop.position?.[0]?.toFixed(6) + "," + stop.position?.[1]?.toFixed(6);
      const stopDist = waypointStopDistances[key] ?? stop.distanz_m;
      return {
        distanzM: stopDist,
        socPct: stop.ziel_soc_pct,
        zeitpunkt: stop.ankunftszeit,
        critical: true,
      };
    });
    // Merge and sort corrected samples with waypoint samples
    const splicedRouteWithCorrectedSamples = {
      ...splicedRoute,
      samples: [...correctedSamples, ...waypointSamples].sort(
        (a, b) => a.distanzM - b.distanzM,
      ),
    };
    const routeCoordinates = splicedRouteWithCorrectedSamples.coordinates;

    const routeGeoJson: GeoJSON.Feature<GeoJSON.LineString> = {
      type: "Feature" as const,
      properties: {},
      geometry: {
        type: "LineString" as const,
        coordinates: routeCoordinates,
      },
    };

    // Route als GeoJSON Source (lineMetrics fuer `line-progress`, siehe
    // `buildSocGradientExpression`) + zwei Layer: dezente Basislinie +
    // SoC-Gradient obendrauf. Ersetzt die vorherigen 1+N Sources/Layer
    // (ein flacher Layer pro Farbsegment) durch genau zwei GPU-Layer,
    // unabhängig von der Trip-Länge.
    map.addSource("route", {
      type: "geojson" as const,
      lineMetrics: true,
      data: routeGeoJson,
    });
    map.addLayer({
      id: "route",
      type: "line" as const,
      source: "route",
      paint: {
        "line-color": "#3b82f6",
        "line-width": 3,
        "line-opacity": 0.6,
      } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);

    // Hover-Tooltip: bei Mausbewegung ueber der Route die Mausposition auf
    // die tatsaechlich gezeichnete (gesplicete) Linie projizieren und den
    // Stuetzpunkt mit der naechstgelegenen Distanz-entlang-der-Linie
    // anzeigen - NICHT den raeumlich naechstgelegenen Simulationsframe (der
    // wenige Kilometer nach einem Ladehalt-Abstecher noch die Vor-Lade-Werte
    // liefern konnte, siehe `buildRouteHoverText`).
    const handleRouteMouseMove = (e: MapMouseEvent) => {
      const distAlongM = projectDistanceAlongLineM(routeCoordinates, [
        e.lngLat.lng,
        e.lngLat.lat,
      ]);
      const sample = findNearestRouteSample(
        splicedRouteWithCorrectedSamples.samples,
        distAlongM,
      );
      if (!sample) return;
      setRouteHoverInfo({ x: e.point.x, y: e.point.y, sample });
    };
    const handleRouteMouseLeave = () => setRouteHoverInfo(null);
    map.on("mousemove", "route", handleRouteMouseMove);
    map.on("mouseleave", "route", handleRouteMouseLeave);
    routeHoverHandlersRef.current = {
      move: handleRouteMouseMove,
      leave: handleRouteMouseLeave,
    };

    // SoC-Gradient: EIN Source+Layer PRO Leg (siehe `splitRouteIntoLegs`)
    // statt eines gemeinsamen Layers ueber die gesamte Route - MapLibre
    // backt `line-gradient` in eine feste 256-Texel-Texture ueber die
    // GESAMTE Linienlaenge; bei einer einzigen, mehrere hundert/tausend km
    // langen Route waere ein an einem Ladehalt technisch korrekt auf <1 m
    // kollabierter SoC-Sprung (siehe `CHARGE_JUMP_EPSILON_M`) weit unter der
    // Texture-Aufloesung und wuerde schlicht nicht dargestellt (dokumentiertes
    // MapLibre/Mapbox-Verhalten). Jeder Leg bekommt so seine eigene, viel
    for (const [legIndex, leg] of splitRouteIntoLegs(
      splicedRouteWithCorrectedSamples,
    ).entries()) {
      const sourceId = `route-leg-${legIndex}`;
      const legGeoJson: GeoJSON.Feature<GeoJSON.LineString> = {
        type: "Feature" as const,
        properties: {},
        geometry: {
          type: "LineString" as const,
          coordinates: leg.coordinates,
        },
      };
      map.addSource(sourceId, {
        type: "geojson" as const,
        lineMetrics: true,
        data: legGeoJson,
      });
      map.addLayer({
        id: `${sourceId}-gradient`,
        type: "line",
        source: sourceId,
        paint: {
          "line-width": 4,
          "line-opacity": 0.9,
          "line-gradient": buildSocGradientExpression(
            leg.samples,
            leg.totalDistanceM,
          ),
        },
      } as unknown as LayerSpecification);
      routeLegSourceIdsRef.current.push(sourceId);
    }
    // Baustellen-Marker hinzufügen: ein Marker pro Eintrag aus
    // `simulationResult.construction_zones`, mit Klick-Popup für Details.
    // WICHTIG: Vor Ladehalt-Markern hinzufügen, damit Ladehalt-Marker
    // über den Baustellen-Markern liegen (reine DOM-Reihenfolge, siehe
    // `raiseStopMarkersToTop` fürs analoge Prinzip bei Stopp-Markern).
    for (const zone of simulationResult.construction_zones) {
      const element = buildConstructionZoneMarkerElement();
      const marker = new Marker({ element })
        .setLngLat(toLngLat(zone.position))
        .setPopup(
          new Popup({ offset: 12 }).setHTML(
            buildConstructionZonePopupHtml(zone),
          ),
        )
        .addTo(map);
      constructionZoneMarkersRef.current.push(marker);
    }

    // Ladehalt-Marker hinzufügen: ein Marker pro tatsächlichem Ladehalt aus
    // `simulationResult.charging_stops` (nicht pro LADEN-Frame – ein Halt
    // kann mehrere Frames erzeugen, siehe `buildChargingStopMarkerElement`),
    // exakt auf der Position der Ladestation, mit Klick-Popup für Details.
    for (const stop of simulationResult.charging_stops) {
      const element = buildChargingStopMarkerElement();
      const marker = new Marker({ element })
        .setLngLat(toLngLat(stop.position))
        .setPopup(
          new Popup({ offset: 14 }).setHTML(buildChargingStopPopupHtml(stop)),
        )
        .addTo(map);
      chargingStopMarkersRef.current.push(marker);
    }

    // Stopp-Marker koennen zu diesem Zeitpunkt bereits existieren (siehe
    // zweiter Marker-Effekt unten) - jetzt neu hinzugekommene Baustellen-/
    // Ladehalt-Marker wieder unter sie schieben (siehe `raiseStopMarkersToTop`).
    raiseStopMarkersToTop();

    // Kamera auf gesamte Route zentrieren
    const bounds = routeCoordinates.reduce(
      (b, coord) => b.extend(coord),
      new LngLatBounds(),
    );
    map.fitBounds(bounds, { padding: 50 });
  }, [simulationResult, isMapLoaded]);

  // Stopp-Marker verwalten: erstellen/aktualisieren/entfernen passend zu
  // `stops`. Bestehende Marker werden per setLngLat verschoben statt
  // neu erzeugt, um Flicker und Drag-State-Verlust zu vermeiden.
  // Stopps ohne position werden übersprungen (noch nicht geocoded).
  // Popup-Inhalt wird bei jedem Lauf neu gesetzt (auch für bestehende
  // Marker), damit ein neu berechnetes Simulationsergebnis dessen
  // Aufenthalts-Details (siehe `findWaypointStopAt`/`buildStopPopupHtml`)
  // nachzieht, ohne den Marker selbst (und damit Drag-State) neu zu
  // erzeugen.
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const markers = markersRef.current;
    const stops = stopsProp ?? [];
    const waypointStops = simulationResult?.waypoint_stops ?? [];
    const seen = new Set<string>();

    // Nur Stopps mit aufgelöster Position rendern.

    for (let i = 0; i < stops.length; i++) {
      const stop = stops[i];
      if (!stop.position) continue;

      seen.add(stop.id);
      const role = stopRole(i, stops.length);
      const waypointStop = findWaypointStopAt(waypointStops, stop.position);
      const popupHtml = buildStopPopupHtml(stop, role, waypointStop);
      const existing = markers[stop.id];
      if (existing) {
        // Nur Position aktualisieren – Marker-Instanz und Drag-State bleiben
        existing.setLngLat(toLngLat(stop.position));
        existing.setPopup(new Popup({ offset: 14 }).setHTML(popupHtml));
      } else {
        // Neuen Marker erzeugen
        const element = buildMarkerElement(role);
        const marker = new Marker({ element, draggable: true });
        marker.setLngLat(toLngLat(stop.position));
        marker.setPopup(new Popup({ offset: 14 }).setHTML(popupHtml));
        marker.on("dragend", () => {
          const ll = marker.getLngLat();
          // [lat, lon] – projektweite Konvention (siehe geo-utils.ts)
          onStopMove?.(stop.id, [ll.lat, ll.lng]);
        });
        marker.addTo(map);
        markers[stop.id] = marker;
      }
    }

    // Marker entfernen, deren ID nicht mehr in stops enthalten ist
    for (const id of Object.keys(markers)) {
      if (!seen.has(id)) {
        markers[id].remove();
        delete markers[id];
      }
    }

    // Sicherstellen, dass Stopp-Marker (auch neu erzeugte) ueber ggf. schon
    // vorhandenen Baustellen-/Ladehalt-Markern liegen (siehe
    // `raiseStopMarkersToTop`).
    raiseStopMarkersToTop();

    return () => {
      // Beim Unmount alle Marker entfernen
      for (const id of Object.keys(markers)) {
        markers[id].remove();
        delete markers[id];
      }
    };
  }, [stopsProp, isMapLoaded, onStopMove, simulationResult]);

  // Auswahlmodus: Kartenklick setzt Position für pickingStopId.
  // Handler wird nur registriert, wenn pickingStopId nicht-null ist.
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const canvas = map.getCanvas();

    if (!pickingStopId) {
      // Kein Auswahlmodus – Cursor zurücksetzen, kein Click-Handler aktiv
      canvas.style.cursor = "";
      return;
    }

    // Cursor als Fadenkreuz signalisieren Auswahl
    canvas.style.cursor = "crosshair";

    const onClick = (e: { lngLat: { lat: number; lng: number } }) => {
      // [lat, lon] – projektweite Konvention
      onPickPosition?.(pickingStopId, [e.lngLat.lat, e.lngLat.lng]);
    };
    map.on("click", onClick);

    return () => {
      map.off("click", onClick);
      canvas.style.cursor = "";
    };
  }, [pickingStopId, isMapLoaded, onPickPosition]);

  /** Helfer: Supercharger von Tesla aktualisieren und Popover neu bauen. */
  async function handleSuperchargerRefresh(
    station: SuperchargerStation,
    popup: Popup,
  ) {
    const slug = station.slug;
    setRefreshingSlug(slug);

    try {
      const updated = await refreshSupercharger(slug);

      // Station in der Liste aktualisieren
      setSuperchargerStations((prev) =>
        prev.map((s) => (s.slug === slug ? updated : s)),
      );

      // Popover-Inhalt mit aktualisierten Daten neu bauen
      const popupEl = buildSuperchargerPopoverElement(updated, false, () =>
        handleSuperchargerRefresh(updated, popup),
      );
      popup.setDOMContent(popupEl);
    } catch (err: unknown) {
      // Fehler im Popover anzeigen, letzten bekannten Stand beibehalten
      const msg = err instanceof Error ? err.message : "Fehler";
      const popupEl = buildSuperchargerPopoverElement(station, false, () =>
        handleSuperchargerRefresh(station, popup),
      );
      const errorBanner = document.createElement("div");
      errorBanner.style.cssText =
        "color:#ef4444;font-size:12px;margin-top:4px;";
      errorBanner.textContent = `Fehler: ${msg}`;
      popupEl.appendChild(errorBanner);
      popup.setDOMContent(popupEl);
    } finally {
      setRefreshingSlug(null);
    }
  }

  /** Fügt die (dauerhaft vorhandene, zunächst ausgeblendete) Source und
   * die Cluster-/Punkt-Layer für das Supercharger-Overlay hinzu und
   * registriert die Klick-/Hover-Handler einmalig. Sichtbarkeit wird
   * separat über `visibility` gesteuert (siehe Effects unten) statt die
   * Layer bei jedem Ein-/Ausblenden neu anzulegen – vermeidet doppelt
   * registrierte Event-Handler bei wiederholtem Toggle. Klick-Handler
   * lesen Stationsdaten über `superchargerStationsRef`, nicht über einen
   * Closure-Stand von `superchargerStations`/`refreshingSlug`.
   */
  function addSuperchargerLayers(map: Map) {
    if (map.getSource("superchargers")) return;

    map.addSource("superchargers", {
      type: "geojson",
      data: buildSuperchargerGeoJson([]),
      cluster: true,
      clusterRadius: 50,
      clusterMaxZoom: 14,
    });

    map.addLayer({
      id: "supercharger-clusters",
      type: "circle",
      source: "superchargers",
      filter: ["has", "point_count"],
      layout: { visibility: "none" },
      paint: {
        "circle-color": "#dc2626",
        "circle-radius": ["step", ["get", "point_count"], 14, 25, 18, 100, 24],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);

    map.addLayer({
      id: "supercharger-cluster-count",
      type: "symbol",
      source: "superchargers",
      filter: ["has", "point_count"],
      layout: {
        visibility: "none",
        "text-field": ["get", "point_count_abbreviated"],
        "text-font": ["Noto Sans Bold"],
        "text-size": 12,
      } satisfies LayerSpecification["layout"],
      paint: {
        "text-color": "#ffffff",
      } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);

    map.addLayer({
      id: "supercharger-unclustered",
      type: "circle",
      source: "superchargers",
      filter: ["!", ["has", "point_count"]],
      layout: { visibility: "none" },
      paint: {
        "circle-color": "#dc2626",
        "circle-radius": 8,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);

    map.on("click", "supercharger-clusters", (e) => {
      const feature = map.queryRenderedFeatures(e.point, {
        layers: ["supercharger-clusters"],
      })[0];
      const clusterId = feature?.properties?.cluster_id as number | undefined;
      if (
        !feature ||
        clusterId === undefined ||
        feature.geometry.type !== "Point"
      ) {
        return;
      }
      const source = map.getSource("superchargers") as GeoJSONSource;
      const coordinates = feature.geometry.coordinates as [number, number];
      source.getClusterExpansionZoom(clusterId).then((zoom) => {
        map.easeTo({ center: coordinates, zoom });
      });
    });

    map.on("click", "supercharger-unclustered", (e) => {
      const slug = e.features?.[0]?.properties?.slug as string | undefined;
      const station = superchargerStationsRef.current.find(
        (s) => s.slug === slug,
      );
      if (!station) return;

      activePopoverRef.current?.remove();
      const popup = new Popup({ offset: 14, closeButton: true });
      const popupEl = buildSuperchargerPopoverElement(
        station,
        refreshingSlug === station.slug,
        () => handleSuperchargerRefresh(station, popup),
      );
      popup.setDOMContent(popupEl);
      popup.setLngLat([station.longitude, station.latitude]);
      popup.addTo(map);
      activePopoverRef.current = popup;
    });

    for (const layerId of [
      "supercharger-clusters",
      "supercharger-unclustered",
    ]) {
      map.on("mouseenter", layerId, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layerId, () => {
        map.getCanvas().style.cursor = "";
      });
    }
  }

  // Supercharger-Layer einmalig anlegen (ausgeblendet) - siehe
  // `addSuperchargerLayers`.
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    addSuperchargerLayers(mapRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMapLoaded]);

  // Sichtbarkeit umschalten (Layer bleiben angelegt, nur `visibility`
  // wechselt - vermeidet Neuanlegen/erneutes Registrieren der Handler bei
  // jedem Toggle).
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const visibility = superchargerVisible ? "visible" : "none";
    for (const layerId of SUPERCHARGER_LAYER_IDS) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, "visibility", visibility);
      }
    }
  }, [superchargerVisible, isMapLoaded]);

  // Stationsdaten laden, sobald das Overlay eingeblendet wird; beim
  // Ausblenden zuruecksetzen, damit ein erneutes Einblenden frische Daten
  // laedt statt (potenziell veralteter) Cache-Daten.
  useEffect(() => {
    if (!superchargerVisible) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSuperchargerStations([]);
      setSuperchargerError(null);
      return;
    }
    if (
      !isMapLoaded ||
      superchargerStations.length > 0 ||
      superchargerLoading
    ) {
      return;
    }
    setSuperchargerLoading(true);
    setSuperchargerError(null);
    fetchSuperchargers()
      .then((stations) => {
        setSuperchargerStations(stations);
        setSuperchargerLoading(false);
      })
      .catch((err: unknown) => {
        setSuperchargerLoading(false);
        const msg = err instanceof Error ? err.message : "Unbekannter Fehler";
        setSuperchargerError(msg);
      });
    // superchargerStations.length/superchargerLoading bewusst nicht in den
    // Dependencies - wir setzen den State hier selbst.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [superchargerVisible, isMapLoaded]);

  // GeoJSON-Source synchron mit dem State halten (initiales Laden +
  // Refresh via `handleSuperchargerRefresh`) und aktuellen Stand für die
  // Klick-Handler in `addSuperchargerLayers` referenzierbar halten.
  useEffect(() => {
    superchargerStationsRef.current = superchargerStations;
    if (!isMapLoaded || !mapRef.current) return;
    const source = mapRef.current.getSource("superchargers") as
      GeoJSONSource | undefined;
    source?.setData(buildSuperchargerGeoJson(superchargerStations));
  }, [superchargerStations, isMapLoaded]);

  return (
    <div
      className="map-container"
      ref={mapContainerRef}
      style={{ width: "100%", height: "100%" }}
    >
      {/* Supercharger-Toggle-Button */}
      <button
        onClick={onToggleSuperchargers}
        style={{
          position: "absolute",
          top: "1rem",
          left: "1rem",
          zIndex: 10,
          padding: "8px 14px",
          fontSize: "13px",
          fontWeight: 600,
          border: "none",
          borderRadius: "6px",
          cursor: "pointer",
          background: superchargerVisible ? "#2563eb" : "#ffffff",
          color: superchargerVisible ? "#ffffff" : "#333333",
          boxShadow: "0 1px 6px rgba(0,0,0,0.18)",
          display: "flex",
          alignItems: "center",
          gap: "6px",
        }}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill={superchargerVisible ? "#fff" : "#2563eb"}
        >
          <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
        </svg>
        Supercharger
      </button>

      {/* Lade-Indikator */}
      {superchargerLoading && (
        <div
          style={{
            position: "absolute",
            top: "1rem",
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10,
            padding: "6px 16px",
            fontSize: "13px",
            borderRadius: "6px",
            background: "rgba(0,0,0,0.7)",
            color: "#fff",
          }}
        >
          Lade Supercharger…
        </div>
      )}

      {/* Fehler-Indikator */}
      {superchargerError && (
        <div
          style={{
            position: "absolute",
            bottom: "1rem",
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10,
            padding: "6px 16px",
            fontSize: "13px",
            borderRadius: "6px",
            background: "#ef4444",
            color: "#fff",
          }}
        >
          {superchargerError}
        </div>
      )}

      {/* Routen-Hover-Tooltip: Datum/Zeit + SoC am naechstgelegenen Streckenpunkt */}
      {routeHoverInfo && (
        <div
          style={{
            position: "absolute",
            left: routeHoverInfo.x + 14,
            top: routeHoverInfo.y + 14,
            zIndex: 10,
            padding: "4px 8px",
            fontSize: "12px",
            borderRadius: "4px",
            background: "rgba(0,0,0,0.75)",
            color: "#fff",
            pointerEvents: "none",
            whiteSpace: "nowrap",
          }}
        >
          {buildRouteHoverText(routeHoverInfo.sample)}
        </div>
      )}
    </div>
  );
}

export default MapVisualization;
