"""Tests für das weather-Modul: Konfiguration und Fixtures.

Fixtures für das weather-Modul:
- weather_sample_north_wind: Nordwind-Wetterdaten
- weather_sample_east_wind: Ostwind-Wetterdaten
"""

from datetime import datetime

import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherSample

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


@pytest.fixture
def weather_sample_north_wind() -> WeatherSample:
    """Wetterdaten mit Nordwind (0°)."""
    return WeatherSample(
        koordinate=BERLIN,
        zeitpunkt=datetime(2026, 8, 2, 12, 0),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=10.0,
        windrichtung_deg=0.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )


@pytest.fixture
def weather_sample_east_wind() -> WeatherSample:
    """Wetterdaten mit Ostwind (90°)."""
    return WeatherSample(
        koordinate=BERLIN,
        zeitpunkt=datetime(2026, 8, 2, 12, 0),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=10.0,
        windrichtung_deg=90.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )


@pytest.fixture
def weather_query_berlin_noon() -> WeatherSample:
    """Wetterabfrage für Berlin Mittag."""
    return WeatherSample(
        koordinate=BERLIN,
        zeitpunkt=datetime(2026, 8, 2, 12, 0),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=10.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )
