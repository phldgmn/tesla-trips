import { useEffect, type RefObject } from "react";
import { Marker, Popup } from "maplibre-gl";
import { toLngLat } from "../../../utils/geo-utils";
import type { ConstructionZone } from "../../../types";
import { buildConstructionZoneMarkerElement } from "../markers";
import { buildConstructionZonePopupHtml } from "../popups";
import { isConstructionZoneVisibleAtZoom } from "../construction-zone-visibility";
import { raiseStopMarkersToTop, type MapRef } from "./map-ref";

/** One marker per construction zone, with a click popup. Markers are shown or
 *  hidden per zoom level depending on the zone length (see
 *  `isConstructionZoneVisibleAtZoom`); they stay in the DOM, only their
 *  `display` toggles. */
export function useConstructionZoneMarkers(
  mapRef: MapRef,
  isMapLoaded: boolean,
  zones: ConstructionZone[] | undefined,
  stopMarkersRef: RefObject<Record<string, Marker>>,
): void {
  useEffect(() => {
    const map = mapRef.current;
    if (!isMapLoaded || !map || !zones) return;

    const entries = zones.map((zone) => ({
      lengthM: zone.lengthM,
      marker: new Marker({ element: buildConstructionZoneMarkerElement() })
        .setLngLat(toLngLat(zone.position))
        .setPopup(
          new Popup({ offset: 12 }).setHTML(
            buildConstructionZonePopupHtml(zone),
          ),
        )
        .addTo(map),
    }));

    const updateVisibility = () => {
      const zoom = map.getZoom();
      for (const { marker, lengthM } of entries) {
        // "flex" rather than "": an empty string drops the inline
        // `display:flex` from the element's `cssText`, which falls back to
        // `display:block` and left-aligns the SVG icon.
        marker.getElement().style.display = isConstructionZoneVisibleAtZoom(
          lengthM,
          zoom,
        )
          ? "flex"
          : "none";
      }
    };
    // Set visibility for the current zoom right away; "zoom" (not "zoomend")
    // gives immediate feedback while zooming.
    updateVisibility();
    map.on("zoom", updateVisibility);
    raiseStopMarkersToTop(stopMarkersRef);

    return () => {
      map.off("zoom", updateVisibility);
      for (const { marker } of entries) marker.remove();
    };
  }, [mapRef, isMapLoaded, zones, stopMarkersRef]);
}
