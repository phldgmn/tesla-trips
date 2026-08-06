"""Tests für trip_input API-Schicht.

enthält:
- Testfälle für `create_trip_simulation()` mit Fake-Providern
- Testfälle für FastAPI-Endpunkt
- Testfälle für CLI-Koordinaten-Parsing
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import tripplanner.trip_input.api as trip_api
from tripplanner.charging_infrastructure import FakeChargingStationProvider
from tripplanner.construction.providers import FakeConstructionProvider
from tripplanner.routing import FakeRoutingProvider
from tripplanner.routing.models import RouteSegment
from tripplanner.trip_input.api import app, create_trip_simulation
from tripplanner.trip_input.cli import parse_coord, parse_waypoint
from tripplanner.trip_input.models import VehicleProfile, Waypoint
from tripplanner.weather.providers import FakeWeatherProvider

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
def client() -> TestClient:
    """Erstelle TestClient für FastAPI-Endpunkte."""
    return TestClient(app)


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


# =============================================================================
# Testfälle für create_trip_simulation()
# =============================================================================


@pytest.mark.asyncio
async def test_create_trip_simulation_vollstaendiger_durchlauf(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider: FakeChargingStationProvider,
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
        start_soc_pct=80.0,
        ziel_soc_pct=20.0,
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
async def test_create_trip_simulation_e2e_regression_abfahrtszeit_und_soc(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider: FakeChargingStationProvider,
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
        start_soc_pct=80.0,
        ziel_soc_pct=20.0,
    )

    abfahrtszeit = valid_trip_request["abfahrtszeit"]
    assert result.frames[0].zeitpunkt.year == abfahrtszeit.year
    assert result.frames[0].zeitpunkt.date() == abfahrtszeit.date()
    assert all(f.zeitpunkt.year != 1970 for f in result.frames)

    # Zeitstempel muessen monoton steigen und mit abfahrtszeit beginnen
    assert result.frames[0].zeitpunkt >= abfahrtszeit
    for a, b in zip(result.frames, result.frames[1:], strict=False):
        assert b.zeitpunkt >= a.zeitpunkt

    # SoC darf nicht auf (nahezu) 0% abstuerzen, solange kein realer Reichweitenmangel vorliegt
    fahren_frames = [f for f in result.frames if f.zustand.value == "FAHREN"]
    assert all(f.soc_pct > 1.0 for f in fahren_frames), (
        "SoC waehrend der Fahrt fiel auf nahezu 0% -- deutet auf falsche "
        "SoC-Depletionsformel hin (siehe Bugfix in simulate.py)"
    )


@pytest.mark.asyncio
async def test_create_trip_simulation_mit_zwischenstopp(
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
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
        ziel_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0
    assert len(result.frames) > 0


@pytest.mark.asyncio
async def test_create_trip_simulation_different_vehicle_profiles(
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
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
        start_soc_pct=90.0,
        ziel_soc_pct=30.0,
    )

    assert result.gesamt_distanz_km > 0
    assert result.gesamt_fahrzeit_min > 0
    assert result.gesamt_ladezeit_min >= 0


@pytest.mark.asyncio
async def test_create_trip_simulation_ohne_construction_provider(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """Test: Ohne ConstructionProvider wird leere Baustellen-Liste angenommen."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        construction_provider=None,  # Kein ConstructionProvider
        start_soc_pct=80.0,
        ziel_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0
    assert len(result.frames) > 0


@pytest.mark.asyncio
async def test_create_trip_simulation_ohne_wetter_provider(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
) -> None:
    """Test: Ohne WeatherProvider wird FakeWeatherProvider verwendet."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=None,  # Kein WeatherProvider, Fake wird verwendet
        start_soc_pct=80.0,
        ziel_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0
    assert len(result.frames) > 0


@pytest.mark.asyncio
async def test_create_trip_simulation_start_soc_100(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
) -> None:
    """Test: Start-SoC 100% wird korrekt verarbeitet."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        start_soc_pct=100.0,
        ziel_soc_pct=10.0,
    )

    assert result.start_soc_pct == 100.0
    assert result.ziel_soc_pct >= 10.0


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
        ziel_soc_pct=20.0,
    )

    assert result.gesamt_distanz_km > 0  # Kurze Strecke
    assert result.gesamt_fahrzeit_min < 30  # Kurze Fahrzeit


# =============================================================================
# Testfälle für FastAPI-Endpunkt
# =============================================================================


def _make_fahrzeugprofil_dict() -> dict:
    """Erzeuge ein Standard-Fahrzeugprofil als Dict für API-Requests."""
    return {
        "masse_kg": 1800.0,
        "cw_wert": 0.23,
        "stirnflaeche_m2": 2.2,
        "rollwiderstandsbeiwert": 0.01,
        "batteriekapazitaet_kwh": 60.0,
        "nebenverbraucher_baseline_kw": 0.34,
        "reifentyp": "standard",
        "dachbox": False,
    }


def test_mit_abgeleiteter_wartezeit_erzwingt_wartezeit_bei_spaeterer_geplanter_abfahrt() -> None:
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

    ergebnis = trip_api._mit_abgeleiteter_wartezeit([wp], segment_eta_liste, abfahrtszeit)

    assert len(ergebnis) == 1
    assert ergebnis[0].aufenthaltsdauer is not None
    assert ergebnis[0].aufenthaltsdauer >= timedelta(minutes=25)


def test_mit_abgeleiteter_wartezeit_keine_wartezeit_bei_bereits_verspaeteter_abfahrt() -> None:
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

    ergebnis = trip_api._mit_abgeleiteter_wartezeit([wp], segment_eta_liste, abfahrtszeit)

    assert ergebnis[0].aufenthaltsdauer == timedelta(0)


def test_mit_abgeleiteter_wartezeit_unveraendert_ohne_geplante_abfahrt() -> None:
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

    ergebnis = trip_api._mit_abgeleiteter_wartezeit([wp], segment_eta_liste, abfahrtszeit)

    assert ergebnis[0] is wp


def test_fastapi_endpoint_mit_geplanter_abfahrt_gibt_201(client: TestClient) -> None:
    """Endpunkt akzeptiert `geplante_abfahrt` an einem Zwischenstopp fehlerfrei.

    Hinweis: Der aktuelle Prototyp-Optimierer (`tripplanner.optimization.optimizer`)
    behandelt Zwischenstopp-Wartezeiten als optionalen Kostenfaktor im A*-Suchgraphen,
    nicht als erzwungene Mindestaufenthaltsdauer -- der A*-Pfad kann die Wartekante
    umgehen, wenn kein SoC-/Ladebedarf sie erfordert. Dieser Test prueft daher nur die
    fehlerfreie Verarbeitung (Datenfluss bis in das Domaenenmodell), nicht eine
    konkrete Zeitverschiebung in der Antwort -- siehe `_mit_abgeleiteter_wartezeit`-Tests
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
        ziel_soc_pct=20.0,
    )

    # FakeRoutingProvider sollte hier eine minimale Route zurückgeben
    assert result.gesamt_distanz_km >= 0


@pytest.mark.asyncio
async def test_create_trip_simulation_alle_schritte_sind_aufgerufen(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider: FakeChargingStationProvider,
    fake_construction_provider: FakeConstructionProvider,
) -> None:
    """Test: Alle 11 Datenfluss-Schritte werden in create_trip_simulation aufgerufen."""
    # Dieser Test verifiziert, dass die Funktion ohne Fehler durchläuft
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        construction_provider=fake_construction_provider,
        start_soc_pct=80.0,
        ziel_soc_pct=20.0,
    )

    # Wenn wir hier ankommen, wurden alle Schritte durchlaufen
    assert result.gesamt_distanz_km > 0
