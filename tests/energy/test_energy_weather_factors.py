"""Additional tests for weather-dependent factors in energy consumption.

Ensures that wet/snow conditions and temperature-driven air density affect the
segment energy calculation.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from tripplanner.energy.energy import calculate_segment_consumption
from tripplanner.energy.models import VehicleEnergyParameters
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents

# Reuse fixtures from conftest via pytest injection


def _make_weather(
    temperature_c: float,
    precipitation_mm: float = 0.0,
    snowfall_cm: float = 0.0,
    pressure_hpa: float = 1013.25,
) -> WeatherSample:
    """Helper to create a WeatherSample with minimal required fields.

    The coordinate and timestamp are dummy values; they are not used in the
    consumption calculation apart from being stored on the model.
    """
    return WeatherSample(
        coordinate=(0.0, 0.0),
        timestamp=datetime.utcnow(),
        temperature_c=temperature_c,
        wind_speed_ms=0.0,
        wind_direction_deg=0.0,
        precipitation_mm=precipitation_mm,
        snowfall_cm=snowfall_cm,
        pressure_hpa=pressure_hpa,
        humidity_pct=50.0,
        solar_radiation_wm2=400.0,
        cloudiness_pct=20.0,
    )


@pytest.fixture
def wind_still() -> WindComponents:
    """Windless components for baseline tests."""
    return WindComponents(segment_index=0, gegenwind_ms=0.0, seitenwind_ms=0.0)


@pytest.fixture
def vehicle_params() -> VehicleEnergyParameters:
    """Tesla Model-3 default parameters."""
    return VehicleEnergyParameters()


@pytest.fixture
def flat_segment(segment_eben):  # type: ignore
    """Flat segment of 1 km at 120 km/h (from existing fixture)."""
    return segment_eben


@pytest.fixture
def flat_gradient(gradient_eben):  # type: ignore
    """Zero gradient (flat) (from existing fixture)."""
    return gradient_eben


def test_wet_road_increases_consumption(
    flat_segment,
    flat_gradient,
    wind_still,
    vehicle_params,
):
    dry = _make_weather(temperature_c=20.0, precipitation_mm=0.0, snowfall_cm=0.0)
    wet = _make_weather(temperature_c=20.0, precipitation_mm=5.0, snowfall_cm=0.0)

    dry_res = calculate_segment_consumption(
        segment=flat_segment,
        gradient=flat_gradient,
        wetter=dry,
        wind=wind_still,
        fahrzeug_params=vehicle_params,
    )
    wet_res = calculate_segment_consumption(
        segment=flat_segment,
        gradient=flat_gradient,
        wetter=wet,
        wind=wind_still,
        fahrzeug_params=vehicle_params,
    )

    assert wet_res.energiebedarf_kwh > dry_res.energiebedarf_kwh


def test_snow_road_increases_consumption_more_than_wet(
    flat_segment,
    flat_gradient,
    wind_still,
    vehicle_params,
):
    wet = _make_weather(temperature_c=0.0, precipitation_mm=5.0, snowfall_cm=0.0)
    snow = _make_weather(temperature_c=-5.0, precipitation_mm=0.0, snowfall_cm=2.0)

    wet_res = calculate_segment_consumption(
        segment=flat_segment,
        gradient=flat_gradient,
        wetter=wet,
        wind=wind_still,
        fahrzeug_params=vehicle_params,
    )
    snow_res = calculate_segment_consumption(
        segment=flat_segment,
        gradient=flat_gradient,
        wetter=snow,
        wind=wind_still,
        fahrzeug_params=vehicle_params,
    )

    # Snow should cause higher rolling resistance than plain rain.
    assert snow_res.energiebedarf_kwh > wet_res.energiebedarf_kwh


def test_colder_air_increases_drag_due_to_density(
    flat_segment,
    flat_gradient,
    wind_still,
    vehicle_params,
):
    warm = _make_weather(temperature_c=20.0, pressure_hpa=1013.25)
    cold = _make_weather(temperature_c=-20.0, pressure_hpa=1013.25)

    warm_res = calculate_segment_consumption(
        segment=flat_segment,
        gradient=flat_gradient,
        wetter=warm,
        wind=wind_still,
        fahrzeug_params=vehicle_params,
    )
    cold_res = calculate_segment_consumption(
        segment=flat_segment,
        gradient=flat_gradient,
        wetter=cold,
        wind=wind_still,
        fahrzeug_params=vehicle_params,
    )

    # Cold air is denser, leading to higher aerodynamic drag.
    assert cold_res.energiebedarf_kwh > warm_res.energiebedarf_kwh
