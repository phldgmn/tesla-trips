"""Pytest-Fixtures für `routing`-Tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tripplanner.routing.models import GraphHopperResponse
from tripplanner.trip_input.models import TripRequest, VehicleProfile, Waypoint

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "routing"


@pytest.fixture
def graphhopper_response_basic() -> GraphHopperResponse:
    """Minimale GraphHopper-Antwort ohne Path-Details."""
    data = json.loads((FIXTURES_DIR / "graphhopper_response_basic.json").read_text())
    return GraphHopperResponse.model_validate(data)


@pytest.fixture
def graphhopper_response_with_details() -> GraphHopperResponse:
    """GraphHopper-Antwort mit road_class/max_speed/average_slope/surface Details."""
    data = json.loads((FIXTURES_DIR / "graphhopper_response_with_details.json").read_text())
    return GraphHopperResponse.model_validate(data)


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    """Beispiel-Fahrzeugprofil für TripRequests."""
    return VehicleProfile(
        masse_kg=1706.0,
        cw_wert=0.23,
        stirnflaeche_m2=2.22,
        rollwiderstandsbeiwert=0.011,
        batteriekapazitaet_kwh=62.5,
    )


@pytest.fixture
def trip_request(vehicle_profile: VehicleProfile) -> TripRequest:
    """Beispiel-TripRequest ohne Zwischenstopps (Berlin -> Hamburg)."""
    return TripRequest(
        start=(52.5200, 13.4050),
        ziel=(53.5511, 9.9937),
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
    )


@pytest.fixture
def trip_request_with_waypoints(vehicle_profile: VehicleProfile) -> TripRequest:
    """Beispiel-TripRequest mit einem Zwischenstopp (Berlin -> Hannover -> Hamburg)."""
    return TripRequest(
        start=(52.5200, 13.4050),
        ziel=(53.5511, 9.9937),
        zwischenstopps=[Waypoint(koordinate=(52.3759, 9.7320))],
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
    )
