import { useEffect, type RefObject } from "react";
import { Marker, Popup } from "maplibre-gl";
import { toLngLat } from "../../../utils/geo-utils";
import { refreshSuperchargerPricing } from "../../../api/chargingApi";
import type { ChargingStop } from "../../../types";
import { buildChargingStopMarkerElement } from "../markers";
import { buildChargingStopPopupElement } from "../popups";
import type { PricingRefreshState } from "../superchargers";
import { raiseStopMarkersToTop, type MapRef } from "./map-ref";

/** Re-fetches a charging stop's pricing from Tesla and re-renders its popup.
 *  Only updates this popup, not the trip totals - those need a new route
 *  computation (see `buildChargingStopPopupElement`). */
async function refreshChargingStopPricing(
  stop: ChargingStop,
  render: (state: PricingRefreshState) => void,
) {
  render({ status: "loading" });
  try {
    const pricing = await refreshSuperchargerPricing(stop.stationId);
    render({ status: "loaded", pricing });
  } catch (err: unknown) {
    render({
      status: "error",
      message: err instanceof Error ? err.message : "Fehler",
    });
  }
}

/** One marker per actual charging stop (not per charging frame), placed at
 *  the station, with a click popup. Without a known price the popup also
 *  offers a pricing refresh button. */
export function useChargingStopMarkers(
  mapRef: MapRef,
  isMapLoaded: boolean,
  chargingStops: ChargingStop[] | undefined,
  stopMarkersRef: RefObject<Record<string, Marker>>,
): void {
  useEffect(() => {
    const map = mapRef.current;
    if (!isMapLoaded || !map || !chargingStops) return;

    const markers = chargingStops.map((stop) => {
      const popup = new Popup({ offset: 14 });
      const render = (state: PricingRefreshState) => {
        popup.setDOMContent(
          buildChargingStopPopupElement(stop, state, () =>
            refreshChargingStopPricing(stop, render),
          ),
        );
      };
      render({ status: "idle" });
      return new Marker({ element: buildChargingStopMarkerElement() })
        .setLngLat(toLngLat(stop.position))
        .setPopup(popup)
        .addTo(map);
    });
    raiseStopMarkersToTop(stopMarkersRef);

    return () => {
      for (const marker of markers) marker.remove();
    };
  }, [mapRef, isMapLoaded, chargingStops, stopMarkersRef]);
}
