import { type ReactNode } from "react";
import { CalendarDays, Car, type LucideIcon } from "lucide-react";
import {
  formatDrivingSegmentDistance,
  formatDrivingSegmentDuration,
} from "./form-helpers";
import {
  formatShortDate,
  formatTime,
  formatDateTime,
} from "@/utils/datetime-utils";

/** Timeline row sub-components for the route timeline display.
 *  These are pure presentational components used by TripPlannerForm.
 */
const ROW_GAP = "0.85rem";

/** An entry in the vertical route timeline: icon marker on the left (on the
 *  continuous line, analogous to common "tracking timeline" components) +
 *  arbitrary content (stop/charging stop/ferry card) on the right. */
export function TimelineRow({
  icon: Icon,
  background,
  children,
}: {
  icon: LucideIcon;
  background: string;
  children: ReactNode;
}) {
  return (
    <li
      style={{
        position: "relative",
        marginLeft: "1.5rem",
        paddingBottom: ROW_GAP,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: "-1.9rem",
          top: 0,
          width: "1.8rem",
          height: "1.8rem",
          borderRadius: "9999px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background,
          boxShadow: "0 0 0 4px #fafafa",
          zIndex: 1,
        }}
      >
        <Icon size={15} strokeWidth={2} color="#1f2937" />
      </span>
      {children}
    </li>
  );
}

/** Small badge "hanging" on the top or bottom map frame with
 *  time (and optional SoC, for charging stops) - overlays the frame line
 *  instead of claiming additional vertical space in the map layout
 *  (`position: absolute`, no effect on map height). The surrounding
 *  map container needs `position: relative` for this. Only rendered
 *  when a time is known ("where applicable", see caller). `dateShort`
 *  adds the date if needed (e.g. when arrival and departure of THE SAME
 *  entry fall on different calendar days, see
 *  `isDayChange` calls in callers). */
export function TimeBadge({
  edge,
  iso,
  socPct,
  showDate = false,
  showTime = true,
}: {
  edge: "oben" | "unten";
  iso: string;
  socPct?: number;
  showDate?: boolean;
  showTime?: boolean;
}) {
  const edgeStyle =
    edge === "oben"
      ? { top: 0, left: "0.75rem", transform: "translateY(-50%)" }
      : { bottom: 0, right: "0.75rem", transform: "translateY(50%)" };
  let _iso = undefined;
  if (iso) {
    if (showDate && showTime) {
      _iso = formatDateTime(iso);
    } else if (showDate) {
      _iso = formatShortDate(iso);
    } else if (showTime) {
      _iso = formatTime(iso);
    }
  }
  return (
    <span
      style={{
        position: "absolute",
        ...edgeStyle,
        background: "white",
        border: "1px solid #d1d5db",
        borderRadius: "999px",
        padding: "0.05rem 0.45rem",
        fontSize: "0.65rem",
        fontWeight: 600,
        color: "#374151",
        whiteSpace: "nowrap",
        zIndex: 1,
      }}
    >
      {_iso}
      {socPct !== undefined ? ` · ${Math.round(socPct)}%` : ""}
    </span>
  );
}

/** Compact day-change separator in the route timeline: thin line with
 *  the two adjacent calendar days (previous day on top, new day
 *  on bottom) in small font - deliberately kept brief to use barely any
 *  additional vertical space in the list (see `isDayChange`).
 *  Carries its own icon marker on the timeline line like `TimelineRow`,
 *  so the day change is immediately recognizable there instead of only in
 *  the small font. Only rendered between two entries WITHOUT a driving segment
 *  in between (see `DayChangeEntry` docstring in
 *  `route-entries.ts` - with a driving segment the day change is instead
 *  combined in its row, see `DrivingSegmentRow`). */
export function DaySeparator({
  previousIso,
  currentIso,
}: {
  previousIso: string;
  currentIso: string;
}) {
  return (
    <li
      style={{
        position: "relative",
        listStyle: "none",
        marginLeft: "1.5rem",
        paddingBottom: ROW_GAP,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: "-1.9rem",
          top: 0,
          width: "1.8rem",
          height: "1.8rem",
          borderRadius: "9999px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#e5e7eb",
          boxShadow: "0 0 0 4px #fafafa",
          zIndex: 1,
        }}
      >
        <CalendarDays size={13} strokeWidth={2} color="#4b5563" />
      </span>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "0.1rem",
          minHeight: "1.8rem",
        }}
      >
        <span style={{ fontSize: "0.65rem", lineHeight: 1, color: "#9ca3af" }}>
          {formatShortDate(previousIso)}
        </span>
        <div style={{ width: "100%", height: "1px", background: "#e5e7eb" }} />
        <span style={{ fontSize: "0.65rem", lineHeight: 1, color: "#9ca3af" }}>
          {formatShortDate(currentIso)}
        </span>
      </div>
    </li>
  );
}

/** Kompakte "Fahrt dazwischen"-Zeile in der Routen-Timeline: gefahrene
 *  Strecke und Zeit zwischen zwei Stopp-/Ladehalt-/Fähren-Karten (siehe
 *  `Fahrsegment` in `route-entries.ts`). Bewusst deutlich unauffälliger
 *  als ein "echter" Halt - kein Kartenrahmen, kein farbiger Icon-Kreis
 *  (nur das blasse Icon direkt auf der Timeline-Linie), einzeilig statt
 *  mehrzeilig - repräsentiert schließlich nur die Verbindung dazwischen,
 *  nicht einen eigenen Stopp. Überspannt das Fahrsegment einen Tageswechsel
 *  (`dayChange` gesetzt), werden Strecke, Zeit UND beide Kalendertage in
 *  dieser einen Zeile kombiniert, statt zusätzlich einen separaten
 *  `Tagestrenner` zu rendern. */
export function DriveSegmentRow({
  distanceKm: distanceKm,
  durationMin: durationMin,
  dayChange,
}: {
  distanceKm: number;
  durationMin: number;
  dayChange?: { fromIso: string; toIso: string };
}) {
  return (
    <li
      style={{
        position: "relative",
        listStyle: "none",
        marginLeft: "1.5rem",
        paddingBottom: ROW_GAP,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: "-1.40625rem",
          top: "50%",
          transform: "translateY(-50%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1,
        }}
      >
        <Car size={13} strokeWidth={2} color="#b0b5bd" />
      </span>
      <div style={{ lineHeight: 1 }}>
        <span style={{ fontSize: "0.7rem", color: "#9ca3af" }}>
          {formatDrivingSegmentDistance(distanceKm)} ·{" "}
          {formatDrivingSegmentDuration(durationMin)}
          {dayChange && (
            <>
              {" · "}
              {formatShortDate(dayChange.fromIso)} →{" "}
              {formatShortDate(dayChange.toIso)}
            </>
          )}
        </span>
      </div>
    </li>
  );
}
