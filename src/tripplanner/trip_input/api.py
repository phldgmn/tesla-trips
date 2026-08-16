"""API-Schicht für trip_input: Orchestrierung der 11 Datenfluss-Schritte.

Diese Modul implementiert:
- `create_trip_simulation()`: Kernfunktion zur Orchestrierung aller Schritte
- FastAPI-Endpunkt `POST /trips` mit `TripRequestAPI` Request-Model
"""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from tripplanner.charging_infrastructure import (
    ChargingStation,
    ChargingStationProvider,
    FakeChargingStationProvider,
    StallType,
    get_all_charging_stations,
)
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.construction.models import ConstructionZone, Land
from tripplanner.construction.providers import FakeConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.models import ElevationPoint, SegmentGradient
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.energy import berechne_segment_verbrauch
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters
from tripplanner.geo import haversine_distance_m
from tripplanner.optimization import create_networkx_optimizer
from tripplanner.optimization.models import ChargingPlan, OptimizationConstraints
from tripplanner.routing import (
    FakeRoutingProvider,
    GraphHopperClient,
    GraphHopperRoutingProvider,
    RoutingProvider,
    erkenne_faehren,
)
from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment
from tripplanner.simulation import simulate_trip
from tripplanner.simulation.models import LadehaltDetour, TripSimulationResult
from tripplanner.trip_input.models import (
    FaehrZeitfenster,
    TripRequest,
    VehicleProfile,
    Waypoint,
)
from tripplanner.weather import FakeWeatherProvider
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.wind import compute_wind_components_for_route
from tripplanner.wind.models import WindComponents

if TYPE_CHECKING:
    pass

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


async def _step_1_route_berechnen(
    anfrage: TripRequest,
    routing_provider: RoutingProvider | None = None,
) -> Route:
    """Schritt 1: OSM-Routing berechnen (inkl. Zwischenstopps als Pflicht-Waypoints).

    Als Default-Provider wird `FakeRoutingProvider` verwendet, damit die Pipeline
    ohne echten GraphHopper-Server läuft. Für Produktion kann ein echter Provider
    wie `GraphHopperRoutingProvider` übergeben werden. Ruft `berechne_route()`
    (nicht `berechne_route_mit_waypoints()`) auf, damit Präferenzen aus `anfrage`
    (z. B. Fährvermeidung) den Provider erreichen.
    """
    provider = routing_provider or FakeRoutingProvider()
    return await provider.berechne_route(anfrage)


def _step_2_hoehenprofil_extractieren(route: Route) -> list[ElevationPoint]:
    """Schritt 2: Höhenprofil extrahieren.

    Als Default-Provider wird eine Fake-Implementierung genutzt, da keine
    echten DEM-Kacheln verfügbar sind. Dies ist eine bewusste Design-Entscheidung
    für die lokale Orchestrierung ohne externe Abhängigkeiten.

    Für Produktion kann ein echter `ElevationProvider` mit DEM-Daten
    (z. B. Copernicus DEM oder SRTM) eingebunden werden.
    """
    elevation_provider = ElevationProvider(data_source=FakeDataSource())
    return elevation_provider.get_elevation_profile(route)


def _step_3_segmentierung(route: Route) -> list[RouteSegment]:
    """Schritt 3: Route in Segmente unterteilen.

    Diese Information ist bereits in `route.segments` enthalten.
    """
    return route.segments


def _step_4_initiale_eta_schaetzen(
    route: Route,
    abfahrtszeit: datetime,
) -> list[tuple[RouteSegment, timedelta]]:
    """Schritt 4: Initiale ETA-Schätzung je Segment.

    Grobe Schätzung basierend auf durchschnittlicher Geschwindigkeit
    (Default: 110 km/h auf Autobahnen, 60 km/h sonst).
    """
    segment_eta_liste: list[tuple[RouteSegment, timedelta]] = []

    for segment in route.segments:
        laenge_km = segment.laenge_m / 1000.0

        # Geschwindigkeit basierend auf Straßenart schätzen
        durchschnittsgeschwindigkeit_kmh = 110.0  # Default: Autobahn
        if segment.oberflaeche and "unpaved" in segment.oberflaeche.lower():
            durchschnittsgeschwindigkeit_kmh = 50.0
        elif segment.oberflaeche and "paved" in segment.oberflaeche.lower():
            durchschnittsgeschwindigkeit_kmh = 100.0

        dauer_stunden = laenge_km / durchschnittsgeschwindigkeit_kmh
        dauer = timedelta(hours=dauer_stunden)

        segment_eta_liste.append((segment, dauer))

    return segment_eta_liste


async def _step_5_wetterabfrage(
    provider: FakeWeatherProvider | None,
    route: Route,
    segment_eta_liste: list[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[WeatherSample]:
    """Schritt 5: Wetterdaten entlang der Route zu den initialen ETAs abrufen.

    Default: `FakeWeatherProvider` für Tests ohne externe API-Aufrufe.
    """
    if provider is None:
        provider = FakeWeatherProvider()

    # Queries erzeugen: Koordinate + ETA pro Segment
    queries: list[WeatherQuery] = []
    aktuelle_zeit = abfahrtszeit

    for segment, dauer in segment_eta_liste:
        # Mittelpunkt des Segments als Abfragepunkt
        mitte_idx = len(segment.geometrie) // 2
        koordinate = segment.geometrie[mitte_idx]
        queries.append(WeatherQuery(koordinate=koordinate, zeitpunkt=aktuelle_zeit))
        aktuelle_zeit += dauer

    return await provider.fetch_weather(queries)


async def _step_6_baustellen(
    construction_provider: FakeConstructionProvider | None,
    route: Route,
    laender: list[str] | None = None,
) -> list[ConstructionZone]:
    """Schritt 6: Baustellen entlang der Route einbeziehen.

    Optional: Für die Erstimplementierung kann mit leerer Liste gearbeitet werden.
    Ein `ConstructionProvider` (z. B. `FakeConstructionProvider`) kann übergeben
    werden, um Baustellendaten zu nutzen.
    """
    if construction_provider is None:
        return []

    laender_enum = [Land[land_code] for land_code in laender or []]
    return await construction_provider.fetch_construction_zones(route, laender_enum or [])


async def _step_7_energieverbrauch_segment(  # noqa: PLR0913, PLR0917
    route_segments: list[RouteSegment],
    segment_eta_liste: list[tuple[RouteSegment, timedelta]],
    weather_samples: list[WeatherSample],
    vehicle_profile: VehicleProfile,
    construction_zones: list[ConstructionZone],
    abfahrtszeit: datetime,
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

    # Gradient und SegmentGradient für jedes Segment berechnen (vereinfacht)
    gradients = [
        SegmentGradient(
            segment_index=s.segment_index,
            steigung_prozent=0.0,  # Vereinfachung: flach
            hoehendifferenz_m=0.0,
            horizontale_distanz_m=s.laenge_m,
        )
        for s in route_segments
    ]

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
                zeitpunkt=abfahrtszeit + segment_eta_liste[idx][1]
                if idx < len(segment_eta_liste)
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

        ergebnis = berechne_segment_verbrauch(
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


async def _step_8_ladeplan_optimieren(  # noqa: PLR0913, PLR0917
    route: Route,
    segment_energy: list[SegmentEnergyResult],
    vehicle_profile: VehicleProfile,
    start_soc_pct: float,
    ziel_soc_pct: float,
    construction_zones: list[ConstructionZone],
    abfahrtszeit: datetime,
    zwischenstopps: list[Waypoint] | None = None,
    charging_provider: ChargingStationProvider | None = None,
    ladedauer_vorgaben: dict[str, int] | None = None,
    faehr_zeitfenster: dict[int, tuple[int, datetime, datetime]] | None = None,
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
    )

    # Ladeinfrastruktur entlang der Route abrufen (Fake-Provider für Tests)
    charging_provider = charging_provider or FakeChargingStationProvider()
    stations_dict = await charging_provider.get_stations_along_route(route, search_radius_km=2.0)
    # `get_stations_along_route()` mappt pro (feingranularem) Segment die
    # Stationen im Suchradius - bei sehr kurzen Segmenten (z. B. ein Segment
    # pro GraphHopper-Polyline-Punktpaar, oft <200 m) liegt dieselbe
    # physische Station meist innerhalb des 2-km-Radius mehrerer
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

    # Gradients für Optimizer (vereinfacht)
    gradients = [
        SegmentGradient(
            segment_index=s.segment_index,
            steigung_prozent=0.0,
            hoehendifferenz_m=0.0,
            horizontale_distanz_m=s.laenge_m,
        )
        for s in route.segments
    ]

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


def _step_9_eta_aktualisieren(
    segment_eta_liste: list[tuple[RouteSegment, timedelta]],
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
    # `segment_eta_liste` wird in `_step_4_initiale_eta_schaetzen()` durch
    # Iteration über `route.segments` IN DERSELBEN REIHENFOLGE aufgebaut (ein
    # Tupel pro Segment, keine Filterung/Umsortierung) - der Listenindex
    # entspricht also bereits exakt dem Segment-Index. `.index()` würde
    # stattdessen für JEDES Segment eine LINEARE Suche mit tiefer Pydantic-
    # Objektgleichheit über ALLE Segmente durchführen (O(n²) mit teurem
    # Vergleich statt O(n)) - bei feingranularen Routen (tausende Segmente,
    # z. B. ein Segment pro GraphHopper-Polyline-Punktpaar) ein spürbarer,
    # zudem komplett unnötiger Kostenfaktor.
    for segment_idx, (segment, urspruengliche_dauer) in enumerate(segment_eta_liste):
        ladezeit = ladezeiten_pro_segment.get(segment_idx, timedelta())
        neue_dauer = urspruengliche_dauer + ladezeit
        neue_eta_liste.append((segment, neue_dauer))

    return neue_eta_liste


def _finde_klammerpunkte(
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
    letzter_index = len(route.geometrie) - 1
    segment_index = min(segment_index, len(route.segments) - 1)

    vor_index = segment_index
    distanz_zurueck = 0.0
    while vor_index > 0 and distanz_zurueck < margin_m:
        vor_index -= 1
        distanz_zurueck += route.segments[vor_index].laenge_m

    nach_index = segment_index
    distanz_vor = 0.0
    while nach_index < letzter_index and distanz_vor < margin_m:
        distanz_vor += route.segments[nach_index].laenge_m
        nach_index += 1

    return vor_index, nach_index


async def _step_lade_detours_routen(
    routing_provider: RoutingProvider | None,
    route: Route,
    charging_plan: ChargingPlan,
    abfahrtszeit: datetime,
    fahrzeugprofil: VehicleProfile,
) -> dict[int, LadehaltDetour]:
    """Schritt 8b: Routet fuer jeden Ladehalt eine echte Hin-und-zurueck-Verbindung.

    GraphHopper kennt Ladestationen nicht als Wegpunkte der Hauptroute - die
    Stationswahl erfolgt erst NACH der Routenberechnung durch den Optimierer
    (`_step_8_ladeplan_optimieren`). Deshalb zwei separate, kleine Routing-
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
    `_finde_klammerpunkte`), um Richtungsmehrdeutigkeit bei GraphHopper zu
    vermeiden.

    Returns:
        dict von `id()` des `ChargingStop`-Objekts (Ladehalt) -> `LadehaltDetour`.
        Ein Ladehalt fehlt im Ergebnis, wenn GraphHopper fuer eines der beiden
        Beine keine Route liefern konnte (`ChargingStopSummary.detour_geometrie`
        bleibt dann leer - der Ladehalt selbst bleibt gueltig, nur ohne
        Kartengeometrie fuer den Abstecher).
    """
    provider = routing_provider or FakeRoutingProvider()
    detouren: dict[int, LadehaltDetour] = {}
    for ladehalt in charging_plan.ladehalte:
        vor_index, nach_index = _finde_klammerpunkte(route, ladehalt.segment_index)
        hinweg_anfrage = TripRequest(
            start=route.geometrie[vor_index],
            ziel=ladehalt.station.coordinate,
            abfahrtszeit=abfahrtszeit,
            fahrzeugprofil=fahrzeugprofil,
        )
        rueckweg_anfrage = TripRequest(
            start=ladehalt.station.coordinate,
            ziel=route.geometrie[nach_index],
            abfahrtszeit=abfahrtszeit,
            fahrzeugprofil=fahrzeugprofil,
        )
        try:
            hinweg_route = await provider.berechne_route(hinweg_anfrage)
            rueckweg_route = await provider.berechne_route(rueckweg_anfrage)
        except httpx.HTTPError:
            continue
        station_index = len(hinweg_route.geometrie) - 1
        detouren[id(ladehalt)] = LadehaltDetour(
            geometrie=hinweg_route.geometrie + rueckweg_route.geometrie[1:],
            route_index_vor=vor_index,
            route_index_nach=nach_index,
            station_index=station_index,
        )
    return detouren


def _segment_index_fuer_koordinate(koordinate: Coordinate, segments: list[RouteSegment]) -> int:
    """Segment, dessen Ende der gegebenen Koordinate am nächsten liegt (haversine)."""
    best_idx, best_dist = 0, float("inf")
    for idx, seg in enumerate(segments):
        dist = haversine_distance_m(koordinate, seg.geometrie[-1])
        if dist < best_dist:
            best_dist, best_idx = dist, idx
    return best_idx


def _mit_abgeleiteter_wartezeit(
    zwischenstopps: list[Waypoint],
    segment_eta_liste: list[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[Waypoint]:
    """Leitet aus einem optionalen `geplante_abfahrt` je Wegpunkt eine effektive Wartezeit ab.

    Heuristik: einmalige Annäherung anhand der initialen ETA-Schätzung, keine
    iterative Konvergenz — konsistent mit dem bestehenden Ansatz der iterativen
    ETA/Wetter-Schätzung an anderer Stelle im Modul, hier aber bewusst einstufig.
    """
    segments = [seg for seg, _ in segment_eta_liste]
    kumuliert: list[timedelta] = []
    laufend = timedelta()
    for _, dauer in segment_eta_liste:
        laufend += dauer
        kumuliert.append(laufend)
    ergebnis: list[Waypoint] = []
    for wp in zwischenstopps:
        if wp.geplante_abfahrt is None:
            ergebnis.append(wp)
            continue
        seg_idx = _segment_index_fuer_koordinate(wp.koordinate, segments)
        geschaetzte_ankunft = abfahrtszeit + kumuliert[seg_idx]
        abgeleitete_wartezeit = max(timedelta(), wp.geplante_abfahrt - geschaetzte_ankunft)
        bestehende = wp.aufenthaltsdauer or timedelta()
        ergebnis.append(
            wp.model_copy(update={"aufenthaltsdauer": max(bestehende, abgeleitete_wartezeit)})
        )
    return ergebnis


def _bbox_mitte(sw: Coordinate, no: Coordinate) -> Coordinate:
    """Mittelpunkt einer (lat, lon)-Bounding-Box."""
    return ((sw[0] + no[0]) / 2.0, (sw[1] + no[1]) / 2.0)


def _matche_faehr_zeitfenster(
    erkannte_faehren: list[FaehrSegment],
    faehr_zeitfenster: list[FaehrZeitfenster],
) -> list[FaehrSegment]:
    """Reichert erkannte Fährverbindungen um Nutzer-Zeitfenster an.

    Identifikation über `name` (bei mehrdeutigem Namen über die nächste
    Bounding-Box-Mitte) - analog zur Identifikationskonvention von
    `FaehrAusschluss`. Nicht (mehr) passende Zeitfenster (Name in der aktuellen
    Route nicht mehr vorhanden) werden stillschweigend ignoriert - konsistent
    mit dem selbstkorrigierenden Ansatz der Fährvermeidung (siehe
    docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md).
    """
    ergebnis: list[FaehrSegment] = []
    for faehre in erkannte_faehren:
        kandidaten = [fz for fz in faehr_zeitfenster if fz.name == faehre.name]
        if not kandidaten:
            ergebnis.append(faehre)
            continue
        faehre_mitte = _bbox_mitte(faehre.bbox_sw, faehre.bbox_no)
        beste = min(
            kandidaten,
            key=lambda fz: haversine_distance_m(faehre_mitte, _bbox_mitte(fz.bbox_sw, fz.bbox_no)),
        )
        ergebnis.append(
            faehre.model_copy(update={"abfahrt": beste.abfahrt, "ankunft": beste.ankunft})
        )
    return ergebnis


# =============================================================================
# Kernfunktion: Orchestrierung aller 11 Schritte
# =============================================================================


async def create_trip_simulation(  # noqa: PLR0913, PLR0917
    anfrage_dict: dict[str, object],
    routing_provider: RoutingProvider | None = None,
    elevation_provider: ElevationProvider | None = None,
    weather_provider: FakeWeatherProvider | None = None,
    construction_provider: FakeConstructionProvider | None = None,
    charging_provider: ChargingStationProvider | None = None,
    start_soc_pct: float = 80.0,
    ziel_soc_pct: float = 20.0,
    route_observer: Callable[[Route], None] | None = None,
    faehren_observer: Callable[[list[FaehrSegment]], None] | None = None,
) -> TripSimulationResult:
    """Orchestriert die 11 Datenfluss-Schritte für die Reiseplanung.

    Args:
        anfrage_dict: Dictionary mit TripRequest-Daten (aus API/CLI geparst).
        routing_provider: Optionaler RoutingProvider (Default: FakeRoutingProvider).
        elevation_provider: Optionaler ElevationProvider (Default: FakeDataSource).
        weather_provider: Optionaler WeatherProvider (Default: FakeWeatherProvider).
        construction_provider: Optionaler ConstructionProvider (Default: FakeConstructionProvider).
        charging_provider: Optionaler ChargingStationProvider
            (Default: FakeChargingStationProvider).
        start_soc_pct: Start-SoC in Prozent (Default: 80%).
        ziel_soc_pct: Ziel-SoC in Prozent (Default: 20%).
        route_observer: Optionaler Callback, der unmittelbar nach Schritt 1 (Routing)
            mit der berechneten Route aufgerufen wird (siehe `create_trip_endpoint`).
        faehren_observer: Optionaler Callback, der unmittelbar nach Schritt 1 mit den
            erkannten, um `anfrage.faehr_zeitfenster` angereicherten Fährverbindungen
            aufgerufen wird - dieselbe Liste, die auch zum Bau der Fährfahrplan-Vorgabe
            für `optimizer.optimize()` genutzt wird (siehe `create_trip_endpoint`).

    Returns:
        TripSimulationResult: Vollständige Simulations-Ergebnis.

    Hinweis zu Fake-Providern:
        Alle Provider haben Fake-Implementierungen als Default, damit die Pipeline
        ohne externe APIs (GraphHopper, Open-Meteo, DEM-Server) läuft. Für Produktion
        werden die echten Provider-Klassen verwendet.
    """
    # 1. TripRequest erzeugen
    anfrage = TripRequest.model_validate(anfrage_dict)

    # 2. Step 1: Route berechnen
    route = await _step_1_route_berechnen(anfrage, routing_provider)
    if route_observer is not None:
        route_observer(route)

    # Vom Nutzer vorgegebene Fährfahrpläne gegen die frisch erkannten
    # Fährverbindungen matchen (siehe `_matche_faehr_zeitfenster`) - wird
    # sowohl fuer den Optimizer-Input unten als auch (ueber `faehren_observer`)
    # fuer die API-Antwort (`erkannte_faehren`) benoetigt.
    erkannte_faehren = _matche_faehr_zeitfenster(erkenne_faehren(route), anfrage.faehr_zeitfenster)
    if faehren_observer is not None:
        faehren_observer(erkannte_faehren)

    # 3. Step 2: Höhenprofil extrahieren
    _ = _step_2_hoehenprofil_extractieren(route)

    # 4. Step 3: Segmentierung (bereits in route.segments enthalten)
    segmente = _step_3_segmentierung(route)

    # 5. Step 4: Initiale ETA-Schätzung
    segment_eta_liste = _step_4_initiale_eta_schaetzen(route, anfrage.abfahrtszeit)
    # Hinweis: Die abgeleitete Wartezeit ist ein Kostenfaktor im Optimierer, keine
    # erzwungene Mindestaufenthaltsdauer - der A*-Pfad kann sie umgehen, wenn kein
    # SoC-/Ladebedarf sie erfordert (Prototyp-Optimizer, siehe docs).
    zwischenstopps_mit_wartezeit = _mit_abgeleiteter_wartezeit(
        anfrage.zwischenstopps, segment_eta_liste, anfrage.abfahrtszeit
    )

    # 6. Step 5: Wetterabfrage
    wetter_samples = await _step_5_wetterabfrage(
        weather_provider,
        route,
        segment_eta_liste,
        anfrage.abfahrtszeit,
    )

    # 7. Step 6: Baustellen (optional)
    baustellen = await _step_6_baustellen(construction_provider, route, ["DE", "DK", "SE"])

    # 8. Step 7: Energieverbrauch berechnen
    energie_ergebnisse = await _step_7_energieverbrauch_segment(
        segmente,
        segment_eta_liste,
        wetter_samples,
        anfrage.fahrzeugprofil,
        baustellen,
        anfrage.abfahrtszeit,
    )

    # Vom Nutzer vorgegebene, gematchte Fährfahrpläne als Optimizer-Input
    # aufbereiten (nur Fähren mit tatsaechlich zugeordneter Zeit).
    faehr_pins = {
        f.segment_index_start: (f.segment_index_end, f.abfahrt, f.ankunft)
        for f in erkannte_faehren
        if f.abfahrt is not None and f.ankunft is not None
    }
    ladedauer_map = {v.station_id: v.ladedauer_s for v in anfrage.ladedauer_vorgaben}

    # 9. Step 8: Ladeplan optimieren
    ladeplan = await _step_8_ladeplan_optimieren(
        route,
        energie_ergebnisse,
        anfrage.fahrzeugprofil,
        start_soc_pct,
        ziel_soc_pct,
        baustellen,
        anfrage.abfahrtszeit,
        zwischenstopps=zwischenstopps_mit_wartezeit,
        charging_provider=charging_provider,
        ladedauer_vorgaben=ladedauer_map,
        faehr_zeitfenster=faehr_pins,
    )

    # 9b. Fuer jeden Ladehalt eine echte Hin-und-zurueck-Verbindung zur
    # Ladestation routen (siehe `_step_lade_detours_routen`), damit die Karte
    # den Abstecher strassengetreu statt als Luftlinie zeigt.
    ladehalt_detouren = await _step_lade_detours_routen(
        routing_provider,
        route,
        ladeplan,
        anfrage.abfahrtszeit,
        anfrage.fahrzeugprofil,
    )

    # 10. Step 9: ETA aktualisieren
    _ = _step_9_eta_aktualisieren(segment_eta_liste, ladeplan)

    # 11. Step 10: Simulation aufrufen
    simulationsergebnis = simulate_trip(
        route=route,
        charging_plan=ladeplan,
        segment_energy=energie_ergebnisse,
        weather_samples=wetter_samples,
        start_soc_pct=start_soc_pct,
        max_iterations=3,
        convergence_threshold_minutes=30.0,
        output_resolution_seconds=60,
        abfahrtszeit=anfrage.abfahrtszeit,
        battery_capacity_kwh=anfrage.fahrzeugprofil.batteriekapazitaet_kwh,
        ladehalt_detouren=ladehalt_detouren,
    )

    # 12. Step 11: Ergebnis zurückgeben
    return simulationsergebnis


# =============================================================================
# FastAPI-Endpunkt
# =============================================================================


logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Verwaltet den Lebenszyklus der prozessweiten Provider-Ressourcen.

    Der GraphHopper-HTTP-Client und der Tesla-Supercharger-DB-Zugriff werden
    einmalig beim Start erzeugt (Connection-/Verbindungs-Pooling über alle
    Requests hinweg) statt pro Request neu aufgebaut zu werden. Die
    GraphHopper-Basis-URL ist über die Umgebungsvariable `GRAPHHOPPER_URL`
    konfigurierbar (Default: `http://localhost:8989`, siehe README.md).
    """
    base_url = os.environ.get(GRAPHHOPPER_URL_ENV_VAR, DEFAULT_GRAPHHOPPER_BASE_URL)
    app.state.graphhopper_client = GraphHopperClient(base_url=base_url)
    # TeslaChargingStationProvider öffnet beim Konstruieren eine SQLite-Verbindung
    # zu data/tesla_superchargers.db (siehe README.md) und cached die geladenen
    # Stationen prozessweit - einmalig hier erzeugen statt pro Request neu zu
    # öffnen/laden.
    app.state.charging_provider = TeslaChargingStationProvider()
    try:
        yield
    finally:
        await app.state.graphhopper_client.close()
        app.state.charging_provider.close()


app = FastAPI(title="Tesla Trip Planner API", version="0.1.0", lifespan=_lifespan)


def get_routing_provider(request: Request) -> RoutingProvider:
    """FastAPI-Dependency: liefert den produktiven RoutingProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `GraphHopperClient` für echtes Straßenrouting über OSM-Daten. In Tests via
    `app.dependency_overrides[get_routing_provider]` durch `FakeRoutingProvider`
    ersetzbar (siehe AGENTS.md: keine Live-Calls externer Datenquellen in
    Unit-Tests).
    """
    return GraphHopperRoutingProvider(request.app.state.graphhopper_client)


def get_charging_provider(request: Request) -> ChargingStationProvider:
    """FastAPI-Dependency: liefert den produktiven ChargingStationProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `TeslaChargingStationProvider` (SQLite-DB `data/tesla_superchargers.db`,
    siehe README.md) für echte Supercharger-Standorte. In Tests via
    `app.dependency_overrides[get_charging_provider]` durch eine
    `FakeChargingStationProvider`-Instanz mit angepassten Stationen ersetzbar
    (siehe AGENTS.md: keine Live-Calls externer Datenquellen in Unit-Tests).
    """
    # `app.state` ist bei Starlette/FastAPI dynamisch typisiert (Any) - der
    # explizite cast dokumentiert die durch `_lifespan` garantierte Invariante
    # (dort wird `charging_provider` als `TeslaChargingStationProvider`
    # gesetzt, die das `ChargingStationProvider`-Protocol erfüllt).
    return cast("ChargingStationProvider", request.app.state.charging_provider)


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


@app.post("/trips", response_model=TripSimulationResultAPI, status_code=201)
async def create_trip_endpoint(
    request: TripRequestAPI,
    # B008: Depends(...) im Default ist das FastAPI-Standardidiom für Dependency
    # Injection, kein veränderliches Objekt/kein echter Bug (siehe FastAPI-Doku).
    routing_provider: RoutingProvider = Depends(get_routing_provider),  # noqa: B008
    charging_provider: ChargingStationProvider = Depends(get_charging_provider),  # noqa: B008
) -> TripSimulationResultAPI:
    """Erstellt eine neue Reise-Simulation.

    Nutzt `create_trip_simulation()` zur Orchestrierung aller 11 Datenfluss-Schritte.
    Routing erfolgt über den echten GraphHopper-Server (`get_routing_provider`);
    ohne laufenden Server (siehe README.md) schlägt der Request mit 502 fehl.
    """
    # TripRequestAPI nach TripRequest konvertieren
    anfrage_dict: dict[str, object] = {
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

    erkannte_faehren: list[FaehrSegment] = []

    def _faehren_erfassen(faehren: list[FaehrSegment]) -> None:
        nonlocal erkannte_faehren
        erkannte_faehren = faehren

    route_geometrie: list[Coordinate] = []

    def _route_erfassen(route: Route) -> None:
        nonlocal route_geometrie
        route_geometrie = route.geometrie

    try:
        ergebnis = await create_trip_simulation(
            anfrage_dict,
            routing_provider=routing_provider,
            charging_provider=charging_provider,
            start_soc_pct=request.start_soc_pct,
            ziel_soc_pct=request.ziel_soc_pct,
            faehren_observer=_faehren_erfassen,
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
                for f in erkannte_faehren
            ],
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Route nicht durchführbar: {e!s}",
        ) from e
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Routing-Server (GraphHopper) nicht erreichbar oder lieferte einen Fehler: {e}",
        ) from e
    except Exception as e:
        logger.exception(
            "Fehler bei der Routensimulation: %s", e, extra={"traceback": traceback.format_exc()}
        )
        raise HTTPException(status_code=500, detail=f"Simulation fehlgeschlagen: {e!s}") from e
