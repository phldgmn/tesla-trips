import { useEffect, useRef, useState } from "react";
import {
  Map,
  Marker,
  Popup,
  LngLatBounds,
  LayerSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { toLngLat } from "../utils/geo-utils";
import { TripSimulationResult } from "../types";
import type { Stop } from "../types/trip-request";

// Farbpalette für SoC-Verlauf: rot → orange → gelb → grün
export function socToColor(soc: number): string {
  if (soc <= 20) return "#ef4444";
  if (soc <= 40) return "#f97316";
  if (soc <= 60) return "#eab308";
  if (soc <= 80) return "#84cc16";
  return "#22c55e";
}

// Berechnet mittleren SoC für ein Segment (basierend auf Start- und End-SoC)
export function segmentAvgSoc(startSoc: number, endSoc: number): number {
  return (startSoc + endSoc) / 2;
}

/** Rolle eines Stopps, abgeleitet aus seiner Position im Array. */
export type StopRole = "start" | "end" | "middle";

/** Bestimmt die Rolle eines Stopps anhand seines Index und der Gesamtzahl. */
export function stopRole(index: number, total: number): StopRole {
  if (index === 0) return "start";
  if (index === total - 1) return "end";
  return "middle";
}

/** Liefert die Markerfarbe (CSS-Hex) für eine StopRole. */
export function roleToMarkerColor(role: StopRole): string {
  switch (role) {
    case "start":
      return "#22c55e"; // grün
    case "end":
      return "#ef4444"; // rot
    case "middle":
      return "#3b82f6"; // blau
  }
}

/** Anzeige-Label für einen Stopp in Popup/Header (Rolle als Fallback). */
export function roleToLabel(role: StopRole): string {
  switch (role) {
    case "start":
      return "Start";
    case "end":
      return "Ziel";
    case "middle":
      return "Zwischenstopp";
  }
}

/** Kürzel für die Markerdarstellung: A (Start), B (Ziel), Punkt sonst. */
export function roleToMarkerGlyph(role: StopRole): string {
  switch (role) {
    case "start":
      return "A";
    case "end":
      return "B";
    case "middle":
      return "●";
  }
}

/** Erzeugt ein gestyltes DOM-Element für einen Stopp-Marker. */
export function buildMarkerElement(role: StopRole): HTMLElement {
  const el = document.createElement("div");
  const color = roleToMarkerColor(role);
  el.textContent = roleToMarkerGlyph(role);
  const isMiddle = role === "middle";
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:28px",
    "height:28px",
    "border-radius:50%",
    `background-color:${color}`,
    "border:2px solid #ffffff",
    "color:#ffffff",
    "font-weight:700",
    "font-size:14px",
    "line-height:1",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:grab",
  ]
    .concat(isMiddle ? [] : ["font-family:system-ui,sans-serif"])
    .join(";");
  return el;
}

/** Popup-Text für einen Stopp: Adresse falls vorhanden, sonst Rolle + gerundete Koordinaten. */
export function buildPopupText(stop: Stop, role: StopRole): string {
  if (stop.address) return stop.address;
  if (!stop.position) return roleToLabel(role);
  const [lat, lng] = stop.position;
  const r = (n: number) => Math.round(n * 1_000_000) / 1_000_000;
  return `${roleToLabel(role)} (${r(lat)}, ${r(lng)})`;
}

interface MapProps {
  /** Simulationsergebnis (optional – entfällt im Planungsmodus). */
  simulationResult?: TripSimulationResult;
  /** Stopp-Liste (Start/Zwischenstopps/Ziel). Rolle ergibt sich aus dem Index. */
  stops?: Stop[];
  /** Wenn gesetzt: Kartenklick wählt Position für diesen Stopp (statt neuen Marker). */
  pickingStopId?: string | null;
  /** Callback bei Kartenklick während pickingStopId aktiv ist. */
  onPickPosition?: (stopId: string, position: [number, number]) => void;
  /** Callback beim Verschieben eines Stopp-Markers (dragend). */
  onStopMove?: (stopId: string, position: [number, number]) => void;
}

/** Map-Komponente für die Visualisierung der Simulationsergebnisse.
 *
 * Darstellung:
 * - Route als LineString mit Farbsegmenten basierend auf SoC (nur wenn
 *   `simulationResult` vorhanden)
 * - Marker für Ladehalte und Zwischenstopps (nur aus Simulationsergebnis)
 * - Bearbeitbare Stopp-Marker (Start/Zwischenstopp/Ziel) aus `stops` –
 *   draggable, mit Popup und dragend-Callback. Stopps ohne Position
 *   werden übersprungen.
 * - Kartenklick im Auswahlmodus (`pickingStopId`) liefert Koordinate zurück
 */
export function MapVisualization({
  simulationResult,
  stops: stopsProp,
  pickingStopId,
  onPickPosition,
  onStopMove,
}: MapProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const [isMapLoaded, setIsMapLoaded] = useState(false);

  // Verwaltung der Stopp-Marker, keyed by stop.id
  // Record (kein typeof Map, um Konflikte mit MapLibre zu vermeiden)
  const markersRef = useRef<Record<string, Marker>>({});
  // Anzahl der zuletzt hinzugefügten Routensegmente (für Cleanup bei Wechsel
  // in den Planungsmodus ohne simulationResult)
  const segmentCountRef = useRef<number>(0);

  useEffect(() => {
    // Map initialisieren
    if (!mapContainerRef.current || mapRef.current) return;

    mapRef.current = new Map({
      container: mapContainerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [0, 0],
      zoom: 2,
    });

    mapRef.current.on("load", () => {
      setIsMapLoaded(true);
    });

    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;

    // Wenn kein Simulationsergebnis vorhanden ist: bisherige Routen-/Marker-
    // Quellen und Layer entfernen, damit der Planungsmodus sauber ist.
    if (!simulationResult) {
      // Segment-Layer und -Quellen entfernen ( IDs: route-segment-i )
      const segmentCount = segmentCountRef.current;
      for (let i = 0; i < segmentCount; i++) {
        const layerId = `route-segment-${i}`;
        if (map.getLayer(layerId)) map.removeLayer(layerId);
        if (map.getSource(layerId)) map.removeSource(layerId);
      }
      segmentCountRef.current = 0;
      // Haupt-Route
      if (map.getLayer("route")) map.removeLayer("route");
      if (map.getSource("route")) map.removeSource("route");
      // Ladehalte
      if (map.getLayer("charger-markers")) map.removeLayer("charger-markers");
      if (map.getSource("chargers")) map.removeSource("chargers");
      // Zwischenstopps
      if (map.getLayer("waypoint-markers")) map.removeLayer("waypoint-markers");
      if (map.getSource("waypoints")) map.removeSource("waypoints");
      return;
    }

    // Route in GeoJSON konvertieren (Koordinaten: (lat, lon) → [lng, lat])
    const routeCoordinates = simulationResult.frames.map((frame) =>
      toLngLat(frame.position),
    );

    const routeGeoJson: GeoJSON.Feature<GeoJSON.LineString> = {
      type: "Feature" as const,
      properties: {},
      geometry: {
        type: "LineString" as const,
        coordinates: routeCoordinates,
      },
    };

    // Bestehende Quellen entfernen, um Konflikte zu vermeiden
    if (map.getSource("route")) {
      map.removeSource("route");
    }
    if (map.getSource("chargers")) {
      map.removeSource("chargers");
    }
    if (map.getSource("waypoints")) {
      map.removeSource("waypoints");
    }

    // Route als GeoJSON Source hinzufügen
    map.addSource("route", {
      type: "geojson" as const,
      data: routeGeoJson,
    });

    // Route-Linie mit segmentierten Farben (kein echter Gradient, sondern viele einzelne Linien)
    const frames = simulationResult.frames;
    const numSegments = Math.max(10, frames.length - 1);

    for (let i = 0; i < numSegments; i++) {
      const startIdx = Math.floor((i / numSegments) * frames.length);
      const endIdx = Math.floor(((i + 1) / numSegments) * frames.length);
      const startFrame = frames[startIdx];
      const endFrame = frames[Math.min(endIdx, frames.length - 1)];

      // Segment-GeoJSON
      const segmentGeoJson: GeoJSON.Feature<GeoJSON.LineString> = {
        type: "Feature" as const,
        properties: {},
        geometry: {
          type: "LineString" as const,
          coordinates: [
            toLngLat(startFrame.position),
            toLngLat(endFrame.position),
          ],
        },
      };

      const avgSoc = segmentAvgSoc(startFrame.soc_pct, endFrame.soc_pct);
      const color = socToColor(avgSoc);

      map.addSource(`route-segment-${i}`, {
        type: "geojson" as const,
        data: segmentGeoJson,
      });

      map.addLayer({
        id: `route-segment-${i}`,
        type: "line" as const,
        source: `route-segment-${i}`,
        paint: {
          "line-color": color,
          "line-width": 4,
          "line-opacity": 0.9,
        } satisfies LayerSpecification["paint"],
      } satisfies LayerSpecification);
    }
    // Für das Cleanup beim Wechsel in den Planungsmodus merken
    segmentCountRef.current = numSegments;

    // Ladehalte-Marker hinzufügen (aus SimulationResult)
    // Wir simulieren hier Ladehalte, indem wir Frames mit Zustand 'LADEN' nutzen
    const chargerStops = simulationResult.frames.filter(
      (f) => f.zustand === "LADEN",
    );

    // Entferne alte Marker-Source
    if (map.getSource("chargers")) {
      map.removeSource("chargers");
    }

    if (chargerStops.length > 0) {
      const chargerCoords = chargerStops.map((f) => toLngLat(f.position));

      const chargersGeoJson: GeoJSON.FeatureCollection<GeoJSON.Point> = {
        type: "FeatureCollection" as const,
        features: chargerCoords.map((coord) => ({
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: coord,
          },
          properties: {
            type: "charger" as const,
          },
        })),
      };

      map.addSource("chargers", {
        type: "geojson" as const,
        data: chargersGeoJson,
      });

      map.addLayer({
        id: "charger-markers",
        type: "circle" as const,
        source: "chargers",
        paint: {
          "circle-color": "#f59e0b",
          "circle-radius": 8,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        } satisfies LayerSpecification["paint"],
      } satisfies LayerSpecification);
    }

    // Zwischenstopps-Marker hinzufügen (Frames mit Zustand 'PAUSE')
    const waypointStops = simulationResult.frames.filter(
      (f) => f.zustand === "PAUSE",
    );

    if (waypointStops.length > 0) {
      const waypointCoords = waypointStops.map((f) => toLngLat(f.position));

      const waypointsGeoJson: GeoJSON.FeatureCollection<GeoJSON.Point> = {
        type: "FeatureCollection" as const,
        features: waypointCoords.map((coord) => ({
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: coord,
          },
          properties: {
            type: "waypoint" as const,
          },
        })),
      };

      if (map.getSource("waypoints")) {
        map.removeSource("waypoints");
      }

      map.addSource("waypoints", {
        type: "geojson" as const,
        data: waypointsGeoJson,
      });

      map.addLayer({
        id: "waypoint-markers",
        type: "circle" as const,
        source: "waypoints",
        paint: {
          "circle-color": "#3b82f6",
          "circle-radius": 6,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        } satisfies LayerSpecification["paint"],
      } satisfies LayerSpecification);
    }

    // Kamera auf Route zentrieren
    const bounds = routeCoordinates.reduce(
      (bounds, coord) => bounds.extend(coord),
      new LngLatBounds(),
    );
    map.fitBounds(bounds, { padding: 50 });
  }, [simulationResult, isMapLoaded]);

  // Stopp-Marker verwalten: erstellen/aktualisieren/entfernen passend zu
  // `stops`. Bestehende Marker werden per setLngLat verschoben statt
  // neu erzeugt, um Flicker und Drag-State-Verlust zu vermeiden.
  // Stopps ohne position werden übersprungen (noch nicht geocoded).
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const markers = markersRef.current;
    const stops = stopsProp ?? [];
    const seen = new Set<string>();

    // Nur Stopps mit aufgelöster Position rendern.

    for (let i = 0; i < stops.length; i++) {
      const stop = stops[i];
      if (!stop.position) continue;

      seen.add(stop.id);
      const role = stopRole(i, stops.length);
      const existing = markers[stop.id];
      if (existing) {
        // Nur Position aktualisieren – Marker-Instanz und Drag-State bleiben
        existing.setLngLat(toLngLat(stop.position));
      } else {
        // Neuen Marker erzeugen
        const element = buildMarkerElement(role);
        const marker = new Marker({ element, draggable: true });
        marker.setLngLat(toLngLat(stop.position));
        const popupText = buildPopupText(stop, role);
        marker.setPopup(new Popup({ offset: 14 }).setHTML(popupText));
        marker.on("dragend", () => {
          const ll = marker.getLngLat();
          // [lat, lon] – projektweite Konvention (siehe geo-utils.ts)
          onStopMove?.(stop.id, [ll.lat, ll.lng]);
        });
        marker.addTo(map);
        markers[stop.id] = marker;
      }
    }

    // Marker entfernen, deren ID nicht mehr in stops enthalten ist
    for (const id of Object.keys(markers)) {
      if (!seen.has(id)) {
        markers[id].remove();
        delete markers[id];
      }
    }

    return () => {
      // Beim Unmount alle Marker entfernen
      for (const id of Object.keys(markers)) {
        markers[id].remove();
        delete markers[id];
      }
    };
  }, [stopsProp, isMapLoaded, onStopMove]);

  // Auswahlmodus: Kartenklick setzt Position für pickingStopId.
  // Handler wird nur registriert, wenn pickingStopId nicht-null ist.
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const canvas = map.getCanvas();

    if (!pickingStopId) {
      // Kein Auswahlmodus – Cursor zurücksetzen, kein Click-Handler aktiv
      canvas.style.cursor = "";
      return;
    }

    // Cursor als Fadenkreuz signalisieren Auswahl
    canvas.style.cursor = "crosshair";

    const onClick = (e: { lngLat: { lat: number; lng: number } }) => {
      // [lat, lon] – projektweite Konvention
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
    />
  );
}

export default MapVisualization;
