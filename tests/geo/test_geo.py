"""Unit-Tests für tripplanner.geo: bearing_deg, haversine_distance_m, geodesic_length_m."""

import math

import pytest

from tripplanner.geo import Coordinate, bearing_deg, geodesic_length_m, haversine_distance_m

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


def test_bearing_deg_berlin_to_hamburg_is_northwest() -> None:
    """Berlin liegt südöstlich von Hamburg -> Bearing Berlin->Hamburg liegt im WNW-Quadranten."""
    bearing = bearing_deg(BERLIN, HAMBURG)
    assert bearing == pytest.approx(298.0, abs=1.5)


def test_bearing_deg_due_north() -> None:
    """Ziel exakt nördlich -> Bearing 0°."""
    start: Coordinate = (50.0, 10.0)
    end: Coordinate = (51.0, 10.0)
    assert bearing_deg(start, end) == pytest.approx(0.0, abs=1e-6)


def test_bearing_deg_due_east() -> None:
    """Ziel exakt östlich (am Äquator, um Meridian-Konvergenz zu vermeiden) -> Bearing 90°."""
    start: Coordinate = (0.0, 10.0)
    end: Coordinate = (0.0, 11.0)
    assert bearing_deg(start, end) == pytest.approx(90.0, abs=1e-6)


def test_bearing_deg_due_south() -> None:
    """Ziel exakt südlich -> Bearing 180°."""
    start: Coordinate = (50.0, 10.0)
    end: Coordinate = (49.0, 10.0)
    assert bearing_deg(start, end) == pytest.approx(180.0, abs=1e-6)


def test_bearing_deg_due_west() -> None:
    """Ziel exakt westlich (am Äquator) -> Bearing 270°."""
    start: Coordinate = (0.0, 10.0)
    end: Coordinate = (0.0, 9.0)
    assert bearing_deg(start, end) == pytest.approx(270.0, abs=1e-6)


def test_bearing_deg_always_in_range() -> None:
    """Bearing ist stets in [0, 360)."""
    bearing = bearing_deg((10.0, 10.0), (-10.0, -10.0))
    assert 0.0 <= bearing < 360.0


def test_haversine_distance_berlin_hamburg_matches_known_value() -> None:
    """Bekannte Luftliniendistanz Berlin<->Hamburg ist rund 255 km."""
    distance_m = haversine_distance_m(BERLIN, HAMBURG)
    assert distance_m == pytest.approx(255_000, rel=0.03)


def test_haversine_distance_is_symmetric() -> None:
    """Distanz(a, b) == Distanz(b, a)."""
    assert haversine_distance_m(BERLIN, HAMBURG) == pytest.approx(
        haversine_distance_m(HAMBURG, BERLIN)
    )


def test_haversine_distance_zero_for_identical_point() -> None:
    """Distanz eines Punktes zu sich selbst ist 0."""
    assert haversine_distance_m(BERLIN, BERLIN) == pytest.approx(0.0, abs=1e-6)


def test_haversine_distance_quarter_meridian_matches_earth_radius() -> None:
    """Distanz vom Äquator zum Nordpol entspricht einem Viertel des Erdumfangs."""
    equator: Coordinate = (0.0, 0.0)
    north_pole: Coordinate = (90.0, 0.0)
    expected = math.pi / 2 * 6_371_000.0
    assert haversine_distance_m(equator, north_pole) == pytest.approx(expected, rel=1e-6)


def test_geodesic_length_m_sums_consecutive_segments() -> None:
    """Länge eines Pfads entspricht der Summe der Großkreisdistanzen aufeinanderfolgender Punkte."""
    path: list[Coordinate] = [(50.0, 9.0), (50.0, 9.5), (50.5, 9.5)]
    expected = haversine_distance_m(path[0], path[1]) + haversine_distance_m(path[1], path[2])
    assert geodesic_length_m(path) == pytest.approx(expected)


def test_geodesic_length_m_two_points_matches_haversine() -> None:
    """Pfad aus zwei Punkten entspricht der einfachen Großkreisdistanz."""
    expected = haversine_distance_m(BERLIN, HAMBURG)
    assert geodesic_length_m([BERLIN, HAMBURG]) == pytest.approx(expected)


def test_geodesic_length_m_empty_path_is_zero() -> None:
    """Leerer Pfad hat Länge 0."""
    assert geodesic_length_m([]) == 0.0


def test_geodesic_length_m_single_point_is_zero() -> None:
    """Pfad mit nur einem Punkt hat Länge 0 (keine Distanz definiert)."""
    assert geodesic_length_m([BERLIN]) == 0.0
