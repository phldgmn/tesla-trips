"""Wetter-Datenmodelle für das Tesla-Tripplaner-Projekt.

Diese Module definieren die offiziellen Typen aus dem Register:
- `WeatherQuery`: Abfrage für einen einzelnen Wetterereignis
- `WeatherSample`: Wetterdaten für einen Zeitpunkt an einer Koordinate
- `OpenMeteoResponse`: Raw-Response von Open-Meteo Forecast API (intern)
- `WeatherDetailLevel`: Steuerungsgrad der Wetterauflösung ("off" | "low" | "medium" | "high")
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
    """Abfrage für ein einzelnes Wetterereignis."""

    koordinate: Coordinate
    """WGS84 (lat, lon)."""

    zeitpunkt: datetime
    """Zeitpunkt der Wetterabfrage."""


class WeatherSample(BaseModel):
    """Wetterdaten für einen Zeitpunkt an einer Koordinate."""

    koordinate: Coordinate
    """WGS84 (lat, lon)."""

    zeitpunkt: datetime
    """Zeitpunkt der Wetterdaten."""

    temperatur_c: float = Field(ge=-100.0, le=70.0, description="Temperatur in °C")
    """Temperatur in °C."""

    windgeschwindigkeit_ms: float = Field(ge=0.0, description="Windgeschwindigkeit in m/s")
    """Windgeschwindigkeit in m/s."""

    windrichtung_deg: float = Field(
        ge=0.0, le=360.0, description="Windrichtung in Grad (0° = N, 90° = O)"
    )
    """Windrichtung in Grad (0° = N, 90° = O)."""

    niederschlag_mm: float = Field(ge=0.0, description="Niederschlag in mm (Stundensumme)")
    """Niederschlag in mm (Stundensumme)."""

    schneefall_cm: float = Field(ge=0.0, description="Schneefall in cm (Wasserequivalent)")
    """Schneefall in cm (Wasserequivalent)."""

    luftdruck_hpa: float = Field(ge=870.0, le=1084.0, description="Luftdruck in hPa (MSL)")
    """Luftdruck in hPa (MSL)."""

    luftfeuchtigkeit_pct: float = Field(
        ge=0.0, le=100.0, description="Relative Luftfeuchtigkeit in %"
    )
    """Relative Luftfeuchtigkeit in %."""

    globalstrahlung_wm2: float = Field(ge=0.0, description="Globalstrahlung in W/m² (Stundensumme)")
    """Globalstrahlung in W/m² (Stundensumme)."""

    bewoelkung_pct: float = Field(ge=0.0, le=100.0, description="Bewölkung in %")
    """Bewölkung in %."""


class OpenMeteoResponse(BaseModel):
    """Raw-Response von Open-Meteo Forecast API (nur für interne Verarbeitung)."""

    latitude: float
    """Breitengrad."""

    longitude: float
    """Längengrad."""

    timezone: str
    """Zeitzone (IANA-Name)."""

    timezone_abbreviation: str
    """Zeitzonen-Kürzel."""

    elevation: float
    """Höhe über NN in Metern."""

    hourly: dict[str, list[float | int | str | None]]
    """Hourly-Daten als Dictionary mit Parameternamen als Keys."""

    hourly_units: dict[str, str]
    """Einheiten für die hourly-Daten."""
