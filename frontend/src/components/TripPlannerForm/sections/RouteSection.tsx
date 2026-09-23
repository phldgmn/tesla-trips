import { useState } from "react";
import type { Stop } from "../../../types/trip-request";
import type {
  ChargingStop,
  FerrySegment,
  SimulationFrame,
} from "../../../types";
import { buildRouteEntries } from "../../../utils/route-entries";
import { isDayChange } from "../../../utils/datetime-utils";
import { DaySeparator, DriveSegmentRow } from "../timeline-rows";
import type { Geocoding } from "../useGeocoding";
import type { TripPlannerState } from "../useTripPlannerState";
import { StopCard } from "./StopCard";
import { ChargingStopCard } from "./ChargingStopCard";
import { FerryCard } from "./FerryCard";

interface RouteSectionProps {
  state: TripPlannerState;
  geocoding: Geocoding;
  stops: Stop[];
  pickingStopId: string | null;
  onRequestPick: (stopId: string | null) => void;
  isSubmitting: boolean;
  detectedFerries?: FerrySegment[];
  chargingStops?: ChargingStop[];
  frames?: SimulationFrame[];
}

/** Route timeline: stops, charging stops and (non-ignored) ferries sorted
 *  chronologically, with drive segments and day separators in between (see
 *  `utils/route-entries.ts`). */
export function RouteSection({
  state,
  geocoding,
  stops,
  pickingStopId,
  onRequestPick,
  isSubmitting,
  detectedFerries,
  chargingStops,
  frames,
}: RouteSectionProps) {
  const [socEditingStopId, setSocEditingStopId] = useState<string | null>(null);
  const [pickerTargetId, setPickerTargetId] = useState<string | null>(null);
  const routeEntries = buildRouteEntries({
    stops,
    frames,
    chargingStops,
    detectedFerries,
    avoidedFerries: state.avoidedFerries,
  });

  return (
    <ol
      style={{
        listStyle: "none",
        margin: 0,
        marginBottom: "0.75rem",
        padding: 0,
        marginLeft: "0.9rem",
        borderLeft: "2px solid #e5e7eb",
      }}
    >
      {routeEntries.map((entry, entryIdx) => {
        switch (entry.art) {
          case "Stopp":
            return (
              <StopCard
                key={entry.stop.id}
                state={state}
                geocoding={geocoding}
                stops={stops}
                entry={entry}
                pickingStopId={pickingStopId}
                onRequestPick={onRequestPick}
                isSubmitting={isSubmitting}
                socEditingStopId={socEditingStopId}
                onSocEditingChange={setSocEditingStopId}
                pickerTargetId={pickerTargetId}
                onPickerTargetChange={setPickerTargetId}
              />
            );
          case "Ladehalt":
            return (
              <ChargingStopCard
                key={entry.chargingStop.stationId}
                state={state}
                chargingStop={entry.chargingStop}
                isSubmitting={isSubmitting}
              />
            );
          case "Tagestrenner":
            return (
              <DaySeparator
                key={`separator-${entryIdx}`}
                previousIso={entry.vonIso}
                currentIso={entry.bisIso}
              />
            );
          case "Fahrsegment":
            // A drive segment across midnight shows distance, time and both
            // dates in ONE row instead of an extra day separator (see
            // `DrivingSegmentEntry` in `route-entries.ts`).
            return (
              <DriveSegmentRow
                key={`segment-${entryIdx}`}
                distanceKm={entry.distanceKm}
                durationMin={entry.durationMin}
                dayChange={
                  isDayChange(entry.vonIso, entry.bisIso)
                    ? { vonIso: entry.vonIso, bisIso: entry.bisIso }
                    : undefined
                }
              />
            );
          case "Fähre":
            return (
              <FerryCard
                key={`ferry-${entry.ferry.name}-${entryIdx}`}
                state={state}
                entry={entry}
                isSubmitting={isSubmitting}
              />
            );
        }
      })}
    </ol>
  );
}
