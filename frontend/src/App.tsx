import { useCallback, useState } from "react";

import { usePersistentState } from "./utils/persistent-state";

import { MapVisualization } from "./components/Map";
import { TripPlannerForm } from "./components/TripPlannerForm";
import { TripSummary } from "./components/TripSummary";
import { submitTripRequest, TripApiError } from "./api/tripApi";
import type { TripSimulationResult } from "./types";
import type { Stop, TripRequestPayload } from "./types/trip-request";

/**
 * Zwei Start-Stopps: Berlin Hauptbahnhof → Berlin-Köpenick.
 * Der standardmäßig geladene GraphHopper-Datensatz deckt inzwischen ganz
 * Deutschland, Dänemark und Schweden ab (siehe README.md,
 * `scripts/prepare_osm_extract.sh`, `docker-compose.yml`) - ein
 * Default-Ziel außerhalb dieser drei Länder würde GraphHopper weiterhin
 * mit HTTP 400 ("Point out of bounds") ablehnen.
 * Beide Stopps haben eine aufgelöste Koordinate und Adresse, damit die
 * Karte sofort etwas anzeigt. Die Rolle (Start/Zwischenstopp/Ziel) ergibt
 * sich rein aus der Position im Array – `stops[0]` = Abfahrt,
 * `stops[-1]` = Ziel. Stopps dazwischen = Zwischenstopps.
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

/** Haupt-App-Komponente: koordiniert Planungsformular, Karte und Ergebnisanzeige.
 *
 * Zustand:
 * - `stops`: dynamische, geordnete Liste von Stop-Objekten (mind. 2 Stops).
 *   Der erste Eintrag ist die Abfahrt, der letzte das Ziel. Zwischenstopps
 *   können beliebig hinzugefügt/entfernt/umsortiert werden.
 * - `pickingStopId`: welcher Stopp gerade per Kartenklick platziert wird.
 * - `simulationResult`/`isSubmitting`/`submitError`: Ergebnis des letzten
 *   `POST /trips`-Aufrufs.
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

  /** Aktualisiert die Position eines einzelnen Stopps in der Stops-Liste. */
  const applyStopPositionUpdate = useCallback(
    (stopId: string, position: [number, number]) => {
      setStops((prev) =>
        prev.map((s) => (s.id === stopId ? { ...s, position } : s)),
      );
    },
    [setStops],
  );

  /** Einmaliger Kartenklick im Auswahlmodus: Koordinate speichern,
   *  dann den Auswahlmodus verlassen. */
  const handlePickPosition = useCallback(
    (stopId: string, position: [number, number]) => {
      applyStopPositionUpdate(stopId, position);
      setPickingStopId(null);
    },
    [applyStopPositionUpdate],
  );

  /** Drag-and-Drop auf der Karte: Stopp-Position aktualisieren, aber den
   *  Auswahlmodus NICHT verlassen (unabhängige Interaktion). */
  const handleStopMove = useCallback(
    (stopId: string, position: [number, number]) => {
      applyStopPositionUpdate(stopId, position);
    },
    [applyStopPositionUpdate],
  );

  /** Reise berechnen lassen. */
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
        erkannteFaehren={simulationResult?.erkannte_faehren}
        chargingStops={simulationResult?.charging_stops}
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
            <TripSummary result={simulationResult} stops={stops} />
          </div>
        )}
      </div>
    </div>
  );
}

export default App;