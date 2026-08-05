/** Entry-Point des Visualization-Frontend.
 *
 * Initialisiert MapLibre GL JS und rendert die Hauptkomponente.
 *
 * WICHTIG: MapLibre GL JS benötigt einen Worker für WebGL.
 * In Vite wird dieser automatisch über den Import-Url-Plugin geladen.
 */

import { createRoot } from "react-dom/client";
import { MapVisualization } from "./components/Map";
import { TripSimulationResult } from "./types";

// Demo-Simulationsergebnis (für Entwicklung/Testing)
const demoSimulationResult: TripSimulationResult = {
  frames: [
    {
      zeitpunkt: "2026-01-01T08:00:00Z",
      position: [52.52, 13.4],
      soc_pct: 80,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 120,
    },
    {
      zeitpunkt: "2026-01-01T08:01:00Z",
      position: [52.53, 13.42],
      soc_pct: 78,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 115,
    },
    {
      zeitpunkt: "2026-01-01T08:02:00Z",
      position: [52.55, 13.45],
      soc_pct: 75,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 110,
    },
    {
      zeitpunkt: "2026-01-01T08:03:00Z",
      position: [52.57, 13.48],
      soc_pct: 72,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 100,
    },
    {
      zeitpunkt: "2026-01-01T08:04:00Z",
      position: [52.6, 13.52],
      soc_pct: 68,
      zustand: "LADEN",
      geschwindigkeit_kmh: 0,
    },
    {
      zeitpunkt: "2026-01-01T08:15:00Z",
      position: [52.6, 13.52],
      soc_pct: 85,
      zustand: "LADEN",
      geschwindigkeit_kmh: 0,
    },
    {
      zeitpunkt: "2026-01-01T08:16:00Z",
      position: [52.62, 13.55],
      soc_pct: 84,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 110,
    },
    {
      zeitpunkt: "2026-01-01T08:17:00Z",
      position: [52.65, 13.58],
      soc_pct: 82,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 115,
    },
    {
      zeitpunkt: "2026-01-01T08:18:00Z",
      position: [52.68, 13.62],
      soc_pct: 80,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 120,
    },
    {
      zeitpunkt: "2026-01-01T08:19:00Z",
      position: [52.7, 13.65],
      soc_pct: 78,
      zustand: "PAUSE",
      geschwindigkeit_kmh: 0,
    },
    {
      zeitpunkt: "2026-01-01T08:30:00Z",
      position: [52.7, 13.65],
      soc_pct: 78,
      zustand: "PAUSE",
      geschwindigkeit_kmh: 0,
    },
    {
      zeitpunkt: "2026-01-01T08:31:00Z",
      position: [52.72, 13.68],
      soc_pct: 77,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 120,
    },
    {
      zeitpunkt: "2026-01-01T08:32:00Z",
      position: [52.75, 13.72],
      soc_pct: 75,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 115,
    },
    {
      zeitpunkt: "2026-01-01T08:33:00Z",
      position: [52.78, 13.75],
      soc_pct: 72,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 110,
    },
    {
      zeitpunkt: "2026-01-01T08:34:00Z",
      position: [52.8, 13.78],
      soc_pct: 70,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 100,
    },
  ],
  gesamt_distanz_km: 45.5,
  gesamt_fahrzeit_min: 34,
  gesamt_ladezeit_min: 11,
  start_soc_pct: 80,
  ziel_soc_pct: 70,
};

// DOM-Element holen und Root erzeugen
const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root-Element #root nicht gefunden");
}

const root = createRoot(rootEl);

// App rendern
root.render(
  <div style={{ width: "100vw", height: "100vh" }}>
    <MapVisualization simulationResult={demoSimulationResult} />
  </div>,
);
