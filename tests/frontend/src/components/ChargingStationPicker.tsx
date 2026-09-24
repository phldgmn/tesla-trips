import { useState } from "react";
import {
  SUPERCHARGER_STATIONS,
  type SuperchargerStation,
} from "@/data/superchargers";

export interface ChargingStationPickerProps {
  onSelect: (station: { position: [number, number]; label: string }) => void;
  onCancel?: () => void;
}

/**
 * Filtert Supercharger-Stationen case-insensitiv gegen `name` ODER `country`.
 * Leerer/Whitespace-Query gibt alle Stationen unverändert zurück.
 */
export function filterStations(
  stations: SuperchargerStation[],
  query: string,
): SuperchargerStation[] {
  const q = query.trim().toLowerCase();
  if (q === "") return stations;
  return stations.filter(
    (s) =>
      s.name.toLowerCase().includes(q) || s.country.toLowerCase().includes(q),
  );
}

/**
 * Kleines Inline-Picker-Panel zum Auswählen einer Ladestation.
 * Kein Modal/Portal – wird vom Eltern-Element bedingt gerendert.
 */
export function ChargingStationPicker({
  onSelect,
  onCancel,
}: ChargingStationPickerProps) {
  const [query, setQuery] = useState("");

  const matches = filterStations(SUPERCHARGER_STATIONS, query);

  return (
    <div
      style={{
        border: "1px solid #d1d5db",
        borderRadius: 8,
        padding: 12,
        background: "#fff",
      }}
    >
      <label
        htmlFor="charging-station-filter"
        style={{ display: "block", marginBottom: 8, fontWeight: 600 }}
      >
        Ladestation suchen
      </label>
      <input
        id="charging-station-filter"
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Name oder Land …"
        style={{
          width: "100%",
          padding: "6px 8px",
          marginBottom: 8,
          boxSizing: "border-box",
        }}
      />

      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          maxHeight: 260,
          overflowY: "auto",
        }}
      >
        {matches.map((station) => (
          <li key={station.stationId}>
            <button
              type="button"
              onClick={() =>
                onSelect({
                  position: station.position,
                  label: station.name,
                })
              }
              style={{
                width: "100%",
                textAlign: "left",
                padding: "8px",
                margin: "2px 0",
                border: "1px solid #e5e7eb",
                borderRadius: 6,
                background: "#f9fafb",
                cursor: "pointer",
              }}
            >
              <div style={{ fontWeight: 600 }}>{station.name}</div>
              <div style={{ fontSize: 13, color: "#4b5563" }}>
                {station.country} · {station.maxChargingPowerKw} kW
              </div>
            </button>
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={() => onCancel?.()}
        style={{
          marginTop: 8,
          padding: "6px 12px",
          border: "1px solid #d1d5db",
          borderRadius: 6,
          background: "#fff",
          cursor: "pointer",
        }}
      >
        Abbrechen
      </button>
    </div>
  );
}

export default ChargingStationPicker;
