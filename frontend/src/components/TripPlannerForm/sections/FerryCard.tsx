import { Ship } from "lucide-react";
import type { FerryExclusion } from "../../../types/trip-request";
import type { RouteEntry } from "../../../utils/route-entries";
import {
  splitIsoToDateTime,
  formatDayMonth,
  isDayChange,
  combineDateTimeToIso,
} from "../../../utils/datetime-utils";
import { differsAsTime } from "../form-helpers";
import {
  sameFerryExclusion,
  toggleFerryExclusion,
  setFerryTimeWindowFor,
  ferryKey,
} from "../ferry-helpers";
import { TimelineRow, TimeBadge } from "../timeline-rows";
import type { TripPlannerState } from "../useTripPlannerState";

interface FerryCardProps {
  state: TripPlannerState;
  entry: Extract<RouteEntry, { art: "Fähre" }>;
  isSubmitting: boolean;
}

/** Timeline card for a detected ferry: "ignore" button plus an optional
 *  timetable (departure/arrival) used as a schedule constraint. */
export function FerryCard({ state, entry, isSubmitting }: FerryCardProps) {
  const {
    avoidedFerries,
    setAvoidedFerries,
    ferryTimeWindows,
    setFerryTimeWindows,
    ferryTimeWindowDraft,
    setFerryTimeWindowDraft,
    submit,
  } = state;
  const ferry = entry.ferry;
  const exclusionEntry: FerryExclusion = {
    name: ferry.name,
    bboxSw: ferry.bboxSw,
    bboxNe: ferry.bboxNe,
  };
  const timeWindowEntry = ferryTimeWindows.find((f) =>
    sameFerryExclusion(f, exclusionEntry),
  );
  const key = ferryKey(exclusionEntry);
  const draft = ferryTimeWindowDraft[key] ?? {
    departure: timeWindowEntry?.departure ?? "",
    arrival: timeWindowEntry?.arrival ?? "",
  };
  const departureParts = draft.departure
    ? splitIsoToDateTime(draft.departure)
    : null;
  const arrivalParts = draft.arrival ? splitIsoToDateTime(draft.arrival) : null;

  const handleTimeWindowChange = (
    field: "departure" | "arrival",
    date: string,
    time: string,
  ) => {
    const iso = date && time ? combineDateTimeToIso(date, time) : "";
    const nextDraft = {
      departure: field === "departure" ? iso : draft.departure,
      arrival: field === "arrival" ? iso : draft.arrival,
    };
    setFerryTimeWindowDraft((prev) => ({ ...prev, [key]: nextDraft }));
    const next = setFerryTimeWindowFor(
      ferryTimeWindows,
      exclusionEntry,
      nextDraft.departure,
      nextDraft.arrival,
    );
    setFerryTimeWindows(next);
    if (nextDraft.departure && nextDraft.arrival) {
      submit({ ferryTimeWindows: next });
    }
  };

  const handleIgnore = () => {
    const next = toggleFerryExclusion(avoidedFerries, exclusionEntry, true);
    setAvoidedFerries(next);
    submit({ avoidedFerries: next });
  };

  const departureDateShort =
    entry.timing.departure !== null &&
    isDayChange(entry.timing.arrival, entry.timing.departure)
      ? formatDayMonth(entry.timing.departure)
      : undefined;

  return (
    <TimelineRow key={key} icon={Ship} background="#dbeafe">
      <div
        style={{
          position: "relative",
          border: "1px solid #bfdbfe",
          borderRadius: "6px",
          padding: "0.5rem 0.75rem",
          background: "#eff6ff",
          display: "grid",
          gap: "0.4rem",
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
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.5rem",
          }}
        >
          <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
            ⛴ {ferry.name} ({(ferry.lengthM / 1000).toFixed(1)} km)
          </span>
          <button
            type="button"
            onClick={handleIgnore}
            disabled={isSubmitting}
            style={{
              padding: "0.2rem 0.5rem",
              fontSize: "0.75rem",
              background: "#fef2f2",
              border: "1px solid #fecaca",
              borderRadius: "4px",
              color: "#991b1b",
              cursor: isSubmitting ? "not-allowed" : "pointer",
            }}
          >
            Ignorieren
          </button>
        </div>
        <div
          style={{
            display: "grid",
            gap: "0.25rem",
            fontSize: "0.8rem",
          }}
        >
          <span>Fährfahrplan (optional, für die Planung):</span>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <input
              type="date"
              value={departureParts?.date ?? ""}
              disabled={isSubmitting}
              onChange={(e) =>
                handleTimeWindowChange(
                  "departure",
                  e.target.value,
                  departureParts?.time ?? "12:00",
                )
              }
              style={{ flex: 1, padding: "0.3rem" }}
            />
            <input
              type="time"
              value={departureParts?.time ?? ""}
              disabled={isSubmitting}
              onChange={(e) =>
                handleTimeWindowChange(
                  "departure",
                  departureParts?.date ?? "",
                  e.target.value,
                )
              }
              style={{ flex: 1, padding: "0.3rem" }}
            />
          </div>
          <span>Ankunft:</span>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <input
              type="date"
              value={arrivalParts?.date ?? ""}
              disabled={isSubmitting}
              onChange={(e) =>
                handleTimeWindowChange(
                  "arrival",
                  e.target.value,
                  arrivalParts?.time ?? "12:00",
                )
              }
              style={{ flex: 1, padding: "0.3rem" }}
            />
            <input
              type="time"
              value={arrivalParts?.time ?? ""}
              disabled={isSubmitting}
              onChange={(e) =>
                handleTimeWindowChange(
                  "arrival",
                  arrivalParts?.date ?? "",
                  e.target.value,
                )
              }
              style={{ flex: 1, padding: "0.3rem" }}
            />
          </div>
        </div>
      </div>
    </TimelineRow>
  );
}
