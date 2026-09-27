/** Viewer app for the interactive HTML export (see `main.tsx` in this
 *  directory). Read-only layout: trip summary + Zeitplan + compact route
 *  timeline in a sidebar and the full interactive map filling the rest of
 *  the viewport (public OpenFreeMap basemap - the local tile server is not
 *  reachable from a downloaded file). On small screens the sidebar starts
 *  hidden and a menu button toggles it; MapLibre picks up the resulting map
 *  size change itself (`trackResize` defaults to true).
 */

import { useRef, useState } from "react";
import { MapVisualization, onlineBasemapStyle } from "../../components/Map";
import { TripSummary, type TripSummaryRef } from "../../components/TripSummary";
import { formatTimestamp } from "../../utils/datetime-utils";
import type { TripExportData } from "../export-html";
import { ReadOnlyRouteTimeline } from "./ReadOnlyRouteTimeline";
import { Sheet, Menu } from "lucide-react";

interface ExportViewerProps {
  data: TripExportData;
}

/** Viewport width from which the sidebar is shown by default. Below it the
 *  sidebar starts hidden and is toggled via the menu button. */
const SIDEBAR_MIN_WIDTH_QUERY = "(min-width: 900px)";

export function ExportViewer({ data }: ExportViewerProps) {
  const tripSummaryRef = useRef<TripSummaryRef>(null);
  // Sidebar starts hidden on small screens (where it would squeeze the map),
  // shown on large ones. One-time matchMedia probe, not a subscription: the
  // toggle button is always available to flip the state by hand.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => window.matchMedia(SIDEBAR_MIN_WIDTH_QUERY).matches,
  );
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
          // Hidden, not unmounted: keeps the timeline scroll position and
          // lets MapLibre re-measure the map via its own ResizeObserver
          // (`trackResize` default) when the sidebar is shown again.
          display: sidebarOpen ? "block" : "none",
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
      {/* Menu button over the map: shows/hides the sidebar (needed on small
          screens where the sidebar starts hidden). Kept mounted on large
          screens too so the sidebar can be re-hidden there as well. */}
      <div style={{ position: "relative", flex: 1 }}>
        <MapVisualization
          simulationResult={data.result}
          stops={data.stops}
          readOnly
          mapStyle={onlineBasemapStyle}
        />
        <button
          type="button"
          aria-label={sidebarOpen ? "Hide overview" : "Show overview"}
          aria-expanded={sidebarOpen}
          title={sidebarOpen ? "Hide overview" : "Show overview"}
          onClick={() => setSidebarOpen((open) => !open)}
          style={{
            position: "absolute",
            top: "1rem",
            left: "1rem",
            zIndex: 20,
            padding: "8px",
            border: "none",
            borderRadius: "6px",
            cursor: "pointer",
            background: "#ffffff",
            color: "#333333",
            boxShadow: "0 1px 6px rgba(0,0,0,0.18)",
            display: "flex",
            alignItems: "center",
          }}
        >
          <Menu size={16} />
        </button>
      </div>
    </div>
  );
}

export default ExportViewer;
