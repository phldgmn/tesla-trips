import { haversineDistanceM } from "../../utils/geo-utils";
import {
  formatTimestamp,
  formatShortDateTime,
} from "../../utils/datetime-utils";
import { formatCostOrDash } from "../../utils/currency-utils";
import { roleToLabel, type StopRole } from "./markers";
import {
  buildPricingSection,
  buildPricingRefreshIconButton,
  type PricingRefreshState,
} from "./superchargers";
import type {
  ChargingStop,
  ConstructionZone,
  ConstructionZoneEvent,
  WaypointStop,
} from "../../types";
import type { Stop } from "../../types/trip-request";
import type { RouteSample } from "../../utils/route-line";
import { escapeHtml } from "../../utils/html-escape";

/** Renders one label/value table row; both cells are HTML-escaped. */
function renderRow([label, value]: [string, string]): string {
  return (
    `<tr><td style="padding:2px 4px;color:#666;">${escapeHtml(label)}</td>` +
    `<td style="padding:2px 4px;text-align:right;">${escapeHtml(value)}</td></tr>`
  );
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

/** Formatiert eine Dauer in Sekunden als "Xh Ymin" bzw. "Ymin". */
export function formatChargingDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}min` : `${minutes}min`;
}
/** Formatiert eine Laenge in Metern: ab 1000 m in km (eine Dezimalstelle),
 *  darunter als ganze Meter. */
function formatLength(m: number): string {
  if (m >= 1000) {
    return `${(m / 1000).toLocaleString("de-DE", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    })} km`;
  }
  return `${Math.round(m)} m`;
}

/** Popup-HTML fuer einen Ladehalt: Name, Ankunfts-/Ziel-SoC samt Uhrzeit, Dauer, geladene Energie, Preis. */
export function buildChargingStopPopupHtml(stop: ChargingStop): string {
  const rows: [string, string][] = [
    ["Ankunft", `${stop.arrivalSocPct.toFixed(0)}% SoC`],
    ["Ankunftszeit", formatTimestamp(stop.arrivalTime)],
    ["Abfahrt", `${stop.targetSocPct.toFixed(0)}% SoC`],
    ["Abfahrtszeit", formatTimestamp(stop.departureTime)],
    ["Dauer", formatChargingDuration(stop.chargingDurationS)],
    ["Geladen", `${stop.energyChargedKwh.toFixed(1)} kWh`],
    ["Preis", formatCostOrDash(stop.estimatedCost, stop.currency)],
  ];
  const rowsHtml = rows.map(renderRow).join("");
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;">` +
    `<strong style="font-size:14px;">${escapeHtml(stop.name)}</strong>` +
    `<table style="width:100%;border-collapse:collapse;margin-top:4px;">${rowsHtml}</table>` +
    `</div>`
  );
}

/** Baut ein DOM-Element fuer einen Ladehalt-Popup: identischer Inhalt wie
 *  `buildChargingStopPopupHtml`, ergaenzt um eine Preis-Refresh-Aktion
 *  (`buildPricingSection`, siehe `superchargers.ts`), wenn fuer diesen Halt
 *  noch keine Preisdaten gecacht sind (`stop.price_per_kwh === null`).
 *  Gecachte Preise werden bereits serverseitig im `estimated_cost`/`Preis`-
 *  Feld angezeigt - hier geht es nur um das manuelle Nachtriggern des
 *  Scrapes fuer Stationen ohne Preisdaten, nicht um eine Live-Neuberechnung
 *  der Reisekosten (dafuer muss die Route neu berechnet werden).
 */
export function buildChargingStopPopupElement(
  stop: ChargingStop,
  pricing: PricingRefreshState,
  onRefreshPricing: () => void,
): HTMLElement {
  const container = document.createElement("div");
  container.innerHTML = buildChargingStopPopupHtml(stop);
  if (stop.pricePerKwh === null) {
    const content = container.firstElementChild;
    content?.appendChild(
      buildPricingSection(pricing, {
        emptyLabel: "Keine Preisdaten fuer diese Station vorhanden.",
      }),
    );
    const actions = document.createElement("div");
    actions.style.cssText =
      "display:flex;gap:6px;margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb;";
    actions.appendChild(
      buildPricingRefreshIconButton(pricing, onRefreshPricing),
    );
    content?.appendChild(actions);
  }
  return container;
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
    return `<div style="font-family:system-ui,sans-serif;font-size:13px;">${escapeHtml(title)}</div>`;
  }
  const rows: [string, string][] = [
    ["Ankunft", `${waypointStop.arrivalSocPct.toFixed(0)}% SoC`],
    ["Ankunftszeit", formatTimestamp(waypointStop.arrivalTime)],
    ["Abfahrt", `${waypointStop.targetSocPct.toFixed(0)}% SoC`],
    ["Abfahrtszeit", formatTimestamp(waypointStop.departureTime)],
  ];
  if (waypointStop.chargingPowerKw !== null) {
    rows.push([
      "Ladeleistung",
      `${waypointStop.chargingPowerKw.toFixed(1)} kW`,
    ]);
    rows.push(["Geladen", `${waypointStop.energyChargedKwh.toFixed(1)} kWh`]);
  }
  const rowsHtml = rows.map(renderRow).join("");
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;">` +
    `<strong style="font-size:14px;">${escapeHtml(title)}</strong>` +
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
const CLOSURE_TYPE_LABELS: Record<string, string> = {
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

export function buildConstructionZonePopupHtml(zone: ConstructionZone): string {
  function eventRowsHtml(event: ConstructionZoneEvent): string {
    const rows: [string, string][] = [];
    if (event.speedLimitKmh !== null) {
      rows.push(["Tempolimit", `${event.speedLimitKmh} km/h`]);
    }
    if (event.detourNotice !== null) {
      rows.push(["Umleitung", event.detourNotice]);
    }
    rows.push(["Gültig ab", formatTimestamp(event.validFrom)]);
    rows.push([
      "Gültig bis",
      event.validTo !== null ? formatTimestamp(event.validTo) : "unbestimmt",
    ]);
    return rows.map(renderRow).join("");
  }

  const eventCount = zone.events.length;
  const sections = zone.events
    .map((event, index) => {
      const eventLabel =
        CLOSURE_TYPE_LABELS[event.closureType] ?? event.closureType;
      const heading =
        eventCount > 1
          ? `${eventLabel} (${index + 1} von ${eventCount})`
          : eventLabel;
      return (
        `<div style="margin-bottom:${index < eventCount - 1 ? "12px" : "0"};">` +
        (index < eventCount - 1
          ? `<hr style="border:none;border-top:1px solid #e5e7eb;margin:8px 0;">`
          : "") +
        `<strong style="font-size:14px;">${escapeHtml(heading)}</strong>` +
        `<table style="width:100%;border-collapse:collapse;margin-top:4px;">${eventRowsHtml(event)}</table>` +
        `</div>`
      );
    })
    .join("");

  const lengthRow =
    zone.lengthM !== null
      ? `<div style="margin-bottom:12px;"><strong style="font-size:14px;">Länge</strong><table style="width:100%;border-collapse:collapse;margin-top:4px;"><tr><td style="padding:2px 4px;color:#666;">Länge</td><td style="padding:2px 4px;text-align:right;">${escapeHtml(formatLength(zone.lengthM))}</td></tr></table></div>`
      : "";
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;">` +
    `${lengthRow}` +
    `${sections}` +
    `</div>`
  );
}

/** Wandelt eine Windrichtung in Grad (0° = N, 90° = O) in eine knappe
 *  16-Punkte-Himmelsrichtung um (z. B. "NW") - fuer den Routen-Hover-
 *  Tooltip, kompakter als der rohe Gradwert. */
function windDirectionToCardinalDirection(deg: number): string {
  const cardinalDirections = [
    "N",
    "NNO",
    "NO",
    "ONO",
    "O",
    "OSO",
    "SO",
    "SSO",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
  ];
  const index = Math.round(deg / 22.5) % cardinalDirections.length;
  return cardinalDirections[index];
}

/** Tooltip-Text fuer den Routen-Hover: Datum/Zeit, SoC, Geschwindigkeit und
 * (falls beruecksichtigt) angenommenes Wetter am naechstgelegenen
 * Streckenpunkt. `sample` stammt aus `findNearestRouteSample()` ueber die
 * per `projectDistanceAlongLineM()` auf die gezeichnete (gesplicete) Linie
 * projizierte Mausposition - NICHT aus einer raeumlichen Naechster-Punkt-
 * Suche ueber `SimulationFrame.position`, die an Stellen, wo sich die Route
 * raeumlich (aber nicht streckenmaessig) annaehert, z. B. kurz nach einem
 * Ladehalt-Abstecher, den falschen (Vor-Lade-)Frame waehlen konnte.
 *
 * Liefert mehrere `\n`-getrennte Zeilen statt einer einzigen langen Zeile,
 * damit der Tooltip vertikal statt horizontal waechst (siehe `whiteSpace:
 * "pre-line"` am Tooltip-Element in `MapVisualization.tsx`). */
export function buildRouteHoverText(sample: RouteSample): string {
  const lines = [
    formatShortDateTime(sample.timestamp ?? null),
    `${sample.socPct.toFixed(0)}% SoC`,
  ];
  if (sample.speedKmh !== undefined) {
    lines.push(`${sample.speedKmh.toFixed(0)} km/h`);
  }
  if (sample.temperatureC !== undefined) {
    lines.push(`${sample.temperatureC.toFixed(0)}°C`);
  }
  if (
    sample.windSpeedKmh !== undefined ||
    sample.windDirectionDeg !== undefined
  ) {
    const direction =
      sample.windDirectionDeg !== undefined
        ? `${windDirectionToCardinalDirection(sample.windDirectionDeg)} `
        : "";
    const speed =
      sample.windSpeedKmh !== undefined
        ? `${sample.windSpeedKmh.toFixed(0)} km/h`
        : "";
    lines.push(`Wind ${direction}${speed}`.trimEnd());
  }
  if (sample.precipitationMm !== undefined && sample.precipitationMm > 0) {
    lines.push(`${sample.precipitationMm.toFixed(1)} mm/h Regen`);
  }
  return lines.join("\n");
}
