import { useState } from "react";
import type { Stop, TripRequestPayload } from "../../types/trip-request";
import type { ChargingStop, FerrySegment, SimulationFrame } from "../../types";
import { useTripPlannerState } from "./useTripPlannerState";
import { useGeocoding } from "./useGeocoding";
import {
  VehicleSection,
  RouteOptionsSection,
  RouteSection,
  IgnoredFerriesModal,
} from "./sections";

export type { GeocodingState, GeocodeSuggestionDisplay } from "./useGeocoding";

export interface TripPlannerFormProps {
  stops: Stop[];
  onStopsChange: (stops: Stop[]) => void;
  pickingStopId: string | null;
  onRequestPick: (stopId: string | null) => void;
  onSubmit: (payload: TripRequestPayload) => void;
  isSubmitting: boolean;
  submitError?: string | null;
  /** Ferries detected in the last computed route (from
   *  `TripSimulationResult.detectedFerries`); `undefined`/empty until a route
   *  has been computed. */
  detectedFerries?: FerrySegment[];
  /** Charging stops of the last computed plan, shown with an editable
   *  charging duration; `undefined`/empty until a route has been computed. */
  chargingStops?: ChargingStop[];
  /** Simulation frames used to place stops, charging stops and ferries in
   *  time (see `buildRouteEntries`); `undefined`/empty until computed. */
  frames?: SimulationFrame[];
}

/** Trip planner sidebar. State lives in `useTripPlannerState` and
 *  `useGeocoding`; the UI is composed from the components in `./sections`. */
export function TripPlannerForm({
  stops,
  onStopsChange,
  pickingStopId,
  onRequestPick,
  onSubmit,
  isSubmitting,
  submitError,
  detectedFerries,
  chargingStops,
  frames,
}: TripPlannerFormProps) {
  const state = useTripPlannerState({ stops, onStopsChange, onSubmit });
  const geocoding = useGeocoding(stops, onStopsChange);
  const [isIgnoredFerriesModalOpen, setIsIgnoredFerriesModalOpen] =
    useState(false);
  const { validationErrors, addStop: handleAddStop } = state;
  const handleSubmit = () => state.submit();

  return (
    <div
      style={{
        width: "380px",
        maxHeight: "100vh",
        overflowY: "auto",
        padding: "1.5rem",
        background: "#fafafa",
        borderRight: "1px solid #e5e7eb",
        fontFamily: "system-ui, -apple-system, sans-serif",
        fontSize: "0.9rem",
        lineHeight: "1.5",
      }}
    >
      {/* Submit Error Banner */}
      {submitError && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.75rem",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: "4px",
            color: "#991b1b",
            fontSize: "0.85rem",
          }}
        >
          {submitError}
        </div>
      )}

      {/* Validation Errors */}
      {validationErrors.length > 0 && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.75rem",
            background: "#fffbeb",
            border: "1px solid #fde68a",
            borderRadius: "4px",
            color: "#92400e",
            fontSize: "0.85rem",
          }}
        >
          <ul style={{ margin: "0 0 0 1rem", padding: 0 }}>
            {validationErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      <VehicleSection state={state} />
      <RouteOptionsSection
        state={state}
        isSubmitting={isSubmitting}
        onOpenIgnoredFerries={() => setIsIgnoredFerriesModalOpen(true)}
      />
      <RouteSection
        state={state}
        geocoding={geocoding}
        stops={stops}
        pickingStopId={pickingStopId}
        onRequestPick={onRequestPick}
        isSubmitting={isSubmitting}
        detectedFerries={detectedFerries}
        chargingStops={chargingStops}
        frames={frames}
      />
      <IgnoredFerriesModal
        state={state}
        open={isIgnoredFerriesModalOpen}
        onClose={() => setIsIgnoredFerriesModalOpen(false)}
        detectedFerries={detectedFerries}
        isSubmitting={isSubmitting}
      />

      {/* Add Stop Button */}
      <button
        type="button"
        onClick={handleAddStop}
        disabled={isSubmitting || stops.length < 1}
        style={{
          width: "100%",
          padding: "0.5rem",
          marginBottom: "0.75rem",
          background: "#f3f4f6",
          border: "1px solid #d1d5db",
          borderRadius: "4px",
          cursor: isSubmitting || stops.length < 1 ? "not-allowed" : "pointer",
          opacity: isSubmitting || stops.length < 1 ? 0.6 : 1,
        }}
      >
        + Stopp hinzufügen
      </button>

      {/* 4. Submit */}
      <button
        type="button"
        onClick={handleSubmit}
        disabled={isSubmitting || validationErrors.length > 0}
        style={{
          width: "100%",
          padding: "0.75rem",
          fontSize: "1rem",
          fontWeight: 600,
          background:
            validationErrors.length > 0 || isSubmitting ? "#9ca3af" : "#2563eb",
          color: "white",
          border: "none",
          borderRadius: "6px",
          cursor:
            validationErrors.length > 0 || isSubmitting
              ? "not-allowed"
              : "pointer",
        }}
      >
        {isSubmitting ? "Berechne…" : "Route berechnen"}
      </button>
    </div>
  );
}

export default TripPlannerForm;
