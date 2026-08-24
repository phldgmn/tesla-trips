"""Public weather query functions.

Implementiert:
- ``fetch_weather_for_route``: Abruf von Wetterdaten entlang einer Route mit Batching
- ``fetch_weather_by_detail``: Detail-level-aware weather fetching with nearest-neighbor fan-out
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from tripplanner.routing.models import RouteSegment
from tripplanner.weather.models import (
    WeatherDetailLevel,
    WeatherQuery,
    WeatherSample,
)
from tripplanner.weather.providers import WeatherProvider

if TYPE_CHECKING:
    from tripplanner.routing.models import Route

# Every N-th segment is sampled for medium detail (first and last always included).
MEDIUM_DETAIL_SEGMENT_STRIDE: int = 5


def _nearest_sample_assignment(
    segment_count: int,
    sampled_indices: Sequence[int],
) -> list[int]:
    """Return the nearest sampled index for each segment index.

    For every segment *i* in ``range(segment_count)`` the function returns the
    element of *sampled_indices* that is closest to *i* in absolute index
    distance.  Ties are broken by choosing the **lower** index.

    Args:
        segment_count: Total number of segments in the route.
        sampled_indices: Sorted list of segment indices that were actually
            queried.

    Returns:
        A list of length *segment_count* where ``result[i]`` is the
        sampled index assigned to segment *i*.

    Examples:
        >>> _nearest_sample_assignment(12, [0, 5, 10, 11])
        [0, 0, 0, 0, 5, 5, 5, 5, 10, 10, 10, 11]
        >>> _nearest_sample_assignment(5, [0, 4])
        [0, 0, 0, 0, 4]
        >>> _nearest_sample_assignment(1, [0])
        [0]
    """
    if not sampled_indices:
        return list(range(segment_count))

    sampled = list(sampled_indices)
    result: list[int] = []

    for i in range(segment_count):
        best = sampled[0]
        best_dist = abs(i - best)
        for s in sampled[1:]:
            d = abs(i - s)
            if d < best_dist:
                best = s
                best_dist = d
        result.append(best)

    return result


async def fetch_weather_by_detail(
    provider: WeatherProvider,
    route: Route,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
    detail: WeatherDetailLevel,
) -> list[WeatherSample]:
    """Fetch weather samples according to the requested detail level.

    Returns exactly one ``WeatherSample`` per entry in *segment_eta_list* in the
    same order.  The function never returns more or fewer samples than there are
    segments.

    Args:
        provider: Weather provider to use for fetching data.
        route: The computed route (provides segment geometries).
        segment_eta_list: Pairs of ``(segment, elapsed_timedelta)`` where the
            timedelta is the accumulated ETA since *abfahrtszeit* at the start
            of that segment.
        abfahrtszeit: Departure time of the trip (naive or timezone-aware
            ``datetime``).
        detail: Target granularity level.  ``"off"`` is **not** handled here —
            callers must guard against it before invoking this function.

    Returns:
        A list of ``WeatherSample`` objects, one per segment in *segment_eta_list*.

    Raises:
        ValueError: If *detail* is ``"off"`` (caller responsibility).
    """
    segment_count = len(segment_eta_list)
    if segment_count == 0:
        return []

    if detail == "off":
        raise ValueError(
            'fetch_weather_by_detail does not handle detail="off"; '
            "callers must guard before invoking this function."
        )

    if detail == "high":
        return await _fetch_high(provider, route, segment_eta_list, abfahrtszeit)
    elif detail == "low":
        return await _fetch_low(provider, route, segment_eta_list, abfahrtszeit)
    elif detail == "medium":
        return await _fetch_medium(provider, route, segment_eta_list, abfahrtszeit)
    else:
        raise ValueError(f"Unknown detail level: {detail!r}")


def _compute_segment_time(
    seg_idx: int,
    abfahrtszeit: datetime,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
) -> datetime:
    """Compute the ETA timestamp for the given segment index.

    The timestamp is ``abfahrtszeit + sum(durations of all preceding segments)``.
    """
    elapsed = timedelta(0)
    for i in range(seg_idx):
        _, duration = segment_eta_list[i]
        elapsed += duration
    return abfahrtszeit + elapsed


async def _fetch_high(
    provider: WeatherProvider,
    route: Route,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[WeatherSample]:
    """High-detail: one query per segment, identical to today's per-segment loop."""
    queries: list[WeatherQuery] = []
    current_time = abfahrtszeit

    for segment, duration in segment_eta_list:
        geo = segment.geometrie
        koordinate = geo[len(geo) // 2]
        queries.append(WeatherQuery(koordinate=koordinate, zeitpunkt=current_time))
        current_time += duration

    return await provider.fetch_weather(queries)


async def _fetch_low(
    provider: WeatherProvider,
    route: Route,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[WeatherSample]:
    """Low-detail: one representative query at the trip midpoint, broadcast to all.

    The representative query uses:
    - Coordinate: midpoint of the middle segment.
    - Timestamp: ``abfahrtszeit + total_trip_duration / 2`` (mid-trip time).
    """
    segment_count = len(segment_eta_list)
    mid_idx = segment_count // 2
    mid_segment, _ = segment_eta_list[mid_idx]

    total_duration = sum((d for _, d in segment_eta_list), timedelta(0))
    mid_time = abfahrtszeit + total_duration / 2

    geo = mid_segment.geometrie
    mid_coord = geo[len(geo) // 2]

    query = WeatherQuery(koordinate=mid_coord, zeitpunkt=mid_time)
    samples = await provider.fetch_weather([query])

    if not samples:
        return []

    original = samples[0]
    result: list[WeatherSample] = []

    for seg_idx, (segment, _) in enumerate(segment_eta_list):
        seg_geo = segment.geometrie
        seg_coord = seg_geo[len(seg_geo) // 2]
        seg_time = _compute_segment_time(seg_idx, abfahrtszeit, segment_eta_list)
        result.append(
            original.model_copy(
                update={"koordinate": seg_coord, "zeitpunkt": seg_time},
            )
        )
    return result


async def _fetch_medium(
    provider: WeatherProvider,
    route: Route,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[WeatherSample]:
    """Medium-detail: sample every N-th segment, fan out via nearest-neighbor.

    First and last segments are always included regardless of stride alignment.
    """
    segment_count = len(segment_eta_list)
    n = MEDIUM_DETAIL_SEGMENT_STRIDE

    sampled_set: set[int] = {0, segment_count - 1}
    for i in range(0, segment_count, n):
        sampled_set.add(i)
    sampled_indices = sorted(sampled_set)

    queries: list[WeatherQuery] = []
    for idx in sampled_indices:
        segment, _ = segment_eta_list[idx]
        geo = segment.geometrie
        coord = geo[len(geo) // 2]
        seg_time = _compute_segment_time(idx, abfahrtszeit, segment_eta_list)
        queries.append(WeatherQuery(koordinate=coord, zeitpunkt=seg_time))

    samples = await provider.fetch_weather(queries)

    assignment = _nearest_sample_assignment(segment_count, sampled_indices)

    # Map sampled segment index -> position in the queries/samples list
    sampled_pos: dict[int, int] = {s: i for i, s in enumerate(sampled_indices)}

    result: list[WeatherSample] = []
    for seg_idx in range(segment_count):
        sampled_seg = assignment[seg_idx]
        src_sample = samples[sampled_pos[sampled_seg]]
        segment, _ = segment_eta_list[seg_idx]
        seg_geo = segment.geometrie
        seg_coord = seg_geo[len(seg_geo) // 2]
        seg_time = _compute_segment_time(seg_idx, abfahrtszeit, segment_eta_list)
        result.append(
            src_sample.model_copy(
                update={"koordinate": seg_coord, "zeitpunkt": seg_time},
            )
        )
    return result


async def fetch_weather_for_route(
    provider: WeatherProvider,
    route_queries: Sequence[WeatherQuery],
    batch_size: int = 20,
) -> list[WeatherSample]:
    """Abruf von Wetterdaten entlang einer Route mit automatischem Batching.

    Args:
        provider: Der zu verwendende Wetterprovider (in Tests: Fake).
        route_queries: Liste von Abfragen (Koordinate + ETA).
        batch_size: Maximale Anzahl Abfragen pro API-Call (Open-Meteo empfiehlt <=50).

    Returns:
        Liste von WeatherSample in gleicher Reihenfolge wie route_queries.
    """
    if not route_queries:
        return []

    results: list[WeatherSample | None] = [None] * len(route_queries)
    remaining = list(route_queries)

    while remaining:
        batch = remaining[:batch_size]
        remaining = remaining[batch_size:]

        samples = await provider.fetch_weather(batch)
        for sample in samples:
            for idx, query in enumerate(route_queries):
                if query.koordinate == sample.koordinate and query.zeitpunkt == sample.zeitpunkt:
                    if results[idx] is None:
                        results[idx] = sample
                    break

    return [r for r in results if r is not None]
