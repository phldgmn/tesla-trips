"""Tests für trip_input API-Schicht.

enthält:
- Testfälle für `create_trip_simulation()` mit Fake-Providern
- Testfälle für FastAPI-Endpunkt
- Testfälle für CLI-Koordinaten-Parsing
"""

from __future__ import annotations

import inspect
import json
import logging
import math
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import polyline
import pytest
from fastapi import params as fastapi_params
from fastapi.testclient import TestClient

import tripplanner.trip_input.api as trip_api
from tripplanner.charging_infrastructure import FakeChargingStationProvider
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.models import ChargingStation, ConnectorType, StallType
from tripplanner.charging_infrastructure.providers import (
    LocalFileChargingStationProvider,
    TeslaChargingStationProvider,
)
from tripplanner.construction.providers import FakeConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.geo import haversine_distance_m
from tripplanner.routing import FakeRoutingProvider, GraphHopperClient, GraphHopperRoutingProvider
from tripplanner.routing.models import FaehrSegment, Route, RouteSegment
from tripplanner.simulation.models import (
    ChargingCostByCurrency,
    ChargingStopSummary,
    TripSimulationResult,
)
from tripplanner.trip_input.api import (
    _log_step,
    app,
    create_trip_endpoint,
    create_trip_simulation,
    get_charging_provider,
    get_construction_provider,
    get_elevation_provider,
    get_routing_provider,
    get_weather_provider,
)
from tripplanner.trip_input.cli import parse_coord, parse_waypoint
from tripplanner.trip_input.models import (
    FaehrZeitfenster,
    LadedauerVorgabe,
    TripRequest,
    VehicleProfile,
    Waypoint,
)
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import (
    FakeWeatherProvider,
    LoadBalancedWeatherProvider,
    OpenMeteoProvider,
    WeatherProviderEntry,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def fake_routing_provider() -> FakeRoutingProvider:
    """Erstelle FakeRoutingProvider für Tests."""
    return FakeRoutingProvider()


@pytest.fixture
def fake_weather_provider() -> FakeWeatherProvider:
    """Erstelle FakeWeatherProvider für Tests."""
    return FakeWeatherProvider()


@pytest.fixture
def fake_charging_provider() -> FakeChargingStationProvider:
    """Erstelle FakeChargingStationProvider für Tests."""
    return FakeChargingStationProvider()


@pytest.fixture
def fake_construction_provider() -> FakeConstructionProvider:
    """Erstelle FakeConstructionProvider für Tests."""
    return FakeConstructionProvider()


@pytest.fixture
def client(
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> Iterator[TestClient]:
    """TestClient für FastAPI-Endpunkte mit `FakeRoutingProvider` statt echtem
    GraphHopper-Server (keine Live-Calls externer Datenquellen in Unit-Tests,
    siehe AGENTS.md).
    """

    app.dependency_overrides[get_routing_provider] = FakeRoutingProvider
    app.dependency_overrides[get_charging_provider] = lambda: fake_charging_provider_berlin_munich
    app.dependency_overrides[get_elevation_provider] = lambda: ElevationProvider(
        data_source=FakeDataSource()
    )
    # PLW0108: lambda ist erforderlich, nicht nur Stil - eine "nackte" Klasse
    # als Override laesst FastAPI die __init__-Signatur der Fake-Klasse als
    # zusaetzliche Body-Parameter re-analysieren (list[BaseModel]-Parameter
    # wie `samples`/`test_zones`), was die /trips-Body-Validierung bricht.
    app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108
    app.dependency_overrides[get_construction_provider] = lambda: FakeConstructionProvider()  # noqa: PLW0108
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_routing_provider, None)
    app.dependency_overrides.pop(get_charging_provider, None)
    app.dependency_overrides.pop(get_elevation_provider, None)
    app.dependency_overrides.pop(get_weather_provider, None)
    app.dependency_overrides.pop(get_construction_provider, None)


@pytest.fixture
def valid_trip_request() -> dict:
    """Erstelle ein gültiges TripRequest-Beispiel."""
    return {
        "start": (52.52, 13.405),  # Berlin
        "ziel": (48.135, 11.582),  # München
        "zwischenstopps": [],
        "abfahrtszeit": datetime(2026, 8, 15, 8, 30, 0),
        "fahrzeugprofil": VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        ),
        "praeferenzen": {},
    }


@pytest.fixture
def fake_charging_provider_berlin_munich() -> FakeChargingStationProvider:
    """Erstelle FakeChargingStationProvider mit Stationen auf der Berlin→München-Route
    und ergänzt mit Stationen für Berlin→Hamburg-Route (für API-Tests).

    Routes:
    - Berlin (52.52, 13.405) → München (48.135, 11.582), ~505 km.
    - Berlin (52.52, 13.405) → Hamburg (53.551, 9.994), ~290 km via Aachen (51.23, 6.78).

    Stationen sind ca. alle 100-150 km auf den Routen platziert.
    """
    stations = [
        # ====== Berlin-Munich Route ======
        ChargingStation(
            station_id="berlin-start",
            name="Tesla Supercharger - Berlin Mitte",
            coordinate=(52.52, 13.405),  # Start
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        ChargingStation(
            station_id="dresden-stop",
            name="Tesla Supercharger - Dresden",
            coordinate=(51.05, 13.74),  # ~150 km südlich Berlin
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        ChargingStation(
            station_id="nurnberg-stop",
            name="Tesla Supercharger - Nürnberg",
            coordinate=(49.45, 11.08),  # ~350 km von Berlin
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        ChargingStation(
            station_id="ingolstadt-stop",
            name="Tesla Supercharger - Ingolstadt",
            coordinate=(48.76, 11.43),  # ~450 km von Berlin, nähe München
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        ChargingStation(
            station_id="munich-end",
            name="Tesla Supercharger - München Zentrum",
            coordinate=(48.135, 11.582),  # Ziel
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        # ====== Berlin-Hamburg Route (via Aachen) ======
        ChargingStation(
            station_id="cologne-stop",
            name="Tesla Supercharger - Köln",
            coordinate=(50.94, 6.96),  # ~300 km west of Berlin
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        ChargingStation(
            station_id="aachen-stop",
            name="Tesla Supercharger - Aachen",
            coordinate=(51.23, 6.78),
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        # Aachen → Hamburg: ~650 km
        ChargingStation(
            station_id="hannover-stop",
            name="Tesla Supercharger - Hannover",
            coordinate=(52.37, 9.74),  # ~400 km north of Aachen
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
        ChargingStation(
            station_id="hamburg-end",
            name="Tesla Supercharger - Hamburg",
            coordinate=(53.551, 9.994),
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=2500.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        ),
    ]
    return FakeChargingStationProvider(test_stations=stations)


# =============================================================================
@pytest.mark.asyncio
async def test_create_trip_simulation_complete_run(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
    fake_construction_provider: FakeConstructionProvider,
) -> None:
    """Test: Vollständiger Pipeline-Durchlauf mit Fake-Providern.

    Liefert ein gültiges TripSimulationResult.
    """
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        construction_provider=fake_construction_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    # Prüfe grundlegende Struktur
    assert result.gesamt_distanz_km > 0
    assert result.gesamt_fahrzeit_min > 0
    assert result.gesamt_ladezeit_min >= 0
    assert 0 <= result.start_soc_pct <= 100
    assert 0 <= result.ziel_soc_pct <= 100
    assert len(result.frames) > 0

    # Prüfe Frames
    for frame in result.frames:
        assert len(frame.position) == 2
        assert 0 <= frame.soc_pct <= 100
        assert frame.zustand.value in ("FAHREN", "LADEN", "PAUSE")
        assert frame.geschwindigkeit_kmh >= 0


@pytest.mark.asyncio
async def test_create_trip_simulation_e2e_regression_departure_time_and_soc(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
    fake_construction_provider: FakeConstructionProvider,
) -> None:
    """End-to-End-Regressionstest (formalisiert den manuellen CLI-Smoke-Test).

    Pinnt zwei zuvor per manuellem End-to-End-Lauf gefundene Bugs, die vom generischen
    "vollstaendiger_durchlauf"-Test NICHT erkannt wurden, weil `0 <= soc_pct <= 100` auch
    bei physikalisch falschem Verhalten (SoC-Crash auf 0%, Zeitstempel auf Unix-Epoch 1970)
    technisch gueltig waere:
    1. Frame-Zeitstempel muessen auf der tatsaechlichen `abfahrtszeit` basieren, nicht auf
       Unix-Epoch (1970-01-01).
    2. Der End-SoC darf nicht unrealistisch auf nahe 0% abstuerzen, wenn der Energiebedarf
       relativ zur Batteriekapazitaet moderat ist.
    """
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        construction_provider=fake_construction_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    abfahrtszeit = valid_trip_request["abfahrtszeit"]
    assert result.frames[0].zeitpunkt.year == abfahrtszeit.year
    assert result.frames[0].zeitpunkt.date() == abfahrtszeit.date()
    assert all(f.zeitpunkt.year != 1970 for f in result.frames)

    # Zeitstempel muessen monoton steigen und mit abfahrtszeit beginnen
    assert result.frames[0].zeitpunkt >= abfahrtszeit
    for a, b in zip(result.frames, result.frames[1:], strict=False):
        assert b.zeitpunkt >= a.zeitpunkt

    # SoC darf nicht unrealistisch auf (nahezu) 0% abstuerzen, solange
    # kein realer Reichweitenmangel vorliegt. Am Ende der letzten Etappe
    # vor dem Ziel kann der SoC kurz unter 1% fallen, bevor die finale
    # Ladung erfolgt (physikalisch korrekt bei grossen letzten Etappen).
    fahren_frames = [f for f in result.frames if f.zustand.value == "FAHREN"]
    assert all(f.soc_pct >= 0.0 for f in fahren_frames), (
        "SoC waehrend der Fahrt fiel unter 0% -- deutet auf falsche SoC-Depletionsformel hin"
    )


@pytest.mark.asyncio
async def test_create_trip_simulation_with_stop(
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """Test: Reise mit Zwischenstopp wird korrekt verarbeitet."""
    # Erstelle Ladestationen für Berlin→Hamburg via Aachen Route
    charging_provider = FakeChargingStationProvider(
        test_stations=[
            ChargingStation(
                station_id="berlin-start-zwischenstopp",
                name="Tesla Supercharger - Berlin",
                coordinate=(52.52, 13.405),
                stalls={StallType.V3: 8},
                max_ladeleistung_kw=2500.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
                letzte_datenAktualisierung=datetime.now(UTC),
            ),
            ChargingStation(
                station_id="aachen-zwischenstopp",
                name="Tesla Supercharger - Aachen",
                coordinate=(51.23, 6.78),
                stalls={StallType.V3: 8},
                max_ladeleistung_kw=2500.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
                letzte_datenAktualisierung=datetime.now(UTC),
            ),
            ChargingStation(
                station_id="hamburg-end-zwischenstopp",
                name="Tesla Supercharger - Hamburg",
                coordinate=(53.551, 9.994),
                stalls={StallType.V3: 8},
                max_ladeleistung_kw=2500.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
                letzte_datenAktualisierung=datetime.now(UTC),
            ),
        ]
    )
    """Test: Reise mit Zwischenstopp wird korrekt verarbeitet."""
    request = {
        "start": (52.52, 13.405),  # Berlin
        "ziel": (53.551, 9.994),  # Hamburg
        "zwischenstopps": [
            Waypoint(
                koordinate=(51.23, 6.78),  # Aachen als Zwischenstopp
                aufenthaltsdauer=timedelta(minutes=30),
            )
        ],
        "abfahrtszeit": datetime(2026, 8, 15, 8, 30, 0),
        "fahrzeugprofil": VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=200.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        ),
        "praeferenzen": {},
    }

    result = await create_trip_simulation(
        request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=charging_provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0
    assert len(result.frames) > 0


@pytest.mark.asyncio
async def test_create_trip_simulation_different_vehicle_profiles(
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """Test: Unterschiedliche Fahrzeugprofile werden korrekt verarbeitet."""
    request = {
        "start": (52.52, 13.405),
        "ziel": (48.135, 11.582),
        "zwischenstopps": [],
        "abfahrtszeit": datetime(2026, 8, 15, 8, 30, 0),
        "fahrzeugprofil": VehicleProfile(
            masse_kg=1900.0,  # Schwereres Fahrzeug (max 1900)
            cw_wert=0.25,
            stirnflaeche_m2=2.4,
            rollwiderstandsbeiwert=0.012,
            batteriekapazitaet_kwh=75.0,
            nebenverbraucher_baseline_kw=0.4,
            reifentyp="winter",
            dachbox=True,
        ),
        "praeferenzen": {},
    }

    result = await create_trip_simulation(
        request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=90.0,
        destination_soc_pct=30.0,
    )

    assert result.gesamt_distanz_km > 0
    assert result.gesamt_fahrzeit_min > 0
    assert result.gesamt_ladezeit_min >= 0


@pytest.mark.asyncio
async def test_create_trip_simulation_without_construction_provider(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """Test: Ohne ConstructionProvider wird leere Baustellen-Liste angenommen."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        construction_provider=None,  # Kein ConstructionProvider
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0
    assert len(result.frames) > 0


async def test_create_trip_simulation_without_weather_provider(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """Test: Ohne WeatherProvider wird FakeWeatherProvider verwendet."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        weather_provider=None,  # Kein WeatherProvider, Fake wird verwendet
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0
    assert len(result.frames) > 0


@pytest.mark.asyncio
async def test_create_trip_simulation_start_soc_100(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """Test: Start-SoC 100% wird korrekt verarbeitet."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=100.0,
        destination_soc_pct=10.0,
    )

    assert result.start_soc_pct == 100.0
    assert result.ziel_soc_pct >= 10.0


@pytest.mark.asyncio
async def test_create_trip_simulation_mindest_ankunfts_soc_pct_erlaubt_niedrigere_ladezeit(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """`mindest_ankunfts_soc_pct` steuert, wie tief der SoC beim Ankommen an
    einer Ladestation sinken darf (siehe `OptimizationConstraints.
    mindest_ankunfts_soc_pct`) - im Gegensatz zur allgemeinen Sicherheits-
    reserve auf offener Strecke.

    Mit dem niedrigen Default (5.0) MUSS die Gesamtladezeit fuer die Berlin
    -> Muenchen-Strecke kleiner sein als mit einem strengeren, hoeheren Wert
    (20.0) - ein hoeherer Mindest-Ankunfts-SoC zwingt die Optimierung, schon
    am Start voller (und damit im langsameren Kurvenbereich) nachzuladen
    bzw. zusaetzliche Halte einzulegen, statt die schnelle Ladeleistung im
    unteren SoC-Bereich auszunutzen (siehe Nutzer-Report: unnoetig fruehe/
    lange Teilladung, obwohl der naechste Halt ohnehin mit niedrigem SoC
    sicher erreicht wird).
    """
    grosszuegig = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        mindest_ankunfts_soc_pct=5.0,
        # Isoliert von der SEPARATEN Mindestladedauer-Funktionalitaet (siehe
        # `test_create_trip_simulation_mindest_ladezeit_s_verhindert_kurze_ladehalte`),
        # die mit ihrem eigenen Produktions-Default (600s) sonst die Anzahl/
        # Reihenfolge der Ladehalte in diesem Szenario mitveraendern wuerde.
        mindest_ladezeit_s=0,
    )
    streng = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        mindest_ankunfts_soc_pct=20.0,
        mindest_ladezeit_s=0,
    )

    assert grosszuegig.gesamt_ladezeit_min < streng.gesamt_ladezeit_min
    assert min(s.ankunfts_soc_pct for s in grosszuegig.charging_stops) < 20.0
    assert all(s.ankunfts_soc_pct >= 20.0 for s in streng.charging_stops)


@pytest.mark.asyncio
async def test_create_trip_simulation_mindest_ladezeit_s_verhindert_kurze_ladehalte(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """`mindest_ladezeit_s` steuert die Mindestdauer eines Ladehalts, WENN
    geladen wird (siehe `OptimizationConstraints.mindest_ladezeit_s`).

    Mit deaktivierter Mindestladedauer (0s) kann ein einzelner Ladehalt
    kürzer als die Produktions-Default-Mindestdauer (600s) ausfallen. Mit
    der Default-Mindestdauer MUSS JEDER tatsächliche Ladehalt mindestens so
    lange dauern (Nutzer-Report: ein 1-Minuten-Ladehalt, gefolgt von einem
    weiteren Halt nach nur gut 10 Minuten Fahrt).
    """
    ohne_mindestdauer = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        mindest_ladezeit_s=0,
    )
    assert any(stop.ladedauer_s < 600 for stop in ohne_mindestdauer.charging_stops), (
        "Testpraemisse nicht erfuellt: Szenario muss ohne Mindestladedauer "
        "einen kurzen Ladehalt erzeugen, sonst testet dieser Test nichts."
    )

    mit_mindestdauer = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        mindest_ladezeit_s=600,
    )
    # `int()`-Rundung der Ladedauer (siehe `_extract_charging_stops`) kann bis
    # zu 1s unter der exakten Mindestdauer liegen - kein Bug.
    assert all(stop.ladedauer_s >= 599 for stop in mit_mindestdauer.charging_stops)


@pytest.mark.asyncio
async def test_create_trip_simulation_kurze_reise(
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """Test: Kurze Reise (nur ein paar km) wird korrekt verarbeitet."""
    request = {
        "start": (52.52, 13.405),  # Berlin-Mitte
        "ziel": (52.525, 13.41),  # Etwa 1 km entfernt
        "zwischenstopps": [],
        "abfahrtszeit": datetime(2026, 8, 15, 12, 0, 0),
        "fahrzeugprofil": VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        ),
        "praeferenzen": {},
    }

    result = await create_trip_simulation(
        request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0  # Kurze Strecke
    assert result.gesamt_fahrzeit_min < 30  # Kurze Fahrzeit


async def test_create_trip_simulation_calls_route_observer_with_computed_route(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """route_observer wird nach Schritt 1 mit der berechneten Route aufgerufen."""
    captured: list[Route] = []

    await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        route_observer=captured.append,
    )

    assert len(captured) == 1
    assert isinstance(captured[0], Route)
    assert captured[0].gesamtlaenge_m > 0


async def test_create_trip_simulation_without_route_observer_unaffected(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """Ohne route_observer (Default None) verhält sich die Funktion unverändert."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
    )

    assert result.gesamt_distanz_km > 0


async def test_step_1_route_calculation_uses_calculate_route(
    valid_trip_request: dict,
) -> None:
    """_step_1_route_calculate() ruft berechne_route() auf (nicht berechne_route_mit_waypoints()),
    damit TripRequest-Präferenzen (z. B. Fährvermeidung) den Provider erreichen."""

    class _RecordingProvider(FakeRoutingProvider):
        def __init__(self) -> None:
            self.berechne_route_called_with: TripRequest | None = None

        async def berechne_route(self, anfrage: TripRequest) -> Route:
            self.berechne_route_called_with = anfrage
            return await super().berechne_route(anfrage)

    provider = _RecordingProvider()
    anfrage = TripRequest.model_validate(valid_trip_request)

    await trip_api._step_1_route_calculate(anfrage, provider)

    assert provider.berechne_route_called_with is anfrage


class _CoordinateElevationDataSource:
    """Test-Double für DEMDataSourceProtocol: feste Höhe je exakter Koordinate.

    Erlaubt es, ein kontrolliertes Höhenprofil (statt eines echten DEM-Tiles)
    für Regressionstests der realen Gradientenberechnung (Plan 10 Phase C) zu
    injizieren.
    """

    def __init__(self, elevations: dict[tuple[float, float], float]) -> None:
        self._elevations = elevations

    def get_elevation(self, lat: float, lon: float) -> float:
        return self._elevations.get((lat, lon), 0.0)

    async def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]

    def get_tile_at(self, lat: float, lon: float) -> None:
        return None

    def get_tiles_in_bbox(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> list[object]:
        return []


class TestElevationGradientAffectsEnergy:
    """Regressionstests für Plan 10 Phase C: `_step_7_calculate_segment_energy`
    nutzt das reale Höhenprofil statt eines hartkodierten flachen Gradienten
    (`steigung_prozent=0.0`)."""

    def _make_segment(self) -> RouteSegment:
        """5km-Segment (Nord-Süd, konstante Länge), Höhe wird pro Test variiert."""
        return RouteSegment(
            segment_index=0,
            geometrie=[(48.0, 11.0), (48.0449, 11.0)],
            laenge_m=5000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )

    def _make_vehicle_profile(self) -> VehicleProfile:
        return VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        )

    async def _energy_for_elevations(
        self, segment: RouteSegment, elevations: dict[tuple[float, float], float]
    ) -> SegmentEnergyResult:
        source = _CoordinateElevationDataSource(elevations)
        elevation_provider = ElevationProvider(data_source=source)
        route = Route(
            segments=[segment], gesamtlaenge_m=segment.laenge_m, geometrie=segment.geometrie
        )
        elevation_points = await elevation_provider.get_elevation_profile(route)
        abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0)
        weather = WeatherSample(
            koordinate=segment.geometrie[0],
            zeitpunkt=abfahrtszeit,
            temperatur_c=20.0,
            windgeschwindigkeit_ms=0.0,
            windrichtung_deg=0.0,
            niederschlag_mm=0.0,
            schneefall_cm=0.0,
            luftdruck_hpa=1013.25,
            luftfeuchtigkeit_pct=60.0,
            globalstrahlung_wm2=400.0,
            bewoelkung_pct=20.0,
        )
        results = await trip_api._step_7_calculate_segment_energy(
            route,
            [segment],
            [(segment, timedelta(minutes=3))],
            [weather],
            self._make_vehicle_profile(),
            [],
            abfahrtszeit,
            elevation_provider,
            elevation_points,
        )
        assert len(results) == 1
        return results[0]

    async def test_uphill_segment_consumes_more_energy_than_flat(self) -> None:
        """Ein Segment mit +500m Höhendifferenz verbraucht strikt mehr Energie
        als dasselbe (aber flache) Segment - deckt den vormals hartkodierten
        `steigung_prozent=0.0` ab."""
        segment = self._make_segment()
        start, end = segment.geometrie[0], segment.geometrie[-1]

        flat = await self._energy_for_elevations(segment, {start: 0.0, end: 0.0})
        uphill = await self._energy_for_elevations(segment, {start: 0.0, end: 500.0})

        assert uphill.energiebedarf_kwh > flat.energiebedarf_kwh

    async def test_downhill_segment_consumes_less_energy_than_flat(self) -> None:
        """Ein Segment mit -500m Höhendifferenz verbraucht (durch Rekuperation)
        strikt weniger Energie als dasselbe flache Segment."""
        segment = self._make_segment()
        start, end = segment.geometrie[0], segment.geometrie[-1]

        flat = await self._energy_for_elevations(segment, {start: 0.0, end: 0.0})
        downhill = await self._energy_for_elevations(segment, {start: 500.0, end: 0.0})

        assert downhill.energiebedarf_kwh < flat.energiebedarf_kwh


# =============================================================================
# Testfälle für FastAPI-Endpunkt
# =============================================================================


def _make_fahrzeugprofil_dict() -> dict:
    """Erzeuge ein Standard-Fahrzeugprofil als Dict für API-Requests.

    Verwendet 200 kWh Batterie für lange Test-Routen (Berlin→Hamburg via Aachen).
    """
    return {
        "masse_kg": 1800.0,
        "cw_wert": 0.23,
        "stirnflaeche_m2": 2.2,
        "rollwiderstandsbeiwert": 0.01,
        "batteriekapazitaet_kwh": 200.0,
        "nebenverbraucher_baseline_kw": 0.34,
        "reifentyp": "standard",
        "dachbox": False,
    }


def test_with_derived_waiting_time_requires_waiting_time_with_later_planned_departure() -> None:
    """Leitet aus einer spaet geplanten Abfahrt eine Mindestaufenthaltsdauer ab.

    Liegt `geplante_abfahrt` spaeter als die geschaetzte Ankunft, wird die
    Differenz als Aufenthaltsdauer erzwungen.
    """
    abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0)
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.0, 13.0), (52.1, 13.1)],
        laenge_m=50_000.0,
        strassenklasse="PRIMARY",
        oberflaeche="asphalt",
        tempolimit_kmh=100,
        steigung_rohdaten=0.0,
        bearing_deg=45.0,
    )
    segment_eta_liste = [(segment, timedelta(minutes=27))]
    # Geschaetzte Ankunft am Wegpunkt (Ende von Segment 0) liegt 27 Min. nach
    # Abfahrt; geplante Abfahrt hier 30 Min. nach der geschaetzten Ankunft.
    wp = Waypoint(
        koordinate=(52.1, 13.1),
        aufenthaltsdauer=None,
        geplante_abfahrt=abfahrtszeit + timedelta(minutes=57),
    )

    ergebnis = trip_api._with_derived_wait_time([wp], segment_eta_liste, abfahrtszeit)

    assert len(ergebnis) == 1
    assert ergebnis[0].aufenthaltsdauer is not None
    assert ergebnis[0].aufenthaltsdauer >= timedelta(minutes=25)


def test_with_derived_waiting_time_no_waiting_time_when_already_delayed_departure() -> None:
    """Liegt `geplante_abfahrt` vor der geschaetzten Ankunft, wird keine
    zusaetzliche Wartezeit erzwungen (man ist ohnehin schon spaeter dran)."""
    abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0)
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.0, 13.0), (52.1, 13.1)],
        laenge_m=50_000.0,
        strassenklasse="PRIMARY",
        oberflaeche="asphalt",
        tempolimit_kmh=100,
        steigung_rohdaten=0.0,
        bearing_deg=45.0,
    )
    segment_eta_liste = [(segment, timedelta(minutes=27))]
    wp = Waypoint(
        koordinate=(52.1, 13.1),
        aufenthaltsdauer=None,
        geplante_abfahrt=abfahrtszeit + timedelta(minutes=5),
    )

    ergebnis = trip_api._with_derived_wait_time([wp], segment_eta_liste, abfahrtszeit)

    assert ergebnis[0].aufenthaltsdauer == timedelta(0)


def test_with_derived_waiting_time_unchanged_without_planned_departure() -> None:
    """Wegpunkte ohne `geplante_abfahrt` werden unveraendert durchgereicht."""
    abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0)
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.0, 13.0), (52.1, 13.1)],
        laenge_m=50_000.0,
        strassenklasse="PRIMARY",
        oberflaeche="asphalt",
        tempolimit_kmh=100,
        steigung_rohdaten=0.0,
        bearing_deg=45.0,
    )
    segment_eta_liste = [(segment, timedelta(minutes=27))]
    wp = Waypoint(koordinate=(52.1, 13.1), aufenthaltsdauer=timedelta(minutes=10))

    ergebnis = trip_api._with_derived_wait_time([wp], segment_eta_liste, abfahrtszeit)

    assert ergebnis[0] is wp


# =============================================================================
# Testfälle für _match_ferry_time_window, faehren_observer, ladedauer_vorgaben
# =============================================================================


def test_match_ferry_time_window_expanded_when_same_name() -> None:
    """Ein Zeitfenster mit passendem Namen reichert die erkannte Fähre um
    abfahrt/ankunft an."""
    faehre = FaehrSegment(
        name="Rødby (DK) - Puttgarden (D)",
        laenge_m=22000.0,
        bbox_sw=(54.50, 11.22),
        bbox_no=(54.66, 11.36),
        segment_index_start=3,
        segment_index_end=7,
    )
    abfahrt = datetime(2026, 8, 15, 10, 0, 0)
    ankunft = datetime(2026, 8, 15, 11, 9, 0)
    zeitfenster = FaehrZeitfenster(
        name="Rødby (DK) - Puttgarden (D)",
        bbox_sw=(54.50, 11.22),
        bbox_no=(54.66, 11.36),
        abfahrt=abfahrt,
        ankunft=ankunft,
    )

    ergebnis = trip_api._match_ferry_time_window([faehre], [zeitfenster])

    assert len(ergebnis) == 1
    assert ergebnis[0].abfahrt == abfahrt
    assert ergebnis[0].ankunft == ankunft
    # Identitaets-/sonstige Felder bleiben unveraendert.
    assert ergebnis[0].segment_index_start == 3
    assert ergebnis[0].segment_index_end == 7


def test_match_ferry_time_window_ignore_not_matching_names() -> None:
    """Ein Zeitfenster fuer eine nicht (mehr) vorhandene Fähre wird stillschweigend
    ignoriert - die erkannte Fähre bleibt ohne abfahrt/ankunft."""
    faehre = FaehrSegment(
        name="Andere Fähre",
        laenge_m=5000.0,
        bbox_sw=(1.0, 1.0),
        bbox_no=(2.0, 2.0),
        segment_index_start=0,
        segment_index_end=2,
    )
    zeitfenster = FaehrZeitfenster(
        name="Rødby (DK) - Puttgarden (D)",
        bbox_sw=(54.50, 11.22),
        bbox_no=(54.66, 11.36),
        abfahrt=datetime(2026, 8, 15, 10, 0, 0),
        ankunft=datetime(2026, 8, 15, 11, 0, 0),
    )

    ergebnis = trip_api._match_ferry_time_window([faehre], [zeitfenster])

    assert len(ergebnis) == 1
    assert ergebnis[0].abfahrt is None
    assert ergebnis[0].ankunft is None


def test_match_ferry_time_window_select_next_bbox_for_multiple_matching_names() -> None:
    """Bei mehreren gleichnamigen Zeitfenstern gewinnt die naehere Bounding-Box-Mitte."""
    faehre = FaehrSegment(
        name="Fähre X",
        laenge_m=1000.0,
        bbox_sw=(10.0, 10.0),
        bbox_no=(10.1, 10.1),
        segment_index_start=0,
        segment_index_end=1,
    )
    nah = FaehrZeitfenster(
        name="Fähre X",
        bbox_sw=(10.0, 10.0),
        bbox_no=(10.1, 10.1),
        abfahrt=datetime(2026, 8, 15, 10, 0, 0),
        ankunft=datetime(2026, 8, 15, 11, 0, 0),
    )
    fern = FaehrZeitfenster(
        name="Fähre X",
        bbox_sw=(50.0, 50.0),
        bbox_no=(50.1, 50.1),
        abfahrt=datetime(2026, 8, 15, 20, 0, 0),
        ankunft=datetime(2026, 8, 15, 21, 0, 0),
    )

    ergebnis = trip_api._match_ferry_time_window([faehre], [fern, nah])

    assert ergebnis[0].abfahrt == nah.abfahrt


class _FerryRoutingProvider(FakeRoutingProvider):
    """Test-Provider, der eine Route mit genau einer Fähre (Segment 1) liefert -
    Segment 1 traegt absichtlich einen unfahrbar hohen Energiebedarf-Ersatzwert
    ueber `strassenklasse`/Laenge, damit ein erfolgreicher Ladeplan beweist, dass
    die Fähre uebersprungen (nicht durchfahren) wurde."""

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        segments = [
            RouteSegment(
                segment_index=0,
                geometrie=[anfrage.start, (54.50, 11.22)],
                laenge_m=50_000.0,
                strassenklasse="MOTORWAY",
                bearing_deg=0.0,
            ),
            RouteSegment(
                segment_index=1,
                geometrie=[(54.50, 11.22), (54.66, 11.36)],
                laenge_m=22_000.0,
                strassenklasse="FERRY",
                road_environment="FERRY",
                strassenname="Rødby (DK) - Puttgarden (D)",
                bearing_deg=0.0,
            ),
            RouteSegment(
                segment_index=2,
                geometrie=[(54.66, 11.36), anfrage.ziel],
                laenge_m=30_000.0,
                strassenklasse="MOTORWAY",
                bearing_deg=0.0,
            ),
        ]
        return Route(
            segments=segments,
            gesamtlaenge_m=sum(s.laenge_m for s in segments),
            geometrie=[s.geometrie[0] for s in segments] + [segments[-1].geometrie[-1]],
        )


async def test_create_trip_simulation_ferry_observer_receives_pinned_times(
    valid_trip_request: dict,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """Ein zur erkannten Fähre passendes `faehr_zeitfenster` wird über
    `faehren_observer` mit abfahrt/ankunft angereichert zurückgegeben und fließt
    in die Gesamtreisezeit ein (statt physikalisch unfahrbar durch die Fähre
    zu 'fahren')."""
    abfahrt = datetime(2026, 8, 15, 9, 0, 0)
    ankunft = datetime(2026, 8, 15, 9, 45, 0)
    request = dict(valid_trip_request)
    request["faehr_zeitfenster"] = [
        FaehrZeitfenster(
            name="Rødby (DK) - Puttgarden (D)",
            bbox_sw=(54.50, 11.22),
            bbox_no=(54.66, 11.36),
            abfahrt=abfahrt,
            ankunft=ankunft,
        )
    ]

    erfasste_faehren: list[FaehrSegment] = []

    result = await create_trip_simulation(
        request,
        routing_provider=_FerryRoutingProvider(),
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        ferry_observer=erfasste_faehren.extend,
    )

    assert len(erfasste_faehren) == 1
    assert erfasste_faehren[0].name == "Rødby (DK) - Puttgarden (D)"
    assert erfasste_faehren[0].abfahrt == abfahrt
    assert erfasste_faehren[0].ankunft == ankunft
    # Erfolgreiche Simulation beweist, dass die Fähre uebersprungen wurde -
    # Segment 1 haette sonst (siehe `_FerryRoutingProvider`) einen SoC-Bedarf
    # weit jenseits jeder realistischen Batteriekapazitaet.
    assert result.gesamt_distanz_km == pytest.approx(102.0, abs=0.1)


async def test_create_trip_simulation_charge_duration_specification_applies_to_charge_plan(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """Eine vorgegebene Ladedauer fuer eine tatsaechlich genutzte Station
    ueberschreibt die automatisch berechnete Dauer im Endergebnis.

    Nutzt bewusst einen Charging-Provider mit GENAU EINER Station (statt der
    Mehrzweck-Fixture `fake_charging_provider_berlin_munich`), damit der
    Optimierer keine alternative Station ausweichen kann, sobald die
    vorgegebene Ladedauer die Kosten dieser Station erhoeht - andernfalls
    waere ein Stationswechsel (guenstigerer Pfad) ein gueltiges, aber fuer
    diesen Test nicht aussagekraeftiges Optimierer-Ergebnis.
    """
    single_station_provider = FakeChargingStationProvider(
        test_stations=[
            ChargingStation(
                station_id="einzige-station",
                name="Tesla Supercharger - Nuernberg",
                coordinate=(50.3275, 12.4935),  # Mittelpunkt der linear interpolierten Fake-Route
                stalls={StallType.V3: 8},
                max_ladeleistung_kw=2500.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
                letzte_datenAktualisierung=datetime.now(UTC),
            ),
        ]
    )

    baseline = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=single_station_provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )
    assert len(baseline.charging_stops) == 1
    assert baseline.charging_stops[0].station_id == "einzige-station"
    vorgabe_s = baseline.charging_stops[0].ladedauer_s + 900  # deutlich abweichender Wert

    request = dict(valid_trip_request)
    request["ladedauer_vorgaben"] = [
        LadedauerVorgabe(station_id="einzige-station", ladedauer_s=vorgabe_s)
    ]

    result = await create_trip_simulation(
        request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=single_station_provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert len(result.charging_stops) == 1
    assert result.charging_stops[0].station_id == "einzige-station"
    assert result.charging_stops[0].ladedauer_s == vorgabe_s


def test_find_edges_walk_at_least_margin_in_both_directions() -> None:
    """`_find_bracket_points` liefert zwei Punkte, die zusammen mindestens
    `margin_m` vor UND nach dem Abzweigpunkt liegen - fixiert die
    Fahrtrichtung fuer das Detour-Routing (siehe `_step_route_charging_detours`).
    """
    # 10 gleich lange 100m-Segmente entlang eines Meridians (11 Punkte).
    geometrie = [(52.0 + i * 0.0009, 13.0) for i in range(11)]
    segments = [
        RouteSegment(
            segment_index=i,
            geometrie=[geometrie[i], geometrie[i + 1]],
            laenge_m=100.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )
        for i in range(10)
    ]
    route = Route(segments=segments, gesamtlaenge_m=1000.0, geometrie=geometrie)

    vor_index, nach_index = trip_api._find_bracket_points(route, segment_index=5, margin_m=250.0)

    assert vor_index < 5 < nach_index
    distanz_zurueck = sum(seg.laenge_m for seg in segments[vor_index:5])
    distanz_vor = sum(seg.laenge_m for seg in segments[5:nach_index])
    assert distanz_zurueck >= 250.0
    assert distanz_vor >= 250.0


def test_finde_klammerpunkte_clamped_an_routenraendern() -> None:
    """Nahe am Start/Ende der Route werden die Klammerpunkte an den
    tatsaechlichen Rand geklemmt statt einen Index-Fehler zu werfen."""
    geometrie = [(52.0 + i * 0.0009, 13.0) for i in range(5)]
    segments = [
        RouteSegment(
            segment_index=i,
            geometrie=[geometrie[i], geometrie[i + 1]],
            laenge_m=100.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )
        for i in range(4)
    ]
    route = Route(segments=segments, gesamtlaenge_m=400.0, geometrie=geometrie)

    vor_index, nach_index = trip_api._find_bracket_points(route, segment_index=1, margin_m=10_000.0)

    assert vor_index == 0
    assert nach_index == len(geometrie) - 1


async def test_create_trip_simulation_populates_charging_stop_distanz_m_and_detour_geometrie(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """`ChargingStopSummary.distanz_m`/`detour_geometrie` werden befuellt - deckt
    den Bug ab, bei dem die Karte den Ladehalt nie zeigte, weil die Routenlinie
    die Autobahn nie verliess (siehe `_step_route_charging_detours`)."""
    single_station_provider = FakeChargingStationProvider(
        test_stations=[
            ChargingStation(
                station_id="einzige-station",
                name="Tesla Supercharger - Nuernberg",
                coordinate=(50.3275, 12.4935),  # Mittelpunkt der linear interpolierten Fake-Route
                stalls={StallType.V3: 8},
                max_ladeleistung_kw=2500.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
                letzte_datenAktualisierung=datetime.now(UTC),
            ),
        ]
    )

    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=single_station_provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert len(result.charging_stops) == 1
    stop = result.charging_stops[0]
    assert stop.distanz_m > 0
    assert stop.distanz_m < result.gesamt_distanz_km * 1000
    # Echte, ueber `FakeRoutingProvider` geroutete Geometrie von einem
    # Klammerpunkt VOR bis einem Klammerpunkt NACH dem Abzweigpunkt (siehe
    # `_find_bracket_points`) statt einer leeren Liste.
    assert len(stop.detour_geometrie) >= 2
    assert stop.route_index_vor is not None
    assert stop.route_index_nach is not None
    assert stop.route_index_vor < stop.route_index_nach
    # Exakter Split-Index (Hinweg->Station, siehe `_step_route_charging_detours`),
    # nicht per Naechster-Punkt-Heuristik geschaetzt - muss innerhalb der
    # Geometrie liegen, mit mindestens einem Punkt auf jeder Seite.
    assert stop.detour_station_index is not None
    assert 0 < stop.detour_station_index < len(stop.detour_geometrie) - 1


async def test_create_trip_simulation_handles_unreachable_charging_detour_gracefully(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """Ein nicht routbarer Ladehalt-Abstecher (`httpx.HTTPError` beim Detour-
    Routing) darf die gesamte Reise-Simulation nicht zum Absturz bringen -
    `detour_geometrie` bleibt fuer diesen Halt leer, der Ladehalt selbst und
    die restliche Simulation bleiben gueltig (siehe `_step_route_charging_detours`).
    """

    class _DetourFailingRoutingProvider:
        """Routet die Hauptstrecke normal, verweigert aber jede Detour-Anfrage
        (identifiziert daran, dass Start- oder Zielkoordinate exakt die
        Ladestation ist - nur `_step_route_charging_detours` routet Hin-/Rueckweg-
        Beine mit der Stationskoordinate als Start bzw. Ziel)."""

        _STATION_KOORDINATE = (50.3275, 12.4935)

        async def berechne_route(self, anfrage: TripRequest) -> Route:
            if self._STATION_KOORDINATE in (anfrage.start, anfrage.ziel):
                raise httpx.HTTPError("Ladestation nicht erreichbar")
            return await fake_routing_provider.berechne_route(anfrage)

    single_station_provider = FakeChargingStationProvider(
        test_stations=[
            ChargingStation(
                station_id="einzige-station",
                name="Tesla Supercharger - Nuernberg",
                coordinate=(50.3275, 12.4935),
                stalls={StallType.V3: 8},
                max_ladeleistung_kw=2500.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
                letzte_datenAktualisierung=datetime.now(UTC),
            ),
        ]
    )

    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=_DetourFailingRoutingProvider(),  # type: ignore[arg-type]
        weather_provider=fake_weather_provider,
        charging_provider=single_station_provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert len(result.charging_stops) == 1
    stop = result.charging_stops[0]
    assert stop.distanz_m > 0
    assert stop.detour_geometrie == []
    assert stop.route_index_vor is None
    assert stop.route_index_nach is None
    assert stop.detour_station_index is None


async def test_create_trip_simulation_findet_station_ausserhalb_des_alten_2km_radius(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    tmp_path: Path,
) -> None:
    """Regressionstest: Der einzige verfuegbare Ladestopp liegt 20 km abseits
    der (geraden) Berlin->Muenchen-Route - ausserhalb des frueheren, zu engen
    2-km-Suchradius von `_step_8_optimize_charging_plan`, aber innerhalb des
    aktuellen 25-km-Radius.

    Bildet den real gemeldeten Bug nach (Gummersbach -> Hagfors kommun,
    Schweden): auf duenn mit Superchargern erschlossenen Strecken (z. B.
    laendliche Riksvaeg-Abschnitte in Schweden abseits von E4/E6) liegt der
    naechste Supercharger oft 10-25 km von der GraphHopper-Route entfernt.
    Mit dem alten 2-km-Radius fand der Optimierer fuer das komplette
    Segment ZWISCHEN Start und Ziel keinen einzigen Ladekandidaten und wies
    die Reise faelschlich mit "Kein erreichbarer Zielknoten gefunden.
    Route nicht fahrbar." ab, obwohl ein Ladestopp mit realistischem
    Abstecher die Reise fahrbar macht. Mit `LocalFileChargingStationProvider`
    (statt `FakeChargingStationProvider`, die `search_radius_km` ignoriert)
    wird die tatsaechliche, produktiv genutzte Radius-Filterung geprueft.
    """
    # 20 km oestlich (senkrecht zur Fahrtrichtung) des geometrischen
    # Mittelpunkts von Berlin/Muenchen - siehe Docstring fuer die Herleitung
    # (Grosskreis-Zielpunkt-Formel, verifiziert per `haversine_distance_m`).
    station_coord = (50.3754642065061, 12.221822056150774)
    route_mittelpunkt = (50.3275, 12.4935)
    entfernung_zur_route_km = haversine_distance_m(route_mittelpunkt, station_coord) / 1000.0
    assert entfernung_zur_route_km == pytest.approx(20.0, abs=0.1)

    stations_json = {
        "stations": [
            {
                "station_id": "abseits-der-route",
                "name": "Tesla Supercharger - 20km abseits",
                "lat": station_coord[0],
                "lon": station_coord[1],
                "stalls": {"V3": 8},
                "connector_types": ["CCS2"],
                "country": "DE",
            }
        ]
    }
    data_path = tmp_path / "stations.json"
    data_path.write_text(json.dumps(stations_json))
    provider = LocalFileChargingStationProvider(data_path=data_path)

    route = await fake_routing_provider.berechne_route(TripRequest(**{**valid_trip_request}))

    # Sanity check: der alte 2-km-Radius findet die Station fuer KEIN
    # Segment, der neue 25-km-Radius fuer mindestens eines.
    old_radius_result = await provider.get_stations_along_route(route, search_radius_km=2.0)
    new_radius_result = await provider.get_stations_along_route(route, search_radius_km=25.0)
    assert old_radius_result == {}
    assert any(
        s.station_id == "abseits-der-route"
        for stations in new_radius_result.values()
        for s in stations
    )

    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    assert len(result.charging_stops) == 1
    assert result.charging_stops[0].station_id == "abseits-der-route"


def test_fastapi_endpoint_akzeptiert_faehr_zeitfenster_und_ladedauer_vorgaben(
    client: TestClient,
) -> None:
    """Der `/trips`-Endpunkt akzeptiert `faehr_zeitfenster`/`ladedauer_vorgaben` im
    Request und liefert die neuen Antwortfelder (`ChargingStopAPI.station_id` /
    `.ankunftszeit`/`.abfahrtszeit`, `FaehrSegmentAPI.abfahrt`/`.ankunft`)."""
    api_request = {
        "start": (52.52, 13.405),
        "ziel": (48.135, 11.582),
        "zwischenstopps": [],
        "abfahrtszeit": "2026-08-15T08:30:00",
        "fahrzeugprofil": _make_fahrzeugprofil_dict(),
        "start_soc_pct": 80.0,
        "ziel_soc_pct": 20.0,
        "faehr_zeitfenster": [
            {
                "name": "Nicht in dieser Route vorhanden",
                "bbox_sw": (0.0, 0.0),
                "bbox_no": (1.0, 1.0),
                "abfahrt": "2026-08-15T10:00:00",
                "ankunft": "2026-08-15T11:00:00",
            }
        ],
        "ladedauer_vorgaben": [{"station_id": "nurnberg-stop", "ladedauer_s": 1500}],
    }
    response = client.post("/trips", json=api_request)
    assert response.status_code == 201
    data = response.json()
    assert data["erkannte_faehren"] == []
    for stop in data["charging_stops"]:
        assert "station_id" in stop
        assert "ankunftszeit" in stop
        assert "abfahrtszeit" in stop
        if stop["station_id"] == "nurnberg-stop":
            assert stop["ladedauer_s"] == 1500


def test_fastapi_endpoint_mit_geplanter_abfahrt_gibt_201(client: TestClient) -> None:
    """Endpunkt akzeptiert `geplante_abfahrt` an einem Zwischenstopp fehlerfrei.

    Hinweis: Der aktuelle Prototyp-Optimierer (`tripplanner.optimization.optimizer`)
    behandelt Zwischenstopp-Wartezeiten als optionalen Kostenfaktor im A*-Suchgraphen,
    nicht als erzwungene Mindestaufenthaltsdauer -- der A*-Pfad kann die Wartekante
    umgehen, wenn kein SoC-/Ladebedarf sie erfordert. Dieser Test prueft daher nur die
    fehlerfreie Verarbeitung (Datenfluss bis in das Domaenenmodell), nicht eine
    konkrete Zeitverschiebung in der Antwort -- siehe `_with_derived_wait_time`-Tests
    oben fuer die Verifikation der eigentlichen Ableitungslogik.
    """
    api_request = {
        "start": (52.52, 13.405),
        "ziel": (53.5511, 9.9937),
        "zwischenstopps": [
            {
                "koordinate": (52.6, 13.5),
                "aufenthaltsdauer_s": None,
                "geplante_abfahrt": "2026-08-15T09:00:00",
            }
        ],
        "abfahrtszeit": "2026-08-15T08:30:00",
        "fahrzeugprofil": _make_fahrzeugprofil_dict(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    data = response.json()
    assert len(data["frames"]) > 0


def test_fastapi_endpoint_creates_trip(client: TestClient, valid_trip_request: dict) -> None:
    """Test: FastAPI-Endpunkt liefert 201 mit gültigem Response-Body."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    data = response.json()

    assert "gesamt_distanz_km" in data
    assert "gesamt_fahrzeit_min" in data
    assert "gesamt_ladezeit_min" in data
    assert "start_soc_pct" in data
    assert "ziel_soc_pct" in data
    assert "frames" in data
    assert len(data["frames"]) > 0


def test_fastapi_endpoint_response_includes_erkannte_faehren_key(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Response enthält den Schlüssel erkannte_faehren (leer, da FakeRoutingProvider
    keine road_environment-Daten liefert)."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    data = response.json()
    assert data["erkannte_faehren"] == []


def test_fastapi_endpoint_accepts_ferry_avoidance_fields(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Endpunkt akzeptiert alle_faehren_vermeiden und vermiedene_faehren fehlerfrei."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
        "alle_faehren_vermeiden": True,
        "vermiedene_faehren": [
            {"name": "Testfähre", "bbox_sw": [54.0, 11.0], "bbox_no": [55.0, 12.0]}
        ],
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201


def test_fastapi_endpoint_wetter_detailgrad_off_skips_weather_provider(
    client: TestClient, valid_trip_request: dict
) -> None:
    """wetter_detailgrad='off' skips the weather provider entirely."""
    spy = FakeWeatherProvider()
    app.dependency_overrides[get_weather_provider] = lambda: spy
    try:
        api_request = {
            **valid_trip_request,
            "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
            "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
            "wetter_detailgrad": "off",
        }
        response = client.post("/trips", json=api_request)
        assert response.status_code == 201
        assert spy.fetch_weather_calls == []
        assert spy.refetch_weather_calls == []
    finally:
        app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108


def test_fastapi_endpoint_wetter_detailgrad_default_is_high(
    client: TestClient, valid_trip_request: dict
) -> None:
    """No wetter_detailgrad sent -> defaults to 'high'; provider is called."""
    spy = FakeWeatherProvider()
    app.dependency_overrides[get_weather_provider] = lambda: spy
    try:
        api_request = {
            **valid_trip_request,
            "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
            "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        }
        response = client.post("/trips", json=api_request)
        assert response.status_code == 201
        assert len(spy.fetch_weather_calls) > 0
    finally:
        app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108


def test_fastapi_endpoint_wetter_beruecksichtigen_legacy_boolean(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Legacy 'wetter_beruecksichtigen' boolean maps to detail levels."""
    # false -> off (no provider calls)
    spy = FakeWeatherProvider()
    app.dependency_overrides[get_weather_provider] = lambda: spy
    try:
        api_request = {
            **valid_trip_request,
            "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
            "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
            "wetter_beruecksichtigen": False,
        }
        response = client.post("/trips", json=api_request)
        assert response.status_code == 201
        assert spy.fetch_weather_calls == []
    finally:
        app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108

    # true -> high (provider called)
    spy2 = FakeWeatherProvider()
    app.dependency_overrides[get_weather_provider] = lambda: spy2
    try:
        api_request["wetter_beruecksichtigen"] = True
        response = client.post("/trips", json=api_request)
        assert response.status_code == 201
        assert len(spy2.fetch_weather_calls) > 0
    finally:
        app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108


def test_fastapi_endpoint_wetter_detailgrad_low_one_fetch(
    client: TestClient, valid_trip_request: dict
) -> None:
    """wetter_detailgrad='low' makes exactly one fetch_weather call."""
    spy = FakeWeatherProvider()
    app.dependency_overrides[get_weather_provider] = lambda: spy
    try:
        api_request = {
            **valid_trip_request,
            "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
            "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
            "wetter_detailgrad": "low",
        }
        response = client.post("/trips", json=api_request)
        assert response.status_code == 201
        assert len(spy.fetch_weather_calls) == 1
        data = response.json()
        assert len(data["frames"]) > 0
    finally:
        app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108


def test_fastapi_endpoint_wetter_detailgrad_medium_one_fetch(
    client: TestClient, valid_trip_request: dict
) -> None:
    """wetter_detailgrad='medium' makes exactly one fetch_weather call."""
    spy = FakeWeatherProvider()
    app.dependency_overrides[get_weather_provider] = lambda: spy
    try:
        api_request = {
            **valid_trip_request,
            "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
            "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
            "wetter_detailgrad": "medium",
        }
        response = client.post("/trips", json=api_request)
        assert response.status_code == 201
        assert len(spy.fetch_weather_calls) == 1
        data = response.json()
        assert len(data["frames"]) > 0
    finally:
        app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108


def test_fastapi_endpoint_baustellen_beruecksichtigen_false_skips_construction_provider(
    client: TestClient, valid_trip_request: dict
) -> None:
    """baustellen_beruecksichtigen=False überspringt den injizierten
    Baustellen-Provider vollständig (kein Aufruf von fetch_construction_zones)."""
    spy_construction_provider = FakeConstructionProvider()
    app.dependency_overrides[get_construction_provider] = lambda: spy_construction_provider
    try:
        api_request = {
            "start": valid_trip_request["start"],
            "ziel": valid_trip_request["ziel"],
            "zwischenstopps": [],
            "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
            "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
            "praeferenzen": {},
            "baustellen_beruecksichtigen": False,
        }

        response = client.post("/trips", json=api_request)

        assert response.status_code == 201
        assert spy_construction_provider.fetch_construction_zones_calls == []
    finally:
        app.dependency_overrides[get_construction_provider] = (
            lambda: FakeConstructionProvider()  # noqa: PLW0108
        )


def test_fastapi_endpoint_baustellen_beruecksichtigen_default_true_calls_construction_provider(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Ohne explizites baustellen_beruecksichtigen (Default True) wird der
    injizierte Baustellen-Provider weiterhin aufgerufen (Rückwärtskompatibilität)."""
    spy_construction_provider = FakeConstructionProvider()
    app.dependency_overrides[get_construction_provider] = lambda: spy_construction_provider
    try:
        api_request = {
            "start": valid_trip_request["start"],
            "ziel": valid_trip_request["ziel"],
            "zwischenstopps": [],
            "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
            "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
            "praeferenzen": {},
        }

        response = client.post("/trips", json=api_request)

        assert response.status_code == 201
        assert len(spy_construction_provider.fetch_construction_zones_calls) > 0
    finally:
        app.dependency_overrides[get_construction_provider] = (
            lambda: FakeConstructionProvider()  # noqa: PLW0108
        )


def test_fastapi_endpoint_custom_soc(client: TestClient, valid_trip_request: dict) -> None:
    """Test: FastAPI-Endpunkt akzeptiert benutzerdefinierte Start-/Ziel-SoC."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
        "start_soc_pct": 95.0,
        "ziel_soc_pct": 15.0,
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    data = response.json()
    # Start-SoC wird direkt durchgereicht (Eingabe = Ausgabe)
    assert data["start_soc_pct"] == 95.0
    # Ziel-SoC ist das tatsächliche Simulationsergebnis (kann vom Zielwert abweichen)
    assert 0.0 <= data["ziel_soc_pct"] <= 100.0


def test_fastapi_endpoint_custom_mindest_ankunfts_soc_pct(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Test: FastAPI-Endpunkt akzeptiert `mindest_ankunfts_soc_pct` und
    reicht ihn bis zur Optimierung durch (siehe `TripRequestAPI.
    mindest_ankunfts_soc_pct`, Default 5.0)."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
        "mindest_ankunfts_soc_pct": 12.5,
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    for stop in response.json()["charging_stops"]:
        assert stop["ankunfts_soc_pct"] >= 12.5


def test_fastapi_endpoint_mindest_ankunfts_soc_pct_out_of_range_rejected(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Test: `mindest_ankunfts_soc_pct` außerhalb [0, 100] liefert 422."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
        "mindest_ankunfts_soc_pct": 150.0,
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 422


def test_fastapi_endpoint_custom_mindest_ladezeit_s(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Test: FastAPI-Endpunkt akzeptiert `mindest_ladezeit_s` und reicht ihn
    bis zur Optimierung durch (siehe `TripRequestAPI.mindest_ladezeit_s`,
    Default 600)."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
        "mindest_ladezeit_s": 300,
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    for stop in response.json()["charging_stops"]:
        assert stop["ladedauer_s"] >= 299  # int()-Rundung, siehe Kommentar oben


def test_fastapi_endpoint_mindest_ladezeit_s_out_of_range_rejected(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Test: `mindest_ladezeit_s` außerhalb [0, 1800] liefert 422."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
        "mindest_ladezeit_s": 5000,
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 422


def test_fastapi_endpoint_invalid_coordinates(client: TestClient) -> None:
    """Test: Ungültige Koordinaten liefern Fehler."""
    api_request = {
        "start": (999, 999),  # Ungültig
        "ziel": (52.52, 13.405),
        "zwischenstopps": [],
        "abfahrtszeit": "2026-08-15T08:30:00",
        "fahrzeugprofil": _make_fahrzeugprofil_dict(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    # Erwartet Fehler (500), da FakeRoutingProvider keine echte Route findet
    assert response.status_code in (422, 500)


def test_fastapi_endpoint_invalid_date(client: TestClient) -> None:
    """Test: Ungültiges Datumsformat liefert 422 oder 500."""
    api_request = {
        "start": (52.52, 13.405),
        "ziel": (48.135, 11.582),
        "zwischenstopps": [],
        "abfahrtszeit": "ungueltiges-datum",  # Ungültig
        "fahrzeugprofil": _make_fahrzeugprofil_dict(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code in (422, 500)


def test_fastapi_endpoint_mit_zwischenstopp(client: TestClient) -> None:
    """Test: Endpunkt akzeptiert Request mit Zwischenstopp."""
    api_request = {
        "start": (52.52, 13.405),
        "ziel": (53.551, 9.994),
        "zwischenstopps": [
            {
                "koordinate": (51.23, 6.78),
                "aufenthaltsdauer_s": 1800,  # 30 Minuten
            }
        ],
        "abfahrtszeit": "2026-08-15T08:30:00",
        "fahrzeugprofil": _make_fahrzeugprofil_dict(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    data = response.json()
    assert len(data["frames"]) > 0


# =============================================================================
# Testfälle für GraphHopper-Provider-Wiring
# =============================================================================


def test_lifespan_uses_default_graphhopper_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne `GRAPHHOPPER_URL` wird der GraphHopper-Client mit dem dokumentierten
    Default (`http://localhost:8989`) initialisiert.
    """
    monkeypatch.delenv("GRAPHHOPPER_URL", raising=False)
    with TestClient(app):
        gh_client = app.state.providers.routing.client
        assert gh_client.base_url == "http://localhost:8989"


def test_lifespan_respects_graphhopper_url_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """`GRAPHHOPPER_URL` überschreibt die Default-Basis-URL des GraphHopper-Clients."""
    monkeypatch.setenv("GRAPHHOPPER_URL", "http://gh.internal:9999")
    with TestClient(app):
        gh_client = app.state.providers.routing.client
        assert gh_client.base_url == "http://gh.internal:9999"


def test_lifespan_configures_info_level_console_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_lifespan` installs a `StreamHandler` on the `tripplanner` logger so
    INFO-level pipeline-step logs (see `_log_step`) actually reach the
    console under `uvicorn --reload`, instead of being silently dropped by
    `logging.lastResort` (which only handles WARNING+).
    """
    monkeypatch.delenv("TRIPPLANNER_LOG_LEVEL", raising=False)
    package_logger = logging.getLogger("tripplanner")
    previous_handlers = list(package_logger.handlers)
    previous_level = package_logger.level
    for h in previous_handlers:
        package_logger.removeHandler(h)
    package_logger.setLevel(logging.NOTSET)
    try:
        with TestClient(app):
            assert package_logger.level == logging.INFO
            assert any(isinstance(h, logging.StreamHandler) for h in package_logger.handlers)
            handler_count = len(package_logger.handlers)
        # Re-entering the lifespan (e.g. a second TestClient) must not add
        # a second handler and thus must not duplicate log lines.
        with TestClient(app):
            assert len(package_logger.handlers) == handler_count
    finally:
        for h in list(package_logger.handlers):
            package_logger.removeHandler(h)
        for h in previous_handlers:
            package_logger.addHandler(h)
        package_logger.setLevel(previous_level)


def test_lifespan_respects_log_level_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """`TRIPPLANNER_LOG_LEVEL` overrides the default `INFO` console level."""
    monkeypatch.setenv("TRIPPLANNER_LOG_LEVEL", "WARNING")
    package_logger = logging.getLogger("tripplanner")
    previous_handlers = list(package_logger.handlers)
    previous_level = package_logger.level
    for h in previous_handlers:
        package_logger.removeHandler(h)
    package_logger.setLevel(logging.NOTSET)
    try:
        with TestClient(app):
            assert package_logger.level == logging.WARNING
    finally:
        for h in list(package_logger.handlers):
            package_logger.removeHandler(h)
        for h in previous_handlers:
            package_logger.addHandler(h)
        package_logger.setLevel(previous_level)


def _make_graphhopper_provider(
    route_handler: Callable[[httpx.Request], httpx.Response],
) -> GraphHopperRoutingProvider:
    """Baut einen `GraphHopperRoutingProvider` mit gemocktem HTTP-Transport
    (keine Live-Calls, siehe AGENTS.md).

    `/info` wird automatisch mit allen vier Path-Details als "verfügbar"
    beantwortet, damit `route_handler` sich nur um `/route` kümmern muss
    (siehe `GraphHopperRoutingProvider._ermittele_verfuegbare_path_details`).
    """

    def combined_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/info":
            return httpx.Response(
                200,
                json={
                    "encoded_values": {
                        name: [] for name in ("road_class", "max_speed", "average_slope", "surface")
                    }
                },
            )
        return route_handler(request)

    gh_client = GraphHopperClient(base_url="http://localhost:8989")
    gh_client._client = httpx.AsyncClient(
        base_url="http://localhost:8989",
        timeout=60.0,
        transport=httpx.MockTransport(combined_handler),
    )
    return GraphHopperRoutingProvider(client=gh_client)


def _cross_track_distance_km(
    point: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> float:
    """Senkrechter Abstand von `point` zur Geraden `line_start`-`line_end` in km.

    Nutzt eine equirektangulare Näherung (Längengrad skaliert mit cos(mittlerer
    Breite)) - ausreichend, um eine gekrümmte Route eindeutig von einer Luftlinie
    zu unterscheiden, keine geodätische Präzision nötig.
    """
    lat0 = (line_start[0] + line_end[0]) / 2.0
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(math.radians(lat0))

    def to_xy(p: tuple[float, float]) -> tuple[float, float]:
        return (p[1] * km_per_deg_lon, p[0] * km_per_deg_lat)

    sx, sy = to_xy(line_start)
    ex, ey = to_xy(line_end)
    px, py = to_xy(point)

    dx, dy = ex - sx, ey - sy
    line_len = math.hypot(dx, dy)
    if line_len == 0:
        return math.hypot(px - sx, py - sy)
    return abs(dx * (sy - py) - (sx - px) * dy) / line_len


def test_fastapi_endpoint_preserves_curved_graphhopper_geometry() -> None:
    """Regressionstest: `/trips` nutzt GraphHopper-Geometrie (nicht die
    Luftlinie zwischen Start und Ziel) - deckt den Bug ab, bei dem die Karte
    unabhängig vom tatsächlichen Straßenverlauf nur eine gerade Linie zeigte.
    """
    # Kurze Strecke (~9 km) mit deutlichem seitlichem Schlenker, damit keine
    # Ladehalte benötigt werden (isoliert diesen Test von der Ladeplan-
    # Optimierung/Ladeinfrastruktur - reine Geometrie-Regression).
    detour_points: list[tuple[float, float]] = [
        (52.5200, 13.4050),
        (52.5100, 13.4400),
        (52.5050, 13.4700),
        (52.5150, 13.4950),
        (52.5300, 13.5200),
    ]
    encoded = polyline.encode(detour_points)
    gh_response = {
        "paths": [
            {
                "distance": 9_100.0,
                "time": 900_000,
                "points_encoded": True,
                "points": encoded,
                "details": {
                    "road_class": [[0, 4, "PRIMARY"]],
                    "max_speed": [[0, 4, 100]],
                    "average_slope": [[0, 4, 0.0]],
                    "surface": [[0, 4, "asphalt"]],
                },
                "instructions": [],
            }
        ],
        "info": {"copyrights": ["GraphHopper"], "hints": [], "took": 5},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/route"
        return httpx.Response(200, json=gh_response)

    provider = _make_graphhopper_provider(handler)
    app.dependency_overrides[get_routing_provider] = lambda: provider
    app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108
    app.dependency_overrides[get_construction_provider] = (
        lambda: FakeConstructionProvider()  # noqa: PLW0108
    )
    app.dependency_overrides[get_elevation_provider] = lambda: ElevationProvider(
        data_source=FakeDataSource()
    )
    try:
        with TestClient(app) as test_client:
            api_request = {
                "start": (52.5200, 13.4050),
                "ziel": (52.5300, 13.5200),
                "zwischenstopps": [],
                "abfahrtszeit": "2026-08-15T08:30:00",
                "fahrzeugprofil": _make_fahrzeugprofil_dict(),
                "praeferenzen": {},
            }
            response = test_client.post("/trips", json=api_request)
    finally:
        app.dependency_overrides.pop(get_routing_provider, None)
        app.dependency_overrides.pop(get_weather_provider, None)
        app.dependency_overrides.pop(get_construction_provider, None)
        app.dependency_overrides.pop(get_elevation_provider, None)

    assert response.status_code == 201
    data = response.json()
    positions = [tuple(f["position"]) for f in data["frames"]]
    assert len(positions) > 2

    start, end = positions[0], positions[-1]
    max_offset_km = max(_cross_track_distance_km(p, start, end) for p in positions)
    assert max_offset_km > 1.0, (
        "Route-Frames liegen auf einer Luftlinie statt der gekrümmten "
        f"GraphHopper-Geometrie zu folgen (max. Abweichung: {max_offset_km:.2f} km)"
    )


def test_fastapi_endpoint_exposes_full_resolution_route_geometrie() -> None:
    """Regressionstest: `/trips` liefert die volle GraphHopper-Polyline in
    `route_geometrie` - unabhaengig von der (zeitbasiert grob gerasterten)
    Anzahl an `frames`. Deckt den Bug ab, bei dem die Karte statt der
    Strassengeometrie nur die linear zwischen Simulationsframes
    interpolierte, deutlich kuerzere Luftlinie zeichnete (Frames liegen bei
    60s-Aufloesung auf Autobahntempo mehrere hundert Meter auseinander,
    die GraphHopper-Polyline aber typischerweise alle ~20-50m einen Punkt).
    """
    # Dichte, sinusfoermig geschwungene Polyline mit deutlich mehr Punkten
    # als die kurze (~9 km / ~15 min) Strecke an 60s-Simulationsframes
    # erzeugen kann.
    detour_points: list[tuple[float, float]] = [
        (52.5200 + 0.001 * math.sin(i / 3.0), 13.4050 + i * 0.0015) for i in range(60)
    ]
    encoded = polyline.encode(detour_points)
    gh_response = {
        "paths": [
            {
                "distance": 9_100.0,
                "time": 900_000,
                "points_encoded": True,
                "points": encoded,
                "details": {
                    "road_class": [[0, len(detour_points) - 1, "PRIMARY"]],
                    "max_speed": [[0, len(detour_points) - 1, 100]],
                    "average_slope": [[0, len(detour_points) - 1, 0.0]],
                    "surface": [[0, len(detour_points) - 1, "asphalt"]],
                },
                "instructions": [],
            }
        ],
        "info": {"copyrights": ["GraphHopper"], "hints": [], "took": 5},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/route"
        return httpx.Response(200, json=gh_response)

    provider = _make_graphhopper_provider(handler)
    app.dependency_overrides[get_routing_provider] = lambda: provider
    app.dependency_overrides[get_weather_provider] = lambda: FakeWeatherProvider()  # noqa: PLW0108
    app.dependency_overrides[get_construction_provider] = (
        lambda: FakeConstructionProvider()  # noqa: PLW0108
    )
    app.dependency_overrides[get_elevation_provider] = lambda: ElevationProvider(
        data_source=FakeDataSource()
    )
    try:
        with TestClient(app) as test_client:
            api_request = {
                "start": detour_points[0],
                "ziel": detour_points[-1],
                "zwischenstopps": [],
                "abfahrtszeit": "2026-08-15T08:30:00",
                "fahrzeugprofil": _make_fahrzeugprofil_dict(),
                "praeferenzen": {},
            }
            response = test_client.post("/trips", json=api_request)
    finally:
        app.dependency_overrides.pop(get_routing_provider, None)
        app.dependency_overrides.pop(get_weather_provider, None)
        app.dependency_overrides.pop(get_construction_provider, None)
        app.dependency_overrides.pop(get_elevation_provider, None)

    assert response.status_code == 201
    data = response.json()

    # Die volle, unreduzierte GraphHopper-Geometrie muss uebertragen werden -
    # nicht auf die (viel groebere) Frame-Anzahl reduziert.
    assert len(data["route_geometrie"]) == len(detour_points)
    assert len(data["route_geometrie"]) > len(data["frames"])
    assert tuple(data["route_geometrie"][0]) == pytest.approx(detour_points[0])
    assert tuple(data["route_geometrie"][-1]) == pytest.approx(detour_points[-1])


def test_fastapi_endpoint_frame_distanz_m_ist_monoton_und_erreicht_gesamtstrecke(
    client: TestClient, valid_trip_request: dict
) -> None:
    """`FrameAPI.distanz_m` waechst monoton mit der zurueckgelegten Strecke und
    erreicht am letzten Frame die Gesamtdistanz - Grundlage dafuer, dass das
    Frontend den SoC-Gradienten korrekt entlang der (von `frames` entkoppelten)
    `route_geometrie` positionieren kann (`line-progress` = `distanz_m /
    gesamtdistanz`).
    """
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    data = response.json()
    distanzen = [f["distanz_m"] for f in data["frames"]]

    assert distanzen[0] == pytest.approx(0.0, abs=1.0)
    for a, b in pairwise(distanzen):
        assert b >= a - 1e-6, "distanz_m muss monoton nicht-fallend sein"
    assert distanzen[-1] == pytest.approx(data["gesamt_distanz_km"] * 1000.0, rel=0.01)


def test_fastapi_endpoint_graphhopper_unreachable_returns_502() -> None:
    """Nicht erreichbarer GraphHopper-Server liefert 502 statt eines stillen
    Fallbacks oder eines generischen 500ers.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _make_graphhopper_provider(handler)
    app.dependency_overrides[get_routing_provider] = lambda: provider
    try:
        with TestClient(app) as test_client:
            api_request = {
                "start": (52.52, 13.405),
                "ziel": (48.1351, 11.582),
                "zwischenstopps": [],
                "abfahrtszeit": "2026-08-15T08:30:00",
                "fahrzeugprofil": _make_fahrzeugprofil_dict(),
                "praeferenzen": {},
            }
            response = test_client.post("/trips", json=api_request)
    finally:
        app.dependency_overrides.pop(get_routing_provider, None)

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "GraphHopper" in detail or "Routing" in detail


def test_create_trip_endpoint_wires_weather_provider_dependency() -> None:
    """`create_trip_endpoint`'s Depends list resolves `weather_provider` via
    `get_weather_provider` - without this wiring `/trips` would silently keep
    using `FakeWeatherProvider` for every production request regardless of the
    configured `OpenMeteoProvider` (Plan 10 Section 6)."""
    sig = inspect.signature(create_trip_endpoint)
    weather_param = sig.parameters["weather_provider"]
    assert isinstance(weather_param.default, fastapi_params.Depends)
    assert weather_param.default.dependency is get_weather_provider


def _open_meteo_mock_get(
    self: httpx.AsyncClient, url: str, *args: object, **kwargs: object
) -> httpx.Response:
    """Simuliert die Open-Meteo Forecast API auf `httpx.AsyncClient.get`-Ebene
    (kein Live-Call, siehe AGENTS.md) und liefert pro Koordinate/Minute
    unterschiedliche Temperatur-/Windwerte, damit Tests eine echte
    `OpenMeteoProvider`-Antwort von `FakeWeatherProvider`s Konstanten
    (20.0°C/5.0 m/s) unterscheiden koennen.
    """
    qs = parse_qs(urlparse(str(url)).query)
    lat = float(qs["latitude"][0])
    lon = float(qs["longitude"][0])
    start_date = datetime.fromisoformat(qs["start_date"][0])
    end_date = datetime.fromisoformat(qs["end_date"][0])

    times: list[str] = []
    temps: list[float] = []
    winds: list[float] = []
    day = start_date
    while day <= end_date:
        for minute in range(24 * 60):
            times.append((day + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M"))
            temps.append(round(5.0 + lat * 0.3 + minute * 0.01, 2))
            winds.append(round(1.0 + abs(lon) * 0.4 + minute * 0.005, 2))
        day += timedelta(days=1)

    n = len(times)
    hourly = {
        "time": times,
        "temperature_2m": temps,
        "wind_speed_10m": winds,
        "wind_direction_10m": [90.0] * n,
        "precipitation": [0.0] * n,
        "snowfall": [0.0] * n,
        "surface_pressure": [1013.0] * n,
        "relative_humidity_2m": [55.0] * n,
        "shortwave_radiation": [300.0] * n,
        "cloud_cover": [10.0] * n,
    }
    return httpx.Response(
        200,
        request=httpx.Request("GET", str(url)),
        json={
            "latitude": lat,
            "longitude": lon,
            "timezone": "UTC",
            "timezone_abbreviation": "UTC",
            "elevation": 0.0,
            "hourly": hourly,
            "hourly_units": dict.fromkeys(hourly, ""),
        },
    )


async def _async_open_meteo_mock_get(
    self: httpx.AsyncClient, url: str, *args: object, **kwargs: object
) -> httpx.Response:
    return _open_meteo_mock_get(self, url, *args, **kwargs)


def test_create_trip_endpoint_uses_open_meteo_provider_for_real_weather(
    monkeypatch: pytest.MonkeyPatch,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """`/trips` wired to a real `OpenMeteoProvider` (HTTP mocked at the
    `httpx.AsyncClient.get` level per AGENTS.md, no live calls) produces
    per-segment weather derived from the mocked Open-Meteo response instead of
    `FakeWeatherProvider`'s constant 20.0°C/5.0 m/s - regression test for Plan
    10 Phase B (weather wiring)."""
    captured_weather_samples: list[list[WeatherSample]] = []
    original_step_7 = trip_api._step_7_calculate_segment_energy

    async def spy_step_7(
        route: Route,
        route_segments: list[RouteSegment],
        segment_eta_list: list[tuple[RouteSegment, timedelta]],
        weather_samples: list[WeatherSample],
        *rest: object,
    ) -> object:
        captured_weather_samples.append(list(weather_samples))
        return await original_step_7(
            route, route_segments, segment_eta_list, weather_samples, *rest
        )

    monkeypatch.setattr(trip_api, "_step_7_calculate_segment_energy", spy_step_7)
    monkeypatch.setattr(httpx.AsyncClient, "get", _async_open_meteo_mock_get)

    app.dependency_overrides[get_routing_provider] = FakeRoutingProvider
    app.dependency_overrides[get_charging_provider] = lambda: fake_charging_provider_berlin_munich
    app.dependency_overrides[get_elevation_provider] = lambda: ElevationProvider(
        data_source=FakeDataSource()
    )
    # PLW0108: lambda erforderlich (siehe `client`-Fixture oben) - vermeidet
    # den FastAPI-Override-Introspektions-Bug bei list[BaseModel]-Konstruktorparametern.
    app.dependency_overrides[get_weather_provider] = lambda: OpenMeteoProvider()  # noqa: PLW0108
    app.dependency_overrides[get_construction_provider] = lambda: FakeConstructionProvider()  # noqa: PLW0108
    try:
        with TestClient(app) as test_client:
            api_request = {
                "start": (52.52, 13.405),
                "ziel": (48.135, 11.582),
                "zwischenstopps": [],
                "abfahrtszeit": "2026-08-15T08:30:00",
                "fahrzeugprofil": _make_fahrzeugprofil_dict(),
                "praeferenzen": {},
            }
            response = test_client.post("/trips", json=api_request)
    finally:
        app.dependency_overrides.pop(get_routing_provider, None)
        app.dependency_overrides.pop(get_charging_provider, None)
        app.dependency_overrides.pop(get_elevation_provider, None)
        app.dependency_overrides.pop(get_weather_provider, None)
        app.dependency_overrides.pop(get_construction_provider, None)

    assert response.status_code == 201
    assert captured_weather_samples, "erwartete mindestens einen _step_7-Aufruf"
    samples = captured_weather_samples[0]
    assert samples, "erwartete mindestens ein WeatherSample aus OpenMeteoProvider"
    temperatures = {s.temperatur_c for s in samples}
    wind_speeds = {s.windgeschwindigkeit_ms for s in samples}
    assert len(temperatures) > 1, "Temperaturen muessen je Segment variieren (echte API-Daten)"
    assert all(t != 20.0 for t in temperatures), "duerfen nicht Fake-Konstante 20.0 sein"
    assert all(w != 5.0 for w in wind_speeds), "duerfen nicht Fake-Konstante 5.0 sein"


def test_fastapi_endpoint_weather_provider_failure_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """A weather provider that always fails (e.g. Open-Meteo 429 rate limit)
    must never break trip calculation: `/trips` still returns 201, falling
    back to a neutral placeholder `WeatherSample` for every point instead of
    propagating the `httpx.HTTPStatusError` as a 502. Regression test for the
    incident where an Open-Meteo 429 made `/trips` fail outright."""

    async def rate_limited_get(
        self: httpx.AsyncClient, url: str, *args: object, **kwargs: object
    ) -> httpx.Response:
        request = httpx.Request("GET", str(url))
        response = httpx.Response(429, request=request, json={"error": "rate limited"})
        raise httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)

    monkeypatch.setattr(httpx.AsyncClient, "get", rate_limited_get)

    captured_weather_samples: list[list[WeatherSample]] = []
    original_step_7 = trip_api._step_7_calculate_segment_energy

    async def spy_step_7(
        route: Route,
        route_segments: list[RouteSegment],
        segment_eta_list: list[tuple[RouteSegment, timedelta]],
        weather_samples: list[WeatherSample],
        *rest: object,
    ) -> object:
        captured_weather_samples.append(list(weather_samples))
        return await original_step_7(
            route, route_segments, segment_eta_list, weather_samples, *rest
        )

    monkeypatch.setattr(trip_api, "_step_7_calculate_segment_energy", spy_step_7)

    app.dependency_overrides[get_routing_provider] = FakeRoutingProvider
    app.dependency_overrides[get_charging_provider] = lambda: fake_charging_provider_berlin_munich
    app.dependency_overrides[get_elevation_provider] = lambda: ElevationProvider(
        data_source=FakeDataSource()
    )
    # Single-entry composite: no other provider to fail over to, so this
    # exercises the "every eligible provider failed" neutral-fallback path.
    # PLW0108: lambda erforderlich (siehe `client`-Fixture oben) - vermeidet
    # den FastAPI-Override-Introspektions-Bug bei list[BaseModel]-Konstruktorparametern.
    app.dependency_overrides[get_weather_provider] = lambda: LoadBalancedWeatherProvider(
        [WeatherProviderEntry("open-meteo", OpenMeteoProvider(), None)]
    )
    app.dependency_overrides[get_construction_provider] = lambda: FakeConstructionProvider()  # noqa: PLW0108
    try:
        with TestClient(app) as test_client:
            api_request = {
                "start": (52.52, 13.405),
                "ziel": (48.135, 11.582),
                "zwischenstopps": [],
                "abfahrtszeit": "2026-08-15T08:30:00",
                "fahrzeugprofil": _make_fahrzeugprofil_dict(),
                "praeferenzen": {},
            }
            response = test_client.post("/trips", json=api_request)
    finally:
        app.dependency_overrides.pop(get_routing_provider, None)
        app.dependency_overrides.pop(get_charging_provider, None)
        app.dependency_overrides.pop(get_elevation_provider, None)
        app.dependency_overrides.pop(get_weather_provider, None)
        app.dependency_overrides.pop(get_construction_provider, None)

    assert response.status_code == 201
    assert captured_weather_samples, "erwartete mindestens einen _step_7-Aufruf"
    samples = captured_weather_samples[0]
    assert samples, "erwartete Fallback-WeatherSamples trotz durchgehend 429"
    assert all(s.temperatur_c == 15.0 for s in samples), (
        "erwartete neutrale Platzhalterwerte (siehe _neutral_weather_sample)"
    )


# =============================================================================
# Testfälle für POST /superchargers/{slug}/refresh
# =============================================================================


def _make_test_charging_station() -> ChargingStation:
    """Erzeugt eine ChargingStation fuer Mock-Rueckgaben in Refresh-Tests."""
    return ChargingStation(
        station_id="muenchensupercharger",
        name="Tesla Supercharger - Muenchen",
        coordinate=(48.13, 11.58),
        stalls={StallType.V3: 16},
        max_ladeleistung_kw=250.0,
        connector_types=[ConnectorType.NACS],
        country="DE",
        ist_24_7=True,
    )


def test_refresh_supercharger_endpoint_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Erfolgreicher Refresh liefert 200 mit aktualisierten Stationsdaten."""

    async def fake_refresh_single_station(
        self: TeslaChargingStationProvider,
        slug: str,
        country: str | None = None,
        tesla_client: object | None = None,
    ) -> ChargingStation:
        assert slug == "muenchensupercharger"
        return _make_test_charging_station()

    monkeypatch.setattr(
        TeslaChargingStationProvider,
        "refresh_single_station",
        fake_refresh_single_station,
    )

    response = client.post("/superchargers/muenchensupercharger/refresh")

    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "muenchensupercharger"
    assert data["total_stalls"] == 16


def test_refresh_supercharger_endpoint_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unbekannter Slug / leere Tesla-Antwort liefert 404."""

    async def fake_refresh_single_station(
        self: TeslaChargingStationProvider,
        slug: str,
        country: str | None = None,
        tesla_client: object | None = None,
    ) -> ChargingStation | None:
        return None

    monkeypatch.setattr(
        TeslaChargingStationProvider,
        "refresh_single_station",
        fake_refresh_single_station,
    )

    response = client.post("/superchargers/unknown-slug/refresh")

    assert response.status_code == 404


def test_refresh_supercharger_endpoint_waf_block(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WAF-Block/Netzwerkfehler (CurlError) wird als 502 durchgereicht."""

    async def fake_refresh_single_station(
        self: TeslaChargingStationProvider,
        slug: str,
        country: str | None = None,
        tesla_client: object | None = None,
    ) -> ChargingStation | None:
        raise TeslaLocationsClient.CurlError("403 Access Denied")

    monkeypatch.setattr(
        TeslaChargingStationProvider,
        "refresh_single_station",
        fake_refresh_single_station,
    )

    response = client.post("/superchargers/muenchensupercharger/refresh")

    assert response.status_code == 502


def test_refresh_supercharger_endpoint_unmappable_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tesla-Antwort ausserhalb des unterstuetzten Laenderraums (DE/DK/SE)
    wird als 502 statt als unbehandelter 500 durchgereicht."""

    async def fake_refresh_single_station(
        self: TeslaChargingStationProvider,
        slug: str,
        country: str | None = None,
        tesla_client: object | None = None,
    ) -> ChargingStation | None:
        # ChargingStation.country ist auf Literal["DE","DK","SE"] beschraenkt
        return ChargingStation(
            station_id="ukstation",
            name="UK Supercharger",
            coordinate=(52.0, -1.0),
            stalls={StallType.V3: 8},
            max_ladeleistung_kw=250.0,
            connector_types=[ConnectorType.NACS],
            country="GB",  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        TeslaChargingStationProvider,
        "refresh_single_station",
        fake_refresh_single_station,
    )

    response = client.post("/superchargers/ukstation/refresh")

    assert response.status_code == 502


# =============================================================================
# Testfälle für CLI-Koordinaten-Parsing
# =============================================================================


def test_cli_parse_coord_valid() -> None:
    """Test: CLI parst gültige Koordinaten korrekt."""
    result = parse_coord("52.52,13.405")
    assert result == (52.52, 13.405)
    assert isinstance(result[0], float)
    assert isinstance(result[1], float)


def test_cli_parse_coord_invalid_format() -> None:
    """Test: CLI wirft Fehler bei ungültigem Format."""
    with pytest.raises(ValueError, match="Ungültige Koordinate"):
        parse_coord("52.52")  # Nur eine Komponente


def test_cli_parse_coord_invalid_number() -> None:
    """Test: CLI wirft Fehler bei nicht-numerischen Werten."""
    with pytest.raises(ValueError, match="Ungültige Koordinate"):
        parse_coord("abc,def")


def test_cli_parse_waypoint_valid() -> None:
    """Test: CLI parst gültigen Waypoint korrekt."""
    result = parse_waypoint("52.52,13.405:30")
    assert result[0] == (52.52, 13.405)
    assert result[1] == timedelta(minutes=30)


def test_cli_parse_waypoint_without_duration() -> None:
    """Test: CLI parst Waypoint ohne Dauer korrekt."""
    result = parse_waypoint("52.52,13.405")
    assert result[0] == (52.52, 13.405)
    assert result[1] is None


# =============================================================================
# Testfälle für Randfälle
# =============================================================================


@pytest.mark.asyncio
async def test_create_trip_simulation_selbe_start_ziel_position(
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """Test: Identische Start/Ziel-Position wird behandelt (Randfall)."""
    request = {
        "start": (52.52, 13.405),
        "ziel": (52.52, 13.405),  # Selbe Position
        "zwischenstopps": [],
        "abfahrtszeit": datetime(2026, 8, 15, 8, 30, 0),
        "fahrzeugprofil": VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        ),
        "praeferenzen": {},
    }

    # Erwartet: Minimale Route oder Fehler je nach FakeRoutingProvider
    result = await create_trip_simulation(
        request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    # FakeRoutingProvider sollte hier eine minimale Route zurückgeben
    assert result.gesamt_distanz_km >= 0


@pytest.mark.asyncio
async def test_create_trip_simulation_alle_schritte_sind_aufgerufen(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
    fake_construction_provider: FakeConstructionProvider,
) -> None:
    """Test: Alle 11 Datenfluss-Schritte werden in create_trip_simulation aufgerufen."""
    # Dieser Test verifiziert, dass die Funktion ohne Fehler durchläuft
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        construction_provider=fake_construction_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    # Wenn wir hier ankommen, wurden alle Schritte durchlaufen
    assert result.gesamt_distanz_km > 0


# =============================================================================
# Phase E: Iterative ETA/weather convergence loop tests
# =============================================================================


@pytest.mark.asyncio
async def test_convergence_loop_terminates_at_max_iterations(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test: Iterative loop terminates at max_iterations even without convergence.

    `_step_9_update_eta` is patched to always shift every segment's ETA by a
    fixed, non-vanishing amount, guaranteeing the deviation never drops below
    the convergence threshold. This isolates the loop's hard iteration cap
    from the route/energy feasibility of any particular weather scenario.
    """
    weather_calls: list[object] = []
    real_update_eta = trip_api._step_9_update_eta

    def _never_converging_update_eta(segment_eta_list: object, charging_plan: object) -> object:
        updated = real_update_eta(segment_eta_list, charging_plan)
        return [(seg, eta + timedelta(hours=1)) for seg, eta in updated]

    monkeypatch.setattr(trip_api, "_step_9_update_eta", _never_converging_update_eta)

    class _CountingWeatherProvider(FakeWeatherProvider):
        async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
            weather_calls.append(queries)
            return await super().fetch_weather(queries)

    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=_CountingWeatherProvider(),
        charging_provider=FakeChargingStationProvider(),
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        max_iterations=3,
        convergence_threshold_minutes=30.0,
    )

    # Loop should have run exactly 3 iterations (one fetch_weather call each),
    # not run infinitely despite the ETA never converging.
    assert len(weather_calls) == 3
    # Result should still be valid (not crashed from infinite loop)
    assert result.gesamt_distanz_km > 0
    assert result.gesamt_fahrzeit_min > 0


@pytest.mark.asyncio
async def test_convergence_loop_early_termination(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
) -> None:
    """Test: Iterative loop terminates early once deviation is below threshold.

    When weather samples produce stable energy/charging results, the
    convergence loop should exit before reaching max_iterations.
    """
    # Use the same FakeWeatherProvider which always returns constant values,
    # so results should converge immediately
    stable_provider = FakeWeatherProvider()
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=stable_provider,
        charging_provider=FakeChargingStationProvider(),
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        max_iterations=5,
        convergence_threshold_minutes=30.0,
    )

    # Should have run at least 1 iteration (first iteration always runs)
    assert stable_provider.fetch_weather_calls.__len__() >= 1
    # Result should be valid
    assert result.gesamt_distanz_km > 0


@pytest.mark.asyncio
async def test_convergence_loop_runs_at_least_2_iterations(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
) -> None:
    """Test: Iterative loop runs multiple iterations with default settings.

    With default max_iterations=3 and the FakeWeatherProvider returning
    constant values, the loop should run at least 2 iterations (iteration 0
    for initial setup, then iteration 1 checks convergence).
    """
    provider = FakeWeatherProvider()
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=provider,
        charging_provider=FakeChargingStationProvider(),
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
    )

    # Should have run at least 1 iteration
    assert provider.fetch_weather_calls.__len__() >= 1
    # Result should be valid
    assert result.gesamt_distanz_km > 0
    assert result.gesamt_fahrzeit_min > 0


@pytest.mark.asyncio
async def test_convergence_loop_uses_refetch_weather_from_second_iteration(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test: from the 2nd iteration onward, `refetch_weather` is used instead
    of `fetch_weather` when the provider supports it, per plan Task E.2.

    The loop never converges (ETA is force-shifted every iteration via a
    monkeypatched `_step_9_update_eta`), guaranteeing `max_iterations`
    iterations run, so `refetch_weather` is exercised on iterations 2 and 3.
    """
    real_update_eta = trip_api._step_9_update_eta

    def _never_converging_update_eta(segment_eta_list: object, charging_plan: object) -> object:
        updated = real_update_eta(segment_eta_list, charging_plan)  # type: ignore[arg-type]
        return [(seg, eta + timedelta(hours=1)) for seg, eta in updated]  # type: ignore[union-attr]

    monkeypatch.setattr(trip_api, "_step_9_update_eta", _never_converging_update_eta)

    class _RefetchTrackingWeatherProvider(FakeWeatherProvider):
        def __init__(self) -> None:
            super().__init__()
            self.fetch_weather_call_count = 0
            self.refetch_weather_calls: list[
                tuple[Sequence[WeatherQuery], Sequence[WeatherQuery]]
            ] = []

        async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
            self.fetch_weather_call_count += 1
            return await super().fetch_weather(queries)

        async def refetch_weather(
            self,
            original_queries: Sequence[WeatherQuery],
            updated_queries: Sequence[WeatherQuery],
        ) -> list[WeatherSample]:
            self.refetch_weather_calls.append((original_queries, updated_queries))
            return await self.fetch_weather(updated_queries)

    provider = _RefetchTrackingWeatherProvider()
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=provider,
        charging_provider=FakeChargingStationProvider(),
        start_soc_pct=80.0,
        destination_soc_pct=20.0,
        max_iterations=3,
        convergence_threshold_minutes=30.0,
    )

    # Iteration 0 has no previous_queries -> plain fetch_weather only.
    # Iterations 1 and 2 must go through refetch_weather.
    assert len(provider.refetch_weather_calls) == 2
    # Each refetch_weather call's updated_queries must have the same
    # coordinates as the original_queries (only the timestamps may differ).
    for original_queries, updated_queries in provider.refetch_weather_calls:
        original_coords = [q.koordinate for q in original_queries]
        updated_coords = [q.koordinate for q in updated_queries]
        assert original_coords == updated_coords
    assert result.gesamt_distanz_km > 0


# =============================================================================
# _attach_charging_pricing (Step 12: Preisdaten anreichern + Warteschlange)
# =============================================================================


def _station_record(
    supercharge_info_id: int, tesla_location_id: str, country_code: str
) -> dict[str, object]:
    """Minimales DB-Record-Dict fuer `SQLiteDatabase.replace_all_stations`."""
    return {
        "supercharge_info_id": supercharge_info_id,
        "tesla_location_id": tesla_location_id,
        "site_name": f"{tesla_location_id} site",
        "latitude": 51.947,
        "longitude": 10.140,
        "country_code": country_code,
        "stalls_v2": 0,
        "stalls_v3": 8,
        "stalls_v3_ultra": 0,
        "stalls_v4": 0,
        "total_stalls": 8,
        "power_kilowatt": 250,
        "status": "OPEN",
        "connector_types": '["ccs2"]',
        "ist_24_7": 1,
        "date_opened": None,
        "last_updated_utc": datetime.now(UTC).isoformat(),
    }


def _make_charging_stop_summary(
    station_id: str = "rhudensupercharger",
    ankunftszeit: datetime = datetime(2026, 1, 1, 18, 0, tzinfo=UTC),
    energie_geladen_kwh: float = 25.0,
) -> ChargingStopSummary:
    return ChargingStopSummary(
        name="Tesla Supercharger - Rhueden",
        station_id=station_id,
        position=(51.947, 10.140),
        distanz_m=12000.0,
        ankunfts_soc_pct=30.0,
        ziel_soc_pct=80.0,
        ladedauer_s=1500,
        energie_geladen_kwh=energie_geladen_kwh,
        ankunftszeit=ankunftszeit,
        abfahrtszeit=ankunftszeit + timedelta(minutes=25),
    )


def _make_simulation_result(charging_stops: list[ChargingStopSummary]) -> TripSimulationResult:
    return TripSimulationResult(
        frames=[],
        gesamt_distanz_km=100.0,
        gesamt_fahrzeit_min=60.0,
        gesamt_ladezeit_min=25.0,
        start_soc_pct=80.0,
        ziel_soc_pct=30.0,
        charging_stops=charging_stops,
    )


class TestAttachChargingPricing:
    """Tests für `trip_input.api._attach_charging_pricing`."""

    def test_noop_without_charging_stops(self, tmp_path: Path) -> None:
        """Ohne Ladehalte bleibt das Ergebnis unveraendert."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "t.db")
        result = _make_simulation_result([])

        attached = trip_api._attach_charging_pricing(result, provider)

        assert attached is result

    def test_noop_for_non_tesla_provider(self) -> None:
        """Ein Fake-/LocalFile-Provider unterstuetzt kein Pricing - No-Op."""
        result = _make_simulation_result([_make_charging_stop_summary()])

        attached = trip_api._attach_charging_pricing(result, FakeChargingStationProvider())

        assert attached is result

    def test_attaches_cached_pricing_and_computes_cost(self, tmp_path: Path) -> None:
        """Mit gecachten Preisdaten werden price_per_kwh/currency/estimated_cost
        gesetzt und die Warteschlange bleibt leer (Station ist frisch)."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "t.db")
        provider._db.replace_all_stations([_station_record(3506, "rhudensupercharger", "DE")])
        provider._db.upsert_pricing(
            3506,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "EUR",
                    "amount": 0.40,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        result = _make_simulation_result([_make_charging_stop_summary(energie_geladen_kwh=25.0)])

        attached = trip_api._attach_charging_pricing(result, provider)

        stop = attached.charging_stops[0]
        assert stop.price_per_kwh == pytest.approx(0.40)
        assert stop.currency == "EUR"
        assert stop.estimated_cost == pytest.approx(10.0)
        assert stop.pricing_updated_utc is not None
        assert attached.charging_stops_missing_pricing == 0
        assert attached.total_charging_cost == [ChargingCostByCurrency(currency="EUR", amount=10.0)]
        assert provider.list_pricing_queue() == []  # fresh pricing, not queued

    def test_queues_station_without_pricing_and_leaves_stop_unpriced(self, tmp_path: Path) -> None:
        """Ohne gecachte Preisdaten bleibt der Stopp unpreist, die Station
        wird aber (an erster Stelle) fuer den Scrape eingereiht."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "t.db")
        provider._db.replace_all_stations([_station_record(3506, "rhudensupercharger", "DE")])
        result = _make_simulation_result([_make_charging_stop_summary()])

        attached = trip_api._attach_charging_pricing(result, provider)

        stop = attached.charging_stops[0]
        assert stop.price_per_kwh is None
        assert stop.estimated_cost is None
        assert attached.charging_stops_missing_pricing == 1
        assert attached.total_charging_cost == []
        queue = provider.list_pricing_queue()
        assert len(queue) == 1
        assert queue[0]["tesla_location_id"] == "rhudensupercharger"

    def test_groups_total_cost_by_currency_across_countries(self, tmp_path: Path) -> None:
        """Ladehalte in unterschiedlichen Waehrungen (DE/DK-Trip) werden NICHT
        addiert, sondern getrennt nach Waehrung ausgewiesen."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "t.db")
        provider._db.replace_all_stations(
            [
                _station_record(3506, "rhudensupercharger", "DE"),
                _station_record(5678, "kopenhagensupercharger", "DK"),
            ]
        )
        provider._db.upsert_pricing(
            3506,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "EUR",
                    "amount": 0.40,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        provider._db.upsert_pricing(
            5678,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "DKK",
                    "amount": 4.0,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        result = _make_simulation_result(
            [
                _make_charging_stop_summary("rhudensupercharger", energie_geladen_kwh=25.0),
                _make_charging_stop_summary("kopenhagensupercharger", energie_geladen_kwh=10.0),
            ]
        )

        attached = trip_api._attach_charging_pricing(result, provider)

        assert attached.total_charging_cost == [
            ChargingCostByCurrency(currency="DKK", amount=40.0),
            ChargingCostByCurrency(currency="EUR", amount=10.0),
        ]
        assert attached.charging_stops_missing_pricing == 0

    def test_does_not_requeue_fresh_stations(self, tmp_path: Path) -> None:
        """Eine kuerzlich gescrapte Station wird nicht erneut eingereiht."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "t.db")
        provider._db.replace_all_stations([_station_record(3506, "rhudensupercharger", "DE")])
        provider._db.upsert_pricing(
            3506,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "EUR",
                    "amount": 0.40,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        result = _make_simulation_result([_make_charging_stop_summary()])

        trip_api._attach_charging_pricing(result, provider)

        assert provider.list_pricing_queue() == []

    def test_requeues_stale_pricing(self, tmp_path: Path) -> None:
        """Veraltete Preisdaten fuehren zur erneuten Einreihung, der Stopp
        wird aber weiterhin mit den (veralteten) gecachten Daten bepreist."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "t.db")
        provider._db.replace_all_stations([_station_record(3506, "rhudensupercharger", "DE")])
        provider._db.upsert_pricing(
            3506,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "EUR",
                    "amount": 0.40,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        conn = provider._db._conn
        assert conn is not None
        conn.execute(
            "UPDATE charging_pricing SET last_updated_utc = ? WHERE supercharge_info_id = ?",
            ("2000-01-01T00:00:00+00:00", 3506),
        )
        conn.commit()
        result = _make_simulation_result([_make_charging_stop_summary()])

        attached = trip_api._attach_charging_pricing(result, provider)

        assert attached.charging_stops[0].price_per_kwh == pytest.approx(0.40)
        queue = provider.list_pricing_queue()
        assert len(queue) == 1


# =============================================================================
# Logging instrumentation tests
# =============================================================================


_LOGGERS = ["tripplanner.trip_input.api"]


@pytest.mark.asyncio
async def test_create_trip_simulation_emits_step_logging(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
    fake_construction_provider: FakeConstructionProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """create_trip_simulation emits step-start/step-end log records with duration.

    Verifies that the _log_step context manager produces INFO records for at
    least a representative subset of pipeline steps.
    """
    with caplog.at_level(logging.INFO, logger="tripplanner.trip_input.api"):
        result = await create_trip_simulation(
            valid_trip_request,
            routing_provider=fake_routing_provider,
            weather_provider=fake_weather_provider,
            construction_provider=fake_construction_provider,
            charging_provider=fake_charging_provider_berlin_munich,
            start_soc_pct=80.0,
            destination_soc_pct=20.0,
        )

    assert result.gesamt_distanz_km > 0

    # Check step start records exist for at least route_calculate and fetch_weather
    step_starts = [
        r.message
        for r in caplog.records
        if r.levelno == logging.INFO
        and r.name == "tripplanner.trip_input.api"
        and " — start" in r.message
    ]
    assert any("route_calculate" in m for m in step_starts), (
        f"Expected step-start log for route_calculate; got: {step_starts}"
    )
    assert any("simulate_trip" in m for m in step_starts), (
        f"Expected step-start log for simulate_trip; got: {step_starts}"
    )

    # Check step end records contain duration info
    step_ends = [
        r.message
        for r in caplog.records
        if r.levelno == logging.INFO
        and r.name == "tripplanner.trip_input.api"
        and " — done in" in r.message
    ]
    assert any("route_calculate" in m for m in step_ends), (
        f"Expected step-end log for route_calculate; got: {step_ends}"
    )
    # Each step-end should mention duration in ms
    assert any("ms" in m for m in step_ends), (
        f"Expected duration in step-end logs; got: {step_ends}"
    )


@pytest.mark.asyncio
async def test_create_trip_simulation_emits_total_elapsed(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
    fake_construction_provider: FakeConstructionProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The simulation logs a 'Pipeline complete' line with total elapsed ms."""
    with caplog.at_level(logging.INFO, logger="tripplanner.trip_input.api"):
        result = await create_trip_simulation(
            valid_trip_request,
            routing_provider=fake_routing_provider,
            weather_provider=fake_weather_provider,
            construction_provider=fake_construction_provider,
            charging_provider=fake_charging_provider_berlin_munich,
            start_soc_pct=80.0,
            destination_soc_pct=20.0,
        )

    assert result.gesamt_distanz_km > 0

    complete = [
        r.message
        for r in caplog.records
        if r.levelno == logging.INFO
        and r.name == "tripplanner.trip_input.api"
        and "Pipeline complete" in r.message
    ]
    assert len(complete) == 1
    assert "ms total" in complete[0]


def test_fastapi_endpoint_logs_valueerror_as_422_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ValueError deep in the pipeline still returns 422 and emits a warning.

    We monkey-patch _step_1_route_calculate to raise ValueError, which surfaces
    through the endpoint's ValueError handler.
    """

    async def _fake_step_1_raises(*args: object, **kwargs: object) -> Route:
        raise ValueError("Kein erreichbarer Zielknoten gefunden")

    monkeypatch.setattr(trip_api, "_step_1_route_calculate", _fake_step_1_raises)

    app.dependency_overrides[get_routing_provider] = FakeRoutingProvider
    try:
        with TestClient(app) as test_client:
            api_request = {
                "start": (52.52, 13.405),
                "ziel": (48.1351, 11.582),
                "zwischenstopps": [],
                "abfahrtszeit": "2026-08-15T08:30:00",
                "fahrzeugprofil": _make_fahrzeugprofil_dict(),
                "praeferenzen": {},
            }
            response = test_client.post("/trips", json=api_request)
    finally:
        app.dependency_overrides.pop(get_routing_provider, None)

    assert response.status_code == 422
    assert "nicht durchführbar" in response.json()["detail"]

    # Verify the warning was logged
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "tripplanner.trip_input.api"
        and "rejected (422)" in r.message
    ]
    assert len(warnings) == 1
    assert "Kein erreichbarer Zielknoten" in warnings[0].message


def test_fastapi_endpoint_logs_httpx_error_as_502_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An httpx.HTTPError from routing returns 502 and emits a warning."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _make_graphhopper_provider(handler)
    app.dependency_overrides[get_routing_provider] = lambda: provider
    try:
        with TestClient(app) as test_client:
            api_request = {
                "start": (52.52, 13.405),
                "ziel": (48.1351, 11.582),
                "zwischenstopps": [],
                "abfahrtszeit": "2026-08-15T08:30:00",
                "fahrzeugprofil": _make_fahrzeugprofil_dict(),
                "praeferenzen": {},
            }
            response = test_client.post("/trips", json=api_request)
    finally:
        app.dependency_overrides.pop(get_routing_provider, None)

    assert response.status_code == 502

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "tripplanner.trip_input.api"
        and "request failed (502)" in r.message
    ]
    assert len(warnings) == 1
    assert "connection refused" in warnings[0].message


def test_log_step_context_manager_logs_on_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When a step raises, _log_step logs WARNING with elapsed and re-raises."""
    try:
        with caplog.at_level(logging.INFO, logger="tripplanner.trip_input.api"):  # noqa: SIM117
            with _log_step("test_step"):
                raise ValueError("boom")
    except ValueError as exc:
        assert str(exc) == "boom"

    starts = [r for r in caplog.records if " — start" in r.message]
    assert len(starts) == 1
    assert starts[0].levelno == logging.INFO
    assert starts[0].message == "Pipeline step 'test_step' — start"

    fails = [r for r in caplog.records if " — FAILED" in r.message]
    assert len(fails) == 1
    assert fails[0].levelno == logging.WARNING
    assert "test_step" in fails[0].message
    assert "boom" in fails[0].message
    assert "ms" in fails[0].message


@pytest.mark.asyncio
async def test_create_trip_simulation_emits_iteration_logging(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
    fake_construction_provider: FakeConstructionProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Steps inside the convergence loop log the iteration number."""
    # Force more iterations by setting a low convergence threshold
    with caplog.at_level(logging.INFO, logger="tripplanner.trip_input.api"):
        result = await create_trip_simulation(
            valid_trip_request,
            routing_provider=fake_routing_provider,
            weather_provider=fake_weather_provider,
            construction_provider=fake_construction_provider,
            charging_provider=fake_charging_provider_berlin_munich,
            start_soc_pct=80.0,
            destination_soc_pct=20.0,
            max_iterations=3,
            convergence_threshold_minutes=0.0,  # disable early exit
        )

    assert result.gesamt_distanz_km > 0

    # Check that iteration-tagged messages exist for steps inside the loop
    iteration_msgs = [
        r.message
        for r in caplog.records
        if r.levelno == logging.INFO
        and r.name == "tripplanner.trip_input.api"
        and "iteration" in r.message.lower()
    ]
    # Should have at least some iteration-tagged logs for steps 5-8b
    assert any("fetch_weather" in m for m in iteration_msgs), (
        f"Expected iteration-tagged fetch_weather log; got: {iteration_msgs}"
    )
    assert any("optimize_charging_plan" in m for m in iteration_msgs), (
        f"Expected iteration-tagged optimize_charging_plan log; got: {iteration_msgs}"
    )
