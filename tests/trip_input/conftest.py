"""Test-Konfiguration für trip_input API.

enthält:
- pytest fixtures für Test-Daten
- shared helpers
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tripplanner.trip_input.models import VehicleProfile, Waypoint
from tripplanner.trip_input.schemas.request import VehicleProfileAPI


@pytest.fixture
def berlin_muenchen_request() -> dict:
    """TripRequest für Berlin nach München."""
    return {
        "start": (52.52, 13.405),  # Berlin
        "destination": (48.135, 11.582),  # München
        "waypoints": [],
        "departure_time": datetime(2026, 8, 15, 8, 30, 0),
        "vehicle_profile": VehicleProfile(
            mass_kg=1800.0,
            drag_coefficient=0.23,
            frontal_area_m2=2.2,
            rolling_resistance_coefficient=0.01,
            battery_capacity_kwh=60.0,
            auxiliary_baseline_kw=0.34,
            tire_type="standard",
            roof_box=False,
        ),
        "preferences": {},
    }


@pytest.fixture
def berlin_hamburg_request() -> dict:
    """TripRequest für Berlin nach Hamburg mit Zwischenstopp."""
    return {
        "start": (52.52, 13.405),  # Berlin
        "destination": (53.551, 9.994),  # Hamburg
        "waypoints": [
            Waypoint(
                coordinate=(51.23, 6.78),
                stay_duration=timedelta(minutes=30),
            )
        ],
        "departure_time": datetime(2026, 8, 15, 8, 30, 0),
        "vehicle_profile": VehicleProfile(
            mass_kg=1800.0,
            drag_coefficient=0.23,
            frontal_area_m2=2.2,
            rolling_resistance_coefficient=0.01,
            battery_capacity_kwh=60.0,
            auxiliary_baseline_kw=0.34,
            tire_type="standard",
            roof_box=False,
        ),
        "preferences": {},
    }


@pytest.fixture
def kurze_reise_request() -> dict:
    """TripRequest für sehr kurze Reise (ein paar km)."""
    return {
        "start": (52.52, 13.405),  # Berlin-Mitte
        "destination": (52.525, 13.41),  # Etwa 1 km entfernt
        "waypoints": [],
        "departure_time": datetime(2026, 8, 15, 12, 0, 0),
        "vehicle_profile": VehicleProfile(
            mass_kg=1800.0,
            drag_coefficient=0.23,
            frontal_area_m2=2.2,
            rolling_resistance_coefficient=0.01,
            battery_capacity_kwh=60.0,
            auxiliary_baseline_kw=0.34,
            tire_type="standard",
            roof_box=False,
        ),
        "preferences": {},
    }


@pytest.fixture
def heavy_vehicle_request() -> dict:
    """TripRequest mit schwerem Fahrzeug (z. B. mit Anhänger)."""
    return {
        "start": (52.52, 13.405),
        "destination": (48.135, 11.582),
        "waypoints": [],
        "departure_time": datetime(2026, 8, 15, 8, 30, 0),
        "vehicle_profile": VehicleProfile(
            mass_kg=2500.0,  # Schwereres Fahrzeug
            drag_coefficient=0.35,  # Schlechtere Aerodynamik
            frontal_area_m2=3.0,
            rolling_resistance_coefficient=0.015,
            battery_capacity_kwh=80.0,
            auxiliary_baseline_kw=0.5,
            tire_type="performance",
            roof_box=True,
        ),
        "preferences": {},
    }


@pytest.fixture
def api_request_payload(berlin_muenchen_request: dict) -> dict:
    """API-Request-Format für FastAPI-Tests."""
    return {
        "start": berlin_muenchen_request["start"],
        "destination": berlin_muenchen_request["destination"],
        "waypoints": [],
        "departureTime": berlin_muenchen_request["departure_time"].isoformat(),
        "vehicleProfile": VehicleProfileAPI.from_domain(
            berlin_muenchen_request["vehicle_profile"]
        ).model_dump(),
        "preferences": {},
    }


@pytest.fixture
def api_request_payload_with_stop(berlin_hamburg_request: dict) -> dict:
    """API-Request-Format mit Zwischenstopp."""
    wp = berlin_hamburg_request["waypoints"][0]
    return {
        "start": berlin_hamburg_request["start"],
        "destination": berlin_hamburg_request["destination"],
        "waypoints": [
            {
                "coordinate": list(wp.coordinate),
                "stayDurationS": int(wp.stay_duration.total_seconds())
                if wp.stay_duration
                else None,
            }
        ],
        "departureTime": berlin_hamburg_request["departure_time"].isoformat(),
        "vehicleProfile": VehicleProfileAPI.from_domain(
            berlin_hamburg_request["vehicle_profile"]
        ).model_dump(),
        "preferences": {},
    }
