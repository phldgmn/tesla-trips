import { TripSimulationResult } from "./types";
import { MapVisualization } from "./components/Map";

interface AppProps {
  simulationResult: TripSimulationResult;
}

/** Haupt-App-Komponente */
export function App({ simulationResult }: AppProps) {
  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <MapVisualization simulationResult={simulationResult} />
    </div>
  );
}

export default App;
