"""Caching helpers for weather providers."""

import json
from datetime import datetime

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherSample


def _cache_key(
    coordinate: Coordinate,
    zeitpunkt: datetime,
) -> tuple[Coordinate, datetime]:
    """Builds a cache key with grid-rounded coordinates and hour-snapped timestamp.

    The 0.1° grid rounding collapses near-duplicate segment-midpoint coordinates
    into a single cache entry; the hour snap aligns cache lookups across the
    iterative convergence loop's up-to-3 re-runs (Open-Meteo returns hourly data,
    so the timestamp granularity is already coarser than the original query).

    The returned ``WeatherSample`` still carries the *original* coordinate and
    timestamp — only the cache lookup/storage key is snapped.
    """
    rounded_coord = (round(coordinate[0], 1), round(coordinate[1], 1))
    snapped_time = zeitpunkt.replace(minute=0, second=0, microsecond=0)
    return (rounded_coord, snapped_time)


def _cache_str_key(
    coordinate: Coordinate,
    zeitpunkt: datetime,
) -> str:
    """Serialises a ``(Coordinate, datetime)`` cache key to a JSON string.

    The string representation is used by the persistent ``TTLCache`` layer;
    the in-memory ``_cache`` dict still uses the native
    ``tuple[Coordinate, datetime]`` as its key to avoid repeated
    serialisation round-trips on hot in-process lookups.
    """
    rounded = (round(coordinate[0], 1), round(coordinate[1], 1))
    snapped = zeitpunkt.replace(minute=0, second=0, microsecond=0)
    return json.dumps((rounded, snapped.isoformat(timespec="minutes")))


def _cache_deserialize(data: object) -> WeatherSample:
    """Reconstructs a ``WeatherSample`` from a JSON-serialised dict."""
    return WeatherSample.model_validate(data)
