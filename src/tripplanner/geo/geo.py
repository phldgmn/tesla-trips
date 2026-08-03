"""Geografische Grundberechnungen: Vorwärtsazimut und Großkreisdistanz.

Reine, abhängigkeitsfreie Funktionen ohne Zustand — siehe Modul-Docstring in
`tripplanner.geo.__init__` für die projektweite Koordinatenkonvention.
"""

import math

Coordinate = tuple[float, float]
"""Eine WGS84-Koordinate als `(lat, lon)` in Dezimalgrad."""

_EARTH_RADIUS_M = 6_371_000.0


def bearing_deg(start: Coordinate, end: Coordinate) -> float:
    """Berechnet den Vorwärtsazimut (Bearing) von `start` nach `end`.

    Nutzt die Großkreis-Bearing-Formel auf der WGS84-Kugelapproximation.

    Args:
        start: Startkoordinate `(lat, lon)` in Dezimalgrad.
        end: Zielkoordinate `(lat, lon)` in Dezimalgrad.

    Returns:
        Bearing in Grad, normalisiert auf `[0, 360)`. `0°` = Nord, `90°` = Ost.
    """
    lat1 = math.radians(start[0])
    lat2 = math.radians(end[0])
    delta_lon = math.radians(end[1] - start[1])

    x = math.sin(delta_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)

    bearing = math.degrees(math.atan2(x, y))
    return bearing % 360.0


def haversine_distance_m(a: Coordinate, b: Coordinate) -> float:
    """Berechnet die Großkreisdistanz zwischen zwei WGS84-Koordinaten.

    Args:
        a: Erste Koordinate `(lat, lon)` in Dezimalgrad.
        b: Zweite Koordinate `(lat, lon)` in Dezimalgrad.

    Returns:
        Distanz in Metern (Luftlinie entlang der Erdoberfläche).
    """
    lat1 = math.radians(a[0])
    lat2 = math.radians(b[0])
    delta_lat = math.radians(b[0] - a[0])
    delta_lon = math.radians(b[1] - a[1])

    h = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))
    return _EARTH_RADIUS_M * c
