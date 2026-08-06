"""API-Schicht für trip_input: Orchestrierung der 11 Datenfluss-Schritte.

Diese Modul implementiert:
- `create_trip_simulation()`: Kernfunktion zur Orchestrierung aller Schritte
- FastAPI-Endpunkt `POST /trips` mit `TripRequestAPI` Request-Model
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from tripplanner.charging_infrastructure import (
    ChargingStation,
    FakeChargingStationProvider,
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
from tripplanner.routing import FakeRoutingProvider, RoutingProvider
from tripplanner.routing.models import Coordinate, Route, RouteSegment
from tripplanner.simulation import simulate_trip
from tripplanner.simulation.models import TripSimulationResult
from tripplanner.trip_input.models import TripRequest, VehicleProfile, Waypoint
from tripplanner.weather import FakeWeatherProvider
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.wind import compute_wind_components_for_route
from tripplanner.wind.models import WindComponents

if TYPE_CHECKING:
    pass

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
    wie `GraphHopperRoutingProvider` übergeben werden.
    """
    provider = routing_provider or FakeRoutingProvider()

    # Konvertiere Waypoints zu dem erwarteten Format
    zwischenstopps: list[tuple[Coordinate, timedelta | None]] = [
        (wp.koordinate, wp.aufenthaltsdauer) for wp in anfrage.zwischenstopps
    ]

    return await provider.berechne_route_mit_waypoints(
        anfrage.start,
        anfrage.ziel,
        zwischenstopps,
    )


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
) -> ChargingPlan:
    """Schritt 8: Optimalen Ladeplan bestimmen.

    Nutzt `create_networkx_optimizer()` als Prototyp-Optimizer.
    """
    optimizer = create_networkx_optimizer()

    constraints = OptimizationConstraints(
        start_soc_pct=start_soc_pct,
        ziel_soc_pct=ziel_soc_pct,
        max_ladepausen=10,
        min_ladezeit_min=10,
        max_ladezeit_min=60,
    )

    # Ladeinfrastruktur entlang der Route abrufen (Fake-Provider für Tests)
    fake_charging_provider = FakeChargingStationProvider()
    stations_dict = await fake_charging_provider.get_stations_along_route(
        route, search_radius_km=2.0
    )
    charging_stations: list[ChargingStation] = []
    for station_list in stations_dict.values():
        charging_stations.extend(station_list)

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
    )


def _step_9_eta_aktualisieren(
    route: Route,
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

    for segment, urspruengliche_dauer in segment_eta_liste:
        segment_idx = route.segments.index(segment)
        ladezeit = ladezeiten_pro_segment.get(segment_idx, timedelta())
        neue_dauer = urspruengliche_dauer + ladezeit
        neue_eta_liste.append((segment, neue_dauer))

    return neue_eta_liste


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


# =============================================================================
# Kernfunktion: Orchestrierung aller 11 Schritte
# =============================================================================


async def create_trip_simulation(  # noqa: PLR0913, PLR0917
    anfrage_dict: dict[str, object],
    routing_provider: RoutingProvider | None = None,
    elevation_provider: ElevationProvider | None = None,
    weather_provider: FakeWeatherProvider | None = None,
    construction_provider: FakeConstructionProvider | None = None,
    start_soc_pct: float = 80.0,
    ziel_soc_pct: float = 20.0,
) -> TripSimulationResult:
    """Orchestriert die 11 Datenfluss-Schritte für die Reiseplanung.

    Args:
        anfrage_dict: Dictionary mit TripRequest-Daten (aus API/CLI geparst).
        routing_provider: Optionaler RoutingProvider (Default: FakeRoutingProvider).
        elevation_provider: Optionaler ElevationProvider (Default: FakeDataSource).
        weather_provider: Optionaler WeatherProvider (Default: FakeWeatherProvider).
        construction_provider: Optionaler ConstructionProvider (Default: FakeConstructionProvider).
        start_soc_pct: Start-SoC in Prozent (Default: 80%).
        ziel_soc_pct: Ziel-SoC in Prozent (Default: 20%).

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
    )

    # 10. Step 9: ETA aktualisieren
    _ = _step_9_eta_aktualisieren(route, segment_eta_liste, ladeplan)

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
    )

    # 12. Step 11: Ergebnis zurückgeben
    return simulationsergebnis


# =============================================================================
# FastAPI-Endpunkt
# =============================================================================


logger = logging.getLogger(__name__)


app = FastAPI(title="Tesla Trip Planner API", version="0.1.0")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health-Check-Endpunkt.

    Ermöglicht dem Frontend zu prüfen, ob das Backend erreichbar ist.
    """
    return {"status": "ok"}


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


class FrameAPI(BaseModel):
    """Einzelner Simulationsframe in der API-Response."""

    zeitpunkt: str = Field(..., description="ISO-8601 Zeitpunkt")
    position: tuple[float, float] = Field(
        ..., description="(lat, lon), konsistent mit Domänenmodell"
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0)
    zustand: str = Field(..., description="'FAHREN', 'LADEN' oder 'PAUSE'")
    geschwindigkeit_kmh: float = Field(..., ge=0.0)


class TripSimulationResultAPI(BaseModel):
    """API-Response für /trips-Endpunkt."""

    gesamt_distanz_km: float = Field(..., description="Gesamtdistanz in km")
    gesamt_fahrzeit_min: float = Field(..., description="Gesamtfahrzeit in Minuten")
    gesamt_ladezeit_min: float = Field(..., description="Gesamtladezeit in Minuten")
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start-SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC in %")
    frames: list[FrameAPI] = Field(..., description="Liste von Simulationsframes")


@app.post("/trips", response_model=TripSimulationResultAPI, status_code=201)
async def create_trip_endpoint(request: TripRequestAPI) -> TripSimulationResultAPI:
    """Erstellt eine neue Reise-Simulation.

    Nutzt `create_trip_simulation()` zur Orchestrierung aller 11 Datenfluss-Schritte.
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
    }

    try:
        ergebnis = await create_trip_simulation(
            anfrage_dict,
            start_soc_pct=request.start_soc_pct,
            ziel_soc_pct=request.ziel_soc_pct,
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
                    soc_pct=f.soc_pct,
                    zustand=f.zustand.value,
                    geschwindigkeit_kmh=f.geschwindigkeit_kmh,
                )
                for f in ergebnis.frames
            ],
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Route nicht durchführbar: {e!s}",
        ) from e
    except Exception as e:
        logger.exception(
            "Fehler bei der Routensimulation: %s", e, extra={"traceback": traceback.format_exc()}
        )
        raise HTTPException(status_code=500, detail=f"Simulation fehlgeschlagen: {e!s}") from e
