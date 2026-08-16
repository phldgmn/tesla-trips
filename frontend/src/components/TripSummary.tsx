/** Komponente zur Anzeige der Reisezusammenfassung nach der Simulation.
 *
 * Zeigt Gesamtdistanz, Fahrzeit, Ladezeit und Start-/Ziel-SoC. Der
 * detaillierte, chronologische "Zeitplan" (alle Stopps, Ladehalte und vom
 * Nutzer terminierten Fähren mit Streckenabschnitten, SoC-Verlauf und
 * geladener Energie) wird platzsparend in einem Vollbild-Modal angezeigt,
 * das über einen Button geöffnet wird (siehe `Modal`).
 */

import { useState } from "react";

import {
  estimateWaypointTimings,
  estimatePositionTiming,
  cumulativeDistancesKm,
  findNearestFrameIndex,
} from "../utils/timing-utils";
import { formatZeitpunkt } from "../utils/datetime-utils";
import { Modal } from "./Modal";
import type { TripSimulationResult } from "../types";
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
export interface ZeitplanEintrag {
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
  ankunftsSocPct: number | null;
  /** SoC bei Abfahrt in %, oder null (z. B. am Ziel, das keine Abfahrt hat). */
  abfahrtsSocPct: number | null;
  /** Während eines Ladehalts geladene Energie in kWh, sonst null. */
  energieGeladenKwh: number | null;
}

/** Baut den vereinheitlichten, chronologischen Zeitplan aus Stopps, Ladehalten
 *  und erkannten Fähren, sortiert nach Ankunft (Stopps ohne Ankunft, z. B.
 *  der Start, werden nach ihrer Abfahrt einsortiert). Fähren ohne vom Nutzer
 *  vorgegebene Zeit erhalten eine geschätzte Ankunfts-/Abfahrtszeit anhand
 *  ihrer Bounding-Box-Mitte (siehe `estimatePositionTiming`), damit auch sie
 *  chronologisch einsortiert werden können (gleiches Vorgehen wie in
 *  `route-eintraege.ts` für die Routen-Hoverkarte).
 *
 * Ergänzt für jeden Eintrag (soweit ermittelbar) die seit dem vorherigen
 * Eintrag gefahrene Strecke/Zeit sowie den SoC bei Ankunft/Abfahrt, indem der
 * jeweils nächstgelegene Simulationsframe herangezogen wird (siehe
 * `findNearestFrameIndex`/`cumulativeDistancesKm`). Für Ladehalte stammen
 * SoC und geladene Energie direkt aus dem `ChargingStop` (exakt statt
 * geschätzt). */
export function buildZeitplan(
  result: TripSimulationResult,
  stops: Stop[],
): ZeitplanEintrag[] {
  const { frames } = result;
  const timings = estimateWaypointTimings(frames, stops);

  const stopEintraege: ZeitplanEintrag[] = stops.map((stop, i) => ({
    key: `stopp-${stop.id}`,
    art: "Stopp",
    label: stopLabel(stop),
    arrival: timings[i]?.arrival ?? null,
    departure: timings[i]?.departure ?? null,
    distanceSinceLastKm: null,
    durationSinceLastMin: null,
    ankunftsSocPct: null,
    abfahrtsSocPct: null,
    energieGeladenKwh: null,
  }));

  const ladehaltEintraege: ZeitplanEintrag[] = result.charging_stops.map(
    (stop) => ({
      key: `ladehalt-${stop.station_id}-${stop.ankunftszeit}`,
      art: "Ladehalt",
      label: stop.name,
      arrival: stop.ankunftszeit,
      departure: stop.abfahrtszeit,
      distanceSinceLastKm: null,
      durationSinceLastMin: null,
      ankunftsSocPct: stop.ankunfts_soc_pct,
      abfahrtsSocPct: stop.ziel_soc_pct,
      energieGeladenKwh: stop.energie_geladen_kwh,
    }),
  );

  const faehrEintraege: ZeitplanEintrag[] = result.erkannte_faehren.map(
    (f, idx) => {
      let arrival = f.abfahrt;
      let departure = f.ankunft;
      if (arrival === null || departure === null) {
        const bboxMitte: [number, number] = [
          (f.bbox_sw[0] + f.bbox_no[0]) / 2,
          (f.bbox_sw[1] + f.bbox_no[1]) / 2,
        ];
        const geschaetzt = estimatePositionTiming(bboxMitte, frames);
        arrival = arrival ?? geschaetzt.arrival;
        departure = departure ?? geschaetzt.departure;
      }
      return {
        key: `faehre-${idx}-${f.name}`,
        art: "Fähre",
        label: f.name,
        arrival,
        departure,
        distanceSinceLastKm: null,
        durationSinceLastMin: null,
        ankunftsSocPct: null,
        abfahrtsSocPct: null,
        energieGeladenKwh: null,
      };
    },
  );

  const sortiert = [
    ...stopEintraege,
    ...ladehaltEintraege,
    ...faehrEintraege,
  ].sort((a, b) => {
    const zeitA = a.arrival ?? a.departure ?? "";
    const zeitB = b.arrival ?? b.departure ?? "";
    return zeitA.localeCompare(zeitB);
  });

  // Zweiter Durchlauf: Strecke/Zeit seit dem vorherigen Eintrag sowie (für
  // Stopp/Fähre) SoC bei Ankunft/Abfahrt anhand der nächstgelegenen
  // Simulationsframes ergänzen.
  const cumulative = cumulativeDistancesKm(frames);
  let prevExitIso: string | null = null;
  let prevExitIdx: number | null = null;

  for (const eintrag of sortiert) {
    const arrivalIso = eintrag.arrival ?? eintrag.departure;
    const exitIso = eintrag.departure ?? eintrag.arrival;
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
      eintrag.distanceSinceLastKm = Math.max(
        0,
        cumulative[arrivalIdx] - cumulative[prevExitIdx],
      );
      eintrag.durationSinceLastMin = Math.max(
        0,
        (new Date(arrivalIso).getTime() - new Date(prevExitIso).getTime()) /
          60000,
      );
    }

    if (eintrag.art !== "Ladehalt") {
      eintrag.ankunftsSocPct =
        eintrag.arrival && arrivalIdx !== null
          ? frames[arrivalIdx].soc_pct
          : null;
      eintrag.abfahrtsSocPct =
        eintrag.departure && exitIdx !== null ? frames[exitIdx].soc_pct : null;
    }

    prevExitIso = exitIso;
    prevExitIdx = exitIdx;
  }

  return sortiert;
}

function TripSummary({ result, stops }: TripSummaryProps) {
  const [zeitplanOpen, setZeitplanOpen] = useState(false);
  const zeitplan = buildZeitplan(result, stops);

  return (
    <div
      style={{
        padding: "1rem",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <h2 style={{ marginBottom: "0.75rem" }}>Reisezusammenfassung</h2>

      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          marginBottom: "1rem",
        }}
      >
        <tbody>
          <tr>
            <td style={labelCellStyle}>Gesamtdistanz</td>
            <td style={valueCellStyle}>{formatKm(result.gesamt_distanz_km)}</td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Fahrzeit</td>
            <td style={valueCellStyle}>
              {formatMinuten(result.gesamt_fahrzeit_min)}
            </td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Ladezeit</td>
            <td style={valueCellStyle}>
              {formatMinuten(result.gesamt_ladezeit_min)}
            </td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Start-SoC</td>
            <td style={valueCellStyle}>{formatSoc(result.start_soc_pct)}</td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Ziel-SoC</td>
            <td style={valueCellStyle}>{formatSoc(result.ziel_soc_pct)}</td>
          </tr>
          {result.erkannte_faehren.length > 0 && (
            <tr>
              <td style={labelCellStyle}>Fähren</td>
              <td style={valueCellStyle}>
                {result.erkannte_faehren.map((f) => f.name).join(", ")}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <button
        type="button"
        onClick={() => setZeitplanOpen(true)}
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
        Zeitplan öffnen ({zeitplan.length} Einträge)
      </button>

      <Modal
        open={zeitplanOpen}
        onClose={() => setZeitplanOpen(false)}
        title="Zeitplan"
        size="fullscreen"
      >
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={headerCellStyle}>Art</th>
              <th style={headerCellStyle}>Ort</th>
              <th style={headerCellStyle}>Ankunft</th>
              <th style={headerCellStyle}>Abfahrt</th>
              <th style={headerCellStyle}>Strecke seit letztem</th>
              <th style={headerCellStyle}>Dauer seit letztem</th>
              <th style={headerCellStyle}>SoC bei Ankunft</th>
              <th style={headerCellStyle}>SoC bei Abfahrt</th>
              <th style={headerCellStyle}>Geladene Energie</th>
            </tr>
          </thead>
          <tbody>
            {zeitplan.map((eintrag) => (
              <tr key={eintrag.key}>
                <td style={cellStyle}>{eintrag.art}</td>
                <td style={cellStyle}>{eintrag.label}</td>
                <td style={cellStyle}>{formatZeitpunkt(eintrag.arrival)}</td>
                <td style={cellStyle}>{formatZeitpunkt(eintrag.departure)}</td>
                <td style={cellStyle}>
                  {formatKmOrDash(eintrag.distanceSinceLastKm)}
                </td>
                <td style={cellStyle}>
                  {formatMinutenOrDash(eintrag.durationSinceLastMin)}
                </td>
                <td style={cellStyle}>
                  {formatSocOrDash(eintrag.ankunftsSocPct)}
                </td>
                <td style={cellStyle}>
                  {formatSocOrDash(eintrag.abfahrtsSocPct)}
                </td>
                <td style={cellStyle}>
                  {formatKwhOrDash(eintrag.energieGeladenKwh)}
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

function formatMinuten(min: number): string {
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}

function formatMinutenOrDash(min: number | null): string {
  return min === null ? "–" : formatMinuten(min);
}

function formatKmOrDash(km: number | null): string {
  return km === null ? "–" : formatKm(km);
}

function formatSocOrDash(pct: number | null): string {
  return pct === null ? "–" : formatSoc(pct);
}

function formatKwhOrDash(kwh: number | null): string {
  if (kwh === null) return "–";
  return `${kwh.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} kWh`;
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

export default TripSummary;
export { TripSummary };
