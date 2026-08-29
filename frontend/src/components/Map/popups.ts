import { haversineDistanceM } from "../../utils/geo-utils";
import { formatZeitpunkt } from "../../utils/datetime-utils";
import { formatCostOrDash } from "../../utils/currency-utils";
import { roleToLabel, type StopRole } from "./markers";
import type {
  ChargingStop,
  ConstructionZone,
  ConstructionZoneEvent,
  WaypointStop,
} from "../../types";
import type { Stop } from "../../types/trip-request";
import type { RouteSample } from "../../utils/route-line";

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
function formatLaenge(m: number): string {
  if (m >= 1000) {
    return `${(m / 1000).toLocaleString("de-DE", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    })} km`;
  }
  return `${m} m`;
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

  const lengthRow =
    zone.laenge_m !== null
      ? `<div style="margin-bottom:12px;"><strong style="font-size:14px;">Länge</strong><table style="width:100%;border-collapse:collapse;margin-top:4px;"><tr><td style="padding:2px 4px;color:#666;">Länge</td><td style="padding:2px 4px;text-align:right;">${formatLaenge(zone.laenge_m)}</td></tr></table></div>`
      : "";
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;">` +
    `${lengthRow}` +
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
