import { useEffect, useRef, useState } from "react";
import { Map, LngLatBounds, LayerSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { toLngLat } from "../utils/geo-utils";
import { TripSimulationResult } from "../types";

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

interface MapProps {
  simulationResult: TripSimulationResult;
}

/** Map-Komponente für die Visualisierung der Simulationsergebnisse.
 *
 * Darstellung:
 * - Route als LineString mit Farbsegmenten basierend auf SoC
 * - Marker für Ladehalte und Zwischenstopps
 */
export function MapVisualization({ simulationResult }: MapProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const [isMapLoaded, setIsMapLoaded] = useState(false);

  useEffect(() => {
    // Map initialisieren
    if (!mapContainerRef.current || mapRef.current) return;

    mapRef.current = new Map({
      container: mapContainerRef.current,
      style:
        "https://api.protomaps.com/styles/v2/basic.json?key=1956787d7b8a15d5",
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

    // Existing source? Remove it to avoid conflicts
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

  return (
    <div
      className="map-container"
      ref={mapContainerRef}
      style={{ width: "100%", height: "100%" }}
    />
  );
}

export default MapVisualization;
