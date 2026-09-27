/** Builds the interactive HTML export for a trip and triggers a browser
 *  download.
 *
 * The pre-bundled viewer assets (see `vite-plugins/trip-export-bundle.ts`,
 * exposed as `virtual:trip-export-bundle`) are imported dynamically so the
 * ~2 MB bundle is not loaded into the main app bundle.
 */

import type { TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";
import {
  buildExportHtml,
  buildTripExportData,
  exportFileName,
} from "./export-html";

/** Builds the interactive HTML export for `result` and triggers a browser
 *  download. */
export async function downloadTripExport(
  result: TripSimulationResult,
  stops: Stop[],
): Promise<void> {
  const { default: bundle } = await import("virtual:trip-export-bundle");
  const data = buildTripExportData(result, stops, new Date());
  const html = buildExportHtml(data, bundle);

  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = exportFileName(data);
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Defer the revoke past the click so the download has a chance to start.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
