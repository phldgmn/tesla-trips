import { useEffect, useRef, useState } from "react";
import {
  Map,
  Marker,
  Popup,
  LngLatBounds,
  LayerSpecification,
  GeoJSONSource,
  type MapMouseEvent,
} from "maplibre-gl";
import { toLngLat } from "../../utils/geo-utils";
import {
  buildSplicedRoute,
  splitRouteIntoLegs,
  projectDistanceAlongLineM,
  findNearestRouteSample,
  type RouteSample,
} from "../../utils/route-line";
import { usePersistentState } from "../../utils/persistent-state";
import type { ChargingStop, TripSimulationResult } from "../../types";
import type { Stop } from "../../types/trip-request";
import {
  fetchSuperchargers,
  fetchSuperchargerPricing,
  refreshSupercharger,
  refreshSuperchargerPricing,
  type SuperchargerStation,
} from "../../api/chargingApi";
import { basemapStyle } from "./basemap";
import { buildSocGradientExpression } from "./soc-gradient";
import { isConstructionZoneVisibleAtZoom } from "./construction-zone-visibility";
import {
  buildMarkerElement,
  stopRole,
  buildChargingStopMarkerElement,
  buildConstructionZoneMarkerElement,
} from "./markers";
import {
  buildChargingStopPopupElement,
  buildStopPopupHtml,
  buildConstructionZonePopupHtml,
  buildRouteHoverText,
  findWaypointStopAt,
} from "./popups";
import {
  buildSuperchargerPopoverElement,
  buildSuperchargerGeoJson,
  SUPERCHARGER_LAYER_IDS,
  type PricingRefreshState,
} from "./superchargers";
import {
  DEFAULT_MAP_VIEW,
  isValidMapViewState,
  type MapViewState,
} from "./view-state";

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
  // Ladehalt-Marker (ein Eintrag pro tatsaechlichem Ladehalt aus
  // `simulationResult.charging_stops`), analog zu `markersRef` fuer Stopps.
  const chargingStopMarkersRef = useRef<Marker[]>([]);
  // Baustellen-Marker (ein Eintrag pro `simulationResult.construction_zones`),
  // analog zu `chargingStopMarkersRef` - zusaetzlich `laengeM`, um Marker
  // je nach Zoom-Level ein-/auszublenden (siehe
  // `updateConstructionZoneVisibility`/`isConstructionZoneVisibleAtZoom`).
  const constructionZoneMarkersRef = useRef<
    { marker: Marker; laengeM: number | null }[]
  >([]);

  // Supercharger-Overlay
  const [superchargerStations, setSuperchargerStations] = useState<
    SuperchargerStation[]
  >([]);
  const [superchargerLoading, setSuperchargerLoading] = useState(false);
  const [superchargerError, setSuperchargerError] = useState<string | null>(
    null,
  );
  const [refreshingSlug, setRefreshingSlug] = useState<string | null>(null);
  const superchargerStationsRef = useRef<SuperchargerStation[]>([]);
  const activePopoverRef = useRef<Popup | null>(null);
  // Zwischengespeicherter Preis-Refresh-Zustand je Station-Slug fuer das
  // Supercharger-Popover (siehe `handleSuperchargerPricingRefresh`) -
  // ueberlebt Popover-Oeffnen/Schliessen, wird aber nicht persistiert.
  const superchargerPricingRef = useRef<Record<string, PricingRefreshState>>(
    {},
  );
  // Routen-Hover: zeigt Datum/Zeit + SoC des naechstgelegenen Streckenpunkts
  // (per Distanz-entlang-der-Linie, nicht raeumlich) in einem kleinen
  // Tooltip neben dem Cursor an.
  const [routeHoverInfo, setRouteHoverInfo] = useState<{
    x: number;
    y: number;
    sample: RouteSample;
  } | null>(null);
  const routeHoverHandlersRef = useRef<{
    move: (e: MapMouseEvent) => void;
    leave: () => void;
  } | null>(null);
  // Ids der pro-Leg SoC-Gradient-Sources (siehe `splitRouteIntoLegs`) - fuer
  // sauberes Entfernen in `clearSimulationLayers` beim naechsten Rebuild.
  const routeLegSourceIdsRef = useRef<string[]>([]);

  // Kartenausschnitt (Mittelpunkt + Zoom) wird in `localStorage` gespiegelt
  // und beim Neuladen wiederhergestellt, statt jedes Mal bei der
  // Welt-Ansicht zu starten. `initialMapViewRef` friert den beim Mount
  // wiederhergestellten Wert ein - der Map-Konstruktor braucht ihn nur
  // einmalig, spätere Änderungen laufen über `moveend` (s. u.).
  const [mapView, setMapView] = usePersistentState<MapViewState | null>(
    "map-view",
    null,
  );
  const initialMapViewRef = useRef<MapViewState>(
    mapView && isValidMapViewState(mapView) ? mapView : DEFAULT_MAP_VIEW,
  );

  useEffect(() => {
    // Map initialisieren
    if (!mapContainerRef.current || mapRef.current) return;

    mapRef.current = new Map({
      container: mapContainerRef.current,
      style: basemapStyle,
      center: initialMapViewRef.current.center,
      zoom: initialMapViewRef.current.zoom,
    });

    mapRef.current.on("load", () => {
      setIsMapLoaded(true);
    });
    mapRef.current.on("moveend", () => {
      const map = mapRef.current;
      if (!map) return;
      const center = map.getCenter();
      setMapView({ center: [center.lng, center.lat], zoom: map.getZoom() });
    });
    // Baustellen-Marker je nach Zoom-Level ein-/ausblenden (siehe
    // `updateConstructionZoneVisibility`) - "zoom" statt "zoomend" fuer
    // sofortiges Feedback waehrend des Zoomens statt Nachhinken.
    mapRef.current.on("zoom", () => {
      updateConstructionZoneVisibility();
    });

    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `setMapView` (React-Setter, stabile Identität) wird bewusst nicht aufgenommen: der Effect soll nur beim Mount laufen, siehe `initialMapViewRef`.
  }, []);

  /** Entfernt alle Routen- und Marker-Sources/Layer aus der Karte. */
  function clearSimulationLayers(map: Map) {
    // Route (Basislinie, ein Source/Layer) + pro-Leg SoC-Gradient-Layer
    // (siehe `splitRouteIntoLegs` - eigene Texture-Aufloesung pro Leg statt
    // einer gemeinsamen 256-Texel-Texture ueber die gesamte Route).
    for (const sourceId of routeLegSourceIdsRef.current) {
      const layerId = `${sourceId}-gradient`;
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(sourceId)) map.removeSource(sourceId);
    }
    routeLegSourceIdsRef.current = [];
    if (map.getLayer("route")) map.removeLayer("route");
    if (map.getSource("route")) map.removeSource("route");
    // Hover-Handler der Haupt-Route abmelden (sonst haeufen sich beim
    // Neuaufbau der Route mehrere Listener mit veralteten `frames`-Closures)
    if (routeHoverHandlersRef.current) {
      map.off("mousemove", "route", routeHoverHandlersRef.current.move);
      map.off("mouseleave", "route", routeHoverHandlersRef.current.leave);
      routeHoverHandlersRef.current = null;
    }
    setRouteHoverInfo(null);
    // Ladehalte
    for (const marker of chargingStopMarkersRef.current) {
      marker.remove();
    }
    chargingStopMarkersRef.current = [];
    // Baustellen
    for (const { marker } of constructionZoneMarkersRef.current) {
      marker.remove();
    }
    constructionZoneMarkersRef.current = [];
  }

  /** Blendet jeden Baustellen-Marker per CSS ein/aus, je nachdem ob seine
   *  `laengeM` beim aktuellen Kartenzoom noch als sichtbar gilt (siehe
   *  `isConstructionZoneVisibleAtZoom`) - Marker bleiben dabei im DOM
   *  (kein `addTo`/`remove`), nur ihre Sichtbarkeit toggelt. */
  function updateConstructionZoneVisibility() {
    const map = mapRef.current;
    if (!map) return;
    const zoom = map.getZoom();
    for (const { marker, laengeM } of constructionZoneMarkersRef.current) {
      const visible = isConstructionZoneVisibleAtZoom(laengeM, zoom);
      // "flex" statt "" setzen: leerer String entfernt die inline
      // `display:flex`-Deklaration aus `buildConstructionZoneMarkerElement`s
      // `cssText` komplett (statt sie nur "freizugeben"), wodurch der Div auf
      // `display:block` zurueckfaellt und das SVG-Icon linksbuendig statt
      // zentriert dargestellt wird.
      marker.getElement().style.display = visible ? "flex" : "none";
    }
  }

  /** Verschiebt jedes Stopp-Marker-Element (siehe `markersRef`) ans Ende
   *  seines DOM-Parents, damit Stopp-Marker IMMER ueber Baustellen-/
   *  Ladehalt-Markern liegen - rein per DOM-Reihenfolge (spaeter im DOM =
   *  oben im Paint-Stack), NICHT per inline `position`/`z-index` auf dem
   *  Marker-Element selbst (das durchkreuzt MapLibres eigene Transform-
   *  basierte Positionierung und liess Marker beim Zoomen von ihrer
   *  Koordinate abdriften). `appendChild` auf einem bereits eingehaengten
   *  Knoten verschiebt ihn nur um - Inline-Styles/Transform, die MapLibre
   *  selbst auf dem Element verwaltet, bleiben unangetastet.
   *
   *  Wird am Ende BEIDER Marker-Effekte aufgerufen (Simulationsergebnis- UND
   *  Stopp-Effekt), damit die Reihenfolge unabhaengig davon stimmt, welcher
   *  der beiden zuletzt gelaufen ist. */
  function raiseStopMarkersToTop() {
    for (const marker of Object.values(markersRef.current)) {
      const el = marker.getElement();
      el.parentNode?.appendChild(el);
    }
  }

  /** Helfer: Preisdaten eines Ladehalts (Route) von Tesla nachtriggern und
   *  das Popup mit dem Ergebnis neu bauen. Aktualisiert nur die Anzeige in
   *  diesem Popup, nicht die Gesamtroute/-kosten - dafuer muss die Route
   *  neu berechnet werden (siehe `buildChargingStopPopupElement`). */
  async function handleChargingStopPricingRefresh(
    stop: ChargingStop,
    render: (state: PricingRefreshState) => void,
  ) {
    render({ status: "loading" });
    try {
      const pricing = await refreshSuperchargerPricing(stop.station_id);
      render({ status: "loaded", pricing });
    } catch (err: unknown) {
      render({
        status: "error",
        message: err instanceof Error ? err.message : "Fehler",
      });
    }
  }

  /** Helfer: gecachte Preisdaten fuer ein Supercharger-Popover lazy laden
   *  (ohne Scrape) und das Popover mit dem Ergebnis neu bauen. */
  async function loadSuperchargerPricing(
    station: SuperchargerStation,
    popup: Popup,
  ) {
    const previous = superchargerPricingRef.current[station.slug];
    superchargerPricingRef.current[station.slug] = { status: "loading" };
    rerenderSuperchargerPopover(station, popup);
    try {
      const pricing = await fetchSuperchargerPricing(station.slug);
      superchargerPricingRef.current[station.slug] = {
        status: "loaded",
        pricing,
      };
    } catch (err: unknown) {
      superchargerPricingRef.current[station.slug] = {
        status: "error",
        message: err instanceof Error ? err.message : "Fehler",
        pricing: previous?.status === "loaded" ? previous.pricing : undefined,
      };
    }
    rerenderSuperchargerPopover(station, popup);
  }

  /** Helfer: Preisdaten eines Supercharger-Popovers von Tesla scrapen und
   *  neu speichern (im Gegensatz zu `loadSuperchargerPricing`, das nur
   *  gecachte Daten liest). */
  async function handleSuperchargerPricingRefresh(
    station: SuperchargerStation,
    popup: Popup,
  ) {
    const previous = superchargerPricingRef.current[station.slug];
    superchargerPricingRef.current[station.slug] = { status: "loading" };
    rerenderSuperchargerPopover(station, popup);
    try {
      const pricing = await refreshSuperchargerPricing(station.slug);
      superchargerPricingRef.current[station.slug] = {
        status: "loaded",
        pricing,
      };
    } catch (err: unknown) {
      superchargerPricingRef.current[station.slug] = {
        status: "error",
        message: err instanceof Error ? err.message : "Fehler",
        pricing: previous?.status === "loaded" ? previous.pricing : undefined,
      };
    }
    rerenderSuperchargerPopover(station, popup);
  }

  /** Baut das Supercharger-Popover mit dem aktuellen Metadaten- und
   *  Preis-Refresh-Zustand neu (gemeinsamer Helfer fuer alle drei
   *  Refresh-/Load-Pfade, damit Metadaten- und Preisanzeige nie
   *  gegenseitig ueberschrieben werden). */
  function rerenderSuperchargerPopover(
    station: SuperchargerStation,
    popup: Popup,
  ) {
    const pricing = superchargerPricingRef.current[station.slug] ?? {
      status: "idle",
    };
    const popupEl = buildSuperchargerPopoverElement(
      station,
      refreshingSlug === station.slug,
      () => handleSuperchargerRefresh(station, popup),
      pricing,
      () => handleSuperchargerPricingRefresh(station, popup),
    );
    popup.setDOMContent(popupEl);
  }

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
      rerenderSuperchargerPopover(updated, popup);
    } catch (err: unknown) {
      // Fehler im Popover anzeigen, letzten bekannten Stand beibehalten
      const msg = err instanceof Error ? err.message : "Fehler";
      rerenderSuperchargerPopover(station, popup);
      const errorBanner = document.createElement("div");
      errorBanner.style.cssText =
        "color:#ef4444;font-size:12px;margin-top:4px;";
      errorBanner.textContent = `Fehler: ${msg}`;
      popup.getElement()?.querySelector("div")?.appendChild(errorBanner);
    } finally {
      setRefreshingSlug(null);
    }
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

    // Route in GeoJSON konvertieren: volle GraphHopper-Geometrie (nicht die
    // zeitbasiert groben Simulationsframes) fuer eine winkeltreue Linie, die
    // dem tatsaechlichen Strassenverlauf folgt, GESPLICET mit echten,
    // geroutete Abstechern zu jedem Ladehalt (`ChargingStop.detour_geometrie`)
    // - sonst wuerde die Route nie die Ladestation verlassen (siehe
    // `buildSplicedRoute`). Liefert passend dazu SoC-Stuetzpunkte fuer
    // `buildSocGradientExpression`.
    //
    // Zwischenstopps (`waypoint_stops`) werden HIER als Detours OHNE eigene
    // Geometrie (leere `detourGeometrie`) eingespeist statt als reine
    // SoC-Stuetzpunkte in den Fahr-Frames: Ladehalte bekommen dadurch schon
    // eine eigene, kurze Gradient-Leg (siehe `splitRouteIntoLegs`) mit exaktem
    // Ankunfts-/Abfahrts-Sprung (`emitChargeJump` in `buildSplicedRoute`) -
    // Zwischenstopps OHNE dieses Splitting blieben Teil einer einzigen,
    // tausende km langen Leg. MapLibres `line-gradient` backt aber pro Leg
    // eine FESTE 256-Textur-Aufloesung ueber deren GESAMTE Laenge; bei einer
    // 1500 km langen Leg verschmiert das jeden noch so exakten SoC-Sprung
    // ueber mehrere km (sichtbar als falsche Farbe rund um den Zwischenstopp
    // auf der Karte, unabhaengig davon, wie exakt die zugrundeliegenden
    // SoC-Werte sind). Das Splitting behebt das exakt wie bei Ladehalten.
    const splicedRoute = buildSplicedRoute(
      simulationResult.route_geometrie,
      [
        ...simulationResult.charging_stops.map((stop) => ({
          position: stop.position,
          distanzM: stop.distanz_m,
          detourGeometrie: stop.detour_geometrie,
          stationIndex: stop.detour_station_index,
          routeIndexVor: stop.route_index_vor,
          routeIndexNach: stop.route_index_nach,
          ankunftsSocPct: stop.ankunfts_soc_pct,
          zielSocPct: stop.ziel_soc_pct,
          ankunftszeit: stop.ankunftszeit,
          abfahrtszeit: stop.abfahrtszeit,
        })),
        ...simulationResult.waypoint_stops.map((stop) => ({
          position: stop.position,
          distanzM: stop.distanz_m,
          detourGeometrie: [],
          stationIndex: null,
          routeIndexVor: null,
          routeIndexNach: null,
          ankunftsSocPct: stop.ankunfts_soc_pct,
          zielSocPct: stop.ziel_soc_pct,
          ankunftszeit: stop.ankunftszeit,
          abfahrtszeit: stop.abfahrtszeit,
        })),
      ],
      // Reine Fahr-Frames fuer den Gradienten innerhalb jeder Leg - die
      // Ankunfts-/Abfahrts-Spruenge selbst kommen jetzt ausschliesslich aus
      // den oben uebergebenen Detours (Ladehalte UND Zwischenstopps).
      simulationResult.frames
        .filter((f) => f.zustand === "FAHREN")
        .map((f) => ({
          distanzM: f.distanz_m,
          socPct: f.soc_pct,
          zeitpunkt: f.zeitpunkt,
          geschwindigkeitKmh: f.geschwindigkeit_kmh,
          temperaturC: f.temperatur_c ?? undefined,
          windgeschwindigkeitKmh:
            f.windgeschwindigkeit_ms !== null
              ? f.windgeschwindigkeit_ms * 3.6
              : undefined,
          windrichtungDeg: f.windrichtung_deg ?? undefined,
          niederschlagMm: f.niederschlag_mm ?? undefined,
        })),
    );
    const routeCoordinates = splicedRoute.coordinates;

    const routeGeoJson: GeoJSON.Feature<GeoJSON.LineString> = {
      type: "Feature" as const,
      properties: {},
      geometry: {
        type: "LineString" as const,
        coordinates: routeCoordinates,
      },
    };

    // Route als GeoJSON Source (lineMetrics fuer `line-progress`, siehe
    // `buildSocGradientExpression`) + zwei Layer: dezente Basislinie +
    // SoC-Gradient obendrauf. Ersetzt die vorherigen 1+N Sources/Layer
    // (ein flacher Layer pro Farbsegment) durch genau zwei GPU-Layer,
    // unabhängig von der Trip-Länge.
    map.addSource("route", {
      type: "geojson" as const,
      lineMetrics: true,
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

    // Hover-Tooltip: bei Mausbewegung ueber der Route die Mausposition auf
    // die tatsaechlich gezeichnete (gesplicete) Linie projizieren und den
    // Stuetzpunkt mit der naechstgelegenen Distanz-entlang-der-Linie
    // anzeigen - NICHT den raeumlich naechstgelegenen Simulationsframe (der
    // wenige Kilometer nach einem Ladehalt-Abstecher noch die Vor-Lade-Werte
    // liefern konnte, siehe `buildRouteHoverText`).
    const handleRouteMouseMove = (e: MapMouseEvent) => {
      const distAlongM = projectDistanceAlongLineM(routeCoordinates, [
        e.lngLat.lng,
        e.lngLat.lat,
      ]);
      const sample = findNearestRouteSample(splicedRoute.samples, distAlongM);
      if (!sample) return;
      setRouteHoverInfo({ x: e.point.x, y: e.point.y, sample });
    };
    const handleRouteMouseLeave = () => setRouteHoverInfo(null);
    map.on("mousemove", "route", handleRouteMouseMove);
    map.on("mouseleave", "route", handleRouteMouseLeave);
    routeHoverHandlersRef.current = {
      move: handleRouteMouseMove,
      leave: handleRouteMouseLeave,
    };

    // SoC-Gradient: EIN Source+Layer PRO Leg (siehe `splitRouteIntoLegs`)
    // statt eines gemeinsamen Layers ueber die gesamte Route - MapLibre
    // backt `line-gradient` in eine feste 256-Texel-Texture ueber die
    // GESAMTE Linienlaenge; bei einer einzigen, mehrere hundert/tausend km
    // langen Route waere ein an einem Ladehalt technisch korrekt auf <1 m
    // kollabierter SoC-Sprung (siehe `CHARGE_JUMP_EPSILON_M`) weit unter der
    // Texture-Aufloesung und wuerde schlicht nicht dargestellt (dokumentiertes
    // MapLibre/Mapbox-Verhalten). Jeder Leg bekommt so seine eigene, viel
    for (const [legIndex, leg] of splitRouteIntoLegs(splicedRoute).entries()) {
      const sourceId = `route-leg-${legIndex}`;
      const legGeoJson: GeoJSON.Feature<GeoJSON.LineString> = {
        type: "Feature" as const,
        properties: {},
        geometry: {
          type: "LineString" as const,
          coordinates: leg.coordinates,
        },
      };
      map.addSource(sourceId, {
        type: "geojson" as const,
        lineMetrics: true,
        data: legGeoJson,
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
      routeLegSourceIdsRef.current.push(sourceId);
    }
    // Baustellen-Marker hinzufügen: ein Marker pro Eintrag aus
    // `simulationResult.construction_zones`, mit Klick-Popup für Details.
    // WICHTIG: Vor Ladehalt-Markern hinzufügen, damit Ladehalt-Marker
    // über den Baustellen-Markern liegen (reine DOM-Reihenfolge, siehe
    // `raiseStopMarkersToTop` fürs analoge Prinzip bei Stopp-Markern).
    for (const zone of simulationResult.construction_zones) {
      const element = buildConstructionZoneMarkerElement();
      const marker = new Marker({ element })
        .setLngLat(toLngLat(zone.position))
        .setPopup(
          new Popup({ offset: 12 }).setHTML(
            buildConstructionZonePopupHtml(zone),
          ),
        )
        .addTo(map);
      constructionZoneMarkersRef.current.push({
        marker,
        laengeM: zone.laenge_m,
      });
    }
    // Direkt nach dem Anlegen die Sichtbarkeit fuer den aktuellen Zoom
    // setzen - der "zoom"-Listener (siehe Map-Init-Effect) feuert erst bei
    // der naechsten tatsaechlichen Zoom-Aenderung, nicht beim Marker-Anlegen.
    updateConstructionZoneVisibility();

    // Ladehalt-Marker hinzufügen: ein Marker pro tatsächlichem Ladehalt aus
    // `simulationResult.charging_stops` (nicht pro LADEN-Frame – ein Halt
    // kann mehrere Frames erzeugen, siehe `buildChargingStopMarkerElement`),
    // exakt auf der Position der Ladestation, mit Klick-Popup für Details.
    // Fehlt die Preis-Info fuer diesen Halt (`price_per_kwh === null`),
    // zeigt das Popup zusaetzlich einen Preis-Refresh-Button (siehe
    // `buildChargingStopPopupElement`/`handleChargingStopPricingRefresh`).
    for (const stop of simulationResult.charging_stops) {
      const element = buildChargingStopMarkerElement();
      const popup = new Popup({ offset: 14 });
      const renderChargingStopPopup = (state: PricingRefreshState) => {
        popup.setDOMContent(
          buildChargingStopPopupElement(stop, state, () =>
            handleChargingStopPricingRefresh(stop, renderChargingStopPopup),
          ),
        );
      };
      renderChargingStopPopup({ status: "idle" });
      const marker = new Marker({ element })
        .setLngLat(toLngLat(stop.position))
        .setPopup(popup)
        .addTo(map);
      chargingStopMarkersRef.current.push(marker);
    }

    // Stopp-Marker koennen zu diesem Zeitpunkt bereits existieren (siehe
    // zweiter Marker-Effekt unten) - jetzt neu hinzugekommene Baustellen-/
    // Ladehalt-Marker wieder unter sie schieben (siehe `raiseStopMarkersToTop`).
    raiseStopMarkersToTop();

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
  // Popup-Inhalt wird bei jedem Lauf neu gesetzt (auch für bestehende
  // Marker), damit ein neu berechnetes Simulationsergebnis dessen
  // Aufenthalts-Details (siehe `findWaypointStopAt`/`buildStopPopupHtml`)
  // nachzieht, ohne den Marker selbst (und damit Drag-State) neu zu
  // erzeugen.
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const markers = markersRef.current;
    const stops = stopsProp ?? [];
    const waypointStops = simulationResult?.waypoint_stops ?? [];
    const seen = new Set<string>();

    // Nur Stopps mit aufgelöster Position rendern.

    for (let i = 0; i < stops.length; i++) {
      const stop = stops[i];
      if (!stop.position) continue;

      seen.add(stop.id);
      const role = stopRole(i, stops.length);
      const waypointStop = findWaypointStopAt(waypointStops, stop.position);
      const popupHtml = buildStopPopupHtml(stop, role, waypointStop);
      const existing = markers[stop.id];
      if (existing) {
        // Nur Position aktualisieren – Marker-Instanz und Drag-State bleiben
        existing.setLngLat(toLngLat(stop.position));
        existing.setPopup(new Popup({ offset: 14 }).setHTML(popupHtml));
      } else {
        // Neuen Marker erzeugen
        const element = buildMarkerElement(role);
        const marker = new Marker({ element, draggable: true });
        marker.setLngLat(toLngLat(stop.position));
        marker.setPopup(new Popup({ offset: 14 }).setHTML(popupHtml));
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

    // Sicherstellen, dass Stopp-Marker (auch neu erzeugte) ueber ggf. schon
    // vorhandenen Baustellen-/Ladehalt-Markern liegen (siehe
    // `raiseStopMarkersToTop`).
    raiseStopMarkersToTop();

    return () => {
      // Beim Unmount alle Marker entfernen
      for (const id of Object.keys(markers)) {
        markers[id].remove();
        delete markers[id];
      }
    };
  }, [stopsProp, isMapLoaded, onStopMove, simulationResult]);

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

  /** Fügt die (dauerhaft vorhandene, zunächst ausgeblendete) Source und
   * die Cluster-/Punkt-Layer für das Supercharger-Overlay hinzu und
   * registriert die Klick-/Hover-Handler einmalig. Sichtbarkeit wird
   * separat über `visibility` gesteuert (siehe Effects unten) statt die
   * Layer bei jedem Ein-/Ausblenden neu anzulegen – vermeidet doppelt
   * registrierte Event-Handler bei wiederholtem Toggle. Klick-Handler
   * lesen Stationsdaten über `superchargerStationsRef`, nicht über einen
   * Closure-Stand von `superchargerStations`/`refreshingSlug`.
   */
  function addSuperchargerLayers(map: Map) {
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
      paint: {
        "text-color": "#ffffff",
      } satisfies LayerSpecification["paint"],
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
      const station = superchargerStationsRef.current.find(
        (s) => s.slug === slug,
      );
      if (!station) return;

      activePopoverRef.current?.remove();
      const popup = new Popup({ offset: 14, closeButton: true });
      rerenderSuperchargerPopover(station, popup);
      popup.setLngLat([station.longitude, station.latitude]);
      popup.addTo(map);
      activePopoverRef.current = popup;
      if (!superchargerPricingRef.current[station.slug]) {
        void loadSuperchargerPricing(station, popup);
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

  // Supercharger-Layer einmalig anlegen (ausgeblendet) - siehe
  // `addSuperchargerLayers`.
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    addSuperchargerLayers(mapRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMapLoaded]);

  // Sichtbarkeit umschalten (Layer bleiben angelegt, nur `visibility`
  // wechselt - vermeidet Neuanlegen/erneutes Registrieren der Handler bei
  // jedem Toggle).
  useEffect(() => {
    if (!isMapLoaded || !mapRef.current) return;
    const map = mapRef.current;
    const visibility = superchargerVisible ? "visible" : "none";
    for (const layerId of SUPERCHARGER_LAYER_IDS) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, "visibility", visibility);
      }
    }
  }, [superchargerVisible, isMapLoaded]);

  // Stationsdaten laden, sobald das Overlay eingeblendet wird; beim
  // Ausblenden zuruecksetzen, damit ein erneutes Einblenden frische Daten
  // laedt statt (potenziell veralteter) Cache-Daten.
  useEffect(() => {
    if (!superchargerVisible) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSuperchargerStations([]);
      setSuperchargerError(null);
      return;
    }
    if (
      !isMapLoaded ||
      superchargerStations.length > 0 ||
      superchargerLoading
    ) {
      return;
    }
    setSuperchargerLoading(true);
    setSuperchargerError(null);
    fetchSuperchargers()
      .then((stations) => {
        setSuperchargerStations(stations);
        setSuperchargerLoading(false);
      })
      .catch((err: unknown) => {
        setSuperchargerLoading(false);
        const msg = err instanceof Error ? err.message : "Unbekannter Fehler";
        setSuperchargerError(msg);
      });
    // superchargerStations.length/superchargerLoading bewusst nicht in den
    // Dependencies - wir setzen den State hier selbst.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [superchargerVisible, isMapLoaded]);

  // GeoJSON-Source synchron mit dem State halten (initiales Laden +
  // Refresh via `handleSuperchargerRefresh`) und aktuellen Stand für die
  // Klick-Handler in `addSuperchargerLayers` referenzierbar halten.
  useEffect(() => {
    superchargerStationsRef.current = superchargerStations;
    if (!isMapLoaded || !mapRef.current) return;
    const source = mapRef.current.getSource("superchargers") as
      GeoJSONSource | undefined;
    source?.setData(buildSuperchargerGeoJson(superchargerStations));
  }, [superchargerStations, isMapLoaded]);

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

      {/* Routen-Hover-Tooltip: Datum/Zeit, SoC, Geschwindigkeit und (falls
          beruecksichtigt) Wetter am naechstgelegenen Streckenpunkt - mehrzeilig
          (siehe `buildRouteHoverText`), damit der Tooltip vertikal statt
          horizontal waechst. */}
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
