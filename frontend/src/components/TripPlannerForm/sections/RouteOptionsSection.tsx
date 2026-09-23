import { Popover } from "../../Popover";
import type {
  WeatherDetailLevel,
  HighwayPreferenceLevel,
} from "../../../types/trip-request";
import {
  Ship,
  CloudSun,
  Construction,
  Route,
  SignalZero,
  SignalLow,
  SignalMedium,
  SignalHigh,
} from "lucide-react";
import type { TripPlannerState } from "../useTripPlannerState";

interface RouteOptionsSectionProps {
  state: TripPlannerState;
  isSubmitting: boolean;
  onOpenIgnoredFerries: () => void;
}

/** Weather, construction-site, highway and ferry controls in one wrapping row
 *  of pills. The ferry pill is split in two: a toggle on the left (inverted
 *  meaning: on = ferries allowed) and, behind a thin divider, a counter that
 *  only opens the modal of permanently ignored ferries. */
export function RouteOptionsSection({
  state,
  isSubmitting,
  onOpenIgnoredFerries,
}: RouteOptionsSectionProps) {
  const {
    weatherDetailLevel,
    setWeatherDetailLevel,
    considerRoadworks,
    setConsiderRoadworks,
    highwayPreference,
    setHighwayPreference,
    avoidAllFerries,
    setAvoidAllFerries,
    avoidedFerries,
  } = state;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "0.4rem",
        marginBottom: "1rem",
      }}
    >
      <Popover
        content={
          <>
            <div style={{ fontWeight: 600, marginBottom: "0.25rem" }}>
              Wetter: {weatherDetailLevel === "off" && "Aus"}
              {weatherDetailLevel === "low" && "Niedrig"}
              {weatherDetailLevel === "medium" && "Mittel"}
              {weatherDetailLevel === "high" && "Hoch"}
            </div>
            {weatherDetailLevel === "off"
              ? "Wetterdaten werden ignoriert (klicken: Niedrig → Mittel → Hoch → Aus)"
              : "Durchklicken: nächste Stufe (Niedrig → Mittel → Hoch → Aus)"}
          </>
        }
      >
        <button
          type="button"
          onClick={() => {
            const levels: WeatherDetailLevel[] = [
              "off",
              "low",
              "medium",
              "high",
            ];
            const idx = levels.indexOf(weatherDetailLevel);
            setWeatherDetailLevel(levels[(idx + 1) % levels.length]);
          }}
          disabled={isSubmitting}
          aria-pressed={weatherDetailLevel !== "off"}
          aria-label={`Wetterberücksichtigung: ${weatherDetailLevel}`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.2rem",
            padding: "0.35rem 0.55rem",
            background: weatherDetailLevel !== "off" ? "#eff6ff" : "#f9fafb",
            border: `1px solid ${
              weatherDetailLevel !== "off" ? "#93c5fd" : "#e5e7eb"
            }`,
            borderRadius: "999px",
            cursor: isSubmitting ? "not-allowed" : "pointer",
            fontSize: "0.78rem",
            color: weatherDetailLevel !== "off" ? "#1d4ed8" : "#6b7280",
          }}
        >
          <CloudSun size={13} />
          {weatherDetailLevel === "off" && <SignalZero size={13} />}
          {weatherDetailLevel === "low" && <SignalLow size={13} />}
          {weatherDetailLevel === "medium" && <SignalMedium size={13} />}
          {weatherDetailLevel === "high" && <SignalHigh size={13} />}
        </button>
      </Popover>
      <Popover
        content={
          considerRoadworks
            ? "Baustellen werden bei der Berechnung berücksichtigt (klicken zum Deaktivieren)"
            : "Baustellen werden bei der Berechnung ignoriert (klicken zum Aktivieren)"
        }
      >
        <button
          type="button"
          onClick={() => setConsiderRoadworks(!considerRoadworks)}
          disabled={isSubmitting}
          aria-pressed={considerRoadworks}
          aria-label="Baustellen berücksichtigen"
          style={{
            display: "flex",
            alignItems: "center",
            padding: "0.35rem 0.55rem",
            background: considerRoadworks ? "#eff6ff" : "#f9fafb",
            border: `1px solid ${considerRoadworks ? "#93c5fd" : "#e5e7eb"}`,
            borderRadius: "999px",
            cursor: isSubmitting ? "not-allowed" : "pointer",
            fontSize: "0.78rem",
            color: considerRoadworks ? "#1d4ed8" : "#6b7280",
          }}
        >
          <Construction size={13} />
        </button>
      </Popover>
      <Popover
        content={
          <>
            <div style={{ fontWeight: 600, marginBottom: "0.25rem" }}>
              Autobahn: {highwayPreference === "off" && "Aus"}
              {highwayPreference === "low" && "Niedrig"}
              {highwayPreference === "medium" && "Mittel"}
              {highwayPreference === "high" && "Hoch"}
            </div>
            {highwayPreference === "off"
              ? "Autobahnen werden nicht bevorzugt (klicken: Niedrig → Mittel → Hoch → Aus)"
              : "Durchklicken: nächste Stufe (Niedrig → Mittel → Hoch → Aus)"}
          </>
        }
      >
        <button
          type="button"
          onClick={() => {
            const levels: HighwayPreferenceLevel[] = [
              "off",
              "low",
              "medium",
              "high",
            ];
            const idx = levels.indexOf(highwayPreference);
            setHighwayPreference(levels[(idx + 1) % levels.length]);
          }}
          disabled={isSubmitting}
          aria-pressed={highwayPreference !== "off"}
          aria-label={`Autobahnpräferenz: ${highwayPreference}`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.2rem",
            padding: "0.35rem 0.55rem",
            background: highwayPreference !== "off" ? "#eff6ff" : "#f9fafb",
            border: `1px solid ${
              highwayPreference !== "off" ? "#93c5fd" : "#e5e7eb"
            }`,
            borderRadius: "999px",
            cursor: isSubmitting ? "not-allowed" : "pointer",
            fontSize: "0.78rem",
            color: highwayPreference !== "off" ? "#1d4ed8" : "#6b7280",
          }}
        >
          <Route size={13} />
          {highwayPreference === "off" && <SignalZero size={13} />}
          {highwayPreference === "low" && <SignalLow size={13} />}
          {highwayPreference === "medium" && <SignalMedium size={13} />}
          {highwayPreference === "high" && <SignalHigh size={13} />}
        </button>
      </Popover>
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          borderRadius: "999px",
          border: `1px solid ${!avoidAllFerries ? "#93c5fd" : "#e5e7eb"}`,
          overflow: "hidden",
        }}
      >
        <Popover
          content={
            avoidAllFerries
              ? "Fähren werden bei der Berechnung vermieden (klicken zum Erlauben)"
              : "Fähren sind bei der Berechnung erlaubt (klicken zum Vermeiden)"
          }
        >
          <button
            type="button"
            onClick={() => setAvoidAllFerries(!avoidAllFerries)}
            disabled={isSubmitting}
            aria-pressed={!avoidAllFerries}
            aria-label="Fähren erlauben"
            style={{
              display: "flex",
              alignItems: "center",
              padding: "0.35rem 0.5rem 0.35rem 0.55rem",
              background: !avoidAllFerries ? "#eff6ff" : "#f9fafb",
              border: "none",
              cursor: isSubmitting ? "not-allowed" : "pointer",
              fontSize: "0.78rem",
              color: !avoidAllFerries ? "#1d4ed8" : "#6b7280",
            }}
          >
            <Ship size={13} />
          </button>
        </Popover>
        <div
          style={{
            width: "1px",
            background: !avoidAllFerries ? "#93c5fd" : "#e5e7eb",
          }}
        />
        <Popover content="Ignorierte Fähren verwalten">
          <button
            type="button"
            onClick={() => onOpenIgnoredFerries()}
            disabled={isSubmitting}
            style={{
              display: "flex",
              alignItems: "center",
              padding: "0.35rem 0.6rem 0.35rem 0.5rem",
              background: avoidedFerries.length > 0 ? "#fffbeb" : "#f9fafb",
              border: "none",
              cursor: isSubmitting ? "not-allowed" : "pointer",
              fontSize: "0.78rem",
              fontWeight: avoidedFerries.length > 0 ? 600 : 400,
              color: avoidedFerries.length > 0 ? "#b45309" : "#6b7280",
            }}
          >
            {avoidedFerries.length}
          </button>
        </Popover>
      </div>
    </div>
  );
}
