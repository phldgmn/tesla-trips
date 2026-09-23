import { useEffect, useRef, useState } from "react";
import { Map, type Marker } from "maplibre-gl";
import { usePersistentState } from "../../utils/persistent-state";
import type { TripSimulationResult } from "../../types";
import type { Stop } from "../../types/trip-request";
import { basemapStyle } from "./basemap";
import { buildRouteHoverText } from "./popups";
import {
  DEFAULT_MAP_VIEW,
  isValidMapViewState,
  type MapViewState,
} from "./view-state";
import { useRouteLayer } from "./hooks/useRouteLayer";
import { useConstructionZoneMarkers } from "./hooks/useConstructionZoneMarkers";
import { useChargingStopMarkers } from "./hooks/useChargingStopMarkers";
import { useStopMarkers } from "./hooks/useStopMarkers";
import { useSuperchargerLayer } from "./hooks/useSuperchargerLayer";

interface MapProps {
  /** Simulation result (optional - absent in planning mode). */
  simulationResult?: TripSimulationResult;
  /** Stop list (start/waypoints/destination); the role follows from the index. */
  stops?: Stop[];
  /** If set, a map click picks the position for this stop. */
  pickingStopId?: string | null;
  /** Called on a map click while `pickingStopId` is active. */
  onPickPosition?: (stopId: string, position: [number, number]) => void;
  /** Called when a stop marker is dragged (dragend). */
  onStopMove?: (stopId: string, position: [number, number]) => void;
  /** Whether the Supercharger overlay is visible. */
  superchargerVisible?: boolean;
  /** Toggles the Supercharger overlay. */
  onToggleSuperchargers?: () => void;
}

/** Map for planning and for visualizing simulation results.
 *
 * Each layer lives in its own hook under `./hooks`, which owns its effect and
 * cleanup:
 * - `useRouteLayer`: route with a SoC color gradient and hover tooltip
 * - `useConstructionZoneMarkers` / `useChargingStopMarkers`: result markers
 * - `useStopMarkers`: draggable start/waypoint/destination markers
 * - `useSuperchargerLayer`: toggleable Supercharger overlay with popovers
 *
 * A map click in picking mode (`pickingStopId`) reports the coordinate.
 */
export function MapVisualization({
  simulationResult,
  stops,
  pickingStopId,
  onPickPosition,
  onStopMove,
  superchargerVisible,
  onToggleSuperchargers,
}: MapProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const [isMapLoaded, setIsMapLoaded] = useState(false);
  // Stop markers keyed by stop.id; shared so other marker hooks can keep
  // them on top (see `raiseStopMarkersToTop`).
  const stopMarkersRef = useRef<Record<string, Marker>>({});

  // The map viewport (center + zoom) is mirrored to localStorage and
  // restored on reload. `initialMapViewRef` freezes the value restored on
  // mount - the constructor needs it once; later changes go through `moveend`.
  const [mapView, setMapView] = usePersistentState<MapViewState | null>(
    "map-view",
    null,
  );
  const initialMapViewRef = useRef<MapViewState>(
    mapView && isValidMapViewState(mapView) ? mapView : DEFAULT_MAP_VIEW,
  );

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = new Map({
      container: mapContainerRef.current,
      style: basemapStyle,
      center: initialMapViewRef.current.center,
      zoom: initialMapViewRef.current.zoom,
    });
    mapRef.current = map;
    map.on("load", () => setIsMapLoaded(true));
    map.on("moveend", () => {
      const center = map.getCenter();
      setMapView({ center: [center.lng, center.lat], zoom: map.getZoom() });
    });

    return () => {
      mapRef.current = null;
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `setMapView` has a stable identity; this effect must only run on mount (see `initialMapViewRef`).
  }, []);

  const routeHoverInfo = useRouteLayer(mapRef, isMapLoaded, simulationResult);
  // Order matters: construction markers are added before charging-stop
  // markers so the latter paint on top (DOM order).
  useConstructionZoneMarkers(
    mapRef,
    isMapLoaded,
    simulationResult?.constructionZones,
    stopMarkersRef,
  );
  useChargingStopMarkers(
    mapRef,
    isMapLoaded,
    simulationResult?.chargingStops,
    stopMarkersRef,
  );
  useStopMarkers(
    mapRef,
    isMapLoaded,
    stops,
    simulationResult?.waypointStops,
    onStopMove,
    stopMarkersRef,
  );
  const supercharger = useSuperchargerLayer(
    mapRef,
    isMapLoaded,
    superchargerVisible,
  );

  // Picking mode: a map click sets the position for `pickingStopId`. The
  // handler is only registered while `pickingStopId` is set.
  useEffect(() => {
    const map = mapRef.current;
    if (!isMapLoaded || !map) return;
    const canvas = map.getCanvas();

    if (!pickingStopId) {
      canvas.style.cursor = "";
      return;
    }
    canvas.style.cursor = "crosshair";
    const onClick = (e: { lngLat: { lat: number; lng: number } }) => {
      // [lat, lon] - project-wide convention
      onPickPosition?.(pickingStopId, [e.lngLat.lat, e.lngLat.lng]);
    };
    map.on("click", onClick);

    return () => {
      map.off("click", onClick);
      canvas.style.cursor = "";
    };
  }, [pickingStopId, isMapLoaded, onPickPosition]);

  return (
    <div
      className="map-container"
      ref={mapContainerRef}
      style={{ width: "100%", height: "100%" }}
    >
      {/* Supercharger toggle */}
      <button
        onClick={onToggleSuperchargers}
        style={{
          position: "absolute",
          top: "1rem",
          left: "1rem",
          zIndex: 10,
          padding: "8px 14px",
          fontSize: "13px",
          fontWeight: 600,
          border: "none",
          borderRadius: "6px",
          cursor: "pointer",
          background: superchargerVisible ? "#2563eb" : "#ffffff",
          color: superchargerVisible ? "#ffffff" : "#333333",
          boxShadow: "0 1px 6px rgba(0,0,0,0.18)",
          display: "flex",
          alignItems: "center",
          gap: "6px",
        }}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill={superchargerVisible ? "#fff" : "#2563eb"}
        >
          <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
        </svg>
        Supercharger
      </button>

      {/* Loading indicator */}
      {supercharger.loading && (
        <div
          style={{
            position: "absolute",
            top: "1rem",
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10,
            padding: "6px 16px",
            fontSize: "13px",
            borderRadius: "6px",
            background: "rgba(0,0,0,0.7)",
            color: "#fff",
          }}
        >
          Lade Supercharger…
        </div>
      )}

      {/* Error indicator */}
      {supercharger.error && (
        <div
          style={{
            position: "absolute",
            bottom: "1rem",
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10,
            padding: "6px 16px",
            fontSize: "13px",
            borderRadius: "6px",
            background: "#ef4444",
            color: "#fff",
          }}
        >
          {supercharger.error}
        </div>
      )}

      {/* Route hover tooltip: time, SoC, speed and (if considered) weather at
          the nearest route point - multi-line (see `buildRouteHoverText`) so
          it grows vertically rather than horizontally. */}
      {routeHoverInfo && (
        <div
          style={{
            position: "absolute",
            left: routeHoverInfo.x + 14,
            top: routeHoverInfo.y + 14,
            zIndex: 10,
            padding: "4px 8px",
            fontSize: "12px",
            lineHeight: 1.4,
            borderRadius: "4px",
            background: "rgba(0,0,0,0.75)",
            color: "#fff",
            pointerEvents: "none",
            whiteSpace: "pre-line",
          }}
        >
          {buildRouteHoverText(routeHoverInfo.sample)}
        </div>
      )}
    </div>
  );
}

export default MapVisualization;
