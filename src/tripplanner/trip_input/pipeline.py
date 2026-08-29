"""Pipeline für trip_input: Orchestrierung der 11 Datenfluss-Schritte.

Dieses Modul enthält die elf `_step_*`-Funktionen und die zentrale
Orchestrierung `create_trip_simulation()`, die alle Schritte in der
richtigen Reihenfolge ausführt (Routing, Höhenprofil, Segmentierung,
Wetter, Baustellen, Energie, Ladeplanung, Simulation, Preise).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from tripplanner.charging_infrastructure import (
    ChargingStation,
    ChargingStationProvider,
    FakeChargingStationProvider,
)
from tripplanner.construction.models import ConstructionProvider, ConstructionZone, Land
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.models import ElevationPoint
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.energy import calculate_segment_consumption
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters
from tripplanner.geo import haversine_distance_m
from tripplanner.optimization import create_networkx_optimizer
from tripplanner.optimization.detour_routing import precompute_detour_costs
from tripplanner.optimization.models import ChargingPlan, DetourKosten, OptimizationConstraints
from tripplanner.optimization.station_mapping import map_stations_to_segments
from tripplanner.routing import (
    FakeRoutingProvider,
    RoutingProvider,
    erkenne_faehren,
)
from tripplanner.routing.detour_geometry import find_bracket_points
from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment
from tripplanner.simulation import simulate_trip
from tripplanner.simulation.models import (
    LadehaltDetour,
    TripSimulationResult,
)
from tripplanner.trip_input.models import (
    FaehrZeitfenster,
    TripRequest,
    VehicleProfile,
    Waypoint,
)
from tripplanner.weather import FakeWeatherProvider, WeatherDetailLevel, fetch_weather_by_detail
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import WeatherProvider
from tripplanner.wind import compute_wind_components_for_route
from tripplanner.wind.models import WindComponents

if TYPE_CHECKING:
    from tripplanner.optimization.models import ChargingStop
from .schemas.response import _attach_charging_pricing


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
            baustellen=None,
            tempolimit_override_kmh=float(tempolimit) if tempolimit else None,
        )
        ergebnisse.append(ergebnis)

    return ergebnisse


async def _step_8a_fetch_charging_stations(
    charging_provider: ChargingStationProvider | None,
    route: Route,
) -> list[ChargingStation]:
    """Step 8a: Fetch and deduplicate candidate charging stations along the route.

    Extracted out of `_step_8_optimize_charging_plan` so `create_trip_
    simulation` can call it ONCE (before the weather/ETA convergence loop)
    instead of once per iteration - the route never changes between
    iterations, so the station list never changes either, and this call
    also feeds `optimization.detour_routing.precompute_detour_costs`, which
    must not be redone per iteration (it's a batch of routing/elevation
    calls, not free).

    Search radius deliberately 25 km, not the road width (1-2 km): on long-
    distance trips through regions with sparser Supercharger coverage
    (e.g. rural Sweden routes off the E4/E6), the nearest Supercharger
    regularly sits 10-25 km off the GraphHopper-chosen road - too tight a
    radius delivers ZERO candidates for an entire segment there, which
    makes `NetworkXOptimizer.optimize()` incorrectly raise "no reachable
    target node found" even though the route IS drivable with a realistic
    charging detour (repro: Gummersbach -> Hagfors kommun, Sweden - the only
    candidate in the gap, Ulricehamn, sits ~25 km off the route). 25 km is
    deliberately generous (also covers the 20 km distant Jönköping
    Supercharger) while still small enough not to needlessly bloat the
    state-graph size (see docs/plans/07-optimization.md).

    `get_stations_along_route()` maps stations per (fine-grained) route
    segment - with very short segments (e.g. one segment per GraphHopper
    polyline point pair, often < 200 m), the same physical station usually
    falls within the search radius of several consecutive segments and
    shows up repeatedly. Without deduplication by `station_id`, the
    optimizer would model the same station at many neighboring segment
    indices as separate charging opportunities, needlessly bloating the
    state graph (see docs/plans/07-optimization.md) and slowing the search.
    """
    provider = charging_provider or FakeChargingStationProvider()
    stations_dict = await provider.get_stations_along_route(route, search_radius_km=25.0)

    charging_stations: list[ChargingStation] = []
    seen_station_ids: set[str] = set()
    for station_list in stations_dict.values():
        for station in station_list:
            if station.station_id in seen_station_ids:
                continue
            seen_station_ids.add(station.station_id)
            charging_stations.append(station)

    return charging_stations


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
    charging_stations: list[ChargingStation],
    detour_kosten: dict[str, DetourKosten],
    zwischenstopps: list[Waypoint] | None = None,
    ladedauer_vorgaben: dict[str, int] | None = None,
    faehr_zeitfenster: dict[int, tuple[int, datetime, datetime]] | None = None,
    mindest_ankunfts_soc_pct: float = 5.0,
    max_lade_soc_pct: float = 100.0,
    mindest_ladezeit_s: int = 600,
) -> ChargingPlan:
    """Step 8: Determine the optimal charging plan.

    Uses `create_networkx_optimizer()` as the prototype optimizer.
    `charging_stations` and `detour_kosten` are precomputed ONCE by
    `create_trip_simulation` (see `_step_8a_fetch_charging_stations` and
    `optimization.detour_routing.precompute_detour_costs`) - not fetched or
    computed here, so they stay identical (and are only computed once)
    across every weather/ETA-convergence iteration.
    """
    optimizer = create_networkx_optimizer()
    constraints = OptimizationConstraints(
        ziel_soc_pct=ziel_soc_pct,
        max_ladezeit_s=3600,
        mindest_ankunfts_soc_pct=mindest_ankunfts_soc_pct,
        mindest_ladezeit_s=mindest_ladezeit_s,
        max_lade_soc_pct=max_lade_soc_pct,
    )

    waypoints = list(zwischenstopps) if zwischenstopps else []

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
        detour_kosten=detour_kosten,
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
    `find_bracket_points`), um Richtungsmehrdeutigkeit bei GraphHopper zu
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
        vor_index, after_index = find_bracket_points(route, ladehalt.segment_index)
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
            route_index_vor=find_bracket_points(route, ladehalt.segment_index)[0],
            route_index_nach=find_bracket_points(route, ladehalt.segment_index)[1],
            station_index=station_index,
        )

    return detouren


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


# Keep the logger bound to the original module name so pipeline-step log
# records keep the exact same `name` they had before the split (logging
# assertions and production log output are behavior-preserving).
_logger = logging.getLogger("tripplanner.trip_input.api")


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


async def create_trip_simulation(  # noqa: PLR0913, PLR0915, PLR0917
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
    max_lade_soc_pct: float = 100.0,
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
        max_lade_soc_pct: Upper limit for the target SoC at regular charging
            stops (Supercharger stations), in percent. 100.0 = disabled. See
            `OptimizationConstraints.max_lade_soc_pct`.
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

    # 4. Step 3: Segment routing (already in route.segments)
    with _log_step("segment_route"):
        segments = _step_3_segment_route(route)

    # 5. Step 4: Initial ETA estimate
    with _log_step("estimate_initial_eta"):
        segment_eta_list = _step_4_estimate_initial_eta(route, request.abfahrtszeit)

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

    # 8a. Fetch candidate charging stations and precompute their real,
    # routed detour costs, and fetch construction zones, ONCE - all three
    # depend only on `route`, which is already final at this point, and
    # must not be redone per iteration. `construction_sites` in particular
    # takes no iteration-dependent input at all (unlike `fetch_weather`,
    # which needs the current `segment_eta_list`) - looping it 1-3x
    # inside the convergence loop below refetched byte-identical data on
    # every iteration for nothing.
    #
    # The three branches share no state (elevation extraction only reads
    # `route`/`elevation_provider`; station fetch + detour precompute only
    # read `route`/`charging_provider` plus the same `elevation_provider`,
    # whose tile cache is internally lock-protected against concurrent
    # readers - see `CopernicusDEMDataSource._get_tile_lock`; construction
    # only reads `route`/`construction_provider`). All three are I/O-bound
    # network calls, so running them concurrently instead of sequentially
    # cuts wall-clock time to roughly the slowest of the three instead of
    # their sum.
    async def _fetch_elevation_profile() -> list[ElevationPoint]:
        with _log_step("extract_elevation_profile"):
            return await _step_2_extract_elevation_profile(route, elevation_provider)

    async def _fetch_charging_stations_and_detours() -> tuple[
        list[ChargingStation], dict[str, DetourKosten]
    ]:
        with _log_step("fetch_charging_stations"):
            stations = await _step_8a_fetch_charging_stations(charging_provider, route)

        with _log_step("precompute_detour_costs"):
            station_segments = map_stations_to_segments(stations, route.segments)
            kosten = await precompute_detour_costs(
                routing_provider=routing_provider or FakeRoutingProvider(),
                elevation_provider=elevation_provider,
                vehicle_profile=request.fahrzeugprofil,
                route=route,
                station_segments=station_segments,
                abfahrtszeit=request.abfahrtszeit,
            )
        return stations, kosten

    async def _fetch_construction_zones() -> list[ConstructionZone]:
        with _log_step("construction_sites"):
            return await _step_6_construction_sites(
                construction_provider, route, ["DE", "DK", "SE"]
            )

    (
        elevation_points,
        (charging_stations, detour_kosten),
        construction_zones,
    ) = await asyncio.gather(
        _fetch_elevation_profile(),
        _fetch_charging_stations_and_detours(),
        _fetch_construction_zones(),
    )
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
                zwischenstopps=request.zwischenstopps,
                charging_stations=charging_stations,
                detour_kosten=detour_kosten,
                ladedauer_vorgaben=charging_duration_map,
                faehr_zeitfenster=ferry_pins,
                mindest_ankunfts_soc_pct=mindest_ankunfts_soc_pct,
                mindest_ladezeit_s=mindest_ladezeit_s,
                max_lade_soc_pct=max_lade_soc_pct,
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
            construction_zones=construction_zones,
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
