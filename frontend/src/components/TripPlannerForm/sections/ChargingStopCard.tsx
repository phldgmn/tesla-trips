import { Zap } from "lucide-react";
import type { ChargingStop } from "../../../types";
import { formatDayMonth, isDayChange } from "../../../utils/datetime-utils";
import { formatChargingStationName } from "../form-helpers";
import { setChargingDurationPresetFor } from "../ferry-helpers";
import { TimelineRow, TimeBadge } from "../timeline-rows";
import type { TripPlannerState } from "../useTripPlannerState";

interface ChargingStopCardProps {
  state: TripPlannerState;
  chargingStop: ChargingStop;
  isSubmitting: boolean;
}

/** Timeline card for a planned charging stop with an editable charging
 *  duration override; leaving the field recomputes the route. */
export function ChargingStopCard({
  state,
  chargingStop: stop,
  isSubmitting,
}: ChargingStopCardProps) {
  const { chargingDurations, setChargingDurations, submit } = state;
  const target = chargingDurations.find((v) => v.stationId === stop.stationId);
  const chargingDurationMin = Math.round(
    (target?.chargingDurationS ?? stop.chargingDurationS) / 60,
  );
  const departureDateShort = isDayChange(stop.arrivalTime, stop.departureTime)
    ? formatDayMonth(stop.departureTime)
    : undefined;

  return (
    <TimelineRow key={stop.stationId} icon={Zap} background="#dcfce7">
      <div
        style={{
          position: "relative",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          padding: "0.5rem 0.75rem",
          background: "white",
          fontSize: "0.8rem",
          display: "grid",
          gap: "0.3rem",
        }}
      >
        <TimeBadge
          edge="oben"
          iso={stop.arrivalTime}
          socPct={stop.arrivalSocPct}
        />
        <TimeBadge
          edge="unten"
          iso={stop.departureTime}
          socPct={stop.targetSocPct}
          shortDate={departureDateShort}
        />
        <strong>{formatChargingStationName(stop.name)}</strong>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.4rem",
          }}
        >
          Ladedauer (min)
          <input
            type="number"
            min="0"
            step="5"
            value={chargingDurationMin}
            disabled={isSubmitting}
            onChange={(e) => {
              const next = setChargingDurationPresetFor(
                chargingDurations,
                stop.stationId,
                parseFloat(e.target.value) || 0,
              );
              setChargingDurations(next);
            }}
            onBlur={() => submit()}
            style={{ width: "5rem", padding: "0.3rem" }}
          />
        </label>
      </div>
    </TimelineRow>
  );
}
