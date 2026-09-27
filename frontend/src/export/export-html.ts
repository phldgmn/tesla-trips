/** Pure builder/serializer for the interactive HTML export.
 *
 * Freely free of React and the DOM: builds the trip data payload, embeds it
 * (plus the pre-bundled viewer JS/CSS and the MapLibre worker source) into a
 * single self-contained HTML document and parses such documents back. The
 * actual download lives in `./download-export.ts`, the viewer app in
 * `./viewer/`.
 */

import type { TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";
import { escapeHtml } from "../utils/html-escape";

/** Schema version of the embedded trip data; `parseTripExportData` rejects
 *  everything else (the file can be hand-edited or come from an older
 *  version of the app). */
export const TRIP_EXPORT_VERSION = 1;

/** `id` of the `<script type="application/json">` element holding the trip
 *  data inside the exported HTML file. */
export const EXPORT_DATA_ELEMENT_ID = "trip-export-data";

/** `id` of the `<script type="text/plain">` element holding the MapLibre
 *  worker source inside the exported HTML file. */
export const WORKER_SOURCE_ELEMENT_ID = "maplibre-worker-src";

/** Everything the export needs at display time - the trip data plus the
 *  pre-bundled viewer assets (see `virtual:trip-export-bundle`). */
export interface TripExportData {
  version: typeof TRIP_EXPORT_VERSION;
  /** ISO-8601 timestamp of the export. */
  exportedAt: string;
  /** Display title, e.g. "Berlin → Hamburg". */
  title: string;
  stops: Stop[];
  result: TripSimulationResult;
}

/** Pre-bundled viewer assets produced by the `trip-export-bundle` Vite
 *  plugin (single-file builds, no remaining imports). */
export interface ExportBundle {
  viewerJs: string;
  viewerCss: string;
  workerJs: string;
}

/** Short display label for a stop: part of the address before the first
 *  comma, else the rounded coordinates, else the role. */
function stopLabel(stop: Stop | undefined, fallback: string): string {
  if (stop?.address) {
    const comma = stop.address.indexOf(",");
    return comma > 0 ? stop.address.slice(0, comma) : stop.address;
  }
  if (stop?.position) {
    const [lat, lng] = stop.position;
    return `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  }
  return fallback;
}

/** Builds the trip data embedded into the exported HTML file. `now` is
 *  injected for testability. */
export function buildTripExportData(
  result: TripSimulationResult,
  stops: Stop[],
  now: Date,
): TripExportData {
  return {
    version: TRIP_EXPORT_VERSION,
    exportedAt: now.toISOString(),
    title: `${stopLabel(stops[0], "Start")} → ${stopLabel(
      stops[stops.length - 1],
      "Ziel",
    )}`,
    stops,
    result,
  };
}

// ponytail: these two escapes are only correct where the escaped sequences
// occur inside JS/CSS string or regex literals (true for the minified
// bundles embedded here); upgrade path = base64-encode the bundle and
// `import()` it from a blob URL if a bundle ever contains them outside
// literals.
/** Escapes `</script` and `<!--` sequences so inline JS cannot terminate its
 *  own `<script>` element or open a comment that swallows code. */
function escapeInlineScript(code: string): string {
  return code.replace(/<\/(script)/gi, "<\\/$1").replace(/<!--/g, "<\\!--");
}

/** Escapes `</style` sequences so inline CSS cannot terminate its own
 *  `<style>` element. */
function escapeInlineStyle(css: string): string {
  return css.replace(/<\/(style)/gi, "<\\/$1");
}

/** Builds the single self-contained HTML file: viewer CSS + JS, the MapLibre
 *  worker source and the trip data, all inlined - no external file besides
 *  the (online) basemap tiles. */
export function buildExportHtml(
  data: TripExportData,
  bundle: ExportBundle,
): string {
  return `<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>${escapeHtml(data.title)}</title>
<style>*{margin:0;padding:0;box-sizing:border-box}html,body{width:100%;height:100%}</style>
<style>${escapeInlineStyle(bundle.viewerCss)}</style>
</head>
<body>
<div id="root"></div>
<script type="application/json" id="${EXPORT_DATA_ELEMENT_ID}">${JSON.stringify(data).replace(/</g, "\\u003c")}</script>
<script type="text/plain" id="${WORKER_SOURCE_ELEMENT_ID}">${escapeInlineScript(bundle.workerJs)}</script>
<script type="module">${escapeInlineScript(bundle.viewerJs)}</script>
</body>
</html>`;
}

/** Parses the trip data out of an exported HTML file's data element
 *  (`raw` = the element's textContent). Returns `null` for missing/empty
 *  input, invalid JSON, a foreign version or a structurally broken payload -
 *  the file is a trust boundary (it can be hand-edited), so the viewer
 *  degrades to an error page instead of crashing. */
export function parseTripExportData(
  raw: string | null | undefined,
): TripExportData | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const data = parsed as Partial<TripExportData>;
  if (data.version !== TRIP_EXPORT_VERSION) return null;
  if (!Array.isArray(data.stops)) return null;
  if (
    typeof data.result !== "object" ||
    data.result === null ||
    !Array.isArray(data.result.frames)
  ) {
    return null;
  }
  return data as TripExportData;
}

/** Download file name for an export: `tesla-trip-<departure date>.html`
 *  (fallback: the export date if the simulation has no frames). */
export function exportFileName(data: TripExportData): string {
  const date =
    data.result.frames[0]?.timestamp.slice(0, 10) ??
    data.exportedAt.slice(0, 10);
  return `tesla-trip-${date}.html`;
}
