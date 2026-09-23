import { useEffect, useState } from "react";
import {
  LngLatBounds,
  type LayerSpecification,
  type MapMouseEvent,
} from "maplibre-gl";
import {
  buildSplicedRoute,
  splitRouteIntoLegs,
  projectDistanceAlongLineM,
  findNearestRouteSample,
  type RouteSample,
} from "../../../utils/route-line";
import type { TripSimulationResult } from "../../../types";
import { buildSocGradientExpression } from "../soc-gradient";
import { isLiveMap, type MapRef } from "./map-ref";

export interface RouteHoverInfo {
  x: number;
  y: number;
  sample: RouteSample;
}

/** Draws the simulated route (base line plus one SoC-gradient layer per leg),
 *  wires the hover tooltip and fits the camera to the route. Returns the
 *  current hover info for the tooltip overlay, or `null`. */
export function useRouteLayer(
  mapRef: MapRef,
  isMapLoaded: boolean,
  simulationResult: TripSimulationResult | undefined,
): RouteHoverInfo | null {
  const [routeHoverInfo, setRouteHoverInfo] = useState<RouteHoverInfo | null>(
    null,
  );

  useEffect(() => {
    const map = mapRef.current;
    if (!isMapLoaded || !map || !simulationResult) return;

    // Build the line from the full GraphHopper geometry (not the coarse,
    // time-based simulation frames), SPLICED with the routed detours to each
    // charging stop (`ChargingStop.detourGeometry`) - otherwise the line would
    // never leave the route to reach the station (see `buildSplicedRoute`).
    //
    // Waypoints are fed in as detours WITHOUT geometry rather than as plain
    // SoC samples: that gives each of them its own short gradient leg (see
    // `splitRouteIntoLegs`) with an exact arrival/departure jump. MapLibre
    // bakes `line-gradient` into a FIXED 256-texel texture over a leg's whole
    // length, so on a 1500 km leg any SoC jump would smear over kilometres.
    const splicedRoute = buildSplicedRoute(
      simulationResult.routeGeometry,
      [
        ...simulationResult.chargingStops.map((stop) => ({
          position: stop.position,
          distanceM: stop.distanceM,
          detourGeometrie: stop.detourGeometry,
          stationIndex: stop.detourStationIndex,
          routeIndexVor: stop.routeIndexBefore,
          routeIndexNach: stop.routeIndexAfter,
          arrivalSocPct: stop.arrivalSocPct,
          targetSocPct: stop.targetSocPct,
          ankunftszeit: stop.arrivalTime,
          departure_time: stop.departureTime,
        })),
        ...simulationResult.waypointStops.map((stop) => ({
          position: stop.position,
          distanceM: stop.distanceM,
          detourGeometrie: [],
          stationIndex: null,
          routeIndexVor: null,
          routeIndexNach: null,
          arrivalSocPct: stop.arrivalSocPct,
          targetSocPct: stop.targetSocPct,
          ankunftszeit: stop.arrivalTime,
          departure_time: stop.departureTime,
        })),
      ],
      // Driving frames only, for the gradient inside each leg - the
      // arrival/departure jumps come from the detours passed above.
      simulationResult.frames
        .filter((f) => f.state === "FAHREN")
        .map((f) => ({
          distanceM: f.distanceM,
          socPct: f.socPct,
          zeitpunkt: f.timestamp,
          geschwindigkeitKmh: f.speedKmh,
          temperaturC: f.temperatureC ?? undefined,
          windgeschwindigkeitKmh:
            f.windSpeedMs !== null ? f.windSpeedMs * 3.6 : undefined,
          windrichtungDeg: f.windDirectionDeg ?? undefined,
          niederschlagMm: f.precipitationMm ?? undefined,
        })),
    );
    const routeCoordinates = splicedRoute.coordinates;

    // Base line (lineMetrics for `line-progress`, see
    // `buildSocGradientExpression`); the per-leg gradients go on top.
    map.addSource("route", {
      type: "geojson" as const,
      lineMetrics: true,
      data: {
        type: "Feature" as const,
        properties: {},
        geometry: {
          type: "LineString" as const,
          coordinates: routeCoordinates,
        },
      },
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

    // Hover tooltip: project the cursor onto the drawn (spliced) line and
    // show the sample nearest by distance ALONG the line - not the spatially
    // nearest frame, which a few km after a detour could still show the
    // pre-charge values (see `buildRouteHoverText`).
    const handleMouseMove = (e: MapMouseEvent) => {
      const distAlongM = projectDistanceAlongLineM(routeCoordinates, [
        e.lngLat.lng,
        e.lngLat.lat,
      ]);
      const sample = findNearestRouteSample(splicedRoute.samples, distAlongM);
      if (!sample) return;
      setRouteHoverInfo({ x: e.point.x, y: e.point.y, sample });
    };
    const handleMouseLeave = () => setRouteHoverInfo(null);
    map.on("mousemove", "route", handleMouseMove);
    map.on("mouseleave", "route", handleMouseLeave);

    // SoC gradient: ONE source+layer PER leg instead of one over the whole
    // route, so each leg gets its own texture resolution (see above).
    const legSourceIds: string[] = [];
    for (const [legIndex, leg] of splitRouteIntoLegs(splicedRoute).entries()) {
      const sourceId = `route-leg-${legIndex}`;
      map.addSource(sourceId, {
        type: "geojson" as const,
        lineMetrics: true,
        data: {
          type: "Feature" as const,
          properties: {},
          geometry: {
            type: "LineString" as const,
            coordinates: leg.coordinates,
          },
        },
      });
      map.addLayer({
        id: `${sourceId}-gradient`,
        type: "line",
        source: sourceId,
        paint: {
          "line-width": 4,
          "line-opacity": 0.9,
          "line-gradient": buildSocGradientExpression(
            leg.samples,
            leg.totalDistanceM,
          ),
        },
      } as unknown as LayerSpecification);
      legSourceIds.push(sourceId);
    }

    const bounds = routeCoordinates.reduce(
      (b, coord) => b.extend(coord),
      new LngLatBounds(),
    );
    map.fitBounds(bounds, { padding: 50 });

    return () => {
      setRouteHoverInfo(null);
      if (!isLiveMap(mapRef, map)) return;
      map.off("mousemove", "route", handleMouseMove);
      map.off("mouseleave", "route", handleMouseLeave);
      for (const sourceId of legSourceIds) {
        const layerId = `${sourceId}-gradient`;
        if (map.getLayer(layerId)) map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
      }
      if (map.getLayer("route")) map.removeLayer("route");
      if (map.getSource("route")) map.removeSource("route");
    };
  }, [mapRef, isMapLoaded, simulationResult]);

  return routeHoverInfo;
}
