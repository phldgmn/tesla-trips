"""Tests für das wind-Modul: Konfiguration und Fixtures.

Fixtures für das wind-Modul:
- weather_sample_nordwind: Nordwind (0°)
- weather_sample_ostwind: Ostwind (90°)
- segment_norden: Bearing 0° (Norden)
- segment_sueden: Bearing 180° (Süden)
- segment_osten: Bearing 90° (Osten)
"""

from __future__ import annotations

from datetime import datetime

from tripplanner.geo import Coordinate
from tripplanner.routing.models import RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


def make_weather_sample(
    windgeschwindigkeit_ms: float,
    windrichtung_deg: float,
    zeitpunkt_str: str = "2026-08-02T12:00:00",
) -> WeatherSample:
    """Hilfsfunktion zur Erstellung von WeatherSample-Instanzen."""
    return WeatherSample(
        coordinate=BERLIN,
        zeitpunkt=datetime.fromisoformat(zeitpunkt_str),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=windgeschwindigkeit_ms,
        windrichtung_deg=windrichtung_deg,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )


def make_route_segment(segment_index: int, bearing_deg: float) -> RouteSegment:
    """Hilfsfunktion zur Erstellung von RouteSegment-Instanzen."""
    return RouteSegment(
        segment_index=segment_index,
        geometrie=[BERLIN, HAMBURG],
        laenge_m=250_000.0,
        strassenklasse="PRIMARY",
        bearing_deg=bearing_deg,
    )


def make_wind_components(
    segment_index: int,
    gegenwind_ms: float,
    seitenwind_ms: float,
) -> WindComponents:
    """Hilfsfunktion zur Erstellung von WindComponents-Instanzen."""
    return WindComponents(
        segment_index=segment_index,
        gegenwind_ms=gegenwind_ms,
        seitenwind_ms=seitenwind_ms,
    )


# Fixtures fuer Windtests
def weather_sample_nordwind() -> WeatherSample:
    """Wetterdaten mit Nordwind (0°)."""
    return make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0)


def weather_sample_ostwind() -> WeatherSample:
    """Wetterdaten mit Ostwind (90°)."""
    return make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=90.0)


def weather_sample_suedwind() -> WeatherSample:
    """Wetterdaten mit Südwind (180°)."""
    return make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=180.0)


def weather_sample_westwind() -> WeatherSample:
    """Wetterdaten mit Westwind (270°)."""
    return make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=270.0)


def weather_sample_45_grad_wind() -> WeatherSample:
    """Wetterdaten mit Wind aus 45° (Nord-Ost)."""
    return make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=45.0)


def weather_sample_315_grad_wind() -> WeatherSample:
    """Wetterdaten mit Wind aus 315° (Nord-West)."""
    return make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=315.0)


# Fixtures fuer RouteSegment
def segment_norden() -> RouteSegment:
    """Segment mit Bearing 0° (Norden)."""
    return make_route_segment(segment_index=0, bearing_deg=0.0)


def segment_sueden() -> RouteSegment:
    """Segment mit Bearing 180° (Süden)."""
    return make_route_segment(segment_index=0, bearing_deg=180.0)


def segment_osten() -> RouteSegment:
    """Segment mit Bearing 90° (Osten)."""
    return make_route_segment(segment_index=0, bearing_deg=90.0)


def segment_suedosten() -> RouteSegment:
    """Segment mit Bearing 135° (Süd-Osten)."""
    return make_route_segment(segment_index=0, bearing_deg=135.0)


def segment_nordosten() -> RouteSegment:
    """Segment mit Bearing 45° (Nord-Osten)."""
    return make_route_segment(segment_index=0, bearing_deg=45.0)
