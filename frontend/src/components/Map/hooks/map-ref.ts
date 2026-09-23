import type { RefObject } from "react";
import type { Map, Marker } from "maplibre-gl";

/** Ref to the MapLibre instance owned by `MapVisualization`. */
export type MapRef = RefObject<Map | null>;

/** True while `map` is still the live instance behind `mapRef`. Layer cleanups
 *  use this to skip work after the map itself was removed on unmount (calling
 *  `getLayer`/`removeSource` on a removed map throws). */
export function isLiveMap(mapRef: MapRef, map: Map): boolean {
  return mapRef.current === map;
}

/** Moves every stop marker element to the end of its DOM parent so stop
 *  markers always paint above construction-zone and charging-stop markers.
 *  This relies purely on DOM order (later = on top), NOT on inline
 *  `position`/`z-index` on the marker element, which fights MapLibre's own
 *  transform-based positioning and made markers drift while zooming.
 *  `appendChild` on an attached node only moves it, so the transform MapLibre
 *  manages on the element is untouched.
 *
 *  Called by every marker hook after it adds markers, so the order is right
 *  no matter which hook ran last. */
export function raiseStopMarkersToTop(
  stopMarkersRef: RefObject<Record<string, Marker>>,
): void {
  for (const marker of Object.values(stopMarkersRef.current)) {
    const el = marker.getElement();
    el.parentNode?.appendChild(el);
  }
}
