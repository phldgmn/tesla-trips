import { useCallback, useState } from "react";

import { usePersistentState } from "./utils/persistent-state";

import { MapVisualization } from "./components/Map";
import { TripPlannerForm } from "./components/TripPlannerForm";
import { TripSummary } from "./components/TripSummary";
import { submitTripRequest, TripApiError } from "./api/tripApi";
import { downloadTripExport } from "./export/download-export";
import type { TripSimulationResult } from "./types";
import type { Stop, TripRequestPayload } from "./types/trip-request";

/**
 * Two default stops: Berlin Hauptbahnhof → Berlin-Köpenick.
 * The default GraphHopper dataset now covers all of
 * Germany, Denmark and Sweden (see README.md,
 * `scripts/prepare_osm_extract.sh`, `docker-compose.yml`) - a
 * default destination outside these three countries would still be rejected
 * by GraphHopper with HTTP 400 ("Point out of bounds").
 * Both stops have a resolved coordinate and address so the
 * map immediately shows something. The role (start/waypoint/destination) is
 * determined purely by position in the array – `stops[0]` = departure,
 * `stops[-1]` = destination. Stops in between = waypoint stops.
 */
const INITIAL_STOPS: Stop[] = [
  {
    id: crypto.randomUUID(),
    address: "Berlin Hauptbahnhof",
    position: [52.525, 13.369],
  },
  {
    id: crypto.randomUUID(),
    address: "Berlin-Köpenick",
    position: [52.4433, 13.5762],
  },
] as const;

/** Main app component: coordinates the planning form, map and result display.
 *
 * State:
 * - `stops`: dynamic, ordered list of stop objects (at least 2 stops).
 *   The first entry is the departure, the last is the destination. Waypoints
 *   can be added/removed/reordered freely.
 * - `pickingStopId`: which stop is being placed by map click.
 * - `simulationResult`/`isSubmitting`/`submitError`: result of the last
 *   `POST /trips` call.
 */
export function App() {
  const [stops, setStops] = usePersistentState<Stop[]>("stops", INITIAL_STOPS);
  const [pickingStopId, setPickingStopId] = useState<string | null>(null);
  const [simulationResult, setSimulationResult] = useState<
    TripSimulationResult | undefined
  >(undefined);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [superchargerVisible, setSuperchargerVisible] = usePersistentState(
    "supercharger-visible",
    false,
  );

  /** Update the position of a single stop in the stops list without leaving the current selection mode. */
  const applyStopPositionUpdate = useCallback(
    (stopId: string, position: [number, number]) => {
      setStops((prev) =>
        prev.map((s) => (s.id === stopId ? { ...s, position } : s)),
      );
    },
    [setStops],
  );

  /** Single map click in pick mode: save coordinate,
   *  then exit pick mode. */
  const handlePickPosition = useCallback(
    (stopId: string, position: [number, number]) => {
      applyStopPositionUpdate(stopId, position);
      setPickingStopId(null);
    },
    [applyStopPositionUpdate],
  );

  /** Drag-and-drop on the map: update stop position but do NOT
   *  leave the current selection mode (independent interaction). */
  const handleStopMove = useCallback(
    (stopId: string, position: [number, number]) => {
      applyStopPositionUpdate(stopId, position);
    },
    [applyStopPositionUpdate],
  );

  /** Calculate the trip. */
  const handleSubmit = useCallback((payload: TripRequestPayload) => {
    setIsSubmitting(true);
    setSubmitError(null);
    submitTripRequest(payload)
      .then((result) => {
        setSimulationResult(result);
      })
      .catch((error: unknown) => {
        const message =
          error instanceof TripApiError
            ? error.message
            : "Unerwarteter Fehler bei der Routenberechnung.";
        setSubmitError(message);
      })
      .finally(() => {
        setIsSubmitting(false);
      });
  }, []);

  return (
    <div style={{ display: "flex", width: "100vw", height: "100vh" }}>
      <TripPlannerForm
        stops={stops}
        onStopsChange={setStops}
        pickingStopId={pickingStopId}
        onRequestPick={setPickingStopId}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
        submitError={submitError}
        detectedFerries={simulationResult?.detectedFerries}
        chargingStops={simulationResult?.chargingStops}
        frames={simulationResult?.frames}
      />
      <div style={{ position: "relative", flex: 1 }}>
        <MapVisualization
          simulationResult={simulationResult}
          stops={stops}
          pickingStopId={pickingStopId}
          onPickPosition={handlePickPosition}
          onStopMove={handleStopMove}
          superchargerVisible={superchargerVisible}
          onToggleSuperchargers={() => setSuperchargerVisible((v) => !v)}
        />
        {simulationResult && (
          <div
            style={{
              position: "absolute",
              top: "1rem",
              right: "1rem",
              width: "320px",
              maxHeight: "calc(100vh - 2rem)",
              overflowY: "auto",
              background: "white",
              borderRadius: "8px",
              boxShadow: "0 2px 12px rgba(0, 0, 0, 0.18)",
            }}
          >
            <TripSummary
              result={simulationResult}
              stops={stops}
              onExport={() => downloadTripExport(simulationResult, stops)}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
