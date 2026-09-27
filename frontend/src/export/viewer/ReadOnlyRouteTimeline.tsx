/** Read-only route timeline for the interactive export.
 *
 * Mirrors the structure of the planning timeline
 * (`components/TripPlannerForm/sections/RouteSection.tsx`) without any
 * inputs/buttons: entries come from the same `buildRouteEntries` builder and
 * are rendered with the same presentational row components
 * (`TimelineRow`/`TimeBadge`/`DaySeparator`/`DriveSegmentRow`). The editable
 * cards (`StopCard`/`ChargingStopCard`/`FerryCard`) are not reused - they
 * require the full planning state and render inputs.
 */

import { Ship, Zap } from "lucide-react";

import type { TripSimulationResult } from "../../types";
import type { Stop } from "../../types/trip-request";
import { buildRouteEntries } from "../../utils/route-entries";
import { formatDayMonth, isDayChange } from "../../utils/datetime-utils";
import { formatCostOrDash } from "../../utils/currency-utils";
import {
  differsAsTime,
  formatChargingStationName,
  getStopRole,
  getStopTimelineIcon,
} from "../../components/TripPlannerForm/form-helpers";
import {
  DaySeparator,
  DriveSegmentRow,
  TimeBadge,
  TimelineRow,
} from "../../components/TripPlannerForm/timeline-rows";
import { findWaypointStopAt } from "../../components/Map/popups";

/** Card container style, copied from the planning cards
 *  (`ChargingStopCard.tsx`). */
const CARD_STYLE = {
  position: "relative",
  border: "1px solid #e5e7eb",
  borderRadius: "6px",
  padding: "0.5rem 0.75rem",
  background: "white",
  fontSize: "0.8rem",
  display: "grid",
  gap: "0.3rem",
} as const;

interface ReadOnlyRouteTimelineProps {
  result: TripSimulationResult;
  stops: Stop[];
}

/** The chronological route timeline (stops, charging stops, ferries and the
 *  drive segments in between) as a non-interactive overview. */
export function ReadOnlyRouteTimeline({
  result,
  stops,
}: ReadOnlyRouteTimelineProps) {
  // The export has no ignore-ferry state; `[]` matches the Zeitplan
  // (buildTimePlan), which also lists every detected ferry.
  const entries = buildRouteEntries({
    stops,
    frames: result.frames,
    chargingStops: result.chargingStops,
    detectedFerries: result.detectedFerries,
    avoidedFerries: [],
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
      {entries.map((entry, entryIdx) => {
        switch (entry.art) {
          case "Stopp": {
            const { stop, stopIndex: idx } = entry;
            const { Icon, background } = getStopTimelineIcon(stops, idx);
            const departureDateShort =
              entry.timing.departure !== null &&
              isDayChange(entry.timing.arrival, entry.timing.departure)
                ? formatDayMonth(entry.timing.departure)
                : undefined;
            const waypointStop = stop.position
              ? findWaypointStopAt(result.waypointStops, stop.position)
              : null;
            return (
              <TimelineRow key={stop.id} icon={Icon} background={background}>
                <div style={CARD_STYLE}>
                  {entry.timing.arrival !== null && (
                    <TimeBadge
                      edge="oben"
                      iso={entry.timing.arrival}
                      socPct={entry.timing.arrivalSocPct ?? undefined}
                    />
                  )}
                  {entry.timing.departure !== null &&
                    differsAsTime(
                      entry.timing.arrival,
                      entry.timing.departure,
                    ) && (
                      <TimeBadge
                        edge="unten"
                        iso={entry.timing.departure}
                        socPct={entry.timing.departureSocPct ?? undefined}
                        shortDate={departureDateShort}
                      />
                    )}
                  <span
                    style={{
                      fontSize: "0.65rem",
                      fontWeight: 600,
                      color: "#6b7280",
                      textTransform: "uppercase",
                      letterSpacing: "0.03em",
                    }}
                  >
                    {getStopRole(stops, idx)}
                  </span>
                  <strong>
                    {stop.address ??
                      (stop.position
                        ? `${stop.position[0].toFixed(4)}, ${stop.position[1].toFixed(4)}`
                        : "(unbekannt)")}
                  </strong>
                  {waypointStop && waypointStop.energyChargedKwh > 0 && (
                    <span style={{ color: "#374151" }}>
                      Geladen: {waypointStop.energyChargedKwh.toFixed(1)} kWh
                    </span>
                  )}
                </div>
              </TimelineRow>
            );
          }
          case "Ladehalt": {
            const stop = entry.chargingStop;
            const departureDateShort = isDayChange(
              stop.arrivalTime,
              stop.departureTime,
            )
              ? formatDayMonth(stop.departureTime)
              : undefined;
            return (
              <TimelineRow key={stop.stationId} icon={Zap} background="#dcfce7">
                <div style={CARD_STYLE}>
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
                  <span style={{ color: "#374151" }}>
                    {Math.round(stop.chargingDurationS / 60)} min ·{" "}
                    {stop.energyChargedKwh.toFixed(1)} kWh ·{" "}
                    {formatCostOrDash(stop.estimatedCost, stop.currency)}
                  </span>
                </div>
              </TimelineRow>
            );
          }
          case "Fähre": {
            const ferry = entry.ferry;
            const departureDateShort =
              entry.timing.departure !== null &&
              isDayChange(entry.timing.arrival, entry.timing.departure)
                ? formatDayMonth(entry.timing.departure)
                : undefined;
            return (
              <TimelineRow
                key={`ferry-${ferry.name}-${entryIdx}`}
                icon={Ship}
                background="#dbeafe"
              >
                <div style={CARD_STYLE}>
                  {entry.timing.arrival !== null && (
                    <TimeBadge edge="oben" iso={entry.timing.arrival} />
                  )}
                  {entry.timing.departure !== null &&
                    differsAsTime(
                      entry.timing.arrival,
                      entry.timing.departure,
                    ) && (
                      <TimeBadge
                        edge="unten"
                        iso={entry.timing.departure}
                        shortDate={departureDateShort}
                      />
                    )}
                  <strong>{ferry.name}</strong>
                  <span style={{ color: "#374151" }}>
                    {(ferry.lengthM / 1000).toFixed(1)} km Fähre
                  </span>
                </div>
              </TimelineRow>
            );
          }
          case "Tagestrenner":
            return (
              <DaySeparator
                key={`separator-${entryIdx}`}
                previousIso={entry.vonIso}
                currentIso={entry.bisIso}
              />
            );
          case "Fahrsegment":
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
        }
      })}
    </ol>
  );
}

export default ReadOnlyRouteTimeline;
