"""API-Schicht für trip_input: Orchestrierung der 11 Datenfluss-Schritte.

Diese Modul implementiert:
- `create_trip_simulation()`: Kernfunktion zur Orchestrierung aller Schritte
- FastAPI-Endpunkt `POST /trips` mit `TripRequestAPI` Request-Model
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import traceback
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator

from tripplanner.charging_infrastructure import (
    ChargingStation,
    ChargingStationProvider,
    FakeChargingStationProvider,
    StallType,
    get_all_charging_stations,
)
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.pricing import select_owner_rate_for_time
from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.construction.models import ConstructionProvider, ConstructionZone, Land
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.models import ElevationPoint
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.energy import calculate_segment_consumption
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters
from tripplanner.geo import haversine_distance_m
from tripplanner.optimization import create_networkx_optimizer
from tripplanner.optimization.models import ChargingPlan, OptimizationConstraints
from tripplanner.routing import (
    FakeRoutingProvider,
    RoutingProvider,
    erkenne_faehren,
)
from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment
from tripplanner.simulation import simulate_trip
from tripplanner.simulation.models import (
    ChargingCostByCurrency,
    ChargingStopSummary,
    LadehaltDetour,
    TripSimulationResult,
)
from tripplanner.trip_input.models import (
    FaehrZeitfenster,
    TripRequest,
    VehicleProfile,
    Waypoint,
)
from tripplanner.trip_input.providers_factory import (
    ProductionProviders,
    build_production_providers,
    close_production_providers,
)
from tripplanner.weather import FakeWeatherProvider, WeatherDetailLevel, fetch_weather_by_detail
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import WeatherProvider
from tripplanner.wind import compute_wind_components_for_route
from tripplanner.wind.models import WindComponents

if TYPE_CHECKING:
    from tripplanner.optimization.models import ChargingStop

# =============================================================================
# GraphHopper-Konfiguration
# =============================================================================

# Umgebungsvariable für die GraphHopper-Basis-URL (siehe README.md,
# .github/workflows/ci.yml).
GRAPHHOPPER_URL_ENV_VAR = "GRAPHHOPPER_URL"

# Default-Basis-URL, falls GRAPHHOPPER_URL nicht gesetzt ist.
DEFAULT_GRAPHHOPPER_BASE_URL = "http://localhost:8989"

# =============================================================================
# Helper-Funktionen für die 11 Datenfluss-Schritte
# =============================================================================


async def _step_1_route_calculate(
    request: TripRequest,
    routing_provider: RoutingProvider | None = None,
) -> Route:
    """Schritt 1: OSM-Routing berechnen (inkl. Zwischenstopps als Pflicht-Waypoints).

    Als Default-Provider wird `FakeRoutingProvider` verwendet, damit die Pipeline
    ohne echten GraphHopper-Server läuft. Für Produktion kann ein echter Provider
    wie `GraphHopperRoutingProvider` übergeben werden. Ruft `berechne_route()`
    (nicht `berechne_route_mit_waypoints()`) auf, damit Präferenzen aus `request`
    (z. B. Fährvermeidung) den Provider erreichen.
    """
    provider = routing_provider or FakeRoutingProvider()
    return await provider.berechne_route(request)


async def _step_2_extract_elevation_profile(
    route: Route, elevation_provider: ElevationProvider
) -> list[ElevationPoint]:
    """Step 2: Extract elevation profile along the route.

    Args:
        route: The route to extract elevation from.
        elevation_provider: The elevation provider with data source.

    Returns:
        List of ElevationPoint for all sampling points.
    """
    return await elevation_provider.get_elevation_profile(route)


def _step_3_segment_route(route: Route) -> list[RouteSegment]:
    """Schritt 3: Route in Segmente unterteilen.

    Diese Information ist bereits in `route.segments` enthalten.
    """
    return route.segments


def _step_4_estimate_initial_eta(
    route: Route,
    abfahrtszeit: datetime,
) -> list[tuple[RouteSegment, timedelta]]:
    """Schritt 4: Initiale ETA-Schätzung je Segment.

    Grobe Schätzung basierend auf durchschnittlicher Geschwindigkeit
    (Default: 110 km/h auf Autobahnen, 60 km/h sonst).
    """
    segment_eta_list: list[tuple[RouteSegment, timedelta]] = []

    for segment in route.segments:
        laenge_km = segment.laenge_m / 1000.0

        # Geschwindigkeit basierend auf Straßenart schätzen
        durchschnittsgeschwindigkeit_kmh = 110.0  # Default: Autobahn
        if segment.oberflaeche and "unpaved" in segment.oberflaeche.lower():
            durchschnittsgeschwindigkeit_kmh = 50.0
        elif segment.oberflaeche and "paved" in segment.oberflaeche.lower():
            durchschnittsgeschwindigkeit_kmh = 100.0

        duration_h = laenge_km / durchschnittsgeschwindigkeit_kmh
        dauer = timedelta(hours=duration_h)

        segment_eta_list.append((segment, dauer))

    return segment_eta_list


async def _step_5_fetch_weather(  # noqa: PLR0913, PLR0917
    provider: WeatherProvider | None,
    route: Route,
    segment_eta_list: list[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
    previous_queries: list[WeatherQuery] | None = None,
    weather_detail: WeatherDetailLevel = "high",
) -> tuple[list[WeatherSample], list[WeatherQuery]]:
    """Step 5: Fetch weather data along the route at the current ETAs.

    For ``high`` (the default), the existing per-segment loop with
    refetch is preserved.  For ``low``/``medium``, ``fetch_weather_by_detail``
    is used to fetch a coarser set of weather points with a single provider
    call; the returned ``queries`` tuple element is ``[]`` because low/medium
    never refetch (the convergence loop is capped to 1 iteration).

    Args:
        provider: Weather provider. ``None`` falls back to
            ``FakeWeatherProvider``.
        route: The computed route (provides segment geometries).
        segment_eta_list: Segments with estimated travel time from departure.
        abfahrtszeit: Departure time of the entire trip.
        previous_queries: Queries from the previous iteration (same
            coordinates, old timestamps).  Only used when
            ``weather_detail == "high"`` and the provider supports
            ``refetch_weather``.
        weather_detail: Weather granularity level.  ``"high"`` uses the
            per-segment loop with refetch as today.  ``"low"`` and
            ``"medium"`` call :func:`fetch_weather_by_detail` once;
            ``"off"`` is handled upstream by passing ``provider=None``.

    Returns:
        Tuple of ``WeatherSample`` list (one per segment) and
        ``WeatherQuery`` list.  For ``"low"``/``"medium"`` the query
        list is empty because no refetch is needed.
    """
    if provider is None:
        provider = FakeWeatherProvider()

    if weather_detail in ("low", "medium"):
        samples = await fetch_weather_by_detail(
            provider, segment_eta_list, abfahrtszeit, weather_detail
        )
        return samples, []

    # high: exact today's code path
    queries: list[WeatherQuery] = []
    current_time = abfahrtszeit

    for segment, dauer in segment_eta_list:
        # Mittelpunkt des Segments als Abfragepunkt
        mitte_idx = len(segment.geometrie) // 2
        koordinate = segment.geometrie[mitte_idx]
        queries.append(WeatherQuery(koordinate=koordinate, zeitpunkt=current_time))
        current_time += dauer

    refetch_weather = getattr(provider, "refetch_weather", None)
    if previous_queries is not None and refetch_weather is not None:
        samples = await refetch_weather(previous_queries, queries)
    else:
        samples = await provider.fetch_weather(queries)

    return samples, queries


async def _step_6_construction_sites(
    construction_provider: ConstructionProvider | None,
    route: Route,
    countries: list[str] | None = None,
) -> list[ConstructionZone]:
    """Schritt 6: Baustellen entlang der Route einbeziehen.

    Optional: Für die Erstimplementierung kann mit leerer Liste gearbeitet werden.
    Ein `ConstructionProvider` (z. B. `FakeConstructionProvider`) kann übergeben
    werden, um Baustellendaten zu nutzen.
    """
    if construction_provider is None:
        return []

    countries_enum = [Land[land_code] for land_code in countries or []]
    return await construction_provider.fetch_construction_zones(route, countries_enum or [])


async def _step_7_calculate_segment_energy(  # noqa: PLR0913, PLR0917
    route: Route,
    route_segments: list[RouteSegment],
    segment_eta_list: list[tuple[RouteSegment, timedelta]],
    weather_samples: list[WeatherSample],
    vehicle_profile: VehicleProfile,
    construction_zones: list[ConstructionZone],
    abfahrtszeit: datetime,
    elevation_provider: ElevationProvider,
    elevation_points: list[ElevationPoint],
) -> list[SegmentEnergyResult]:
    """Schritt 7: Energieverbrauch je Segment berechnen.

    Berücksichtigt Wetterdaten, Höhenprofil, Wind und Baustellen-Tempolimits.
    """
    # Baustellen-Tempolimits pro Segment zuordnen
    segment_to_tempolimit: dict[int, int | None] = {}
    for zone in construction_zones:
        for segment_idx in zone.betroffene_segmente:
            if zone.tempolimit_kmh is not None:
                segment_to_tempolimit[segment_idx] = zone.tempolimit_kmh

    # WindComponents für alle Segmente berechnen
    wind_components = compute_wind_components_for_route(weather_samples, route_segments)

    # VehicleEnergyParameters erzeugen
    energy_params = VehicleEnergyParameters(
        masse_kg=vehicle_profile.masse_kg,
        cw_wert=vehicle_profile.cw_wert,
        stirnflaeche_m2=vehicle_profile.stirnflaeche_m2,
        rollwiderstandsbeiwert=vehicle_profile.rollwiderstandsbeiwert,
        batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
        nebenverbraucher_baseline_kw=vehicle_profile.nebenverbraucher_baseline_kw,
        reifentyp=vehicle_profile.reifentyp,
        dachbox=vehicle_profile.dachbox,
    )

    # Reales Höhenprofil-basiertes Gradient je Segment
    gradients = elevation_provider.calculate_segment_gradients(elevation_points, route)

    # Energieverbrauch pro Segment berechnen
    ergebnisse: list[SegmentEnergyResult] = []
    for idx, segment in enumerate(route_segments):
        wetter = weather_samples[idx] if idx < len(weather_samples) else None
        wind = (
            wind_components[idx]
            if idx < len(wind_components)
            else WindComponents(
                segment_index=segment.segment_index,
                gegenwind_ms=0.0,
                seitenwind_ms=0.0,
            )
        )
        gradient = gradients[idx] if idx < len(gradients) else None
        tempolimit = segment_to_tempolimit.get(idx)

        # Wenn wetter None ist, erstelle Dummy
        if wetter is None:
            wetter = WeatherSample(
                koordinate=segment.geometrie[0],
                zeitpunkt=abfahrtszeit + segment_eta_list[idx][1]
                if idx < len(segment_eta_list)
                else abfahrtszeit,
                temperatur_c=20.0,
                windgeschwindigkeit_ms=5.0,
                windrichtung_deg=180.0,
                niederschlag_mm=0.0,
                schneefall_cm=0.0,
                luftdruck_hpa=1013.25,
                luftfeuchtigkeit_pct=60.0,
                globalstrahlung_wm2=400.0,
                bewoelkung_pct=20.0,
            )

        ergebnis = calculate_segment_consumption(
            segment=segment,
            gradient=gradient,  # type: ignore[arg-type]
            wetter=wetter,
            wind=wind,
            fahrzeug_params=energy_params,
            baustellen=construction_zones if construction_zones else None,
            tempolimit_override_kmh=float(tempolimit) if tempolimit else None,
        )
        ergebnisse.append(ergebnis)

    return ergebnisse


async def _step_8_optimize_charging_plan(  # noqa: PLR0913, PLR0917
    route: Route,
    segment_energy: list[SegmentEnergyResult],
    vehicle_profile: VehicleProfile,
    start_soc_pct: float,
    ziel_soc_pct: float,
    construction_zones: list[ConstructionZone],
    abfahrtszeit: datetime,
    elevation_provider: ElevationProvider,
    elevation_points: list[ElevationPoint],
    zwischenstopps: list[Waypoint] | None = None,
    charging_provider: ChargingStationProvider | None = None,
    ladedauer_vorgaben: dict[str, int] | None = None,
    faehr_zeitfenster: dict[int, tuple[int, datetime, datetime]] | None = None,
    mindest_ankunfts_soc_pct: float = 5.0,
    mindest_ladezeit_s: int = 600,
) -> ChargingPlan:
    """Schritt 8: Optimalen Ladeplan bestimmen.

    Nutzt `create_networkx_optimizer()` als Prototyp-Optimizer.
    """
    optimizer = create_networkx_optimizer()

    # Hinweis: `OptimizationConstraints` kennt nur `min_soc_pct`, `ziel_soc_pct`,
    # `max_etappenlaenge_km`, `sicherheitsreserve_pct`, `max_ladezeit_s`
    # (siehe optimization/models.py). Frühere Aufrufe übergaben zusätzlich
    # `start_soc_pct`, `max_ladepausen`, `min_ladezeit_min`, `max_ladezeit_min`
    # - nicht existierende Feldnamen, die Pydantic per Default still verwirft
    # (kein Validierungsfehler), wodurch diese "Constraints" wirkungslos
    # blieben. `start_soc_pct` wird bereits direkt an `optimizer.optimize()`
    # übergeben; `max_ladezeit_min=60` entspricht dem echten Feld
    # `max_ladezeit_s` (in Sekunden).
    constraints = OptimizationConstraints(
        ziel_soc_pct=ziel_soc_pct,
        max_ladezeit_s=3600,
        mindest_ankunfts_soc_pct=mindest_ankunfts_soc_pct,
        mindest_ladezeit_s=mindest_ladezeit_s,
    )

    # Ladeinfrastruktur entlang der Route abrufen (Fake-Provider für Tests).
    # Suchradius bewusst 25 km statt der Straßenbreite (nicht 1-2 km): auf
    # Fernstrecken durch duenner mit Superchargern erschlossene Regionen
    # (z. B. laendliche Schwedenrouten abseits von E4/E6) liegt der naechste
    # Supercharger regelmaessig 10-25 km abseits der von GraphHopper gewaehlten
    # Fahrbahn - ein zu enger Radius liefert dort GAR KEINEN Kandidaten fuer
    # ein ganzes Segment, wodurch `NetworkXOptimizer.optimize()` faelschlich
    # "Kein erreichbarer Zielknoten gefunden" wirft, obwohl die Strecke mit
    # einem realistischen Ladestopp-Abstecher fahrbar ist (siehe
    # Repro: Gummersbach -> Hagfors kommun, Schweden - der einzige Kandidat
    # in der Luecke, Ulricehamn, liegt ca. 25 km von der Route entfernt).
    # 25 km ist bewusst grosszuegig (deckt auch den 20 km entfernten
    # Jönköping-Supercharger ab) und trotzdem klein genug, um die
    # Zustandsgraph-Groesse (siehe unten) nicht unnoetig aufzublaehen.
    charging_provider = charging_provider or FakeChargingStationProvider()
    stations_dict = await charging_provider.get_stations_along_route(route, search_radius_km=25.0)
    # `get_stations_along_route()` mappt pro (feingranularem) Segment die
    # Stationen im Suchradius - bei sehr kurzen Segmenten (z. B. ein Segment
    # pro GraphHopper-Polyline-Punktpaar, oft <200 m) liegt dieselbe
    # physische Station meist innerhalb des Suchradius mehrerer
    # aufeinanderfolgender Segmente und taucht entsprechend oft doppelt auf.
    # Ohne Deduplizierung nach `station_id` würde der Optimierer dieselbe
    # Station an vielen benachbarten Segment-Indizes als eigene Lade-
    # Gelegenheit modellieren, was den Zustandsgraphen unnötig aufbläht
    # (siehe docs/plans/07-optimization.md) und die Suche stark verlangsamt.
    charging_stations: list[ChargingStation] = []
    seen_station_ids: set[str] = set()
    for station_list in stations_dict.values():
        for station in station_list:
            if station.station_id in seen_station_ids:
                continue
            seen_station_ids.add(station.station_id)
            charging_stations.append(station)

    waypoints = list(zwischenstopps) if zwischenstopps else []

    # Reales Höhenprofil-basiertes Gradient je Segment
    gradients = elevation_provider.calculate_segment_gradients(elevation_points, route)

    return optimizer.optimize(
        route=route,
        segments=route.segments,
        gradients=gradients,
        energy_results=segment_energy,
        charging_stations=charging_stations,
        waypoints=waypoints,
        vehicle_profile=vehicle_profile,
        constraints=constraints,
        start_soc_pct=start_soc_pct,
        abfahrtszeit=abfahrtszeit,
        ladedauer_vorgaben=ladedauer_vorgaben,
        faehr_zeitfenster=faehr_zeitfenster,
    )


def _step_9_update_eta(
    segment_eta_list: list[tuple[RouteSegment, timedelta]],
    charging_plan: ChargingPlan,
) -> list[tuple[RouteSegment, timedelta]]:
    """Schritt 9: ETA je Segment mit tatsächlicher Fahr-/Ladezeit aktualisieren.

    Fester Iterationsschritt genügt für diese Orchestrierungsebene;
    echte Iterationsschleife ist bereits konzeptionell in optimization/weather vorgesehen.
    """
    # Einfacher Aktualisierungsschritt: Ladezeiten zu den ETA-Werten addieren
    neue_eta_liste: list[tuple[RouteSegment, timedelta]] = []

    ladezeiten_pro_segment: dict[int, timedelta] = {}
    for stop in charging_plan.ladehalte:
        segment_idx = stop.segment_index
        ladezeit = timedelta(seconds=stop.geschaetzte_ladedauer_s)
        ladezeiten_pro_segment[segment_idx] = ladezeit

    # `segment_idx` per `enumerate()` statt `route.segments.index(segment)`:
    # `segment_eta_list` wird in `_step_4_estimate_initial_eta()` durch
    # Iteration über `route.segments` IN DERSELBEN REIHENFOLGE aufgebaut (ein
    # Tupel pro Segment, keine Filterung/Umsortierung) - der Listenindex
    # entspricht also bereits exakt dem Segment-Index. `.index()` würde
    # stattdessen für JEDES Segment eine LINEARE Suche mit tiefer Pydantic-
    # Objektgleichheit über ALLE Segmente durchführen (O(n²) mit teurem
    # Vergleich statt O(n)) - bei feingranularen Routen (tausende Segmente,
    # z. B. ein Segment pro GraphHopper-Polyline-Punktpaar) ein spürbarer,
    # zudem komplett unnötiger Kostenfaktor.
    for segment_idx, (segment, urspruengliche_dauer) in enumerate(segment_eta_list):
        ladezeit = ladezeiten_pro_segment.get(segment_idx, timedelta())
        new_duration = urspruengliche_dauer + ladezeit
        neue_eta_liste.append((segment, new_duration))

    return neue_eta_liste


def _find_bracket_points(
    route: Route, segment_index: int, margin_m: float = 3000.0
) -> tuple[int, int]:
    """Findet zwei Punkte auf `route.geometrie` deutlich VOR/NACH `segment_index`.

    Ein Detour-Request mit `start == ziel` (derselbe Punkt) ist fuer
    GraphHopper richtungsmehrdeutig: der Router snappt den Punkt auf die
    naeheliegende Fahrbahn OHNE zu wissen, in welche Richtung die Reise
    eigentlich verlaeuft, und kann dadurch an der richtigen Ausfahrt vorbei
    bis zur naechsten fahren muessen, nur um zu wenden. Zwei
    UNTERSCHIEDLICHE, bereits auf der Hauptroute in korrekter Fahrtrichtung
    liegende Punkte (mindestens `margin_m` vor bzw. nach dem eigentlichen
    Abzweigpunkt) legen die Fahrtrichtung dagegen von vornherein eindeutig
    fest - kein Heading-Parameter noetig. `margin_m` muss dabei grosszuegig
    genug sein, um eine tatsaechlich nutzbare Autobahn-Ausfahrt in beide
    Richtungen einzuschliessen: bei zu knappem Rand (empirisch getestet mit
    500 m) landen beide Klammerpunkte oft VOR der naechsten echten Ausfahrt,
    wodurch GraphHopper einen Umweg von mehreren Kilometern ueber die
    naechstgelegene Ausfahrt UND wieder zurueck einschlagen muss, statt der
    kurzen, direkten Anbindung zur Ladestation - live gegen den Projekt-
    GraphHopper-Server verifiziert (Detour/Luftlinie-Verhaeltnis sank von bis
    zu 20x bei 500 m auf ca. 1.2-2x bei 3000 m).

    Returns:
        (vor_index, nach_index): Indizes in `route.geometrie`.
    """
    last_index = len(route.geometrie) - 1
    segment_index = min(segment_index, len(route.segments) - 1)

    vor_index = segment_index
    distanz_zurueck = 0.0
    while vor_index > 0 and distanz_zurueck < margin_m:
        vor_index -= 1
        distanz_zurueck += route.segments[vor_index].laenge_m

    after_index = segment_index
    distanz_vor = 0.0
    while after_index < last_index and distanz_vor < margin_m:
        distanz_vor += route.segments[after_index].laenge_m
        after_index += 1

    return vor_index, after_index


async def _step_route_charging_detours(
    routing_provider: RoutingProvider | None,
    route: Route,
    charging_plan: ChargingPlan,
    abfahrtszeit: datetime,
    fahrzeugprofil: VehicleProfile,
) -> dict[int, LadehaltDetour]:
    """Schritt 8b: Routet fuer jeden Ladehalt eine echte Hin-und-zurueck-Verbindung.

    GraphHopper kennt Ladestationen nicht als Wegpunkte der Hauptroute - die
    Stationswahl erfolgt erst NACH der Routenberechnung durch den Optimierer
    (`_step_8_optimize_charging_plan`). Deshalb zwei separate, kleine Routing-
    Aufrufe pro Ladehalt (typischerweise 0-3 pro Reise, siehe `LadehaltDetour`)
    statt eines gemeinsamen Multi-Waypoint-Aufrufs, der die Segmentierung/
    Energieberechnung der bereits abgeschlossenen Schritte 2-7 invalidieren
    wuerde: ein Hinweg-Bein (Klammerpunkt VOR -> Station) und ein Rueckweg-
    Bein (Station -> Klammerpunkt NACH), statt eines einzelnen Via-Punkt-
    Requests. Das liefert den exakten Index, an dem die Station erreicht
    wird (`LadehaltDetour.station_index` = letzter Punkt des Hinwegs), statt
    ihn ueber eine Naechster-Punkt-Heuristik auf der kombinierten Geometrie zu
    schaetzen - bei Autobahnkreuzen mit mehreren nah beieinander liegenden
    Rampen liefert die Heuristik sonst einen falschen Split und der SoC-
    Sprung beim Laden wird in der Kartendarstellung an der falschen Stelle
    (oder ueber die gesamte Rueckfahrt verschmiert) gezeigt. Start-/Zielpunkt
    der beiden Beine sind bewusst zwei unterschiedliche, auf der Hauptroute
    liegende Klammerpunkte statt desselben Abzweigpunkts (siehe
    `_find_bracket_points`), um Richtungsmehrdeutigkeit bei GraphHopper zu
    vermeiden.

    Returns:
        dict von `id()` des `ChargingStop`-Objekts (Ladehalt) -> `LadehaltDetour`.
        Ein Ladehalt fehlt im Ergebnis, wenn GraphHopper fuer eines der beiden
        Beine keine Route liefern konnte (`ChargingStopSummary.detour_geometrie`
        bleibt dann leer - der Ladehalt selbst bleibt gueltig, nur ohne
        Kartengeometrie fuer den Abstecher).
    """
    provider = routing_provider or FakeRoutingProvider()

    # 1. Build all routing requests (hinweg + rueckweg per stop)
    tasks: list[
        tuple[
            int,
            int,
            ChargingStop,
            TripRequest,
            TripRequest,
        ]
    ] = []
    for stop_idx, ladehalt in enumerate(charging_plan.ladehalte):
        vor_index, after_index = _find_bracket_points(route, ladehalt.segment_index)
        hinweg_anfrage = TripRequest(
            start=route.geometrie[vor_index],
            ziel=ladehalt.station.coordinate,
            abfahrtszeit=abfahrtszeit,
            fahrzeugprofil=fahrzeugprofil,
        )
        rueckweg_anfrage = TripRequest(
            start=ladehalt.station.coordinate,
            ziel=route.geometrie[after_index],
            abfahrtszeit=abfahrtszeit,
            fahrzeugprofil=fahrzeugprofil,
        )
        tasks.append((stop_idx, id(ladehalt), ladehalt, hinweg_anfrage, rueckweg_anfrage))

    if not tasks:
        return {}

    # 2. Fan out all routing coroutines concurrently
    coros: list[Coroutine[None, None, Route]] = []
    for _, _, _, hinweg, rueckweg in tasks:
        coros.append(provider.berechne_route(hinweg))
        coros.append(provider.berechne_route(rueckweg))

    results = await asyncio.gather(*coros, return_exceptions=True)

    # 2b. Distinguish httpx.HTTPError (skip/continue, preserving old behaviour)
    #     from other exception types (re-raise, restoring old propagate-to-500/502
    #     contract) — before f8e76fc only httpx.HTTPError was caught.
    non_http_exceptions: list[BaseException] = []
    for r in results:
        if isinstance(r, BaseException) and not isinstance(r, httpx.HTTPError):
            non_http_exceptions.append(r)
    if non_http_exceptions:
        raise non_http_exceptions[0]

    # 3. Reassemble results per stop (2 results per stop: hinweg, rueckweg)
    detouren: dict[int, LadehaltDetour] = {}
    for stop_idx, stop_id, ladehalt, _, _ in tasks:
        start = stop_idx * 2
        hinweg_result, rueckweg_result = results[start], results[start + 1]

        # Skip this stop if either leg failed
        if isinstance(hinweg_result, BaseException) or isinstance(rueckweg_result, BaseException):
            continue

        hinweg_route = hinweg_result
        rueckweg_route = rueckweg_result
        station_index = len(hinweg_route.geometrie) - 1
        detouren[stop_id] = LadehaltDetour(
            geometrie=hinweg_route.geometrie + rueckweg_route.geometrie[1:],
            route_index_vor=_find_bracket_points(route, ladehalt.segment_index)[0],
            route_index_nach=_find_bracket_points(route, ladehalt.segment_index)[1],
            station_index=station_index,
        )

    return detouren


def _segment_index_for_coordinate(koordinate: Coordinate, segments: list[RouteSegment]) -> int:
    """Segment, dessen Ende der gegebenen Koordinate am nächsten liegt (haversine)."""
    best_idx, best_dist = 0, float("inf")
    for idx, seg in enumerate(segments):
        dist = haversine_distance_m(koordinate, seg.geometrie[-1])
        if dist < best_dist:
            best_dist, best_idx = dist, idx
    return best_idx


def _with_derived_wait_time(
    zwischenstopps: list[Waypoint],
    segment_eta_list: list[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[Waypoint]:
    """Leitet aus einem optionalen `geplante_abfahrt` je Wegpunkt eine effektive Wartezeit ab.

    Heuristik: einmalige Annäherung anhand der initialen ETA-Schätzung, keine
    iterative Konvergenz — konsistent mit dem bestehenden Ansatz der iterativen
    ETA/Wetter-Schätzung an anderer Stelle im Modul, hier aber bewusst einstufig.
    """
    segments = [seg for seg, _ in segment_eta_list]
    kumuliert: list[timedelta] = []
    laufend = timedelta()
    for _, dauer in segment_eta_list:
        laufend += dauer
        kumuliert.append(laufend)
    ergebnis: list[Waypoint] = []
    for wp in zwischenstopps:
        if wp.geplante_abfahrt is None:
            ergebnis.append(wp)
            continue
        seg_idx = _segment_index_for_coordinate(wp.koordinate, segments)
        geschaetzte_ankunft = abfahrtszeit + kumuliert[seg_idx]
        abgeleitete_wartezeit = max(timedelta(), wp.geplante_abfahrt - geschaetzte_ankunft)
        bestehende = wp.aufenthaltsdauer or timedelta()
        ergebnis.append(
            wp.model_copy(update={"aufenthaltsdauer": max(bestehende, abgeleitete_wartezeit)})
        )
    return ergebnis


def _bbox_center(sw: Coordinate, no: Coordinate) -> Coordinate:
    """Mittelpunkt einer (lat, lon)-Bounding-Box."""
    return ((sw[0] + no[0]) / 2.0, (sw[1] + no[1]) / 2.0)


def _match_ferry_time_window(
    detected_ferries: list[FaehrSegment],
    faehr_zeitfenster: list[FaehrZeitfenster],
) -> list[FaehrSegment]:
    """Reichert erkannte Fährverbindungen um Nutzer-Zeitfenster an.

    Identifikation über `name` (bei mehrdeutigem Namen über die nächste
    Bounding-Box-Mitte) - analog zur Identifikationskonvention von
    `FerryExclusion`. Nicht (mehr) passende Zeitfenster (Name in der aktuellen
    Route nicht mehr vorhanden) werden stillschweigend ignoriert - konsistent
    mit dem selbstkorrigierenden Ansatz der Fährvermeidung (siehe
    docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md).
    """
    ergebnis: list[FaehrSegment] = []
    for faehre in detected_ferries:
        kandidaten = [fz for fz in faehr_zeitfenster if fz.name == faehre.name]
        if not kandidaten:
            ergebnis.append(faehre)
            continue
        faehre_mitte = _bbox_center(faehre.bbox_sw, faehre.bbox_no)
        beste = min(
            kandidaten,
            key=lambda fz: haversine_distance_m(faehre_mitte, _bbox_center(fz.bbox_sw, fz.bbox_no)),
        )
        ergebnis.append(
            faehre.model_copy(update={"abfahrt": beste.abfahrt, "ankunft": beste.ankunft})
        )
    return ergebnis


# =============================================================================


_logger = logging.getLogger(__name__)


@contextmanager
def _log_step(
    step_name: str,
    iteration: int | None = None,
) -> Iterator[None]:
    """Context manager that logs step start/end with elapsed duration.

    Logs INFO on entry and INFO with elapsed duration on successful exit.
    On exception, logs WARNING with elapsed duration and re-raises.
    Works with both sync and async calls (wrap ``await step(...)`` in ``with``).

    Args:
        step_name: Human-readable name for the pipeline step.
        iteration: Iteration number (0-based) inside the convergence loop,
            or ``None`` for steps outside the loop.
    """
    iteration_tag = f" (iteration {iteration})" if iteration is not None else ""
    _logger.info("Pipeline step '%s'%s \u2014 start", step_name, iteration_tag)
    t0 = time.perf_counter()
    try:
        yield
    except BaseException as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1_000
        _logger.warning(
            "Pipeline step '%s'%s \u2014 FAILED after %.1f ms: %s",
            step_name,
            iteration_tag,
            elapsed_ms,
            exc,
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1_000
    _logger.info(
        "Pipeline step '%s'%s \u2014 done in %.1f ms",
        step_name,
        iteration_tag,
        elapsed_ms,
    )


# Kernfunktion: Orchestrierung aller 11 Schritte
# =============================================================================


async def create_trip_simulation(  # noqa: PLR0913, PLR0917, PLR0915
    request_dict: dict[str, object],
    routing_provider: RoutingProvider | None = None,
    elevation_provider: ElevationProvider | None = None,
    weather_provider: WeatherProvider | None = None,
    construction_provider: ConstructionProvider | None = None,
    charging_provider: ChargingStationProvider | None = None,
    start_soc_pct: float = 80.0,
    destination_soc_pct: float = 20.0,
    mindest_ankunfts_soc_pct: float = 5.0,
    mindest_ladezeit_s: int = 600,
    max_iterations: int = 3,
    convergence_threshold_minutes: float = 30.0,
    route_observer: Callable[[Route], None] | None = None,
    ferry_observer: Callable[[list[FaehrSegment]], None] | None = None,
    weather_detail: WeatherDetailLevel = "high",
) -> TripSimulationResult:
    """Orchestrates the 11 data flow steps for trip planning.

    Args:
        request_dict: Dictionary with TripRequest data (parsed from API/CLI).
        routing_provider: Optional RoutingProvider (Default: FakeRoutingProvider).
        elevation_provider: Optional ElevationProvider (Default: FakeDataSource).
        weather_provider: Optional WeatherProvider (Default: FakeWeatherProvider).
        construction_provider: Optional ConstructionProvider (Default: FakeConstructionProvider).
        charging_provider: Optional ChargingStationProvider
            (Default: FakeChargingStationProvider).
        start_soc_pct: Starting state of charge in percent (Default: 80%).
        destination_soc_pct: Target state of charge in percent (Default: 20%).
        mindest_ankunfts_soc_pct: Minimum SoC allowed when arriving at a
            charging station, as opposed to the general safety-reserve floor
            elsewhere on the route (Default: 5%). See
            `OptimizationConstraints.mindest_ankunfts_soc_pct`.
        mindest_ladezeit_s: Minimum duration of a charging stop, if any
            charging happens there at all (Default: 600s / 10 min). See
            `OptimizationConstraints.mindest_ladezeit_s`.
        max_iterations: Max iterations for iterative ETA/weather convergence.
            Default: 3.
        convergence_threshold_minutes: Convergence threshold in minutes for early
            termination of the iterative loop. Default: 30.0.
        route_observer: Optional callback called immediately after step 1 (routing)
            with the computed route (see `create_trip_endpoint`).
        ferry_observer: Optional callback called immediately after step 1 with the
            detected ferries enriched with `request.faehr_zeitfenster` - same list
            used for optimizer input (see `create_trip_endpoint`).
        weather_detail: Weather granularity level.  ``"low"`` and ``"medium"``
            fetch weather once and never refetch, so charging-plan
            re-optimization against updated weather in later iterations would
            have no effect — the convergence loop is capped to 1 iteration for
            these levels.

    Returns:
        TripSimulationResult: Complete simulation result.

    Note on Fake Providers:
        All providers have Fake implementations as defaults, so the pipeline
        runs without external APIs (GraphHopper, Open-Meteo, DEM server).
        For production, the actual provider classes are used.
    """
    request = TripRequest.model_validate(request_dict)
    _pipeline_start = time.perf_counter()

    _logger.info(
        "Pipeline start: %d coordinate(s), start_soc=%.1f%%, "
        "destination_soc=%.1f%%, max_iterations=%d",
        len(request.start)
        + len(request.ziel)
        + sum(len(wp.koordinate) for wp in request.zwischenstopps),
        start_soc_pct,
        destination_soc_pct,
        max_iterations,
    )
    # 1. Create TripRequest

    # 2. Step 1: Calculate route
    with _log_step("route_calculate"):
        route = await _step_1_route_calculate(request, routing_provider)
        if route_observer is not None:
            route_observer(route)

    # Match user-specified ferry time windows against detected ferries
    detected_ferries = _match_ferry_time_window(erkenne_faehren(route), request.faehr_zeitfenster)
    if ferry_observer is not None:
        ferry_observer(detected_ferries)

    # 3. Step 2: Extract elevation profile
    if elevation_provider is None:
        elevation_provider = ElevationProvider(data_source=FakeDataSource())
    with _log_step("extract_elevation_profile"):
        elevation_points = await _step_2_extract_elevation_profile(route, elevation_provider)

    # 4. Step 3: Segment routing (already in route.segments)
    with _log_step("segment_route"):
        segments = _step_3_segment_route(route)

    # 5. Step 4: Initial ETA estimate
    with _log_step("estimate_initial_eta"):
        segment_eta_list = _step_4_estimate_initial_eta(route, request.abfahrtszeit)

    # Note: Derived waiting time is a cost factor for the optimizer, not a required
    # minimum stop duration - the A* path can bypass it if no SoC/charging need arises.
    waypoints_with_wait_time = _with_derived_wait_time(
        request.zwischenstopps, segment_eta_list, request.abfahrtszeit
    )

    # Prepare ferry time windows as optimizer input
    ferry_pins = {
        f.segment_index_start: (f.segment_index_end, f.abfahrt, f.ankunft)
        for f in detected_ferries
        if f.abfahrt is not None and f.ankunft is not None
    }
    charging_duration_map = {v.station_id: v.ladedauer_s for v in request.ladedauer_vorgaben}

    loop_max_iterations = 1 if weather_detail in ("low", "medium") else max_iterations
    # 10. Iterative ETA/weather convergence loop
    prev_segment_eta_list: list[tuple[RouteSegment, timedelta]] | None = None
    weather_queries: list[WeatherQuery] | None = None

    for iteration in range(loop_max_iterations):
        # Store previous iteration's ETA for convergence check
        if iteration > 0:
            prev_segment_eta_list = [(seg, eta) for seg, eta in segment_eta_list]

        # Fetch weather with updated ETA-based timestamps; from the 2nd
        # iteration onward, reuse the provider's cache for unchanged points
        # via refetch_weather (if supported) instead of a full refetch.
        with _log_step("fetch_weather", iteration):
            weather_samples, weather_queries = await _step_5_fetch_weather(
                weather_provider,
                route,
                segment_eta_list,
                request.abfahrtszeit,
                previous_queries=weather_queries,
                weather_detail=weather_detail,
            )

        # Construction sites (optional)
        with _log_step("construction_sites", iteration):
            construction_zones = await _step_6_construction_sites(
                construction_provider, route, ["DE", "DK", "SE"]
            )

        # Calculate energy consumption
        with _log_step("calculate_segment_energy", iteration):
            energy_results = await _step_7_calculate_segment_energy(
                route,
                segments,
                segment_eta_list,
                weather_samples,
                request.fahrzeugprofil,
                construction_zones,
                request.abfahrtszeit,
                elevation_provider,
                elevation_points,
            )

        # Optimize charging plan
        with _log_step("optimize_charging_plan", iteration):
            charging_plan = await _step_8_optimize_charging_plan(
                route,
                energy_results,
                request.fahrzeugprofil,
                start_soc_pct,
                destination_soc_pct,
                construction_zones,
                request.abfahrtszeit,
                elevation_provider,
                elevation_points,
                zwischenstopps=waypoints_with_wait_time,
                charging_provider=charging_provider,
                ladedauer_vorgaben=charging_duration_map,
                faehr_zeitfenster=ferry_pins,
                mindest_ankunfts_soc_pct=mindest_ankunfts_soc_pct,
                mindest_ladezeit_s=mindest_ladezeit_s,
            )

        # Update ETA with charging plan
        with _log_step("update_eta", iteration):
            segment_eta_list = _step_9_update_eta(segment_eta_list, charging_plan)

        # Recalculate detours based on the final charging plan
        with _log_step("charging_detours", iteration):
            charging_stop_detours = await _step_route_charging_detours(
                routing_provider,
                route,
                charging_plan,
                request.abfahrtszeit,
                request.fahrzeugprofil,
            )

        # Convergence check: compare with previous iteration's ETA
        if iteration > 0 and prev_segment_eta_list is not None:
            max_deviation_seconds = 0.0
            for (_, eta_prev), (_, eta_curr) in zip(
                prev_segment_eta_list, segment_eta_list, strict=True
            ):
                deviation = abs((eta_curr - eta_prev).total_seconds())
                max_deviation_seconds = max(max_deviation_seconds, deviation)

            # Early exit if converged
            if max_deviation_seconds < convergence_threshold_minutes * 60:
                break

    # 11. Step 10: Run simulation
    with _log_step("simulate_trip"):
        simulation_result = simulate_trip(
            route=route,
            charging_plan=charging_plan,
            segment_energy=energy_results,
            start_soc_pct=start_soc_pct,
            output_resolution_seconds=60,
            abfahrtszeit=request.abfahrtszeit,
            battery_capacity_kwh=request.fahrzeugprofil.batteriekapazitaet_kwh,
            charging_stop_detours=charging_stop_detours,
        )

    # 12. Step 11: Return result
    with _log_step("attach_charging_pricing"):
        simulation_result = _attach_charging_pricing(simulation_result, charging_provider)
    total_elapsed_ms = (time.perf_counter() - _pipeline_start) * 1_000
    _logger.info(
        "Pipeline complete: %d frame(s), %d stop(s), %.1f ms total",
        len(simulation_result.frames),
        len(simulation_result.charging_stops),
        total_elapsed_ms,
    )
    return simulation_result


def _attach_charging_pricing(
    simulation_result: TripSimulationResult,
    charging_provider: ChargingStationProvider | None,
) -> TripSimulationResult:
    """Step 12: attaches cached pricing to charging stops.

    Also queues stale/missing stations for a background pricing re-scrape.

    Runs at every route finalization (both `/trips` and the `ttp trips` CLI,
    which both call `create_trip_simulation`). For each charging stop actually
    used by this route:

    1. Reads cached pricing from the database (`TeslaChargingStationProvider.
       get_cached_pricing`) and, if available, selects the applicable
       Tesla-owner rate for the stop's arrival time (`select_owner_rate_for_
       time`), attaching `price_per_kwh`/`currency`/`estimated_cost`/
       `pricing_updated_utc` to the returned `ChargingStopSummary`.
    2. Queues the station for a pricing re-scrape (`enqueue_stations_for_
       pricing_refresh`) if its cached pricing is missing or older than
       `TeslaChargingStationProvider.PRICING_MAX_AGE` - fresh stations are
       left untouched to avoid unnecessary Tesla-API/WAF traffic. The actual
       re-scrape happens out-of-band (see the `charger scrape-pricing` CLI
       command), NEVER synchronously here: a curl-equivalent request per
       station is too slow and WAF-risky to run inline in the request/
       response cycle.

    Also computes `TripSimulationResult.total_charging_cost` (summed per
    currency, since a DE/DK/SE trip can span several) and
    `charging_stops_missing_pricing`.

    Only `TeslaChargingStationProvider` supports cached pricing (SQLite-
    backed) - other providers (`Fake`/`LocalFile`, used in tests and as
    defaults) leave stops unpriced, and this step becomes a no-op.

    Args:
        simulation_result: The simulation result produced by `simulate_trip`.
        charging_provider: The charging provider used for this simulation.

    Returns:
        `simulation_result` with priced `charging_stops` and populated
        `total_charging_cost`/`charging_stops_missing_pricing` (unchanged if
        there are no charging stops or `charging_provider` doesn't support
        cached pricing).
    """
    if not simulation_result.charging_stops or not isinstance(
        charging_provider, TeslaChargingStationProvider
    ):
        return simulation_result

    station_ids = [stop.station_id for stop in simulation_result.charging_stops]
    charging_provider.enqueue_stations_for_pricing_refresh(station_ids)

    priced_stops: list[ChargingStopSummary] = []
    totals_by_currency: dict[str, float] = {}
    missing_pricing = 0
    for stop in simulation_result.charging_stops:
        cached = charging_provider.get_cached_pricing(stop.station_id)
        rate = select_owner_rate_for_time(cached.tiers, stop.ankunftszeit)
        if rate is None:
            priced_stops.append(stop)
            missing_pricing += 1
            continue
        estimated_cost = round(stop.energie_geladen_kwh * rate.amount, 2)
        priced_stops.append(
            stop.model_copy(
                update={
                    "price_per_kwh": rate.amount,
                    "currency": rate.currency,
                    "estimated_cost": estimated_cost,
                    "pricing_updated_utc": cached.updated_utc,
                }
            )
        )
        totals_by_currency[rate.currency] = totals_by_currency.get(rate.currency, 0.0) + (
            estimated_cost
        )

    return simulation_result.model_copy(
        update={
            "charging_stops": priced_stops,
            "total_charging_cost": [
                ChargingCostByCurrency(currency=currency, amount=round(amount, 2))
                for currency, amount in sorted(totals_by_currency.items())
            ],
            "charging_stops_missing_pricing": missing_pricing,
        }
    )


# =============================================================================
# FastAPI-Endpunkt
# =============================================================================


logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Attaches a console handler to the `tripplanner` logger namespace.

    Without this, `uvicorn --reload` (see `run.sh`) never installs a handler
    for application loggers - only `uvicorn.*` loggers get one. Python's
    logging module then falls back to `logging.lastResort`, which only ever
    emits records at WARNING level or above, silently dropping every
    `logger.info(...)` pipeline-step log (see `_log_step`). The level is
    configurable via the `TRIPPLANNER_LOG_LEVEL` environment variable
    (default: `INFO`) so a slower/quieter deployment can raise it without a
    code change. Idempotent: safe to call multiple times (e.g. once per
    FastAPI TestClient lifespan cycle in tests) without installing duplicate
    handlers or duplicate log lines.
    """
    package_logger = logging.getLogger(__name__.split(".")[0])
    level_name = os.environ.get("TRIPPLANNER_LOG_LEVEL", "INFO")
    package_logger.setLevel(level_name)
    if not any(isinstance(h, logging.StreamHandler) for h in package_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        package_logger.addHandler(handler)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Verwaltet den Lebenszyklus der prozessweiten Provider-Ressourcen.

    Der GraphHopper-HTTP-Client und der Tesla-Supercharger-DB-Zugriff werden
    einmalig beim Start erzeugt (Connection-/Verbindungs-Pooling über alle
    Requests hinweg) statt pro Request neu aufgebaut zu werden. Die
    GraphHopper-Basis-URL ist über die Umgebungsvariable `GRAPHHOPPER_URL`
    konfigurierbar (Default: `http://localhost:8989`, siehe README.md).
    """
    _configure_logging()
    providers = await build_production_providers()
    app.state.providers = providers
    try:
        yield
    finally:
        await close_production_providers(providers)


app = FastAPI(title="Tesla Trip Planner API", version="0.1.0", lifespan=_lifespan)


def get_routing_provider(request: Request) -> RoutingProvider:
    """FastAPI-Dependency: liefert den produktiven RoutingProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `GraphHopperClient` für echtes Straßenrouting über OSM-Daten. In Tests via
    `app.dependency_overrides[get_routing_provider]` durch `FakeRoutingProvider`
    ersetzbar (siehe AGENTS.md: keine Live-Calls externer Datenquellen in
    Unit-Tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.routing


def get_charging_provider(request: Request) -> ChargingStationProvider:
    """FastAPI-Dependency: liefert den produktiven ChargingStationProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `TeslaChargingStationProvider` (SQLite-DB `data/tesla_superchargers.db`,
    siehe README.md) für echte Supercharger-Standorte. In Tests via
    `app.dependency_overrides[get_charging_provider]` durch eine
    `FakeChargingStationProvider`-Instanz mit angepassten Stationen ersetzbar
    (siehe AGENTS.md: keine Live-Calls externer Datenquellen in Unit-Tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.charging


def get_elevation_provider(request: Request) -> ElevationProvider:
    """FastAPI-Dependency: returns the production ElevationProvider for `/trips`.

    Uses the `ElevationProvider` created in `_lifespan` for elevation data.
    In tests, can be replaced via `app.dependency_overrides[get_elevation_provider]`
    with `FakeDataSource`.
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.elevation_provider


def get_weather_provider(request: Request) -> WeatherProvider:
    """FastAPI-Dependency: returns the production WeatherProvider for `/trips`.

    Uses the `OpenMeteoProvider` created in `_lifespan` for weather data.
    In tests, can be replaced via `app.dependency_overrides[get_weather_provider]`
    with `FakeWeatherProvider` (see AGENTS.md: no live calls to external data sources
    in unit tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.weather


def get_construction_provider(request: Request) -> ConstructionProvider:
    """FastAPI-Dependency: liefert den produktiven ConstructionProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `ConstructionProvider` für Baustellendaten. In Tests via
    `app.dependency_overrides[get_construction_provider]` durch
    `FakeConstructionProvider` ersetzbar.
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.construction


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health-Check-Endpunkt.

    Ermöglicht dem Frontend zu prüfen, ob das Backend erreichbar ist.
    """
    return {"status": "ok"}


# ── Supercharger API ─────────────────────────────────────────────────────


class SuperchargerStationAPI(BaseModel):
    """API-Response-Modell fuer eine Supercharger-Station."""

    slug: str = Field(..., description="tesla_location_id (location_url_slug)")
    name: str = Field(..., description="Standortname")
    latitude: float = Field(..., description="WGS84 Breitengrad")
    longitude: float = Field(..., description="WGS84 Laengengrad")
    country: str = Field(..., description="ISO-2 Laendercode")
    total_stalls: int = Field(..., description="Anzahl Ladeplaetze")
    power_kilowatt: int = Field(..., description="Maximale Ladeleistung kW")
    status: str = Field(..., description="Betriebsstatus (OPEN, TEMP_CLOSED, ...)")
    stalls_v2: int = Field(default=0)
    stalls_v3: int = Field(default=0)
    stalls_v3_ultra: int = Field(default=0)
    stalls_v4: int = Field(default=0)
    ist_24_7: bool = Field(default=True, description="24/7 zugaenglich")
    date_opened: str | None = Field(default=None, description="Eroeffnungsdatum")


class SuperchargerStationDetailAPI(SuperchargerStationAPI):
    """Detaillierte API-Response fuer eine Supercharger-Station."""

    connector_types: list[str] = Field(default_factory=list)
    last_updated_utc: str = Field(..., description="Letzte Aktualisierung ISO-8601")
    access_type: str | None = Field(default=None)
    open_to_non_tesla: bool = Field(default=False)


def _station_to_api(station: ChargingStation) -> SuperchargerStationAPI:
    """Wandelt ein ChargingStation-Modell in das API-Response-Modell um."""
    return SuperchargerStationAPI(
        slug=station.station_id,
        name=station.name.replace("Tesla Supercharger - ", ""),
        latitude=station.coordinate[0],
        longitude=station.coordinate[1],
        country=station.country,
        total_stalls=sum(station.stalls.values()) if station.stalls else 0,
        power_kilowatt=int(station.max_ladeleistung_kw),
        status=station.status,
        stalls_v2=station.stalls.get(StallType.V2, 0) if station.stalls else 0,
        stalls_v3=station.stalls.get(StallType.V3, 0) if station.stalls else 0,
        stalls_v3_ultra=station.stalls.get(StallType.V3_ULTRA, 0) if station.stalls else 0,
        stalls_v4=station.stalls.get(StallType.V4, 0) if station.stalls else 0,
        ist_24_7=station.ist_24_7 if hasattr(station, "ist_24_7") else True,
        date_opened=None,
    )


@app.get("/superchargers")
async def list_superchargers(
    country: str | None = None,
) -> list[SuperchargerStationAPI]:
    """Listet alle Supercharger-Stationen aus der Datenbank.

    Args:
        country: Optionaler ISO-2 Laenderfilter.

    Returns:
        Liste von SuperchargerStationAPI.
    """
    stations = get_all_charging_stations()
    if country:
        stations = [s for s in stations if s.country == country]
    return [_station_to_api(s) for s in stations]


@app.get("/superchargers/{slug}")
async def get_supercharger_detail(
    slug: str,
) -> SuperchargerStationAPI:
    """Liefert Details zu einer Supercharger-Station.

    Args:
        slug: tesla_location_id (location_url_slug).

    Returns:
        SuperchargerStationAPI.

    Raises:
        HTTPException: 404 wenn Station nicht gefunden.
    """
    stations = get_all_charging_stations()
    station = next(
        (s for s in stations if _station_to_api(s).slug == slug),
        None,
    )
    if station is None:
        raise HTTPException(status_code=404, detail="Station nicht gefunden")
    return _station_to_api(station)


@app.post("/superchargers/{slug}/refresh")
async def refresh_supercharger(slug: str) -> SuperchargerStationAPI:
    """Aktualisiert eine Supercharger-Station mit frischen Daten von der Tesla API.

    Ruft die Tesla API server-seitig ueber System-curl auf (siehe
    `TeslaLocationsClient`), der per SecureTransport-TLS-Fingerprint wie ein
    echter Browser behandelt wird und so den Akamai-WAF-Block umgeht, dem
    Python-HTTP-Clients (httpx) unterliegen. Ein direkter Cross-Origin-Fetch
    aus dem Frontend-JS ist keine Alternative: die Tesla-API liefert keine
    Access-Control-Allow-Origin-Header, wodurch der Browser das Lesen der
    Antwort unabhaengig vom WAF-Status verweigert.

    Args:
        slug: tesla_location_id (location_url_slug).

    Returns:
        Aktualisierte SuperchargerStationAPI.

    Raises:
        HTTPException: 404 wenn Station unbekannt oder Tesla-API keine Daten
            liefert, 502 bei WAF-Block, Netzwerkfehlern oder wenn die
            Tesla-Antwort nicht auf ChargingStation abgebildet werden kann
            (z. B. Land ausserhalb DE/DK/SE).
    """
    provider = TeslaChargingStationProvider()
    try:
        station = await provider.refresh_single_station(slug)
    except TeslaLocationsClient.CurlError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Tesla API nicht erreichbar: {e}",
        ) from e
    except ValidationError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Tesla-Antwort konnte nicht verarbeitet werden: {e}",
        ) from e
    finally:
        # Verbindung deterministisch schliessen: verhindert, dass eine
        # offene Transaktion (z. B. nach einem Fehler) den SQLite-
        # Schreibsperren-Lock fuer nachfolgende Requests blockiert.
        provider._db.close()
    if station is None:
        raise HTTPException(
            status_code=404,
            detail="Station nicht gefunden oder Tesla-API lieferte keine Daten",
        )
    return _station_to_api(station)


class WaypointAPI(BaseModel):
    """API-Request für Zwischenstopp."""

    koordinate: tuple[float, float] = Field(..., description="(lat, lon) Koordinate in Dezimalgrad")
    aufenthaltsdauer_s: int | None = Field(
        None, ge=0, description="Mindestaufenthaltsdauer in Sekunden"
    )
    geplante_abfahrt: str | None = Field(
        None,
        description="Gewünschter frühester Abfahrtszeitpunkt (ISO-8601)",
    )


class FaehrAusschlussAPI(BaseModel):
    """API-Request für eine zu vermeidende, zuvor erkannte Fährverbindung."""

    name: str = Field(..., description="Anzeigename der Fährverbindung")
    bbox_sw: tuple[float, float] = Field(..., description="Südwest-Ecke der Bounding Box")
    bbox_no: tuple[float, float] = Field(..., description="Nordost-Ecke der Bounding Box")


class FaehrZeitfensterAPI(BaseModel):
    """API-Request für einen vorgegebenen Fährfahrplan (Abfahrt/Ankunft)."""

    name: str = Field(..., description="Anzeigename der Fährverbindung")
    bbox_sw: tuple[float, float] = Field(..., description="Südwest-Ecke der Bounding Box")
    bbox_no: tuple[float, float] = Field(..., description="Nordost-Ecke der Bounding Box")
    abfahrt: str = Field(..., description="Vorgegebene Abfahrtszeit (ISO-8601)")
    ankunft: str = Field(..., description="Vorgegebene Ankunftszeit (ISO-8601)")


class LadedauerVorgabeAPI(BaseModel):
    """API-Request für eine vom Nutzer vorgegebene feste Ladedauer an einer Station."""

    station_id: str = Field(..., min_length=1, description="Eindeutige ID der Ladestation")
    ladedauer_s: int = Field(..., ge=0, description="Vorgegebene feste Ladedauer in Sekunden")


class TripRequestAPI(BaseModel):
    """API-Request für /trips-Endpunkt."""

    start: tuple[float, float] = Field(..., description="(lat, lon) Startkoordinate")
    ziel: tuple[float, float] = Field(..., description="(lat, lon) Zielkoordinate")
    zwischenstopps: list[WaypointAPI] = Field(
        default_factory=list, description="Liste von Zwischenstopps"
    )
    abfahrtszeit: str = Field(
        ..., description="ISO-8601 Abfahrtszeit (z. B. '2026-08-15T08:30:00')"
    )
    fahrzeugprofil: VehicleProfile = Field(..., description="Physikalisches Fahrzeugprofil")
    start_soc_pct: float = Field(80.0, ge=0.0, le=100.0, description="Start-SoC in Prozent")
    ziel_soc_pct: float = Field(20.0, ge=0.0, le=100.0, description="Ziel-SoC in Prozent")
    mindest_ankunfts_soc_pct: float = Field(
        5.0,
        ge=0.0,
        le=100.0,
        description=(
            "Minimal zulässiger SoC beim Ankommen an einer Ladestation "
            "(darf niedriger sein als die allgemeine Sicherheitsreserve auf "
            "offener Strecke, da dort garantiert nachgeladen wird)"
        ),
    )
    mindest_ladezeit_s: int = Field(
        600,
        ge=0,
        le=1800,
        description=(
            "Minimale Dauer eines einzelnen Ladevorgangs in Sekunden, wenn "
            "geladen wird (verhindert unnötig kurze Ladehalte, ohne den "
            "Ladehalt an sich zu erzwingen)"
        ),
    )
    praeferenzen: dict[str, object] = Field(default_factory=dict, description="Nutzerpräferenzen")
    alle_faehren_vermeiden: bool = Field(
        default=False, description="Falls True, werden alle Fährverbindungen vermieden"
    )
    vermiedene_faehren: list[FaehrAusschlussAPI] = Field(
        default_factory=list,
        description=(
            "Liste spezifischer, zuvor erkannter Fährverbindungen, die vermieden werden sollen"
        ),
    )
    faehr_zeitfenster: list[FaehrZeitfensterAPI] = Field(
        default_factory=list,
        description=(
            "Vom Nutzer vorgegebene Abfahrts-/Ankunftszeiten für zuvor erkannte Fährverbindungen"
        ),
    )
    ladedauer_vorgaben: list[LadedauerVorgabeAPI] = Field(
        default_factory=list,
        description="Vom Nutzer vorgegebene feste Ladedauern für einzelne Ladehalte",
    )
    wetter_detailgrad: Literal["off", "low", "medium", "high"] = Field(
        default="high",
        description=(
            "Weather detail level: 'off', 'low', 'medium', or 'high'. "
            "'low'/'medium' use coarser weather resolution and complete faster; "
            "'off' skips weather entirely (placeholder values); 'high' uses "
            "per-segment weather (default, exact behavior matching the legacy "
            "wetter_beruecksichtigen=True)."
        ),
    )

    @model_validator(mode="before")
    @staticmethod
    def _map_legacy_wetter_boolean(data: dict[str, object]) -> dict[str, object]:
        """Map legacy wetter_beruecksichtigen boolean to wetter_detailgrad.

        Handles both the old field name (wetter_beruecksichtigen: bool) and
        defensively: the new field name with a boolean value from clients
        that send the new field with the old type.
        """
        if not isinstance(data, dict):
            return data

        # Legacy field name: wetter_beruecksichtigen -> wetter_detailgrad
        if "wetter_beruecksichtigen" in data and "wetter_detailgrad" not in data:
            raw = data.pop("wetter_beruecksichtigen")
            if isinstance(raw, bool):
                data["wetter_detailgrad"] = "high" if raw else "off"
            else:
                raise ValueError(
                    f"wetter_beruecksichtigen must be a boolean (True/False), "
                    f"got {raw!r}. Use wetter_detailgrad instead."
                )

        # Defensive: wetter_detailgrad sent as a raw JSON boolean
        if data.get("wetter_detailgrad") is True:
            data["wetter_detailgrad"] = "high"
        elif data.get("wetter_detailgrad") is False:
            data["wetter_detailgrad"] = "off"

        return data

    baustellen_beruecksichtigen: bool = Field(
        default=True,
        description=(
            "Falls False, wird der Baustellen-Provider für diese Berechnung "
            "übersprungen (keine Geschwindigkeitsreduktion durch Baustellen), um "
            "die Berechnungsdauer zu reduzieren."
        ),
    )


class FrameAPI(BaseModel):
    """Einzelner Simulationsframe in der API-Response."""

    zeitpunkt: str = Field(..., description="ISO-8601 Zeitpunkt")
    position: tuple[float, float] = Field(
        ..., description="(lat, lon), konsistent mit Domänenmodell"
    )
    distanz_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz vom Reisebeginn entlang der Route in Metern"
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0)
    zustand: str = Field(..., description="'FAHREN', 'LADEN' oder 'PAUSE'")
    geschwindigkeit_kmh: float = Field(..., ge=0.0)


class ChargingStopAPI(BaseModel):
    """Ladehalt in der API-Response, ein Eintrag pro tatsaechlichem Halt."""

    name: str = Field(..., description="Name der Ladestation")
    station_id: str = Field(..., description="Eindeutige ID der Ladestation")
    position: tuple[float, float] = Field(..., description="(lat, lon) der Ladestation")
    distanz_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz entlang der Route, an der abgebogen wird"
    )
    detour_geometrie: list[Coordinate] = Field(
        default_factory=list,
        description=(
            "Echte, ueber GraphHopper geroutete Geometrie von der Route zur Ladestation und "
            "zurueck (leer, falls die Detour-Route nicht ermittelt werden konnte)"
        ),
    )
    route_index_vor: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `route_geometrie`, ab dem `detour_geometrie` die Hauptroute ersetzt "
            "(None, falls `detour_geometrie` leer ist)"
        ),
    )
    route_index_nach: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `route_geometrie`, bis zu dem (inklusive) `detour_geometrie` die "
            "Hauptroute ersetzt (None, falls `detour_geometrie` leer ist)"
        ),
    )
    detour_station_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `detour_geometrie`, an dem die Ladestation tatsaechlich erreicht wird "
            "(None, falls `detour_geometrie` leer ist)"
        ),
    )
    ankunfts_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Ankunft in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC nach dem Laden in %")
    ladedauer_s: int = Field(..., ge=0, description="Ladedauer in Sekunden")
    energie_geladen_kwh: float = Field(..., ge=0.0, description="Geladene Energiemenge in kWh")
    ankunftszeit: str = Field(..., description="ISO-8601 Ankunftszeitpunkt an der Station")
    abfahrtszeit: str = Field(..., description="ISO-8601 Abfahrtszeitpunkt von der Station")
    price_per_kwh: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Applicable Tesla-owner rate per kWh at arrival time, from cached "
            "pricing data. None if no pricing data is cached yet for this station."
        ),
    )
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description=(
            "ISO-4217 currency of `price_per_kwh`/`estimated_cost`. None iff those are None."
        ),
    )
    estimated_cost: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Estimated cost of this charging stop (`energie_geladen_kwh * "
            "price_per_kwh`). None if no pricing data is cached yet for this station."
        ),
    )
    pricing_updated_utc: str | None = Field(
        default=None,
        description=(
            "ISO-8601 timestamp of the cached pricing data used for `price_per_kwh`. "
            "None if no pricing data has ever been scraped for this station."
        ),
    )


class FaehrSegmentAPI(BaseModel):
    """API-Response für eine in der berechneten Route erkannte Fährverbindung."""

    name: str = Field(..., description="Fährname (aus GraphHopper street_name oder Fallback)")
    laenge_m: float = Field(..., ge=0, description="Länge der Fährverbindung in Metern")
    bbox_sw: tuple[float, float] = Field(
        ..., description="Südwest-Ecke der gepufferten Bounding Box"
    )
    bbox_no: tuple[float, float] = Field(
        ..., description="Nordost-Ecke der gepufferten Bounding Box"
    )
    abfahrt: str | None = Field(
        default=None,
        description="Vom Nutzer vorgegebene Abfahrtszeit (ISO-8601), sofern vorhanden",
    )
    ankunft: str | None = Field(
        default=None,
        description="Vom Nutzer vorgegebene Ankunftszeit (ISO-8601), sofern vorhanden",
    )


class ChargingCostByCurrencyAPI(BaseModel):
    """Aggregated estimated charging cost in a single currency."""

    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code")
    amount: float = Field(..., ge=0.0, description="Summed cost in `currency`")


class TripSimulationResultAPI(BaseModel):
    """API-Response für /trips-Endpunkt."""

    gesamt_distanz_km: float = Field(..., description="Gesamtdistanz in km")
    gesamt_fahrzeit_min: float = Field(..., description="Gesamtfahrzeit in Minuten")
    gesamt_ladezeit_min: float = Field(..., description="Gesamtladezeit in Minuten")
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start-SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC in %")
    frames: list[FrameAPI] = Field(..., description="Liste von Simulationsframes")
    charging_stops: list[ChargingStopAPI] = Field(
        default_factory=list, description="Ein Eintrag pro Ladehalt, fuer die Kartendarstellung"
    )
    route_geometrie: list[Coordinate] = Field(
        ...,
        description=(
            "Vollstaendige Streckengeometrie der berechneten Route (dichte GraphHopper-"
            "Polyline, nicht auf Simulationsframes reduziert) fuer eine winkeltreue "
            "Kartendarstellung."
        ),
    )
    erkannte_faehren: list[FaehrSegmentAPI] = Field(
        default_factory=list,
        description="In der berechneten Route erkannte Fährverbindungen (leer, falls keine)",
    )
    total_charging_cost: list[ChargingCostByCurrencyAPI] = Field(
        default_factory=list,
        description=(
            "Sum of estimated charging costs across all charging stops, grouped by "
            "currency (empty if no stop has cached pricing data; multiple entries if "
            "stops span several currencies, e.g. a DE-DK-SE trip)."
        ),
    )
    charging_stops_missing_pricing: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of charging stops excluded from `total_charging_cost` "
            "because no pricing data is cached yet for their station."
        ),
    )


@app.post("/trips", response_model=TripSimulationResultAPI, status_code=201)
async def create_trip_endpoint(  # noqa: PLR0913, PLR0917
    request: TripRequestAPI,
    # B008: Depends(...) im Default ist das FastAPI-Standardidiom für Dependency
    # Injection, kein veränderliches Objekt/kein echter Bug (siehe FastAPI-Doku).
    routing_provider: RoutingProvider = Depends(get_routing_provider),  # noqa: B008
    charging_provider: ChargingStationProvider = Depends(get_charging_provider),  # noqa: B008
    weather_provider: WeatherProvider = Depends(get_weather_provider),  # noqa: B008
    construction_provider: ConstructionProvider = Depends(get_construction_provider),  # noqa: B008
    elevation_provider: ElevationProvider = Depends(get_elevation_provider),  # noqa: B008
) -> TripSimulationResultAPI:
    """Create a new trip simulation.

    Uses :func:`create_trip_simulation` to orchestrate all 11 data-flow steps.
    Routing uses the real GraphHopper server (via ``get_routing_provider``);
    without a running server the request fails with 502 (see README.md).

    ``request.wetter_detailgrad`` (``"off"``, ``"low"``, ``"medium"``,
    ``"high"``) drives weather resolution.  ``"off"`` skips the weather
    provider (``None``); ``"high"`` passes it through unchanged;
    ``"low"``/``"medium"`` also pass the provider but with reduced
    query granularity.  ``request.baustellen_beruecksichtigen`` controls
    construction-site detection independently.
    """
    # TripRequestAPI nach TripRequest konvertieren
    request_dict: dict[str, object] = {
        "start": request.start,
        "ziel": request.ziel,
        "zwischenstopps": [
            {
                "koordinate": wp.koordinate,
                "aufenthaltsdauer": timedelta(seconds=wp.aufenthaltsdauer_s)
                if wp.aufenthaltsdauer_s
                else None,
                "geplante_abfahrt": datetime.fromisoformat(wp.geplante_abfahrt)
                if wp.geplante_abfahrt
                else None,
            }
            for wp in request.zwischenstopps
        ],
        "abfahrtszeit": request.abfahrtszeit,
        "fahrzeugprofil": request.fahrzeugprofil.model_dump(),
        "praeferenzen": request.praeferenzen,
        "alle_faehren_vermeiden": request.alle_faehren_vermeiden,
        "vermiedene_faehren": [
            {"name": f.name, "bbox_sw": f.bbox_sw, "bbox_no": f.bbox_no}
            for f in request.vermiedene_faehren
        ],
        "faehr_zeitfenster": [
            {
                "name": f.name,
                "bbox_sw": f.bbox_sw,
                "bbox_no": f.bbox_no,
                "abfahrt": datetime.fromisoformat(f.abfahrt),
                "ankunft": datetime.fromisoformat(f.ankunft),
            }
            for f in request.faehr_zeitfenster
        ],
        "ladedauer_vorgaben": [
            {"station_id": v.station_id, "ladedauer_s": v.ladedauer_s}
            for v in request.ladedauer_vorgaben
        ],
    }

    detected_ferries: list[FaehrSegment] = []

    def _faehren_erfassen(faehren: list[FaehrSegment]) -> None:
        nonlocal detected_ferries
        detected_ferries = faehren

    route_geometrie: list[Coordinate] = []

    def _route_erfassen(route: Route) -> None:
        nonlocal route_geometrie
        route_geometrie = route.geometrie

    try:
        ergebnis = await create_trip_simulation(
            request_dict,
            routing_provider=routing_provider,
            charging_provider=charging_provider,
            weather_provider=(weather_provider if request.wetter_detailgrad != "off" else None),
            weather_detail=request.wetter_detailgrad,
            construction_provider=(
                construction_provider if request.baustellen_beruecksichtigen else None
            ),
            elevation_provider=elevation_provider,
            start_soc_pct=request.start_soc_pct,
            destination_soc_pct=request.ziel_soc_pct,
            mindest_ankunfts_soc_pct=request.mindest_ankunfts_soc_pct,
            mindest_ladezeit_s=request.mindest_ladezeit_s,
            ferry_observer=_faehren_erfassen,
            route_observer=_route_erfassen,
        )

        return TripSimulationResultAPI(
            gesamt_distanz_km=ergebnis.gesamt_distanz_km,
            gesamt_fahrzeit_min=ergebnis.gesamt_fahrzeit_min,
            gesamt_ladezeit_min=ergebnis.gesamt_ladezeit_min,
            start_soc_pct=ergebnis.start_soc_pct,
            ziel_soc_pct=ergebnis.ziel_soc_pct,
            frames=[
                FrameAPI(
                    zeitpunkt=f.zeitpunkt.isoformat(),
                    position=f.position,
                    distanz_m=f.distanz_m,
                    soc_pct=f.soc_pct,
                    zustand=f.zustand.value,
                    geschwindigkeit_kmh=f.geschwindigkeit_kmh,
                )
                for f in ergebnis.frames
            ],
            charging_stops=[
                ChargingStopAPI(
                    name=stop.name,
                    station_id=stop.station_id,
                    position=stop.position,
                    distanz_m=stop.distanz_m,
                    detour_geometrie=stop.detour_geometrie,
                    route_index_vor=stop.route_index_vor,
                    route_index_nach=stop.route_index_nach,
                    detour_station_index=stop.detour_station_index,
                    ankunfts_soc_pct=stop.ankunfts_soc_pct,
                    ziel_soc_pct=stop.ziel_soc_pct,
                    ladedauer_s=stop.ladedauer_s,
                    energie_geladen_kwh=stop.energie_geladen_kwh,
                    ankunftszeit=stop.ankunftszeit.isoformat(),
                    abfahrtszeit=stop.abfahrtszeit.isoformat(),
                    price_per_kwh=stop.price_per_kwh,
                    currency=stop.currency,
                    estimated_cost=stop.estimated_cost,
                    pricing_updated_utc=stop.pricing_updated_utc.isoformat()
                    if stop.pricing_updated_utc
                    else None,
                )
                for stop in ergebnis.charging_stops
            ],
            route_geometrie=route_geometrie,
            erkannte_faehren=[
                FaehrSegmentAPI(
                    name=f.name,
                    laenge_m=f.laenge_m,
                    bbox_sw=f.bbox_sw,
                    bbox_no=f.bbox_no,
                    abfahrt=f.abfahrt.isoformat() if f.abfahrt else None,
                    ankunft=f.ankunft.isoformat() if f.ankunft else None,
                )
                for f in detected_ferries
            ],
            total_charging_cost=[
                ChargingCostByCurrencyAPI(currency=c.currency, amount=c.amount)
                for c in ergebnis.total_charging_cost
            ],
            charging_stops_missing_pricing=ergebnis.charging_stops_missing_pricing,
        )
    except ValueError as e:
        logger.warning("Trip simulation rejected (422): %s", e)
        raise HTTPException(
            status_code=422,
            detail=f"Route nicht durchführbar: {e!s}",
        ) from e
    except httpx.HTTPError as e:
        # Weather providers never raise here anymore (see
        # `LoadBalancedWeatherProvider`: failures fail over between
        # providers and degrade to a neutral placeholder instead of
        # propagating), so any `httpx.HTTPError` reaching this handler
        # originates from the routing (GraphHopper) call.
        logger.warning("Routing provider (GraphHopper) request failed (502): %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Routing-Server (GraphHopper) nicht erreichbar oder lieferte einen Fehler: {e}",
        ) from e
    except Exception as e:
        logger.exception(
            "Fehler bei der Routensimulation: %s", e, extra={"traceback": traceback.format_exc()}
        )
        raise HTTPException(status_code=500, detail=f"Simulation fehlgeschlagen: {e!s}") from e
