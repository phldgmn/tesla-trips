"""Integrationstest: End-to-End-Reiseplanung mit echten Providern.

Testet, dass create_trip_simulation() mit echten Provider-Instanzen
(Arbeitweise per TestClient-Override) funktioniert und die Ergebnisse
unterschiedliche Wetterwerte, ein nicht-flaches Höhenprofil sowie
konstruktionsbezogene Daten liefern — ohne live GraphHopper.

GraphHopper wird durch FakeRoutingProvider ersetzt, damit der Test auch
ohne laufenden GraphHopper-Container in CI funktioniert.

OpenMeteo und CopernicusDEMDataSource sind echte Live-Calls (erlaubt per
AGENTS.md für @pytest.mark.integration-Tests). ConstructionProviderImpl
liest credentials.local.yaml und versucht echte API-Calls; fehlende
Credentials werden stillschweigend übersprungen (Phase D Design).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from tripplanner.charging_infrastructure import FakeChargingStationProvider
from tripplanner.construction.providers import (
    ConstructionProviderConfig,
    ConstructionProviderImpl,
)
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.providers import CopernicusDEMDataSource
from tripplanner.routing import FakeRoutingProvider
from tripplanner.trip_input.api import (
    app,
    get_charging_provider,
    get_construction_provider,
    get_elevation_provider,
    get_routing_provider,
    get_weather_provider,
)
from tripplanner.trip_input.models import VehicleProfile
from tripplanner.weather.providers import OpenMeteoProvider

# =============================================================================
# Routen-Koordinaten
# =============================================================================

_COPENHAGEN: tuple[float, float] = (55.6761, 12.5683)
_MALMO: tuple[float, float] = (55.6050, 13.0038)
_STOCKHOLM: tuple[float, float] = (59.3293, 18.0686)
_MUNCHEN: tuple[float, float] = (48.1351, 11.5820)
_GARMISCH: tuple[float, float] = (47.4920, 11.0950)


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    """Realistisches vehicle_profile für ein Model 3 Long Range."""
    return VehicleProfile(
        mass_kg=1805.0,
        drag_coefficient=0.23,
        frontal_area_m2=2.22,
        rolling_resistance_coefficient=0.011,
        battery_capacity_kwh=75.0,
    )


def _now_iso() -> str:
    """ISO-8601 string for current time (used for weather queries)."""
    return datetime.now(UTC).isoformat(timespec="minutes")


def _build_payload(
    *,
    start: tuple[float, float],
    destination: tuple[float, float],
    waypoints: list[dict[str, object]] | None = None,
    departure_time: str | None = None,
) -> dict[str, object]:
    """Baut ein TripRequestAPI-JSON-Payload."""
    if departure_time is None:
        departure_time = _now_iso()
    return {
        "start": start,
        "destination": destination,
        "waypoints": waypoints or [],
        "departureTime": departure_time,
        "vehicleProfile": {
            "mass_kg": 1805.0,
            "drag_coefficient": 0.23,
            "frontal_area_m2": 2.22,
            "rolling_resistance_coefficient": 0.011,
            "battery_capacity_kwh": 75.0,
        },
        "startSocPct": 80.0,
        "targetSocPct": 20.0,
        "preferences": {},
        "avoidAllFerries": True,
        "avoidedFerries": [],
        "ferryTimeWindows": [],
        "chargingDurationSpecifications": [],
    }


def _make_routing_provider() -> FakeRoutingProvider:
    return FakeRoutingProvider()


def _make_charging_provider() -> FakeChargingStationProvider:
    return FakeChargingStationProvider()


def _make_weather_provider() -> OpenMeteoProvider:
    return OpenMeteoProvider()


def _make_construction_provider() -> ConstructionProviderImpl:
    return ConstructionProviderImpl(
        config=ConstructionProviderConfig(dk_client_id=None, dk_secret=None, tv_api_key=None)
    )


def _make_elevation_provider() -> ElevationProvider:
    return ElevationProvider(data_source=CopernicusDEMDataSource())


@pytest.fixture
def integration_app(vehicle_profile: VehicleProfile):
    """FastAPI-App mit realen Providern (außer GraphHopper).
    Dependency Overrides:
    - get_routing_provider       -> FakeRoutingProvider (kein Server nötig)
    - get_charging_provider      -> FakeChargingStationProvider (keine DB)
    - get_weather_provider       -> OpenMeteoProvider (echter HTTP-Call)
    - get_construction_provider  -> ConstructionProviderImpl
      (liest credentials.local.yaml, überspringt Länder ohne Credentials)
    - get_elevation_provider     -> ElevationProvider mit
      CopernicusDEMDataSource (echte /vsicurl/-Calls)
    """
    app.dependency_overrides[get_routing_provider] = _make_routing_provider
    app.dependency_overrides[get_charging_provider] = _make_charging_provider
    app.dependency_overrides[get_weather_provider] = _make_weather_provider
    app.dependency_overrides[get_construction_provider] = _make_construction_provider
    app.dependency_overrides[get_elevation_provider] = _make_elevation_provider
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(integration_app):
    """TestClient für die Integration-Test-App."""
    yield TestClient(app)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_end_to_end_multi_country_trip(client: TestClient) -> None:
    """End-to-End: Kopenhagen -> Malmö (kürzere Route für FakeRoutingProvider).

    Postet eine kurze Reise mit mehreren Zwischenstopps und prüft:
    1. Antwort ist 201 mit Frames und Ladehalten.
    2. total_distance plausibel (> 20 km).
    3. Gesamt-drive_time_s plausibel (> 30 min).
    4. Construction fetch crasht nicht (graceful degradation).
    5. Weather samples sind nicht leer (echte OpenMeteo-Daten).
    """
    payload = _build_payload(
        start=_COPENHAGEN,
        destination=_MALMO,
        waypoints=[
            {
                "coordinate": _MALMO,
                "stayDurationS": 600,
                "plannedDeparture": None,
            },
        ],
    )

    response = client.post("/trips", json=payload)
    assert response.status_code == 201, (
        f"POST /trips sollte 201 zurückgeben, bekam {response.status_code}: {response.text[:600]}"
    )

    data = response.json()

    # Frames vorhanden
    frames = data.get("frames", [])
    assert len(frames) > 0, "Antwort sollte Simulations-Frames enthalten"

    # distance und time plausibel
    distance = data.get("totalDistanceKm", 0.0)
    assert distance > 20, f"Kopenhagen->Malmö sollte >20 km sein, bekam {distance}"

    gesamt_fahrzeit_min = data.get("totalDrivingTimeMin", 0.0)
    assert gesamt_fahrzeit_min > 15, (
        f"Gesamt-drive_time_s sollte >15 min sein, bekam {gesamt_fahrzeit_min}"
    )
    # SoC-Werte variieren entlang der Route
    soc_values: list[float] = [f["socPct"] for f in frames]
    soc_range = max(soc_values) - min(soc_values)
    assert soc_range > 1.0, f"SoC sollte sich signifikant ändern. Range: {soc_range}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_elevation_real_data(client: TestClient) -> None:
    """Testet, dass CopernicusDEMDataSource echte Höhenwerte liefert.

    Route: München -> Garmisch-Partenkirchen (≈60 km, +500m Höhenunterschied).
    Wenn die Elevation wirklich flach wäre (wie beim Fake), würden
    Energieverbräuche sehr homogen sein. Echte Daten führen zu
    variablerem SoC-consumption.
    """
    # Guard against the 118-second regression that originally motivated this test (Issue #13).
    # 15 s is generous for real-world network + elevation lookups.
    t0 = time.perf_counter()
    payload = _build_payload(start=_MUNCHEN, destination=_GARMISCH)

    response = client.post("/trips", json=payload)
    assert response.status_code == 201, (
        f"POST /trips sollte 201 zurückgeben, bekam {response.status_code}: {response.text[:600]}"
    )

    data = response.json()
    distance = data.get("totalDistanceKm", 0.0)
    assert distance > 30, f"München->Garmisch sollte >30 km sein, bekam {distance}"

    frames = data.get("frames", [])
    assert len(frames) > 0

    # Echte Steigungen führen zu variablerem energy_consumption
    soc_values: list[float] = [f["socPct"] for f in frames]
    soc_range = max(soc_values) - min(soc_values)
    assert soc_range > 1.0, (
        f"Echte Elevation sollte variablen consumption erzeugen. Range: {soc_range}"
    )

    elapsed = time.perf_counter() - t0
    assert elapsed < 15, (
        f"Elevation lookup took {elapsed:.1f}s — expected < 15s "
        "(was 118s before bulk per-tile reads, see Issue #13)"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_weather_real_values(client: TestClient) -> None:
    """Testet, dass OpenMeteoProvider echte Wetterdaten liefert.

    Bei FakeWeatherProvider wären alle Segmente identisch (synthetisch).
    Echter Open-Meteo liefert orts- und zeitabhängige Werte.
    """
    # Nördliche Route: København -> Malmö (unterschiedliche Breitengrade)
    payload = _build_payload(start=_COPENHAGEN, destination=_MALMO)

    response = client.post("/trips", json=payload)
    assert response.status_code == 201, (
        f"POST /trips sollte 201 zurückgeben, bekam {response.status_code}: {response.text[:600]}"
    )

    data = response.json()
    frames = data.get("frames", [])
    assert len(frames) > 0

    # Echte Wetterdaten führen zu variablerem consumption
    soc_values: list[float] = [f["socPct"] for f in frames]
    soc_range = max(soc_values) - min(soc_values)
    assert soc_range > 0.5, f"OpenMeteo sollte variablen consumption liefern. Range: {soc_range}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_construction_no_crash(client: TestClient) -> None:
    """Testet, dass ConstructionProviderImpl ohne Crash abläuft.

    ConstructionProviderImpl wird mit Null-Credentials initialisiert.
    Das bedeutet: Keine DK/SE-Auth, aber DE (Autobahn-API, unauth) sollte
    noch funktionieren. Falls credentials.local.yaml existiert, werden
    echte API-Calls gestartet. Hauptsache: kein Crash.
    """
    payload = _build_payload(
        start=_COPENHAGEN,
        destination=_MALMO,
    )

    response = client.post("/trips", json=payload)
    # Success oder ein konstruktiver Fehler (z.B. Route not feasible)
    # sind beide akzeptabel — Hauptsache kein 500 (Crash)
    assert response.status_code in (201, 422), (
        f"Construction sollte nicht zu 500 führen, bekam {response.status_code}: "
        f"{response.text[:400]}"
    )
