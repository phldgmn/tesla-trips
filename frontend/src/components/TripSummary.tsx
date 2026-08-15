/** Komponente zur Anzeige der Reisezusammenfassung nach der Simulation.
 *
 * Zeigt Gesamtdistanz, Fahrzeit, Ladezeit und Start-/Ziel-SoC sowie ein
 * chronologisches "Zeitplan" mit allen Stopps, Ladehalten (mit tatsächlicher
 * Ankunfts-/Abfahrtszeit) und vom Nutzer terminierten Fähren (Abfahrt/Ankunft).
 * Für jeden Stopp (außer dem letzten) wird zudem eine optionale "Geplante
 * Abfahrt" angezeigt, falls der Nutzer eine hinterlegt hat.
 */

import { estimateWaypointTimings } from "../utils/timing-utils";
import { formatZeitpunkt } from "../utils/datetime-utils";
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
}

/** Baut den vereinheitlichten Zeitplan aus Stopps, Ladehalten und terminierten
 *  Fähren, chronologisch sortiert nach Ankunft (Stopps ohne Ankunft, z. B. der
 *  Start, werden nach ihrer Abfahrt einsortiert). Fähren ohne vom Nutzer
 *  vorgegebene Zeit fehlen bewusst (siehe `TripPlannerForm`, Abschnitt "Fähren"
 *  für deren Terminierung) - ein chronologischer Zeitplan kann nur Einträge
 *  mit bekannter Zeit sinnvoll einordnen. */
export function buildZeitplan(
  result: TripSimulationResult,
  stops: Stop[],
): ZeitplanEintrag[] {
  const timings = estimateWaypointTimings(result, stops);

  const stopEintraege: ZeitplanEintrag[] = stops.map((stop, i) => ({
    key: `stopp-${stop.id}`,
    art: "Stopp",
    label: stopLabel(stop),
    arrival: timings[i]?.arrival ?? null,
    departure: timings[i]?.departure ?? null,
  }));

  const ladehaltEintraege: ZeitplanEintrag[] = result.charging_stops.map(
    (stop) => ({
      key: `ladehalt-${stop.station_id}-${stop.ankunftszeit}`,
      art: "Ladehalt",
      label: stop.name,
      arrival: stop.ankunftszeit,
      departure: stop.abfahrtszeit,
    }),
  );

  const faehrEintraege: ZeitplanEintrag[] = result.erkannte_faehren
    .filter((f) => f.abfahrt !== null && f.ankunft !== null)
    .map((f) => ({
      key: `faehre-${f.name}-${f.abfahrt}`,
      art: "Fähre",
      label: f.name,
      arrival: f.abfahrt,
      departure: f.ankunft,
    }));

  return [...stopEintraege, ...ladehaltEintraege, ...faehrEintraege].sort(
    (a, b) => {
      const zeitA = a.arrival ?? a.departure ?? "";
      const zeitB = b.arrival ?? b.departure ?? "";
      return zeitA.localeCompare(zeitB);
    },
  );
}

function TripSummary({ result, stops }: TripSummaryProps) {
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

      <h3 style={{ marginBottom: "0.5rem" }}>Zeitplan</h3>

      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
        }}
      >
        <thead>
          <tr>
            <th style={headerCellStyle}>Art</th>
            <th style={headerCellStyle}>Stopp</th>
            <th style={headerCellStyle}>Ankunft</th>
            <th style={headerCellStyle}>Abfahrt</th>
          </tr>
        </thead>
        <tbody>
          {zeitplan.map((eintrag) => (
            <tr key={eintrag.key}>
              <td style={cellStyle}>{eintrag.art}</td>
              <td style={cellStyle}>{eintrag.label}</td>
              <td style={cellStyle}>{formatZeitpunkt(eintrag.arrival)}</td>
              <td style={cellStyle}>{formatZeitpunkt(eintrag.departure)}</td>
            </tr>
          ))}
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
