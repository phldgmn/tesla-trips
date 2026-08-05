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
    temperatur_c: float = 20.0,
    windgeschwindigkeit_ms: float = 0.0,
    windrichtung_deg: float = 0.0,
) -> WeatherSample:
    """Hilfsfunktion zur Erstellung von WeatherSample-Instanzen."""
    return WeatherSample(
        koordinate=BERLIN,
        zeitpunkt=datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
        temperatur_c=temperatur_c,
        windgeschwindigkeit_ms=windgeschwindigkeit_ms,
        windrichtung_deg=windrichtung_deg,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.0,
        luftfeuchtigkeit_pct=50.0,
        globalstrahlung_wm2=300.0,
        bewoelkung_pct=0.0,
    )


def make_route_segment(
    segment_index: int,
    laenge_m: float = 1000.0,
    bearing_deg: float = 0.0,
    tempolimit_kmh: int = 120,
    oberflaeche: str | None = None,
    steigung_rohdaten: list[float] | None = None,
) -> RouteSegment:
    """Hilfsfunktion zur Erstellung von RouteSegment-Instanzen."""
    return RouteSegment(
        segment_index=segment_index,
        geometrie=[BERLIN, HAMBURG],
        laenge_m=laenge_m,
        strassenklasse="PRIMARY",
        oberflaeche=oberflaeche,
        tempolimit_kmh=tempolimit_kmh,
        steigung_rohdaten=steigung_rohdaten[0] if steigung_rohdaten else None,
        bearing_deg=bearing_deg,
    )


def make_segment_gradient(
    segment_index: int,
    steigung_prozent: float = 0.0,
    hoehendifferenz_m: float = 0.0,
) -> SegmentGradient:
    """Hilfsfunktion zur Erstellung von SegmentGradient-Instanzen."""
    return SegmentGradient(
        segment_index=segment_index,
        steigung_prozent=steigung_prozent,
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
    geschwindigkeit_m_s: float = 27.78,
    fahrzeit_s: float = 30.0,
    streckenlaenge_m: float = 1000.0,
) -> SegmentEnergyResult:
    """Hilfsfunktion zur Erstellung von SegmentEnergyResult-Instanzen."""
    return SegmentEnergyResult(
        segment_index=segment_index,
        energiebedarf_kwh=energiebedarf_kwh,
        rekuperation_kwh=rekuperation_kwh,
        energiebedarf_brutto_kwh=energiebedarf_brutto_kwh,
        geschwindigkeit_m_s=geschwindigkeit_m_s,
        fahrzeit_s=fahrzeit_s,
        streckenlaenge_m=streckenlaenge_m,
    )


# Fixtures fuer Fahrzeugparameter
@pytest.fixture
def default_model3_params() -> VehicleEnergyParameters:
    """Tesla Model 3 Default-Parameter."""
    return VehicleEnergyParameters()


# Fixtures fuer RouteSegment
@pytest.fixture
def segment_eben() -> RouteSegment:
    """Ebene Strecke (1000 m, Tempolimit 120 km/h, Steigung 0 %)."""
    return make_route_segment(
        segment_index=0,
        laenge_m=1000.0,
        bearing_deg=0.0,
        tempolimit_kmh=120,
        oberflaeche="asphalt",
        steigung_rohdaten=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


@pytest.fixture
def segment_steigung_3pct() -> RouteSegment:
    """Strecke mit +3 % Steigung (800 m, Tempolimit 100 km/h)."""
    return make_route_segment(
        segment_index=1,
        laenge_m=800.0,
        bearing_deg=0.0,
        tempolimit_kmh=100,
        oberflaeche="asphalt",
        steigung_rohdaten=[0.0, 1.5, 3.0, 2.5, 0.0],
    )


@pytest.fixture
def segment_gefaelle_4pct() -> RouteSegment:
    """Strecke mit -4 % Gefaelle (1200 m, Tempolimit 110 km/h)."""
    return make_route_segment(
        segment_index=2,
        laenge_m=1200.0,
        bearing_deg=0.0,
        tempolimit_kmh=110,
        oberflaeche="asphalt",
        steigung_rohdaten=[0.0, -2.0, -4.0, -3.0, 0.0],
    )


# Fixtures fuer SegmentGradient
@pytest.fixture
def gradient_eben() -> SegmentGradient:
    """Ebene Segment-Gradient (0 %)."""
    return make_segment_gradient(segment_index=0, steigung_prozent=0.0, hoehendifferenz_m=0.0)


@pytest.fixture
def gradient_steigung_3pct() -> SegmentGradient:
    """Segment-Gradient mit +3 % Steigung."""
    return make_segment_gradient(segment_index=1, steigung_prozent=3.0, hoehendifferenz_m=24.0)


@pytest.fixture
def gradient_gefaelle_4pct() -> SegmentGradient:
    """Segment-Gradient mit -4 % Gefaelle."""
    return make_segment_gradient(segment_index=2, steigung_prozent=-4.0, hoehendifferenz_m=-48.0)


# Fixtures fuer Wind
@pytest.fixture
def wind_components_windstill() -> WindComponents:
    """Windkomponenten fuer Windstille (0 m/s Gegenwind)."""
    return make_wind_components(segment_index=0, gegenwind_ms=0.0, seitenwind_ms=0.0)


@pytest.fixture
def wind_components_gegenwind() -> WindComponents:
    """Windkomponenten mit Gegenwind (5 m/s)."""
    return make_wind_components(segment_index=0, gegenwind_ms=5.0, seitenwind_ms=1.0)


@pytest.fixture
def wind_components_rueckenwind() -> WindComponents:
    """Windkomponenten mit Rueckenwind (-5 m/s)."""
    return make_wind_components(segment_index=0, gegenwind_ms=-5.0, seitenwind_ms=1.0)


# Fixtures fuer Wetter
@pytest.fixture
def wetter_sample_ref() -> WeatherSample:
    """Referenzwetter (20°C, windstill)."""
    return make_weather_sample(temperatur_c=20.0, windgeschwindigkeit_ms=0.0, windrichtung_deg=0.0)


@pytest.fixture
def wetter_sample_neg10c_heizung() -> WeatherSample:
    """Wetter fuer Heizungsfall (-10°C, Klima auf Heizung)."""
    return make_weather_sample(temperatur_c=-10.0, windgeschwindigkeit_ms=0.0, windrichtung_deg=0.0)


@pytest.fixture
def wetter_sample_32c_klima() -> WeatherSample:
    """Wetter fuer Klimafall (32°C, Klima auf maximale Leistung)."""
    return make_weather_sample(temperatur_c=32.0, windgeschwindigkeit_ms=0.0, windrichtung_deg=0.0)
