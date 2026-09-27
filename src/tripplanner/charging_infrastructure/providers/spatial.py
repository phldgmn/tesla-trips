"""Spatial index helper functions for the charging station provider."""

from __future__ import annotations

from math import ceil

from tripplanner.geo import Coordinate, haversine_distance_m

from ..models import ChargingStation

_LAT_BAND_KM = 5.0
"""Latitude of the latitude bands for the spatial station index (see
`_build_lat_bands`/`_stations_in_radius`). small genug, um bei den in der
practical search radii (1-15 km, see `get_stations_along_route`)
the number of candidates to check per query significantly - regardless
vom tatsaechlichen `radius_km` einer konkreten query korrekt, da
`_stations_in_radius` the number of bands to scan to match
`radius_km` berechnet."""

_KM_PER_LAT_DEG = 111.0
"""Approximation: 1 latitude ≈ 111 km (nearly constant globally - unlike 1
longitude, which shrinks with `cos(lat)`. Hence bucketing ONLY by
latitude, nicht 2D nach Breiten-/Laengengrad - single und ohne
latitude-dependent distortion risk."""


def _build_lat_bands(
    stations: list[ChargingStation], band_km: float = _LAT_BAND_KM
) -> dict[int, list[ChargingStation]]:
    """Buckets stations by latitude band for fast radius searches.

    Replaces the linear full-scan over ALL stations in
    `get_stations_in_radius()`: `get_stations_along_route()` calls this per
    raw segment (for fine-granular routes, e.g. one segment per
    GraphHopper polyline point pair, often thousands of calls) - without index a
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
