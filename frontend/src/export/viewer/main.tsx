/** Entry point of the interactive HTML export (bundled by
 *  `vite-plugins/trip-export-bundle.ts` into a single inline JS blob).
 *
 * Reads the trip data and the MapLibre worker source out of the host HTML
 * document (written by `buildExportHtml`), points MapLibre at the embedded
 * worker and renders the read-only `ExportViewer`.
 */

import { setWorkerUrl } from "maplibre-gl";
import { createRoot } from "react-dom/client";
import {
  EXPORT_DATA_ELEMENT_ID,
  WORKER_SOURCE_ELEMENT_ID,
  parseTripExportData,
} from "../export-html";
import { ExportViewer } from "./ExportViewer";

import "../../index.css";

// The worker source is embedded as plain text (see buildExportHtml). MapLibre
// creates its Web Workers from this URL, so it must be a same-origin,
// fetchable URL: a blob URL (never revoked - MapLibre may spawn more workers
// later, e.g. on style changes).
const workerSrc = document.getElementById(
  WORKER_SOURCE_ELEMENT_ID,
)?.textContent;
if (workerSrc) {
  setWorkerUrl(
    URL.createObjectURL(new Blob([workerSrc], { type: "text/javascript" })),
  );
}

const data = parseTripExportData(
  document.getElementById(EXPORT_DATA_ELEMENT_ID)?.textContent,
);

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root element #root not found");
}

const root = createRoot(rootEl);

if (data === null) {
  root.render(
    <p
      style={{
        padding: "2rem",
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: "#b91c1c",
      }}
    >
      Exportdaten fehlen oder sind beschädigt.
    </p>,
  );
} else {
  root.render(<ExportViewer data={data} />);
}
