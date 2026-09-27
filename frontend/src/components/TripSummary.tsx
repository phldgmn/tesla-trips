/** Component for displaying the trip summary after simulation.
 *
 * Zeigt Gesamtdistanz, Fahrzeit, Ladezeit und Start-/Ziel-SoC. Der
 * detailed, chronological "time plan" (all stops, charging stops and
 * user-terminated ferries with route segments, SoC curve and
 * charged energy) is displayed space-efficiently in a fullscreen modal,
 * that is opened via a button (see `Modal`).
 */

import { forwardRef, useImperativeHandle, useEffect, useState } from "react";

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
import type { ChargingCostByCurrency, TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";
import {
  TripSummaryItemCard,
  TripSummaryItemType,
} from "./TripSummaryItemCard";

export type TripSummaryRef = {
  openSchedule: () => void;
};

export interface TripSummaryProps {
  result: TripSimulationResult;
  stops: Stop[];
  /** Creates the interactive HTML export; the button is hidden when absent,
   *  e.g. inside the export itself. */
  onExport?: () => Promise<void>;
}

/** Short address: only the first part before the first comma (city or street). */
function shortAddress(address: string): string {
  const comma = address.indexOf(",");
  return comma > 0 ? address.slice(0, comma) : address;
}

/** Return a short display address for a stop: short form of address,
 *  otherwise rounded coordinates, otherwise "(unknown)". */
function stopLabel(stop: Stop): string {
  if (stop.address) return shortAddress(stop.address);
  if (stop.position) {
    return `(${stop.position[0].toFixed(4)}, ${stop.position[1].toFixed(4)})`;
  }
  return "(unbekannt)";
}

/** An entry in the unified, chronological time plan - either a
 *  user stop, a charging stop, or a terminated ferry. */
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
  /** SoC at arrival in %, or null (e.g. at the start which has no arrival). */
  arrivalSocPct: number | null;
  /** SoC bei Abfahrt in %, oder null (z. B. am Ziel, das keine Abfahrt hat). */
  departureSocPct: number | null;
  /** Energy charged in kWh during a charging stop, or null. */
  energyChargedKwh: number | null;
  /** Geschätzte Kosten dieses Ladehalts, oder null (kein Ladehalt oder keine
   *  gecachten Preisdaten für die Station vorhanden). */
  estimatedCost: number | null;
  /** ISO-4217-Währung von `estimatedCost`, oder null (siehe `estimatedCost`). */
  costCurrency: string | null;
}

/** Build the unified, chronological time plan from stops, charging stops
 *  and detected ferries, sorted by arrival (stops without arrival, e.g.
 *  the start, are sorted by their departure). Ferries without a user-specified
 *  time get an estimated arrival/departure time based on
 *  their bounding box center (see `estimatePositionTiming`), so they can also
 *  be sorted chronologically (same approach as in
 *  `route-entries.ts` for the route hover card).
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

const TripSummary = forwardRef<TripSummaryRef, TripSummaryProps>(
  ({ result, stops, onExport }, ref) => {
    const [scheduleOpen, setScheduleOpen] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [exportError, setExportError] = useState<string | null>(null);
    const [eurTotal, setEurTotal] = useState<number | null>(null);
    const [eurBreakdown, setEurBreakdown] = useState<
      Array<{ currency: string; originalAmount: number; eurAmount: number }>
    >([]);

    useImperativeHandle(ref, () => ({
      openSchedule: () => setScheduleOpen(true),
    }));

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
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(7rem, 1fr))",
            rowGap: "0.25rem",
            columnGap: "1rem",
            paddingBottom: "1rem",
          }}
        >
          <TripSummaryItemCard
            type={TripSummaryItemType.Distance}
            value={result.totalDistanceKm}
          />
          <TripSummaryItemCard
            type={TripSummaryItemType.DrivingTime}
            value={result.totalDrivingTimeMin}
          />
          <TripSummaryItemCard
            type={TripSummaryItemType.ChargingTime}
            value={result.totalChargingTimeMin}
          />
          <TripSummaryItemCard
            type={TripSummaryItemType.TravelTime}
            value={result.totalDrivingTimeMin + result.totalChargingTimeMin}
          />
          {result.totalWaitingTimeMin > 0 && (
            <TripSummaryItemCard
              type={TripSummaryItemType.WaitingTime}
              value={result.totalWaitingTimeMin}
            />
          )}
          {result.detectedFerries.length > 0 && (
            <TripSummaryItemCard
              type={TripSummaryItemType.Ferries}
              value={result.detectedFerries}
            />
          )}
          {result.chargingStops.length > 0 && (
            <TripSummaryItemCard
              type={TripSummaryItemType.Cost}
              value={{
                eurTotal,
                breakdown: eurBreakdown,
                countMissingPricing: result.chargingStopsMissingPricing,
                stops: result.chargingStops,
              }}
            />
          )}
        </div>

        {onExport && (
          <>
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
            <button
              type="button"
              disabled={exporting}
              onClick={() => {
                setExporting(true);
                setExportError(null);
                onExport()
                  .catch((e: unknown) =>
                    setExportError(e instanceof Error ? e.message : String(e)),
                  )
                  .finally(() => setExporting(false));
              }}
              style={{
                width: "100%",
                marginTop: "0.5rem",
                padding: "0.5rem",
                background: "#f3f4f6",
                border: "1px solid #d1d5db",
                borderRadius: "4px",
                cursor: exporting ? "wait" : "pointer",
                fontSize: "0.85rem",
                fontWeight: 600,
              }}
            >
              {exporting
                ? "Export wird erstellt…"
                : "Interaktiven Export herunterladen"}
            </button>
            {exportError && (
              <div
                style={{
                  color: "#ef4444",
                  fontSize: "0.75rem",
                  marginTop: "0.25rem",
                }}
              >
                Export fehlgeschlagen: {exportError}
              </div>
            )}
          </>
        )}

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
                      ? formatCostOrDash(
                          entry.estimatedCost,
                          entry.costCurrency,
                        )
                      : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      </div>
    );
  },
);

/** Format a distance in km with German locale (comma instead of period),
 *  rounded to exactly one decimal place. */
export function formatKm(km: number): string {
  return `${km.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} km`;
}

/** Formatiert einen SoC-Prozentwert gerundet auf ganze Prozent. */
function formatSoc(pct: number): string {
  return `${Math.round(pct).toLocaleString("de-DE")} %`;
}

export function formatMinutes(min: number): string {
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

/** Format the total charging costs grouped by currency
 *  (`TripSimulationResult.total_charging_cost`), e.g. "10.00 EUR + 40.00 DKK".
 *  Appends a note if pricing data is missing for individual charging stops
 *  (`charging_stops_missing_pricing`). If no pricing data at all
 *  is available, returns a corresponding placeholder text. */
export function formatChargingCosts(
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

TripSummary.displayName = "TripSummary";

export default TripSummary;
export { TripSummary };
