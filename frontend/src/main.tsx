/** Entry point of the Tesla trip planner frontend.
 *
 * Renders the main app (route planning + visualization, see `App.tsx`) and
 * sets up the MapLibre GL JS worker URL (see below).
 */

import { createRoot } from "react-dom/client";
import { setWorkerUrl } from "maplibre-gl";
import { App } from "./App";
import "./index.css";

// MapLibre GL JS spawns a Web Worker (parses vector tiles off the main
// thread) and computes the worker URL at runtime relative to its own module
// URL. Rollup cannot see that dynamically computed URL in the production
// build, so the worker request would 404 and the map would stay in its
// loading state forever (no `load` event; see `vite.config.ts`,
// `optimizeDeps.exclude` for the dev-server-side part of the same problem).
// The `?worker&url` import makes Vite bundle the worker file as its own,
// correctly referenced chunk; `setWorkerUrl()` overrides MapLibre's own
// (broken) computation with it. Owned by the main app entry only - the
// interactive export viewer sets its own (blob) worker URL instead.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(maplibreWorkerUrl);

// Get the DOM element and create the React root
const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root element #root not found");
}

const root = createRoot(rootEl);

root.render(<App />);
