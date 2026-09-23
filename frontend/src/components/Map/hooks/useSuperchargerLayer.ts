import { useEffect, useRef, useState } from "react";
import {
  Popup,
  type GeoJSONSource,
  type LayerSpecification,
  type Map,
} from "maplibre-gl";
import {
  fetchSuperchargers,
  fetchSuperchargerPricing,
  refreshSupercharger,
  refreshSuperchargerPricing,
  type SuperchargerStation,
} from "../../../api/chargingApi";
import {
  buildSuperchargerPopoverElement,
  buildSuperchargerGeoJson,
  SUPERCHARGER_LAYER_IDS,
  type PricingRefreshState,
} from "../superchargers";
import type { MapRef } from "./map-ref";

export interface SuperchargerLayerState {
  loading: boolean;
  error: string | null;
}

/** Supercharger overlay: clustered GeoJSON layers created once (hidden) when
 *  the map loads, toggled via `visibility`, with a click popover offering
 *  metadata and pricing refresh (the browser calls the Tesla API directly).
 *  Station data is loaded when the overlay is shown and dropped when it is
 *  hidden, so showing it again fetches fresh data. */
export function useSuperchargerLayer(
  mapRef: MapRef,
  isMapLoaded: boolean,
  visible: boolean | undefined,
): SuperchargerLayerState {
  const [stations, setStations] = useState<SuperchargerStation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshingSlug, setRefreshingSlug] = useState<string | null>(null);
  // Click handlers read the latest stations from this ref, not a stale closure.
  const stationsRef = useRef<SuperchargerStation[]>([]);
  const activePopoverRef = useRef<Popup | null>(null);
  // Pricing refresh state per station slug; survives popover open/close but
  // is not persisted.
  const pricingRef = useRef<Record<string, PricingRefreshState>>({});

  /** Rebuilds the popover from the current metadata and pricing state (shared
   *  by all refresh/load paths so neither overwrites the other). */
  function rerenderPopover(station: SuperchargerStation, popup: Popup) {
    const pricing = pricingRef.current[station.slug] ?? { status: "idle" };
    popup.setDOMContent(
      buildSuperchargerPopoverElement(
        station,
        refreshingSlug === station.slug,
        () => handleStationRefresh(station, popup),
        pricing,
        () => updatePricing(station, popup, refreshSuperchargerPricing),
      ),
    );
  }

  /** Loads pricing (cached via `fetchSuperchargerPricing`, or scraped anew via
   *  `refreshSuperchargerPricing`) and re-renders the popover. */
  async function updatePricing(
    station: SuperchargerStation,
    popup: Popup,
    load: typeof fetchSuperchargerPricing,
  ) {
    const previous = pricingRef.current[station.slug];
    pricingRef.current[station.slug] = { status: "loading" };
    rerenderPopover(station, popup);
    try {
      const pricing = await load(station.slug);
      pricingRef.current[station.slug] = { status: "loaded", pricing };
    } catch (err: unknown) {
      pricingRef.current[station.slug] = {
        status: "error",
        message: err instanceof Error ? err.message : "Fehler",
        pricing: previous?.status === "loaded" ? previous.pricing : undefined,
      };
    }
    rerenderPopover(station, popup);
  }

  /** Refreshes a station's metadata from Tesla and re-renders the popover. */
  async function handleStationRefresh(
    station: SuperchargerStation,
    popup: Popup,
  ) {
    const slug = station.slug;
    setRefreshingSlug(slug);
    try {
      const updated = await refreshSupercharger(slug);
      setStations((prev) => prev.map((s) => (s.slug === slug ? updated : s)));
      rerenderPopover(updated, popup);
    } catch (err: unknown) {
      // Show the error in the popover and keep the last known data.
      const msg = err instanceof Error ? err.message : "Fehler";
      rerenderPopover(station, popup);
      const errorBanner = document.createElement("div");
      errorBanner.style.cssText =
        "color:#ef4444;font-size:12px;margin-top:4px;";
      errorBanner.textContent = `Fehler: ${msg}`;
      popup.getElement()?.querySelector("div")?.appendChild(errorBanner);
    } finally {
      setRefreshingSlug(null);
    }
  }

  function addLayers(map: Map) {
    if (map.getSource("superchargers")) return;

    map.addSource("superchargers", {
      type: "geojson",
      data: buildSuperchargerGeoJson([]),
      cluster: true,
      clusterRadius: 50,
      clusterMaxZoom: 14,
    });
    map.addLayer({
      id: "supercharger-clusters",
      type: "circle",
      source: "superchargers",
      filter: ["has", "point_count"],
      layout: { visibility: "none" },
      paint: {
        "circle-color": "#dc2626",
        "circle-radius": ["step", ["get", "point_count"], 14, 25, 18, 100, 24],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);
    map.addLayer({
      id: "supercharger-cluster-count",
      type: "symbol",
      source: "superchargers",
      filter: ["has", "point_count"],
      layout: {
        visibility: "none",
        "text-field": ["get", "point_count_abbreviated"],
        "text-font": ["Noto Sans Bold"],
        "text-size": 12,
      } satisfies LayerSpecification["layout"],
      paint: { "text-color": "#ffffff" } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);
    map.addLayer({
      id: "supercharger-unclustered",
      type: "circle",
      source: "superchargers",
      filter: ["!", ["has", "point_count"]],
      layout: { visibility: "none" },
      paint: {
        "circle-color": "#dc2626",
        "circle-radius": 8,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      } satisfies LayerSpecification["paint"],
    } satisfies LayerSpecification);

    map.on("click", "supercharger-clusters", (e) => {
      const feature = map.queryRenderedFeatures(e.point, {
        layers: ["supercharger-clusters"],
      })[0];
      const clusterId = feature?.properties?.cluster_id as number | undefined;
      if (
        !feature ||
        clusterId === undefined ||
        feature.geometry.type !== "Point"
      ) {
        return;
      }
      const source = map.getSource("superchargers") as GeoJSONSource;
      const coordinates = feature.geometry.coordinates as [number, number];
      source.getClusterExpansionZoom(clusterId).then((zoom) => {
        map.easeTo({ center: coordinates, zoom });
      });
    });

    map.on("click", "supercharger-unclustered", (e) => {
      const slug = e.features?.[0]?.properties?.slug as string | undefined;
      const station = stationsRef.current.find((s) => s.slug === slug);
      if (!station) return;

      activePopoverRef.current?.remove();
      const popup = new Popup({ offset: 14, closeButton: true });
      rerenderPopover(station, popup);
      popup.setLngLat([station.longitude, station.latitude]);
      popup.addTo(map);
      activePopoverRef.current = popup;
      if (!pricingRef.current[station.slug]) {
        void updatePricing(station, popup, fetchSuperchargerPricing);
      }
    });

    for (const layerId of [
      "supercharger-clusters",
      "supercharger-unclustered",
    ]) {
      map.on("mouseenter", layerId, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layerId, () => {
        map.getCanvas().style.cursor = "";
      });
    }
  }

  // Create the layers once (hidden); handlers are registered only once.
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    addLayers(mapRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMapLoaded]);

  // Toggle visibility only; layers and handlers stay in place.
  useEffect(() => {
    const map = mapRef.current;
    if (!isMapLoaded || !map) return;
    const visibility = visible ? "visible" : "none";
    for (const layerId of SUPERCHARGER_LAYER_IDS) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, "visibility", visibility);
      }
    }
  }, [mapRef, visible, isMapLoaded]);

  useEffect(() => {
    if (!visible) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStations([]);
      setError(null);
      return;
    }
    if (!isMapLoaded || stations.length > 0 || loading) return;
    setLoading(true);
    setError(null);
    fetchSuperchargers()
      .then((loaded) => {
        setStations(loaded);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setLoading(false);
        setError(err instanceof Error ? err.message : "Unbekannter Fehler");
      });
    // stations.length/loading are intentionally not dependencies - this
    // effect sets them itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, isMapLoaded]);

  // Keep the GeoJSON source and the handler ref in sync with the stations.
  useEffect(() => {
    stationsRef.current = stations;
    if (!isMapLoaded || !mapRef.current) return;
    const source = mapRef.current.getSource("superchargers") as
      GeoJSONSource | undefined;
    source?.setData(buildSuperchargerGeoJson(stations));
  }, [mapRef, stations, isMapLoaded]);

  return { loading, error };
}
