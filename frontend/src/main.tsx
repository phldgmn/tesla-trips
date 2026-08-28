/** Entry-Point des Tesla-Tripplaner-Frontends.
 *
 * Initialisiert MapLibre GL JS und rendert die Haupt-App (Routenplanung +
 * Visualisierung, siehe `App.tsx`).
 *
 * WICHTIG: MapLibre GL JS benötigt einen Worker für WebGL.
 * In Vite wird dieser automatisch über den Import-Url-Plugin geladen.
 */

import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

// DOM-Element holen und Root erzeugen
const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root-Element #root nicht gefunden");
}

const root = createRoot(rootEl);

root.render(<App />);
