import { useEffect, type RefObject } from "react";
import { Marker, Popup } from "maplibre-gl";
import { toLngLat } from "../../../utils/geo-utils";
import type { WaypointStop } from "../../../types";
import type { Stop } from "../../../types/trip-request";
import { buildMarkerElement, stopRole } from "../markers";
import { buildStopPopupHtml, findWaypointStopAt } from "../popups";
import { raiseStopMarkersToTop, type MapRef } from "./map-ref";

/** Draggable start/waypoint/destination markers, kept in sync with `stops`.
 *  Existing markers are moved with `setLngLat` instead of being recreated, to
 *  avoid flicker and losing drag state. Stops without a position (not yet
 *  geocoded) are skipped. Popups are rebuilt on every run so a new simulation
 *  result updates the stay details (see `buildStopPopupHtml`). */
export function useStopMarkers(
  mapRef: MapRef,
  isMapLoaded: boolean,
  stops: Stop[] | undefined,
  waypointStops: WaypointStop[] | undefined,
  onStopMove:
    ((stopId: string, position: [number, number]) => void) | undefined,
  markersRef: RefObject<Record<string, Marker>>,
): void {
  useEffect(() => {
    const map = mapRef.current;
    if (!isMapLoaded || !map) return;
    const markers = markersRef.current;
    const stopList = stops ?? [];
    const seen = new Set<string>();

    for (let i = 0; i < stopList.length; i++) {
      const stop = stopList[i];
      if (!stop.position) continue;

      seen.add(stop.id);
      const role = stopRole(i, stopList.length);
      const waypointStop = findWaypointStopAt(
        waypointStops ?? [],
        stop.position,
      );
      const popup = new Popup({ offset: 14 }).setHTML(
        buildStopPopupHtml(stop, role, waypointStop),
      );
      const existing = markers[stop.id];
      if (existing) {
        existing.setLngLat(toLngLat(stop.position));
        existing.setPopup(popup);
      } else {
        const marker = new Marker({
          element: buildMarkerElement(role),
          draggable: true,
        });
        marker.setLngLat(toLngLat(stop.position));
        marker.setPopup(popup);
        marker.on("dragend", () => {
          const ll = marker.getLngLat();
          // [lat, lon] - project-wide convention (see geo-utils.ts)
          onStopMove?.(stop.id, [ll.lat, ll.lng]);
        });
        marker.addTo(map);
        markers[stop.id] = marker;
      }
    }

    for (const id of Object.keys(markers)) {
      if (!seen.has(id)) {
        markers[id].remove();
        delete markers[id];
      }
    }
    raiseStopMarkersToTop(markersRef);

    return () => {
      for (const id of Object.keys(markers)) {
        markers[id].remove();
        delete markers[id];
      }
    };
  }, [mapRef, isMapLoaded, stops, waypointStops, onStopMove, markersRef]);
}
