/** Komponente zur Anzeige der Reisezusammenfassung nach der Simulation.
 *
 * Zeigt Gesamtdistanz, Fahrzeit, Ladezeit und Start-/Ziel-SoC sowie eine
 * detaillierte Liste aller Stopps mit ermittelten Ankunfts- und
 * Abfahrtszeiten. Für jeden Stopp (außer dem letzten) wird zudem eine
 * optionale "Geplante Abfahrt" angezeigt, falls der Nutzer eine hinterlegt
 * hat.
 */

import { estimateWaypointTimings } from "../utils/timing-utils";
import type { TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";

export interface TripSummaryProps {
  result: TripSimulationResult;
  stops: Stop[];
}

/** Formatiert einen ISO-Zeitstempel für die deutsche Locale. */
function formatZeitpunkt(iso: string | null): string {
  if (iso === null) return "unbekannt";
  try {
    return new Date(iso).toLocaleString("de-DE", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return "ungültig";
  }
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

function TripSummary({ result, stops }: TripSummaryProps) {
  const timings = estimateWaypointTimings(result, stops);

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
          marginBottom: "1.5rem",
        }}
      >
        <tbody>
          <tr>
            <td style={labelCellStyle}>Gesamtdistanz</td>
            <td style={valueCellStyle}>
              {result.gesamt_distanz_km.toFixed(1)} km
            </td>
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
            <td style={valueCellStyle}>{result.start_soc_pct} %</td>
          </tr>
          <tr>
            <td style={labelCellStyle}>Ziel-SoC</td>
            <td style={valueCellStyle}>{result.ziel_soc_pct} %</td>
          </tr>
        </tbody>
      </table>

      <h3 style={{ marginBottom: "0.5rem" }}>Zeitplan</h3>

      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
        }}
      >
        <thead>
          <tr>
            <th style={headerCellStyle}>Stopp</th>
            <th style={headerCellStyle}>Ankunft</th>
            <th style={headerCellStyle}>Abfahrt</th>
          </tr>
        </thead>
        <tbody>
          {stops.map((stop, i) => {
            const timing = timings[i];
            if (!timing) return null;
            return (
              <tr key={stop.id}>
                <td style={cellStyle}>{stopLabel(stop)}</td>
                <td style={cellStyle}>{formatZeitpunkt(timing.arrival)}</td>
                <td style={cellStyle}>{formatZeitpunkt(timing.departure)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function formatMinuten(min: number): string {
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
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
