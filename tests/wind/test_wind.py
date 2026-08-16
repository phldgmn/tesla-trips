"""Tests für das wind-Modul: Berechnung von Gegenwind- und Seitenwind-Komponenten.

Dieses Modul enthält Unit-Tests für die trigonometrische Projektion von Windvektoren
auf die Fahrtrichtung (Bearing) von Route-Segments.

Testfälle gemäß Plan 03, Abschnitt 6:
1. Nordwind (0°) + Bearing Norden (0°) → Rückenwind (-10 m/s), kein Seitenwind
2. Nordwind (0°) + Bearing Süden (180°) → Gegenwind (+10 m/s), kein Seitenwind
3. Ostwind (90°) + Bearing Norden (0°) → Kein Gegenwind, Seitenwind von rechts (+10 m/s)

Zusätzliche Testfälle:
4. 45°-Winkel-Grenzfall (Bearing 45°, Wind aus 225°)
5. Windrichtung-Normalisierung (360° = 0° für Nordwind)
6. compute_wind_components_for_route mit mehreren Segmenten
7. Seitenwind von links (Wind aus 270°, Bearing 0°)
"""

from __future__ import annotations

import math

import pytest

from tripplanner.routing.models import RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.wind import compute_wind_components, compute_wind_components_for_route
from tripplanner.wind.models import WindComponents

from .conftest import make_route_segment, make_weather_sample


def assert_wind_components_close(
    actual: WindComponents,
    expected: WindComponents,
    tol: float = 1e-6,
) -> None:
    """Prüft, ob WindComponents-Werte innerhalb einer Toleranz übereinstimmen."""
    assert actual.segment_index == expected.segment_index, (
        f"segment_index mismatch: {actual.segment_index} != {expected.segment_index}"
    )
    assert math.isclose(actual.gegenwind_ms, expected.gegenwind_ms, abs_tol=tol), (
        f"gegenwind_ms mismatch: {actual.gegenwind_ms} != {expected.gegenwind_ms}"
    )
    assert math.isclose(actual.seitenwind_ms, expected.seitenwind_ms, abs_tol=tol), (
        f"seitenwind_ms mismatch: {actual.seitenwind_ms} != {expected.seitenwind_ms}"
    )


def test_compute_wind_components_nordwind_norden() -> None:
    """Test 1: Nordwind (0°) + Bearing Norden (0°) → Rückenwind.

    Gegeben: Nordwind (0°), Bearing 0° (Norden)
    Erwartet: Gegenwind = -10 m/s (Rückenwind), Seitenwind = 0 m/s

    Begründung: Wind kommt vom Norden (weht nach Süden), Fahrtrichtung ist Norden.
    Der Wind wirkt direkt entgegen der Fahrtrichtung (Rückenwind = negativer Gegenwind).
    """
    weather = make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0)
    segment = make_route_segment(segment_index=0, bearing_deg=0.0)

    result = compute_wind_components(weather, segment)

    assert result.segment_index == 0
    # Windkomponente in Fahrtrichtung: cos(180°) = -1
    assert math.isclose(result.gegenwind_ms, -10.0, abs_tol=1e-6)
    # Kein Seitenwind
    assert math.isclose(result.seitenwind_ms, 0.0, abs_tol=1e-6)


def test_compute_wind_components_nordwind_sueden() -> None:
    """Test 2: Nordwind (0°) + Bearing Süden (180°) → Gegenwind.

    Gegeben: Nordwind (0°), Bearing 180° (Süden)
    Erwartet: Gegenwind = +10 m/s, Seitenwind = 0 m/s

    Begründung: Wind kommt vom Norden (weht nach Süden), Fahrtrichtung ist Süden.
    Der Wind wirkt in Fahrtrichtung (Gegenwind = positiv).
    """
    weather = make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0)
    segment = make_route_segment(segment_index=1, bearing_deg=180.0)

    result = compute_wind_components(weather, segment)

    assert result.segment_index == 1
    # Windkomponente in Fahrtrichtung: cos(0°) = 1
    assert math.isclose(result.gegenwind_ms, 10.0, abs_tol=1e-6)
    # Kein Seitenwind
    assert math.isclose(result.seitenwind_ms, 0.0, abs_tol=1e-6)


def test_compute_wind_components_ostwind_norden() -> None:
    """Test 3: Ostwind (90°) + Bearing Norden (0°) → Seitenwind von rechts.

    Gegeben: Ostwind (90°), Bearing 0° (Norden)
    Erwartet: Gegenwind = 0 m/s, Seitenwind = +10 m/s (von rechts)

    Begründung: Wind kommt vom Osten (weht nach Westen), Fahrtrichtung ist Norden.
    Der Wind wirkt senkrecht zur Fahrtrichtung von rechts (positive y-Richtung).
    """
    weather = make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=90.0)
    segment = make_route_segment(segment_index=2, bearing_deg=0.0)

    result = compute_wind_components(weather, segment)

    assert result.segment_index == 2
    # Kein Gegenwind (Wind senkrecht zur Fahrtrichtung)
    assert math.isclose(result.gegenwind_ms, 0.0, abs_tol=1e-6)
    # Seitenwind von rechts: sin(90°) = 1
    assert math.isclose(result.seitenwind_ms, 10.0, abs_tol=1e-6)


def test_compute_wind_components_45_dregree_edge_case() -> None:
    """Test 4: 45°-Winkel-Grenzfall (Bearing 45°, Wind aus 225°).

    Gegeben: Wind aus 225° (Süd-West), Bearing 45° (Nord-Ost)
    Erwartet: v_long = 10 m/s, v_side = 0 m/s

    Begründung: Der Windvektor (225° + 180° = 405° mod 360 = 45°) ist genau
    entgegengesetzt zur Bearing-Richtung (45°), also delta_theta = 0°.
    """
    # Wind aus 225° (Süd-West), weht nach 45° (Nord-Ost)
    weather = make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=225.0)
    segment = make_route_segment(segment_index=3, bearing_deg=45.0)

    result = compute_wind_components(weather, segment)

    # Windvektor = (225 + 180) % 360 = 405 % 360 = 45°
    # Bearing = 45°, also delta_theta = 45 - 45 = 0°
    # v_long = 10 * cos(0) = 10
    # v_side = 10 * sin(0) = 0
    assert math.isclose(result.gegenwind_ms, 10.0, abs_tol=1e-6)
    assert math.isclose(result.seitenwind_ms, 0.0, abs_tol=1e-6)


def test_compute_wind_components_windrichtung_normalisierung() -> None:
    """Test 5: Windrichtung-Normalisierung (360° = 0° für Nordwind).

    Gegeben: Windrichtung 360° (identisch mit 0°), Bearing 0° (Norden)
    Erwartet: Gegenwind = -10 m/s (Rückenwind), wie bei 0°
    """
    # Windrichtung 360° sollte identisch mit 0° sein
    weather = make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=360.0)
    segment = make_route_segment(segment_index=4, bearing_deg=0.0)

    result = compute_wind_components(weather, segment)

    # Sollte identisch sein mit Test 1 (Nordwind + Norden)
    assert math.isclose(result.gegenwind_ms, -10.0, abs_tol=1e-6)
    assert math.isclose(result.seitenwind_ms, 0.0, abs_tol=1e-6)


def test_compute_wind_components_for_route_multiple_segments() -> None:
    """Test 6: compute_wind_components_for_route mit mehreren Segmenten.

    Gegeben: 3 Segmente mit verschiedenen Bearings, 3 Wetterdatenpunkte
    Erwartet: Liste von 3 WindComponents mit korrekten Werten für jedes Segment
    """
    weather_samples: list[WeatherSample] = [
        make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0),
        make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=90.0),
        make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=180.0),
    ]
    segments: list[RouteSegment] = [
        make_route_segment(segment_index=0, bearing_deg=0.0),  # Norden
        make_route_segment(segment_index=1, bearing_deg=90.0),  # Osten
        make_route_segment(segment_index=2, bearing_deg=180.0),  # Süden
    ]

    result = compute_wind_components_for_route(weather_samples, segments)

    assert len(result) == 3

    # Segment 0: Nordwind (0°), Bearing 0° → Gegenwind = -10 m/s
    assert result[0].segment_index == 0
    assert math.isclose(result[0].gegenwind_ms, -10.0, abs_tol=1e-6)
    assert math.isclose(result[0].seitenwind_ms, 0.0, abs_tol=1e-6)

    # Segment 1: Ostwind (90°), Bearing 90° → Gegenwind = -10 m/s
    assert result[1].segment_index == 1
    assert math.isclose(result[1].gegenwind_ms, -10.0, abs_tol=1e-6)
    assert math.isclose(result[1].seitenwind_ms, 0.0, abs_tol=1e-6)

    # Segment 2: Südwind (180°), Bearing 180° → Gegenwind = -10 m/s
    assert result[2].segment_index == 2
    assert math.isclose(result[2].gegenwind_ms, -10.0, abs_tol=1e-6)
    assert math.isclose(result[2].seitenwind_ms, 0.0, abs_tol=1e-6)


def test_compute_wind_components_seitenwind_links() -> None:
    """Test 7: Seitenwind von links (Wind aus 270°, Bearing 0°).

    Gegeben: Wind aus 270° (Westen), Bearing 0° (Norden)
    Erwartet: Gegenwind = 0 m/s, Seitenwind = -10 m/s (von links)

    Begründung: Wind kommt vom Westen (weht nach Osten), Fahrtrichtung ist Norden.
    Der Wind wirkt senkrecht zur Fahrtrichtung von links (negative y-Richtung).
    """
    weather = make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=270.0)
    segment = make_route_segment(segment_index=5, bearing_deg=0.0)

    result = compute_wind_components(weather, segment)

    # Kein Gegenwind (Wind senkrecht zur Fahrtrichtung)
    assert math.isclose(result.gegenwind_ms, 0.0, abs_tol=1e-6)
    # Seitenwind von links: Windvektor = (270 + 180) % 360 = 90°, Bearing = 0°
    # delta_theta = 0 - 90 = -90°, sin(-90°) = -1
    assert math.isclose(result.seitenwind_ms, -10.0, abs_tol=1e-6)


def test_compute_wind_components_for_route_laengenfehler() -> None:
    """Test 8: compute_wind_components_for_route mit falscher Länge.

    Gegeben: 2 Wetterdatenpunkte, 3 Segmente
    Erwartet: ValueError mit passender Fehlermeldung
    """
    weather_samples: list[WeatherSample] = [
        make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0),
        make_weather_sample(windgeschwindigkeit_ms=10.0, windrichtung_deg=90.0),
    ]
    segments: list[RouteSegment] = [
        make_route_segment(segment_index=0, bearing_deg=0.0),
        make_route_segment(segment_index=1, bearing_deg=90.0),
        make_route_segment(segment_index=2, bearing_deg=180.0),
    ]

    with pytest.raises(
        ValueError,
        match="weather_samples \\(2\\) und segments \\(3\\) müssen gleiche Länge haben",
    ):
        compute_wind_components_for_route(weather_samples, segments)
