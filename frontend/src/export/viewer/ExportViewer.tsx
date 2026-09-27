/** Viewer app for the interactive HTML export (see `main.tsx` in this
 *  directory). Read-only layout: trip summary + Zeitplan + compact route
 *  timeline in a fixed sidebar, the full interactive map on the right
 *  (public OpenFreeMap basemap - the local tile server is not reachable from
 *  a downloaded file).
 */

import { useRef } from "react";
import { MapVisualization, onlineBasemapStyle } from "../../components/Map";
import { TripSummary, type TripSummaryRef } from "../../components/TripSummary";
import { formatTimestamp } from "../../utils/datetime-utils";
import type { TripExportData } from "../export-html";
import { ReadOnlyRouteTimeline } from "./ReadOnlyRouteTimeline";
import { Sheet } from "lucide-react";

interface ExportViewerProps {
  data: TripExportData;
}

export function ExportViewer({ data }: ExportViewerProps) {
  const tripSummaryRef = useRef<TripSummaryRef>(null);
  return (
    <div
      style={{
        display: "flex",
        width: "100vw",
        height: "100vh",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <aside
        style={{
          width: "380px",
          flexShrink: 0,
          overflowY: "auto",
          background: "#fafafa",
          borderRight: "1px solid #e5e7eb",
        }}
      >
        <header style={{ padding: "1rem 1rem 0" }}>
          <h1 style={{ fontSize: "1.05rem", margin: 0 }}>{data.title}</h1>
          <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>
            Erstellt am {formatTimestamp(data.exportedAt)}
          </div>
        </header>
        {/* No onExport: the export button stays hidden inside the export
            itself; the Zeitplan button/modal of TripSummary is the detailed
            time table. */}
        <TripSummary
          result={data.result}
          stops={data.stops}
          ref={tripSummaryRef}
        />
        <section style={{ padding: "0 0rem 1rem" }}>
          <h2
            style={{
              fontSize: "0.9rem",
              margin: "0 0 0.75rem",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span>Route</span>
            <button
              onClick={() => tripSummaryRef.current?.openSchedule()}
              style={{
                fontSize: "0.75rem",
                color: "#2563eb",
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: 0,
              }}
            >
              <Sheet size={18} strokeWidth={2} color="#1f2937" />
            </button>
          </h2>
          <ReadOnlyRouteTimeline result={data.result} stops={data.stops} />
        </section>
      </aside>
      <div style={{ position: "relative", flex: 1 }}>
        <MapVisualization
          simulationResult={data.result}
          stops={data.stops}
          readOnly
          mapStyle={onlineBasemapStyle}
        />
      </div>
    </div>
  );
}

export default ExportViewer;
