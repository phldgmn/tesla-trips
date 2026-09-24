"""Tests für das energy-Modul: Konfiguration und Fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters
from tripplanner.geo import Coordinate
from tripplanner.routing.models import RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


def make_weather_sample(
    temperature_c: float = 20.0,
    wind_speed_ms: float = 0.0,
    wind_direction_deg: float = 0.0,
) -> WeatherSample:
    """Hilfsfunktion zur Erstellung von WeatherSample-Instanzen."""
    return WeatherSample(
        coordinate=BERLIN,
        timestamp=datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
        temperature_c=temperature_c,
        wind_speed_ms=wind_speed_ms,
        wind_direction_deg=wind_direction_deg,
        precipitation_mm=0.0,
        snowfall_cm=0.0,
        pressure_hpa=1013.0,
        humidity_pct=50.0,
        solar_radiation_wm2=300.0,
        cloudiness_pct=0.0,
    )


def make_route_segment(
    segment_index: int,
    length_m: float = 1000.0,
    bearing_deg: float = 0.0,
    speed_limit_kmh: int = 120,
    surface: str | None = None,
    steigung_rohdaten: list[float] | None = None,
) -> RouteSegment:
    """Hilfsfunktion zur Erstellung von RouteSegment-Instanzen."""
    return RouteSegment(
        segment_index=segment_index,
        geometrie=[BERLIN, HAMBURG],
        length_m=length_m,
        strassenklasse="PRIMARY",
        surface=surface,
        speed_limit_kmh=speed_limit_kmh,
        steigung_rohdaten=steigung_rohdaten[0] if steigung_rohdaten else None,
        bearing_deg=bearing_deg,
    )


def make_segment_gradient(
    segment_index: int,
    slope_percent: float = 0.0,
    hoehendifferenz_m: float = 0.0,
) -> SegmentGradient:
    """Hilfsfunktion zur Erstellung von SegmentGradient-Instanzen."""
    return SegmentGradient(
        segment_index=segment_index,
        steigung_prozent=slope_percent,
        hoehendifferenz_m=hoehendifferenz_m,
        horizontale_distanz_m=1000.0,
    )


def make_wind_components(
    segment_index: int,
    gegenwind_ms: float = 0.0,
    seitenwind_ms: float = 0.0,
) -> WindComponents:
    """Hilfsfunktion zur Erstellung von WindComponents-Instanzen."""
    return WindComponents(
        segment_index=segment_index,
        gegenwind_ms=gegenwind_ms,
        seitenwind_ms=seitenwind_ms,
    )


def make_segment_energy_result(
    segment_index: int = 0,
    energiebedarf_kwh: float = 1.5,
    rekuperation_kwh: float = 0.0,
    energiebedarf_brutto_kwh: float = 1.5,
    speed_ms: float = 27.78,
    drive_time_s: float = 30.0,
    segment_length_m: float = 1000.0,
) -> SegmentEnergyResult:
    """Hilfsfunktion zur Erstellung von SegmentEnergyResult-Instanzen."""
    return SegmentEnergyResult(
        segment_index=segment_index,
        energiebedarf_kwh=energiebedarf_kwh,
        rekuperation_kwh=rekuperation_kwh,
        energiebedarf_brutto_kwh=energiebedarf_brutto_kwh,
        speed_ms=speed_ms,
        drive_time_s=drive_time_s,
        segment_length_m=segment_length_m,
    )


# Fixtures fuer Fahrzeugparameter
@pytest.fixture
def default_model3_params() -> VehicleEnergyParameters:
    """Tesla Model 3 Default-Parameter."""
    return VehicleEnergyParameters()


# Fixtures fuer RouteSegment
@pytest.fixture
def segment_eben() -> RouteSegment:
    """Ebene segment (1000 m, speed_limit_kmh 120 km/h, gradient 0 %)."""
    return make_route_segment(
        segment_index=0,
        length_m=1000.0,
        bearing_deg=0.0,
        speed_limit_kmh=120,
        surface="asphalt",
        steigung_rohdaten=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


@pytest.fixture
def segment_steigung_3pct() -> RouteSegment:
    """segment mit +3 % gradient (800 m, speed_limit_kmh 100 km/h)."""
    return make_route_segment(
        segment_index=1,
        length_m=800.0,
        bearing_deg=0.0,
        speed_limit_kmh=100,
        surface="asphalt",
        steigung_rohdaten=[0.0, 1.5, 3.0, 2.5, 0.0],
    )


@pytest.fixture
def segment_gefaelle_4pct() -> RouteSegment:
    """segment mit -4 % Gefaelle (1200 m, speed_limit_kmh 110 km/h)."""
    return make_route_segment(
        segment_index=2,
        length_m=1200.0,
        bearing_deg=0.0,
        speed_limit_kmh=110,
        surface="asphalt",
        steigung_rohdaten=[0.0, -2.0, -4.0, -3.0, 0.0],
    )


# Fixtures fuer SegmentGradient
@pytest.fixture
def gradient_eben() -> SegmentGradient:
    """Ebene Segment-Gradient (0 %)."""
    return make_segment_gradient(segment_index=0, slope_percent=0.0, hoehendifferenz_m=0.0)


@pytest.fixture
def gradient_steigung_3pct() -> SegmentGradient:
    """Segment-Gradient mit +3 % gradient."""
    return make_segment_gradient(segment_index=1, slope_percent=3.0, hoehendifferenz_m=24.0)


@pytest.fixture
def gradient_gefaelle_4pct() -> SegmentGradient:
    """Segment-Gradient mit -4 % Gefaelle."""
    return make_segment_gradient(segment_index=2, slope_percent=-4.0, hoehendifferenz_m=-48.0)


# Fixtures fuer Wind
@pytest.fixture
def wind_components_windstill() -> WindComponents:
    """Windkomponenten fuer Windstille (0 m/s headwind)."""
    return make_wind_components(segment_index=0, gegenwind_ms=0.0, seitenwind_ms=0.0)


@pytest.fixture
def wind_components_gegenwind() -> WindComponents:
    """Windkomponenten mit headwind (5 m/s)."""
    return make_wind_components(segment_index=0, gegenwind_ms=5.0, seitenwind_ms=1.0)


@pytest.fixture
def wind_components_rueckenwind() -> WindComponents:
    """Windkomponenten mit Rueckenwind (-5 m/s)."""
    return make_wind_components(segment_index=0, gegenwind_ms=-5.0, seitenwind_ms=1.0)


# Fixtures fuer Wetter
@pytest.fixture
def wetter_sample_ref() -> WeatherSample:
    """Referenzwetter (20°C, windstill)."""
    return make_weather_sample(temperature_c=20.0, wind_speed_ms=0.0, wind_direction_deg=0.0)


@pytest.fixture
def wetter_sample_neg10c_heizung() -> WeatherSample:
    """Wetter fuer Heizungsfall (-10°C, Klima auf Heizung)."""
    return make_weather_sample(temperature_c=-10.0, wind_speed_ms=0.0, wind_direction_deg=0.0)


@pytest.fixture
def wetter_sample_32c_klima() -> WeatherSample:
    """Wetter fuer Klimafall (32°C, Klima auf maximum Leistung)."""
    return make_weather_sample(temperature_c=32.0, wind_speed_ms=0.0, wind_direction_deg=0.0)
