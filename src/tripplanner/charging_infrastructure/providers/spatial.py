"""Raeumliche Index-Hilfsfunktionen fuer den Ladedraht-Provider."""

from __future__ import annotations

from math import ceil

from tripplanner.geo import Coordinate, haversine_distance_m

from ..models import ChargingStation

_LAT_BAND_KM = 5.0
"""latitude der latitude-Bänder für den räumlichen Stations-Index (siehe
`_build_lat_bands`/`_stations_in_radius`). small genug, um bei den in der
Praxis verwendeten Suchradien (1-15 km, siehe `get_stations_along_route`)
die pro Abfrage zu prüfende Kandidatenzahl massiv zu reduzieren - unabhängig
vom tatsächlichen `radius_km` einer konkreten Abfrage korrekt, da
`_stations_in_radius` die Anzahl der zu scannenden Bänder passend zu
`radius_km` berechnet."""

_KM_PER_LAT_DEG = 111.0
"""Näherung: 1 latitude ≈ 111 km (global nahezu konstant - anders als 1
Längengrad, der mit `cos(lat)` schrumpft). Deshalb Bucketing NUR nach
latitude, nicht 2D nach Breiten-/Längengrad - single und ohne
breitengradabhängiges Verzerrungsrisiko."""


def _build_lat_bands(
    stations: list[ChargingStation], band_km: float = _LAT_BAND_KM
) -> dict[int, list[ChargingStation]]:
    """Bucketiert Stationen nach latitude-Band für schnelle Radius-Suchen.

    Ersetzt den linearen Voll-Scan über ALLE Stationen in
    `get_stations_in_radius()`: `get_stations_along_route()` ruft diese pro
    Roh-Segment auf (bei feingranularen Routen, z. B. ein Segment pro
    GraphHopper-Polyline-Punktpaar, oft tausende Aufrufe) - ohne Index ein
    O(Segmente x Stationen)-Kostenfaktor (siehe docs/plans/07-optimization.md).
    """
    band_deg = band_km / _KM_PER_LAT_DEG
    bands: dict[int, list[ChargingStation]] = {}
    for station in stations:
        lat, _lon = station.coordinate
        bands.setdefault(int(lat // band_deg), []).append(station)
    return bands


def _stations_in_radius(
    lat_bands: dict[int, list[ChargingStation]],
    coordinate: Coordinate,
    radius_km: float,
    band_km: float = _LAT_BAND_KM,
) -> list[ChargingStation]:
    """Liefert alle Stationen aus `lat_bands` innerhalb `radius_km` um `coordinate`.

    Exakt äquivalent zu einem Voll-Scan mit `haversine_distance_m` + Filter
    (siehe `_build_lat_bands`), prüft aber nur Stationen aus den
    latitude-Bändern, die `coordinate` innerhalb `radius_km` überhaupt
    erreichen können - kein Genauigkeitsverlust, nur less Kandidaten.
    Ergebnis unsortiert und ohne Länderfilter (Aufrufer wendet beides bei
    Bedarf selbst an, wie beim bisherigen Voll-Scan).
    """
    lat, _lon = coordinate
    band_deg = band_km / _KM_PER_LAT_DEG
    center_band = int(lat // band_deg)
    band_span = max(1, ceil(radius_km / band_km))

    result: list[ChargingStation] = []
    for band in range(center_band - band_span, center_band + band_span + 1):
        for station in lat_bands.get(band, ()):
            if haversine_distance_m(coordinate, station.coordinate) / 1000.0 <= radius_km:
                result.append(station)
    return result
