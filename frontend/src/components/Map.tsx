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
import {
  fetchSuperchargers,
  refreshSupercharger,
  type SuperchargerStation,
} from "../api/chargingApi";

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

/** Erzeugt ein gestyltes DOM-Element fuer einen Supercharger-Marker (Blitz-Symbol). */
export function buildSuperchargerMarkerElement(): HTMLElement {
  const el = document.createElement("div");
  el.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="#ffffff" style="pointer-events:none;">
    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
  </svg>`;
  el.style.cssText = [
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "width:28px",
    "height:28px",
    "border-radius:50%",
    "background-color:#2563eb",
    "border:2px solid #ffffff",
    "box-shadow:0 1px 4px rgba(0,0,0,0.35)",
    "cursor:pointer",
  ].join(";");
  return el;
}

/** Baut ein DOM-Element fuer ein Supercharger-Popover mit Refresh-Button. */
export function buildSuperchargerPopoverElement(
  station: SuperchargerStation,
  isRefreshing: boolean,
  onRefresh: () => void,
): HTMLElement {
  const statusColor =
    station.status === "OPEN"
      ? "#22c55e"
      : station.status === "TEMP_CLOSED"
        ? "#ef4444"
        : "#f59e0b";

  const container = document.createElement("div");
  container.style.cssText =
    "font-family:system-ui,sans-serif;font-size:13px;min-width:200px;";

  const title = document.createElement("strong");
  title.style.cssText = "font-size:14px;";
  title.textContent = station.name;
  container.appendChild(title);

  const statusRow = document.createElement("div");
  statusRow.style.cssText =
    "margin:6px 0 4px;display:flex;gap:6px;align-items:center;";
  const dot = document.createElement("span");
  dot.style.cssText = `display:inline-block;width:8px;height:8px;border-radius:50%;background:${statusColor};`;
  statusRow.appendChild(dot);
  const statusText = document.createElement("span");
  statusText.textContent = station.status;
  statusRow.appendChild(statusText);
  container.appendChild(statusRow);

  const table = document.createElement("table");
  table.style.cssText = "width:100%;border-collapse:collapse;";
  const rows: [string, string][] = [
    [
      "Stalls",
      `${station.total_stalls} (V2:${station.stalls_v2} V3:${station.stalls_v3} V4:${station.stalls_v4})`,
    ],
    ["Leistung", `${station.power_kilowatt} kW`],
    ["24/7", station.ist_24_7 ? "Ja" : "Nein"],
    ["Eroeffnet", station.date_opened || "Unbekannt"],
  ];
  for (const [label, value] of rows) {
    const tr = document.createElement("tr");
    const tdLabel = document.createElement("td");
    tdLabel.style.cssText = "padding:2px 4px;color:#666;";
    tdLabel.textContent = label;
    tr.appendChild(tdLabel);
    const tdValue = document.createElement("td");
    tdValue.style.cssText = "padding:2px 4px;text-align:right;";
    tdValue.textContent = value;
    tr.appendChild(tdValue);
    table.appendChild(tr);
  }
  container.appendChild(table);

  const btnRow = document.createElement("div");
  btnRow.style.cssText = "margin-top:6px;";
  if (isRefreshing) {
    const spinner = document.createElement("span");
    spinner.style.cssText = "color:#666;font-style:italic;";
    spinner.textContent = "Aktualisiere…";
    btnRow.appendChild(spinner);
  } else {
    const btn = document.createElement("button");
    btn.style.cssText =
      "padding:4px 12px;background:#2563eb;color:white;border:none;border-radius:4px;cursor:pointer;font-size:12px;";
    btn.textContent = "\u{1F504} Von Tesla aktualisieren";
    btn.onclick = onRefresh;
    btnRow.appendChild(btn);
  }
  container.appendChild(btnRow);

  return container;
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
  /** Ob die Supercharger-Overlay auf der Karte sichtbar ist. */
  superchargerVisible?: boolean;
  /** Callback zum Umschalten der Supercharger-Sichtbarkeit. */
  onToggleSuperchargers?: () => void;
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
 * - Supercharger-Overlay mit Ein-/Ausblend-Toggle, Klick → Popover,
 *   Refresh-Button (Browser ruft Tesla-API direkt auf)
 */
export function MapVisualization({
  simulationResult,
  stops: stopsProp,
  pickingStopId,
  onPickPosition,
  onStopMove,
  superchargerVisible,
  onToggleSuperchargers,
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

  // Supercharger-Overlay
  const [superchargerStations, setSuperchargerStations] = useState<
    SuperchargerStation[]
  >([]);
  const [superchargerLoading, setSuperchargerLoading] = useState(false);
  const [superchargerError, setSuperchargerError] = useState<string | null>(
    null,
  );
  const [refreshingSlug, setRefreshingSlug] = useState<string | null>(null);
  const superchargerMarkersRef = useRef<Record<string, Marker>>({});
  const activePopoverRef = useRef<Popup | null>(null);

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

  /** Entfernt alle Routen- und Marker-Sources/Layer aus der Karte. */
  function clearSimulationLayers(map: Map) {
    // Segment-Layer und -Quellen entfernen
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
  }

  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;

    // IMMER zuerst alte Layer/Sources entfernen – egal ob wir in den
    // Planungsmodus wechseln (simulationResult = null) oder ein neues
    // Simulationsergebnis laden.
    clearSimulationLayers(map);

    // Wenn kein Simulationsergebnis vorhanden ist: hier aufhören.
    if (!simulationResult) {
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

    // Route als GeoJSON Source + Hauptlinie hinzufügen
    map.addSource("route", {
      type: "geojson" as const,
      data: routeGeoJson,
    });
    map.addLayer({
      id: "route",
      type: "line" as const,
      source: "route",
      paint: {
        "line-color": "#3b82f6",
        "line-width": 3,
        "line-opacity": 0.6,
      } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);

    // Route-Linie mit segmentierten SoC-Farben (viele kleine Linienabschnitte)
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
    // Für das nächste Cleanup merken
    segmentCountRef.current = numSegments;

    // Ladehalte-Marker hinzufügen (aus SimulationResult)
    const chargerStops = simulationResult.frames.filter(
      (f) => f.zustand === "LADEN",
    );

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

    // Kamera auf gesamte Route zentrieren
    const bounds = routeCoordinates.reduce(
      (b, coord) => b.extend(coord),
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

  /** Helfer: Supercharger von Tesla aktualisieren und Popover neu bauen. */
  async function handleSuperchargerRefresh(
    station: SuperchargerStation,
    popup: Popup,
  ) {
    const slug = station.slug;
    setRefreshingSlug(slug);

    try {
      const updated = await refreshSupercharger(slug);

      // Station in der Liste aktualisieren
      setSuperchargerStations((prev) =>
        prev.map((s) => (s.slug === slug ? updated : s)),
      );

      // Popover-Inhalt mit aktualisierten Daten neu bauen
      const popupEl = buildSuperchargerPopoverElement(updated, false, () =>
        handleSuperchargerRefresh(updated, popup),
      );
      popup.setDOMContent(popupEl);
    } catch (err: unknown) {
      // Fehler im Popover anzeigen, letzten bekannten Stand beibehalten
      const msg = err instanceof Error ? err.message : "Fehler";
      const popupEl = buildSuperchargerPopoverElement(station, false, () =>
        handleSuperchargerRefresh(station, popup),
      );
      const errorBanner = document.createElement("div");
      errorBanner.style.cssText =
        "color:#ef4444;font-size:12px;margin-top:4px;";
      errorBanner.textContent = `Fehler: ${msg}`;
      popupEl.appendChild(errorBanner);
      popup.setDOMContent(popupEl);
    } finally {
      setRefreshingSlug(null);
    }
  }

  /** Helfer: Rendert Supercharger-Marker auf der Karte. */
  function renderSuperchargerMarkers(
    map: Map,
    markers: Record<string, Marker>,
    stations: SuperchargerStation[],
  ) {
    const seen = new Set<string>();

    for (const station of stations) {
      const id = station.slug;
      seen.add(id);

      const existing = markers[id];
      if (existing) {
        existing.setLngLat([station.longitude, station.latitude]);
      } else {
        const element = buildSuperchargerMarkerElement();
        const marker = new Marker({ element })
          .setLngLat([station.longitude, station.latitude])
          .addTo(map);

        // Popover beim Klick: Details + Refresh-Button
        // Hinweis: maplibre-gl's Marker feuert selbst nie ein "click"-Event
        // (nur dragstart/drag/dragend) – daher Listener direkt am DOM-Element.
        element.addEventListener("click", (e) => {
          e.stopPropagation();
          // Vorheriges Popover schliessen
          activePopoverRef.current?.remove();

          const popup = new Popup({ offset: 14, closeButton: true });
          const popupEl = buildSuperchargerPopoverElement(
            station,
            refreshingSlug === station.slug,
            () => handleSuperchargerRefresh(station, popup),
          );
          popup.setDOMContent(popupEl);
          popup.setLngLat([station.longitude, station.latitude]);
          popup.addTo(map);
          activePopoverRef.current = popup;
        });

        markers[id] = marker;
      }
    }

    // Marker entfernen, die nicht mehr in der Liste sind
    for (const id of Object.keys(markers)) {
      if (!seen.has(id)) {
        markers[id].remove();
        delete markers[id];
      }
    }
  }

  // Supercharger-Overlay: Daten laden und Marker rendern
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const markers = superchargerMarkersRef.current;

    if (!superchargerVisible) {
      // Alle Supercharger-Marker entfernen
      for (const id of Object.keys(markers)) {
        markers[id].remove();
        delete markers[id];
      }
      // Overlay wird ausgeblendet: Marker-Aufräumen (externes System) und
      // lokaler State müssen atomar im selben Effect-Lauf passieren, sonst
      // zeigen Popover/Ladeindikator kurz veraltete Stationsdaten.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSuperchargerStations([]);
      setSuperchargerError(null);
      return;
    }

    if (superchargerStations.length > 0) {
      // Marker rendern (bereits geladen)
      renderSuperchargerMarkers(map, markers, superchargerStations);
      return;
    }

    if (superchargerLoading) return;

    // Daten laden
    setSuperchargerLoading(true);
    setSuperchargerError(null);
    fetchSuperchargers()
      .then((stations) => {
        setSuperchargerStations(stations);
        setSuperchargerLoading(false);
        renderSuperchargerMarkers(map, markers, stations);
      })
      .catch((err: unknown) => {
        setSuperchargerLoading(false);
        const msg = err instanceof Error ? err.message : "Unbekannter Fehler";
        setSuperchargerError(msg);
      });
    // Wir muessen superchargerStations.length nicht in dependencies,
    // da wir den state explizit setzen
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [superchargerVisible, isMapLoaded]);

  return (
    <div
      className="map-container"
      ref={mapContainerRef}
      style={{ width: "100%", height: "100%" }}
    >
      {/* Supercharger-Toggle-Button */}
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

      {/* Lade-Indikator */}
      {superchargerLoading && (
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

      {/* Fehler-Indikator */}
      {superchargerError && (
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
          {superchargerError}
        </div>
      )}
    </div>
  );
}

export default MapVisualization;
