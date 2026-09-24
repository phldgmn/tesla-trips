"""Shared helpers for weather provider modules."""

from collections.abc import Sequence
from datetime import datetime

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery


def _group_queries_by_coordinate(
    queries: Sequence[WeatherQuery],
) -> dict[Coordinate, list[tuple[int, WeatherQuery]]]:
    """Groups `queries` by grid-rounded coordinate, preserving each query's index.

    Coordinates are rounded to the 0.1° grid before grouping so that near-duplicate
    segment-midpoint coordinates collapse into a single group — reducing HTTP calls
    by 10-100x in practice. The 0.1° (~11km) is already within the Open-Meteo
    internal tolerance of 0.1°.

    Args:
        queries: Queries in caller order.

    Returns:
        A dict from rounded coordinate to `(original_index, query)` pairs, used by
        every provider below to issue one HTTP request per unique location
        instead of one per `(coordinate, time)` pair.
    """
    groups: dict[Coordinate, list[tuple[int, WeatherQuery]]] = {}
    for idx, query in enumerate(queries):
        rounded = (round(query.coordinate[0], 1), round(query.coordinate[1], 1))
        groups.setdefault(rounded, []).append((idx, query))
    return groups


def _snap_to_hour_z(timestamp: datetime) -> str:
    """Formats `timestamp` snapped to the hour as `YYYY-MM-DDTHH:MM:SSZ`.

    Matches the on-the-hour, UTC, `Z`-suffixed timestamp format used by MET
    Norway, SMHI, and DMI. `timestamp` is treated as naive-UTC, the same
    project-wide convention `_extract_sample_from_response` above relies on.
    """
    return timestamp.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamps `value` to `[lo, hi]` (defensive against out-of-range upstream data)."""
    return max(lo, min(hi, value))
