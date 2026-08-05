"""Test-Konfiguration für trip_input API.

enthält:
- pytest fixtures für Test-Daten
- shared helpers
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tripplanner.trip_input.models import VehicleProfile, Waypoint


@pytest.fixture
def berlin_muenchen_request() -> dict:
    """TripRequest für Berlin nach München."""
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
def berlin_hamburg_request() -> dict:
    """TripRequest für Berlin nach Hamburg mit Zwischenstopp."""
    return {
        "start": (52.52, 13.405),  # Berlin
        "ziel": (53.551, 9.994),  # Hamburg
        "zwischenstopps": [
            Waypoint(
                koordinate=(51.23, 6.78),
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


@pytest.fixture
def kurze_reise_request() -> dict:
    """TripRequest für sehr kurze Reise (ein paar km)."""
    return {
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


@pytest.fixture
def heavy_vehicle_request() -> dict:
    """TripRequest mit schwerem Fahrzeug (z. B. mit Anhänger)."""
    return {
        "start": (52.52, 13.405),
        "ziel": (48.135, 11.582),
        "zwischenstopps": [],
        "abfahrtszeit": datetime(2026, 8, 15, 8, 30, 0),
        "fahrzeugprofil": VehicleProfile(
            masse_kg=2500.0,  # Schwereres Fahrzeug
            cw_wert=0.35,  # Schlechtere Aerodynamik
            stirnflaeche_m2=3.0,
            rollwiderstandsbeiwert=0.015,
            batteriekapazitaet_kwh=80.0,
            nebenverbraucher_baseline_kw=0.5,
            reifentyp="performance",
            dachbox=True,
        ),
        "praeferenzen": {},
    }


@pytest.fixture
def api_request_payload(berlin_muenchen_request: dict) -> dict:
    """API-Request-Format für FastAPI-Tests."""
    return {
        "start": berlin_muenchen_request["start"],
        "ziel": berlin_muenchen_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": berlin_muenchen_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": berlin_muenchen_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
    }


@pytest.fixture
def api_request_payload_mit_zwischenstopp(berlin_hamburg_request: dict) -> dict:
    """API-Request-Format mit Zwischenstopp."""
    wp = berlin_hamburg_request["zwischenstopps"][0]
    return {
        "start": berlin_hamburg_request["start"],
        "ziel": berlin_hamburg_request["ziel"],
        "zwischenstopps": [
            {
                "koordinate": list(wp.koordinate),
                "aufenthaltsdauer_s": int(wp.aufenthaltsdauer.total_seconds())
                if wp.aufenthaltsdauer
                else None,
            }
        ],
        "abfahrtszeit": berlin_hamburg_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": berlin_hamburg_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
    }
