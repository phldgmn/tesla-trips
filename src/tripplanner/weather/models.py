"""weather-data models for the Tesla Tripplanner project.

This module defines the official types from the registry:
- `WeatherQuery`: query for a single weather event
- `WeatherSample`: weather data for a timestamp at a coordinate
- `OpenMeteoResponse`: Raw response from the Open-Meteo Forecast API (internal)
- `WeatherDetailLevel`: granularity of the weather resolution ("off" | "low" | "medium" | "high")
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from tripplanner.geo import Coordinate

WeatherDetailLevel = Literal["off", "low", "medium", "high"]
"""Weather detail granularity level.

``"low"``/``"medium"``/``"high"`` all share the same one-shot, never-
refetching mechanism and only differ in how far apart their sample points
are along the route; every other segment's weather is linearly interpolated
between the two along-route-nearest sampled points.

- ``"off"``: No weather data (placeholder samples).
- ``"low"``: Points every ~200km (see
  `tripplanner.weather.weather.LOW_DETAIL_SAMPLE_SPACING_M`).
- ``"medium"``: Points every ~120km (see
  `tripplanner.weather.weather.MEDIUM_DETAIL_SAMPLE_SPACING_M`).
- ``"high"``: Points every ~60km (see
  `tripplanner.weather.weather.HIGH_DETAIL_SAMPLE_SPACING_M`).

All three also force a fresh point right before and right after any stop
longer than `tripplanner.weather.weather.LONG_STOP_THRESHOLD` (e.g. an
overnight wait at a mandatory waypoint), instead of interpolating stale
pre-stop conditions across the whole stationary period.
"""


class WeatherQuery(BaseModel):
    """Query for a single weather event."""

    coordinate: Coordinate
    """WGS84 (lat, lon)."""

    timestamp: datetime
    """Timestamp of the weather query."""


class WeatherSample(BaseModel):
    """Weather data for a timestamp at a coordinate."""

    coordinate: Coordinate
    """WGS84 (lat, lon)."""

    timestamp: datetime
    """Timestamp of the weather data."""

    temperature_c: float = Field(ge=-100.0, le=70.0, description="temperature in degrees C")
    """temperature in degrees C."""

    wind_speed_ms: float = Field(ge=0.0, description="wind_speed_ms in m/s")
    """wind_speed_ms in m/s."""

    wind_direction_deg: float = Field(
        ge=0.0, le=360.0, description="wind_direction_deg in degrees (0 = N, 90 = E)"
    )
    """wind_direction_deg in Grad (0° = N, 90° = O)."""

    precipitation_mm: float = Field(ge=0.0, description="precipitation in mm (Stundensumme)")
    """precipitation in mm (Stundensumme)."""

    snowfall_cm: float = Field(ge=0.0, description="snowfall in cm (Wasserequivalent)")
    """snowfall in cm (Wasserequivalent)."""

    pressure_hpa: float = Field(ge=870.0, le=1084.0, description="Luftdruck in hPa (MSL)")
    """Air pressure in hPa (MSL)."""

    humidity_pct: float = Field(ge=0.0, le=100.0, description="Relative Luftfeuchtigkeit in %")
    """Relative Luftfeuchtigkeit in %."""

    solar_radiation_wm2: float = Field(ge=0.0, description="Globalstrahlung in W/m² (Stundensumme)")
    """Globalstrahlung in W/m² (Stundensumme)."""

    cloudiness_pct: float = Field(ge=0.0, le=100.0, description="cloud cover in %")
    """cloud cover in %."""


class OpenMeteoResponse(BaseModel):
    """Raw response from the Open-Meteo Forecast API (only for internal processing)."""

    latitude: float
    """latitude."""

    longitude: float
    """Longitude."""

    timezone: str
    """Zeitzone (IANA-Name)."""

    timezone_abbreviation: str
    """Time zone abbreviation."""

    elevation: float
    """Elevation above sea level in meters."""

    hourly: dict[str, list[float | int | str | None]]
    """Hourly data as a dictionary with parameter names as keys."""

    hourly_units: dict[str, str]
    """Units for the hourly data."""
