"""Raeumliche Index-Hilfsfunktionen fuer den Ladedraht-provider."""

from __future__ import annotations

from math import ceil

from tripplanner.geo import Coordinate, haversine_distance_m

from ..models import ChargingStation

_LAT_BAND_KM = 5.0
"""Latitude of the latitude bands for the spatial station index (see
`_build_lat_bands`/`_stations_in_radius`). small genug, um bei den in der
Praxis usesen Suchradien (1-15 km, siehe `get_stations_along_route`)
the number of candidates to check per query significantly - regardless
vom tatsaechlichen `radius_km` einer konkreten query korrekt, da
`_stations_in_radius` die Anzahl der zu scannenden Baender passend zu
`radius_km` berechnet."""

_KM_PER_LAT_DEG = 111.0
"""Naeherung: 1 latitude ≈ 111 km (global nahezu konstant - anders als 1
Laengengrad, der mit `cos(lat)` schrumpft). Deshalb Bucketing NUR nach
latitude, nicht 2D nach Breiten-/Laengengrad - single und ohne
breitengradabhaengiges Verzerrungsrisiko."""


def _build_lat_bands(
    stations: list[ChargingStation], band_km: float = _LAT_BAND_KM
) -> dict[int, list[ChargingStation]]:
    """Buckets stations by latitude band for fast radius searches.

    Replaces the linear full-scan over ALL stations in
    `get_stations_in_radius()`: `get_stations_along_route()` ruft diese pro
    Roh-segment auf (bei feingranularen Routen, z. B. ein segment pro
    GraphHopper-Polyline-Punktpaar, oft tausende Aufrufe) - ohne Index ein
    O(segmente x Stationen)-Kostenfaktor (siehe docs/plans/07-optimization.md).
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

    Exakt aequivalent zu einem Voll-Scan mit `haversine_distance_m` + Filter
    (see ``_build_lat_bands``), but checks only stations from the
    latitude bands that ``coordinate`` within ``radius_km`` can
    reach - no loss of accuracy, just fewer candidates.
    Ergebnis unsortiert und ohne Laenderfilter (caller wendet beides bei
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
