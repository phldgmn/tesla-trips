import { Crosshair, BatteryCharging, Battery, X } from "lucide-react";
import type { Stop } from "../../../types/trip-request";
import type { RouteEntry } from "../../../utils/route-entries";
import {
  splitIsoToDateTime,
  formatDayMonth,
  isDayChange,
} from "../../../utils/datetime-utils";
import { ChargingStationPicker } from "../../ChargingStationPicker";
import {
  getStopRole,
  getStopTimelineIcon,
  differsAsTime,
} from "../form-helpers";
import { TimelineRow, TimeBadge } from "../timeline-rows";
import { GEOCODING_MIN_QUERY_LENGTH, type Geocoding } from "../useGeocoding";
import type { TripPlannerState } from "../useTripPlannerState";

interface StopCardProps {
  state: TripPlannerState;
  geocoding: Geocoding;
  stops: Stop[];
  entry: Extract<RouteEntry, { art: "Stopp" }>;
  pickingStopId: string | null;
  onRequestPick: (stopId: string | null) => void;
  isSubmitting: boolean;
  /** Stop ID whose inline SoC editor is open, or `null`. */
  socEditingStopId: string | null;
  onSocEditingChange: (stopId: string | null) => void;
  /** Stop ID whose charging-station picker is open (at most one), or `null`. */
  pickerTargetId: string | null;
  onPickerTargetChange: (stopId: string | null) => void;
}

/** Timeline card for a start/waypoint/destination stop: address search,
 *  map/charging-station picking, reordering, departure time, SoC and local
 *  charging power. */
export function StopCard({
  state,
  geocoding,
  stops,
  entry,
  pickingStopId,
  onRequestPick,
  isSubmitting,
  socEditingStopId,
  onSocEditingChange: setSocEditingStopId,
  pickerTargetId,
  onPickerTargetChange: setPickerTargetId,
}: StopCardProps) {
  const {
    startSoc,
    setStartSoc,
    targetSoc,
    setTargetSoc,
    moveStopUp: handleMoveUp,
    moveStopDown: handleMoveDown,
    removeStop: handleRemoveStop,
    setLeaveAt: handleLeaveAtChange,
    clearLeaveAt: handleLeaveAtClear,
    setChargingPower: handleChargingPowerChange,
    setStopLocation,
  } = state;
  const {
    geocodingStates,
    handleAddressInput,
    handleSuggestionSelect,
    handleCloseSuggestions,
  } = geocoding;
  const { stop, stopIndex: idx } = entry;
  const role = getStopRole(stops, idx);
  const isLast = idx === stops.length - 1;
  const geoState = geocodingStates[stop.id];
  const showSuggestions = geoState && geoState.suggestions.length > 0;

  // Split leaveAt for the date/time inputs
  const leaveAtParts = stop.leaveAt ? splitIsoToDateTime(stop.leaveAt) : null;
  const showLeaveAt = !isLast;

  const { Icon, background } = getStopTimelineIcon(stops, idx);

  // Day change WITHIN this entry (arrival and departure on different days,
  // e.g. a waypoint over midnight): the departure badge then also shows the
  // date (see `DaySeparatorEntry` in `route-entries.ts`).
  const departureDateShort =
    entry.timing.departure !== null &&
    isDayChange(entry.timing.arrival, entry.timing.departure)
      ? formatDayMonth(entry.timing.departure)
      : undefined;

  return (
    <TimelineRow key={stop.id} icon={Icon} background={background}>
      <div
        style={{
          position: "relative",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          padding: "0.75rem",
          background: "white",
          display: "flex",
          flexDirection: "column",
          gap: "0.5rem",
        }}
      >
        {entry.timing.arrival !== null && (
          <TimeBadge
            edge="oben"
            iso={entry.timing.arrival}
            socPct={entry.timing.arrivalSocPct ?? undefined}
          />
        )}
        {entry.timing.departure !== null &&
          differsAsTime(entry.timing.arrival, entry.timing.departure) && (
            <TimeBadge
              edge="unten"
              iso={entry.timing.departure}
              socPct={entry.timing.departureSocPct ?? undefined}
              shortDate={departureDateShort}
            />
          )}
        {/* Header: Role Badge + Move/Remove */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.5rem",
          }}
        >
          <span
            style={{
              padding: "0.15rem 0.5rem",
              borderRadius: "999px",
              fontSize: "0.7rem",
              fontWeight: 600,
              background:
                idx === 0 ? "#e0e7ff" : isLast ? "#fef3c7" : "#f3f4f6",
              color: idx === 0 ? "#3730a3" : isLast ? "#92400e" : "#4b5563",
            }}
          >
            {role}
          </span>
          <div style={{ display: "flex", gap: "0.25rem" }}>
            {(idx === 0 || isLast) && (
              <button
                type="button"
                onClick={() =>
                  setSocEditingStopId(
                    socEditingStopId === stop.id ? null : stop.id,
                  )
                }
                disabled={isSubmitting}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.15rem",
                  padding: "0.2rem 0.4rem",
                  fontSize: "0.7rem",
                  fontWeight: 600,
                  background:
                    socEditingStopId === stop.id ? "#7c3aed" : "#f5f3ff",
                  border: `1px solid ${socEditingStopId === stop.id ? "#7c3aed" : "#c4b5fd"}`,
                  borderRadius: "4px",
                  color: socEditingStopId === stop.id ? "white" : "#6d28d9",
                  cursor: isSubmitting ? "not-allowed" : "pointer",
                  opacity: isSubmitting ? 0.5 : 1,
                }}
                title={idx === 0 ? "Start-SoC festlegen" : "Ziel-SoC festlegen"}
                aria-label={
                  idx === 0 ? "Start-SoC festlegen" : "Ziel-SoC festlegen"
                }
              >
                <Battery size={12} />
                {idx === 0 ? startSoc : targetSoc}%
              </button>
            )}
            <button
              type="button"
              onClick={() =>
                onRequestPick(pickingStopId === stop.id ? null : stop.id)
              }
              disabled={isSubmitting}
              style={{
                display: "flex",
                padding: "0.2rem",
                background: pickingStopId === stop.id ? "#2563eb" : "#eff6ff",
                border: `1px solid ${pickingStopId === stop.id ? "#2563eb" : "#93c5fd"}`,
                borderRadius: "4px",
                color: pickingStopId === stop.id ? "white" : "#1d4ed8",
                cursor: isSubmitting ? "not-allowed" : "pointer",
                opacity: isSubmitting ? 0.5 : 1,
              }}
              title={
                pickingStopId === stop.id
                  ? "Kartenauswahl abbrechen"
                  : "Auf Karte wählen"
              }
              aria-label={
                pickingStopId === stop.id
                  ? "Kartenauswahl abbrechen"
                  : "Auf Karte wählen"
              }
            >
              {pickingStopId === stop.id ? (
                <X size={14} />
              ) : (
                <Crosshair size={14} />
              )}
            </button>
            <button
              type="button"
              onClick={() =>
                setPickerTargetId(pickerTargetId === stop.id ? null : stop.id)
              }
              disabled={isSubmitting}
              style={{
                display: "flex",
                padding: "0.2rem",
                background: pickerTargetId === stop.id ? "#166534" : "#dcfce7",
                border: `1px solid ${pickerTargetId === stop.id ? "#166534" : "#86efac"}`,
                borderRadius: "4px",
                color: pickerTargetId === stop.id ? "white" : "#166534",
                cursor: isSubmitting ? "not-allowed" : "pointer",
                opacity: isSubmitting ? 0.5 : 1,
              }}
              title={
                pickerTargetId === stop.id
                  ? "Ladestationsauswahl abbrechen"
                  : "Ladestation statt Adresse wählen"
              }
              aria-label={
                pickerTargetId === stop.id
                  ? "Ladestationsauswahl abbrechen"
                  : "Ladestation statt Adresse wählen"
              }
            >
              {pickerTargetId === stop.id ? (
                <X size={14} />
              ) : (
                <BatteryCharging size={14} />
              )}
            </button>
            <button
              type="button"
              onClick={() => handleMoveUp(idx)}
              disabled={idx === 0 || isSubmitting}
              style={{
                padding: "0.2rem 0.5rem",
                fontSize: "0.75rem",
                background: "#f3f4f6",
                border: "1px solid #d1d5db",
                borderRadius: "4px",
                cursor: idx === 0 || isSubmitting ? "not-allowed" : "pointer",
                opacity: idx === 0 || isSubmitting ? 0.5 : 1,
              }}
              aria-label="Nach oben verschieben"
            >
              ↑
            </button>
            <button
              type="button"
              onClick={() => handleMoveDown(idx)}
              disabled={isLast || isSubmitting}
              style={{
                padding: "0.2rem 0.5rem",
                fontSize: "0.75rem",
                background: "#f3f4f6",
                border: "1px solid #d1d5db",
                borderRadius: "4px",
                cursor: isLast || isSubmitting ? "not-allowed" : "pointer",
                opacity: isLast || isSubmitting ? 0.5 : 1,
              }}
              aria-label="Nach unten verschieben"
            >
              ↓
            </button>
            <button
              type="button"
              onClick={() => handleRemoveStop(stop.id)}
              disabled={stops.length <= 2 || isSubmitting}
              style={{
                padding: "0.2rem 0.5rem",
                fontSize: "0.75rem",
                background: "#fef2f2",
                border: "1px solid #fecaca",
                borderRadius: "4px",
                color: "#991b1b",
                cursor:
                  stops.length <= 2 || isSubmitting ? "not-allowed" : "pointer",
                opacity: stops.length <= 2 || isSubmitting ? 0.5 : 1,
              }}
              aria-label="Entfernen"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Inline editor for the start/target SoC, opened from the
            percentage button in the header. */}
        {socEditingStopId === stop.id && (idx === 0 || isLast) && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.4rem",
            }}
          >
            <label style={{ fontSize: "0.8rem", fontWeight: 500 }}>
              {idx === 0 ? "Start-SoC (%)" : "Ziel-SoC (%)"}
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              autoFocus
              value={idx === 0 ? startSoc : targetSoc}
              onChange={(e) => {
                const value = parseInt(e.target.value, 10) || 0;
                if (idx === 0) {
                  setStartSoc(value);
                } else {
                  setTargetSoc(value);
                }
              }}
              onBlur={() => setSocEditingStopId(null)}
              style={{
                width: "5rem",
                padding: "0.3rem",
                fontSize: "0.85rem",
              }}
            />
          </div>
        )}

        {/* Address Input + Geocoding */}
        <div style={{ position: "relative" }}>
          <input
            type="text"
            placeholder="Adresse eingeben…"
            value={stop.address}
            onChange={(e) => handleAddressInput(stop.id, e.target.value)}
            onBlur={() => {
              // Close suggestions after a short delay so a click on a
              // suggestion still registers
              setTimeout(() => handleCloseSuggestions(stop.id), 200);
            }}
            onFocus={(e) => {
              // On focus with enough text: search again
              if (e.target.value.trim().length >= GEOCODING_MIN_QUERY_LENGTH) {
                handleAddressInput(stop.id, e.target.value);
              }
            }}
            disabled={isSubmitting}
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid #d1d5db",
              borderRadius: "4px",
              fontSize: "0.85rem",
              boxSizing: "border-box",
            }}
          />
          {geoState?.loading && (
            <span
              style={{
                position: "absolute",
                right: "0.5rem",
                top: "0.6rem",
                fontSize: "0.75rem",
                color: "#6b7280",
              }}
            >
              Suche…
            </span>
          )}
          {showSuggestions && (
            <ul
              style={{
                position: "absolute",
                top: "100%",
                left: 0,
                right: 0,
                zIndex: 10,
                background: "white",
                border: "1px solid #d1d5db",
                borderTop: "none",
                borderRadius: "0 0 4px 4px",
                listStyle: "none",
                margin: 0,
                padding: 0,
                maxHeight: "200px",
                overflowY: "auto",
                boxShadow: "0 2px 6px rgba(0,0,0,0.1)",
              }}
            >
              {geoState.suggestions.map((sugg, sIdx) => (
                <li
                  key={sIdx}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    handleSuggestionSelect(stop.id, sugg);
                  }}
                  style={{
                    padding: "0.5rem",
                    cursor: "pointer",
                    fontSize: "0.85rem",
                    borderBottom:
                      sIdx < geoState.suggestions.length - 1
                        ? "1px solid #f3f4f6"
                        : "none",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "#f3f4f6";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "white";
                  }}
                >
                  {sugg.label}
                </li>
              ))}
            </ul>
          )}
        </div>

        {pickingStopId === stop.id && (
          <p
            style={{
              margin: 0,
              fontSize: "0.8rem",
              color: "#2563eb",
            }}
          >
            Klicken Sie auf die Karte, um den Punkt zu platzieren.
          </p>
        )}

        {/* Charging station picker (inline, for this stop only) */}
        {pickerTargetId === stop.id && (
          <ChargingStationPicker
            onSelect={(station) => {
              setStopLocation(stop.id, station);
              setPickerTargetId(null);
            }}
            onCancel={() => setPickerTargetId(null)}
          />
        )}

        {/* Departure here (all stops except the last) */}
        {showLeaveAt && (
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: "0.25rem",
              }}
            >
              <label
                style={{
                  fontSize: "0.8rem",
                  fontWeight: 500,
                }}
              >
                Abfahrt hier
              </label>
              {stop.leaveAt && (
                <button
                  type="button"
                  onClick={() => handleLeaveAtClear(stop.id)}
                  style={{
                    display: "flex",
                    padding: "0.1rem 0.3rem",
                    fontSize: "0.8rem",
                    lineHeight: 1,
                    background: "none",
                    border: "none",
                    color: "#991b1b",
                    cursor: "pointer",
                  }}
                  title="Abfahrtszeit löschen"
                  aria-label="Abfahrtszeit löschen"
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <div style={{ display: "flex", gap: "0.4rem" }}>
              <input
                type="date"
                value={leaveAtParts?.date ?? ""}
                onChange={(e) => {
                  const newTime = leaveAtParts?.time ?? "12:00";
                  if (e.target.value) {
                    handleLeaveAtChange(stop.id, e.target.value, newTime);
                  } else {
                    handleLeaveAtClear(stop.id);
                  }
                }}
                style={{
                  flex: 1,
                  padding: "0.4rem",
                  fontSize: "0.85rem",
                }}
              />
              <input
                type="time"
                value={leaveAtParts?.time ?? ""}
                onChange={(e) => {
                  const newDate = leaveAtParts?.date ?? "";
                  if (e.target.value) {
                    handleLeaveAtChange(stop.id, newDate, e.target.value);
                  } else {
                    handleLeaveAtClear(stop.id);
                  }
                }}
                style={{
                  flex: 1,
                  padding: "0.4rem",
                  fontSize: "0.85rem",
                }}
              />
            </div>
            {idx !== 0 && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.4rem",
                  marginTop: "0.5rem",
                }}
              >
                <label
                  style={{
                    fontSize: "0.8rem",
                    fontWeight: 500,
                    whiteSpace: "nowrap",
                  }}
                >
                  Ladeleistung
                </label>
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  placeholder="optional"
                  value={stop.chargingPowerKw ?? ""}
                  onChange={(e) =>
                    handleChargingPowerChange(stop.id, e.target.value)
                  }
                  style={{
                    flex: 1,
                    minWidth: 0,
                    padding: "0.4rem",
                    fontSize: "0.85rem",
                  }}
                />
                <span
                  style={{
                    fontSize: "0.8rem",
                    color: "#6b7280",
                    whiteSpace: "nowrap",
                  }}
                >
                  kW
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </TimelineRow>
  );
}
