/** Komponente zur Anzeige der Reisezusammenfassung nach der Simulation.
 *
 * Zeigt Gesamtdistanz, Fahrzeit, Ladezeit und Start-/Ziel-SoC. Der
 * detaillierte, chronologische "Zeitplan" (alle Stopps, Ladehalte und vom
 * Nutzer terminierten Fähren mit Streckenabschnitten, SoC-Verlauf und
 * geladener Energie) wird platzsparend in einem Vollbild-Modal angezeigt,
 * das über einen Button geöffnet wird (siehe `Modal`).
 */

import { useEffect, useState } from "react";

import {
  estimateWaypointTimings,
  estimatePositionTiming,
  cumulativeDistancesKm,
  findNearestFrameIndex,
} from "../utils/timing-utils";
import { formatShortDate, formatTime } from "../utils/datetime-utils";
import { formatCost, formatCostOrDash } from "../utils/currency-utils";
import { Modal } from "./Modal";
import { convertAllToEUR } from "../utils/currency-conversion";
import { Popover } from "./Popover";
import type { ChargingCostByCurrency, TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";

export interface TripSummaryProps {
  result: TripSimulationResult;
  stops: Stop[];
}

/** Kurzadresse: nur der erste Teil vor dem ersten Komma (Stadt oder Straße). */
function shortAddress(address: string): string {
  const comma = address.indexOf(",");
  return comma > 0 ? address.slice(0, comma) : address;
}

/** Gibt eine kurze Anzeigeadresse eines Stopps zurück: Kurzform der Adresse,
 *  sonst gerundete Koordinaten, sonst "(unbekannt)". */
function stopLabel(stop: Stop): string {
  if (stop.address) return shortAddress(stop.address);
  if (stop.position) {
    return `(${stop.position[0].toFixed(4)}, ${stop.position[1].toFixed(4)})`;
  }
  return "(unbekannt)";
}

/** Ein Eintrag im vereinheitlichten, chronologischen Zeitplan - entweder ein
 *  Nutzer-Stopp, ein Ladehalt oder eine terminierte Fähre. */
export interface TimePlanEntry {
  key: string;
  art: "Stopp" | "Ladehalt" | "Fähre";
  label: string;
  arrival: string | null;
  departure: string | null;
  /** Gefahrene Strecke seit dem vorherigen Eintrag in km, oder null, wenn der
   *  Zeitpunkt dieses oder des vorherigen Eintrags nicht ermittelbar war
   *  (insbesondere der erste Eintrag der Route). */
  distanceSinceLastKm: number | null;
  /** Fahrzeit seit dem vorherigen Eintrag in Minuten, gleiche
   *  Verfügbarkeitsbedingung wie `distanceSinceLastKm`. */
  durationSinceLastMin: number | null;
  /** SoC bei Ankunft in %, oder null (z. B. am Start, der keine Ankunft hat). */
  arrivalSocPct: number | null;
  /** SoC bei Abfahrt in %, oder null (z. B. am Ziel, das keine Abfahrt hat). */
  departureSocPct: number | null;
  /** Während eines Ladehalts geladene Energie in kWh, sonst null. */
  energyChargedKwh: number | null;
  /** Geschätzte Kosten dieses Ladehalts, oder null (kein Ladehalt oder keine
   *  gecachten Preisdaten für die Station vorhanden). */
  estimatedCost: number | null;
  /** ISO-4217-Währung von `estimatedCost`, oder null (siehe `estimatedCost`). */
  costCurrency: string | null;
}

/** Baut den vereinheitlichten, chronologischen Zeitplan aus Stopps, Ladehalten
 *  und erkannten Fähren, sortiert nach Ankunft (Stopps ohne Ankunft, z. B.
 *  der Start, werden nach ihrer Abfahrt einsortiert). Fähren ohne vom Nutzer
 *  vorgegebene Zeit erhalten eine geschätzte Ankunfts-/Abfahrtszeit anhand
 *  ihrer Bounding-Box-Mitte (siehe `estimatePositionTiming`), damit auch sie
 *  chronologisch einsortiert werden können (gleiches Vorgehen wie in
 *  `route-entries.ts` für die Routen-Hoverkarte).
 *
 * Ergänzt für jeden Eintrag (soweit ermittelbar) die seit dem vorherigen
 * Eintrag gefahrene Strecke/Zeit sowie den SoC bei Ankunft/Abfahrt, indem der
 * jeweils nächstgelegene Simulationsframe herangezogen wird (siehe
 * `findNearestFrameIndex`/`cumulativeDistancesKm`). Für Ladehalte stammen
 * SoC und geladene Energie direkt aus dem `ChargingStop` (exakt statt
 * geschätzt). */
export function buildTimePlan(
  result: TripSimulationResult,
  stops: Stop[],
): TimePlanEntry[] {
  const { frames } = result;
  const timings = estimateWaypointTimings(frames, stops);

  // Exakter Zwischenstopp-Aufenthalt (Ankunft/Abfahrt/SoC/geladene Energie
  // aus `result.waypoint_stops`, siehe `ZwischenstoppAufenthalt` im Backend)
  // statt der nur GESCHAETZTEN Werte aus `estimateWaypointTimings` (nächst-
  // gelegener Simulationsframe) - Koordinaten sind identisch, da `Stop.
  // position` unveraendert als `Waypoint.coordinate` an das Backend
  // durchgereicht wird.
  const stopEntries: TimePlanEntry[] = stops.map((stop, i) => {
    const waypointStop = stop.position
      ? result.waypointStops.find(
          (w) =>
            w.position[0] === stop.position?.[0] &&
            w.position[1] === stop.position?.[1],
        )
      : undefined;
    return {
      key: `stopp-${stop.id}`,
      art: "Stopp",
      label: stopLabel(stop),
      arrival: waypointStop?.arrivalTime ?? timings[i]?.arrival ?? null,
      departure: waypointStop?.departureTime ?? timings[i]?.departure ?? null,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      arrivalSocPct: waypointStop?.arrivalSocPct ?? null,
      departureSocPct: waypointStop?.targetSocPct ?? null,
      energyChargedKwh: waypointStop?.energyChargedKwh ?? null,
      estimatedCost: null,
      costCurrency: null,
    };
  });

  const chargingStopEntries: TimePlanEntry[] = result.chargingStops.map(
    (stop) => ({
      key: `ladehalt-${stop.stationId}-${stop.arrivalTime}`,
      art: "Ladehalt",
      label: stop.name,
      arrival: stop.arrivalTime,
      departure: stop.departureTime,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      arrivalSocPct: stop.arrivalSocPct,
      departureSocPct: stop.targetSocPct,
      energyChargedKwh: stop.energyChargedKwh,
      estimatedCost: stop.estimatedCost,
      costCurrency: stop.currency,
    }),
  );

  const ferryEntries: TimePlanEntry[] = result.detectedFerries.map((f, idx) => {
    let arrival = f.departure;
    let departure = f.arrival;
    if (arrival === null || departure === null) {
      const bboxCenter: [number, number] = [
        (f.bboxSw[0] + f.bboxNe[0]) / 2,
        (f.bboxSw[1] + f.bboxNe[1]) / 2,
      ];
      const estimated = estimatePositionTiming(bboxCenter, frames);
      arrival = arrival ?? estimated.arrival;
      departure = departure ?? estimated.departure;
    }
    return {
      key: `ferry-${idx}-${f.name}`,
      art: "Fähre",
      label: f.name,
      arrival,
      departure,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      arrivalSocPct: null,
      departureSocPct: null,
      energyChargedKwh: null,
      estimatedCost: null,
      costCurrency: null,
    };
  });

  const sorted = [...stopEntries, ...chargingStopEntries, ...ferryEntries].sort(
    (a, b) => {
      const timeA = a.arrival ?? a.departure ?? "";
      const timeB = b.arrival ?? b.departure ?? "";
      return timeA.localeCompare(timeB);
    },
  );

  // Zweiter Durchlauf: Strecke/Zeit seit dem vorherigen Eintrag sowie (für
  // Stopp/Fähre) SoC bei Ankunft/Abfahrt anhand der nächstgelegenen
  // Simulationsframes ergänzen.
  const cumulative = cumulativeDistancesKm(frames);
  let prevExitIso: string | null = null;
  let prevExitIdx: number | null = null;

  for (const entry of sorted) {
    const arrivalIso = entry.arrival ?? entry.departure;
    const exitIso = entry.departure ?? entry.arrival;
    const arrivalIdx = arrivalIso
      ? findNearestFrameIndex(arrivalIso, frames)
      : null;
    const exitIdx = exitIso ? findNearestFrameIndex(exitIso, frames) : null;

    if (
      prevExitIdx !== null &&
      prevExitIso !== null &&
      arrivalIdx !== null &&
      arrivalIso !== null
    ) {
      entry.distanceSinceLastKm = Math.max(
        0,
        cumulative[arrivalIdx] - cumulative[prevExitIdx],
      );
      entry.durationSinceLastMin = Math.max(
        0,
        (new Date(arrivalIso).getTime() - new Date(prevExitIso).getTime()) /
          60000,
      );
    }

    if (entry.art !== "Ladehalt") {
      // Exakte Werte (aus `result.waypoint_stops`, siehe oben) NICHT durch
      // die nur geschaetzte Frame-Naeherung ueberschreiben.
      if (entry.arrivalSocPct === null) {
        entry.arrivalSocPct =
          entry.arrival && arrivalIdx !== null
            ? frames[arrivalIdx].socPct
            : null;
      }
      if (entry.departureSocPct === null) {
        entry.departureSocPct =
          entry.departure && exitIdx !== null ? frames[exitIdx].socPct : null;
      }
    }

    prevExitIso = exitIso;
    prevExitIdx = exitIdx;
  }

  return sorted;
}

function TripSummary({ result, stops }: TripSummaryProps) {
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [eurTotal, setEurTotal] = useState<number | null>(null);
  const [eurBreakdown, setEurBreakdown] = useState<
    Array<{ currency: string; originalAmount: number; eurAmount: number }>
  >([]);
  const schedule = buildTimePlan(result, stops);

  useEffect(() => {
    let cancelled = false;
    if (result.totalChargingCost.length > 0) {
      convertAllToEUR(result.totalChargingCost).then(
        ({ totalEUR, breakdown }) => {
          if (cancelled) return;
          setEurTotal(totalEUR);
          setEurBreakdown(breakdown);
        },
      );
    } else {
      Promise.resolve().then(() => {
        if (cancelled) return;
        setEurTotal(null);
        setEurBreakdown([]);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [result.totalChargingCost]);

  return (
    <div
      style={{
        padding: "1rem",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          marginBottom: "1rem",
        }}
      >
        <tbody>
          <tr>
            <td style={labelCellStyle}>Distanz</td>
            <td style={valueCellStyle}>{formatKm(result.totalDistanceKm)}</td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Fahrzeit</td>
            <td style={valueCellStyle}>
              {formatMinutes(result.totalDrivingTimeMin)}
            </td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Ladezeit</td>
            <td style={valueCellStyle}>
              {formatMinutes(result.totalChargingTimeMin)}
            </td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Reisezeit</td>
            <td style={valueCellStyle}>
              {formatMinutes(
                result.totalDrivingTimeMin + result.totalChargingTimeMin,
              )}
            </td>
          </tr>
          {result.totalWaitingTimeMin > 0 && (
            <tr>
              <td style={labelCellStyle}>Wartezeit</td>
              <td style={valueCellStyle}>
                {formatMinutes(result.totalWaitingTimeMin)}
              </td>
            </tr>
          )}
          <tr>
            <td style={labelCellStyle}>Start-SoC</td>
            <td style={valueCellStyle}>{formatSoc(result.startSocPct)}</td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Ziel-SoC</td>
            <td style={valueCellStyle}>{formatSoc(result.targetSocPct)}</td>
          </tr>
          {result.detectedFerries.length > 0 && (
            <tr>
              <td style={labelCellStyle}>Fähren</td>
              <td style={valueCellStyle}>
                {result.detectedFerries.map((f) => f.name).join(", ")}
              </td>
            </tr>
          )}
          {result.chargingStops.length > 0 && (
            <tr>
              <td style={labelCellStyle}>Ladekosten</td>
              <td style={valueCellStyle}>
                {eurTotal !== null ? (
                  <Popover
                    content={
                      <>
                        <div
                          style={{ fontWeight: 600, marginBottom: "0.25rem" }}
                        >
                          Summe: {formatCost(eurTotal, "EUR")}
                        </div>
                        {eurBreakdown.map((b) => (
                          <div
                            key={b.currency}
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              gap: "1rem",
                            }}
                          >
                            <span>
                              {formatCost(b.originalAmount, b.currency)}
                            </span>
                            <span>= {formatCost(b.eurAmount, "EUR")}</span>
                          </div>
                        ))}
                        {result.chargingStopsMissingPricing > 0 && (
                          <div
                            style={{
                              marginTop: "0.25rem",
                              fontSize: "0.75rem",
                              opacity: 0.7,
                            }}
                          >
                            ({result.chargingStopsMissingPricing} Halt
                            {result.chargingStopsMissingPricing === 1
                              ? ""
                              : "e"}{" "}
                            ohne Preisdaten)
                          </div>
                        )}
                      </>
                    }
                  >
                    <span
                      style={{
                        cursor: "help",
                        textDecoration: "underline dotted",
                      }}
                    >
                      {formatCost(eurTotal, "EUR")}
                    </span>
                  </Popover>
                ) : (
                  formatChargingCosts(
                    result.totalChargingCost,
                    result.chargingStopsMissingPricing,
                  )
                )}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <button
        type="button"
        onClick={() => setScheduleOpen(true)}
        style={{
          width: "100%",
          padding: "0.5rem",
          background: "#f3f4f6",
          border: "1px solid #d1d5db",
          borderRadius: "4px",
          cursor: "pointer",
          fontSize: "0.85rem",
          fontWeight: 600,
        }}
      >
        Zeitplan öffnen ({schedule.length} Einträge)
      </button>

      <Modal
        open={scheduleOpen}
        onClose={() => setScheduleOpen(false)}
        title="Zeitplan"
        size="fullscreen"
      >
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={headerCellStyle}>Ort</th>
              <th style={headerCellStyle} colSpan={2}>
                Ankunft
              </th>
              <th style={headerCellStyle}>SoC</th>
              <th style={headerCellStyle} colSpan={2}>
                Abfahrt
              </th>
              <th style={headerCellStyle}>SoC</th>
              <th style={headerCellStyle}>Strecke</th>
              <th style={headerCellStyle}>Dauer</th>
              <th style={headerCellStyle}>Energie</th>
              <th style={headerCellStyle}>Preis</th>
            </tr>
          </thead>
          <tbody>
            {schedule.map((entry) => (
              <tr key={entry.key}>
                <td style={cellStyle}>
                  {entry.label.replace("Tesla Supercharger - ", "")}
                </td>
                <td style={cellStyle}>{formatShortDate(entry.arrival)}</td>
                <td style={rightCellStyle}>{formatTime(entry.arrival)}</td>
                <td style={rightCellStyle}>
                  {formatSocOrDash(entry.arrivalSocPct)}
                </td>
                <td style={cellStyle}>{formatShortDate(entry.departure)}</td>
                <td style={rightCellStyle}>{formatTime(entry.departure)}</td>
                <td style={rightCellStyle}>
                  {formatSocOrDash(entry.departureSocPct)}
                </td>
                <td style={rightCellStyle}>
                  {formatKmOrDash(entry.distanceSinceLastKm)}
                </td>
                <td style={rightCellStyle}>
                  {formatMinutesOrDash(entry.durationSinceLastMin)}
                </td>
                <td style={rightCellStyle}>
                  {formatKwhOrDash(entry.energyChargedKwh)}
                </td>
                <td style={rightCellStyle}>
                  {entry.art === "Ladehalt"
                    ? formatCostOrDash(entry.estimatedCost, entry.costCurrency)
                    : "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Modal>
    </div>
  );
}

/** Formatiert eine Distanz in km mit deutscher Locale (Komma statt Punkt),
 *  fest auf eine Nachkommastelle gerundet. */
function formatKm(km: number): string {
  return `${km.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} km`;
}

/** Formatiert einen SoC-Prozentwert gerundet auf ganze Prozent. */
function formatSoc(pct: number): string {
  return `${Math.round(pct).toLocaleString("de-DE")} %`;
}

function formatMinutes(min: number): string {
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}

function formatMinutesOrDash(min: number | null): string {
  return min === null ? "–" : formatMinutes(min);
}

function formatKmOrDash(km: number | null): string {
  return km === null ? "–" : formatKm(km);
}

function formatSocOrDash(pct: number | null): string {
  return pct === null ? "–" : formatSoc(pct);
}

function formatKwhOrDash(kwh: number | null): string {
  if (kwh === null || kwh === 0) return "–";
  return `${kwh.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} kWh`;
}

/** Formatiert die nach Währung gruppierten Gesamt-Ladekosten
 *  (`TripSimulationResult.total_charging_cost`), z. B. "10,00 € + 40,00 DKK".
 *  Hängt einen Hinweis an, falls für einzelne Ladehalte keine Preisdaten
 *  vorliegen (`charging_stops_missing_pricing`). Ohne jegliche Preisdaten
 *  wird ein entsprechender Platzhaltertext zurückgegeben. */
function formatChargingCosts(
  totals: ChargingCostByCurrency[],
  missingPricingCount: number,
): string {
  const missingSuffix =
    missingPricingCount > 0
      ? ` (${missingPricingCount} Halt${missingPricingCount === 1 ? "" : "e"} ohne Preisdaten)`
      : "";
  if (totals.length === 0) {
    return `Preisdaten noch nicht verfügbar${missingSuffix}`;
  }
  const costsLabel = totals
    .map((entry) => formatCost(entry.amount, entry.currency))
    .join(" + ");
  return `${costsLabel}${missingSuffix}`;
}

const labelCellStyle: React.CSSProperties = {
  padding: "0.25rem 0.5rem",
  fontWeight: 600,
  borderBottom: "1px solid #ddd",
};

const valueCellStyle: React.CSSProperties = {
  padding: "0.25rem 0.5rem",
  textAlign: "right",
  borderBottom: "1px solid #ddd",
};

const headerCellStyle: React.CSSProperties = {
  padding: "0.25rem 0.5rem",
  textAlign: "left",
  borderBottom: "2px solid #333",
  fontWeight: 600,
};

const cellStyle: React.CSSProperties = {
  padding: "0.25rem 0.5rem",
  borderBottom: "1px solid #ddd",
};

const rightCellStyle: React.CSSProperties = {
  ...cellStyle,
  textAlign: "right",
};

export default TripSummary;
export { TripSummary };
