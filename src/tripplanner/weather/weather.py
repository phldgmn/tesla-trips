"""Public weather query functions.

Implementiert:
- ``fetch_weather_for_route``: Abruf von Wetterdaten entlang einer Route mit Batching
- ``fetch_weather_by_detail``: Detail-level-aware weather fetching with nearest-neighbor fan-out
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from tripplanner.geo import Coordinate
from tripplanner.routing.models import RouteSegment
from tripplanner.weather.models import (
    WeatherDetailLevel,
    WeatherQuery,
    WeatherSample,
)
from tripplanner.weather.providers import WeatherProvider

if TYPE_CHECKING:
    pass

# Target spacing (metres) between representative weather sample points along
# the route. Deliberately distance-based, not segment-index-based: a
# `RouteSegment` is one raw routing-provider polyline edge, and its length
# varies wildly with route geometry (a few metres on tight urban curves, up
# to kilometres on straight motorway stretches). Sampling every N-th
# *segment* therefore samples every N-th *point on the polyline*, so a long,
# curvy route with a dense polyline produced far more queries than a short,
# straight one covering the same distance -- on multi-hundred-km trips this
# ballooned into thousands of queries for "medium" detail and triggered
# weather-provider 429s. Spacing by along-route distance keeps the query
# count proportional to trip length regardless of polyline density, and
# lets "low" (previously a single point for the whole trip, see the
# 2026-08-29 bug report) vary sensibly across long trips while staying cheap.
LOW_DETAIL_SAMPLE_SPACING_M: float = 200_000.0
MEDIUM_DETAIL_SAMPLE_SPACING_M: float = 40_000.0


def _cumulative_midpoint_distances_m(
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
) -> list[float]:
    """Computes the along-route distance to each segment's midpoint.

    Args:
        segment_eta_list: Pairs of ``(segment, elapsed_timedelta)`` in route
            order.

    Returns:
        A list of length ``len(segment_eta_list)`` where entry *i* is the
        cumulative route distance (metres) to the midpoint of segment *i*,
        computed from ``RouteSegment.laenge_m``.
    """
    distances: list[float] = []
    travelled = 0.0
    for segment, _ in segment_eta_list:
        distances.append(travelled + segment.laenge_m / 2)
        travelled += segment.laenge_m
    return distances


def _sample_indices_by_spacing(
    distances_m: Sequence[float],
    spacing_m: float,
) -> list[int]:
    """Picks representative segment indices spaced roughly *spacing_m* apart.

    Args:
        distances_m: Along-route distance to each segment's midpoint, in
            ascending order (see `_cumulative_midpoint_distances_m`).
        spacing_m: Target distance between consecutive sampled points.

    Returns:
        Sorted, deduplicated segment indices. The first and last segment are
        always included regardless of spacing, so the sampled range always
        spans the full route.
    """
    count = len(distances_m)
    if count == 0:
        return []

    sampled = {0, count - 1}
    next_threshold = distances_m[0] + spacing_m
    for idx, distance in enumerate(distances_m):
        if distance >= next_threshold:
            sampled.add(idx)
            next_threshold = distance + spacing_m
    return sorted(sampled)


def _nearest_sample_assignment(
    distances_m: Sequence[float],
    sampled_indices: Sequence[int],
) -> list[int]:
    """Returns the along-route-nearest sampled index for each segment.

    For every segment *i* the function returns the element of
    *sampled_indices* whose midpoint distance is closest to segment *i*'s
    midpoint distance. Ties are broken by choosing the **lower** index.

    Args:
        distances_m: Along-route distance to each segment's midpoint (see
            `_cumulative_midpoint_distances_m`); its length is the segment
            count.
        sampled_indices: Segment indices that were actually queried.

    Returns:
        A list of length ``len(distances_m)`` where ``result[i]`` is the
        sampled index assigned to segment *i*.

    Examples:
        >>> _nearest_sample_assignment([0.0, 1.0, 2.0, 3.0, 4.0], [0, 4])
        [0, 0, 0, 4, 4]
        >>> _nearest_sample_assignment([0.0], [0])
        [0]
    """
    if not sampled_indices:
        return list(range(len(distances_m)))

    result: list[int] = []
    for distance in distances_m:
        best = min(
            sampled_indices,
            key=lambda s: (abs(distances_m[s] - distance), s),
        )
        result.append(best)
    return result


async def fetch_weather_by_detail(
    provider: WeatherProvider,
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
        return await _fetch_high(provider, segment_eta_list, abfahrtszeit)
    elif detail == "low":
        return await _fetch_low(provider, segment_eta_list, abfahrtszeit)
    elif detail == "medium":
        return await _fetch_medium(provider, segment_eta_list, abfahrtszeit)
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


async def _fetch_sampled(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
    spacing_m: float,
) -> list[WeatherSample]:
    """Shared "low"/"medium" implementation: distance-spaced sampling with fan-out.

    Queries one representative point roughly every *spacing_m* metres along
    the route (always including the first and last segment), then assigns
    every other segment the along-route-nearest sampled point's weather.

    Args:
        provider: Weather provider to use for fetching data.
        segment_eta_list: Pairs of ``(segment, elapsed_timedelta)`` in route
            order.
        abfahrtszeit: Departure time of the trip.
        spacing_m: Target distance between consecutive sampled points.

    Returns:
        A list of length ``len(segment_eta_list)`` with one fanned-out
        `WeatherSample` per segment, each carrying that segment's own
        coordinate and timestamp.

    Raises:
        RuntimeError: If the provider returns a different number of samples
            than queries (a provider dropped an entry).
    """
    distances = _cumulative_midpoint_distances_m(segment_eta_list)
    sampled_indices = _sample_indices_by_spacing(distances, spacing_m)

    queries: list[WeatherQuery] = []
    for idx in sampled_indices:
        segment, _ = segment_eta_list[idx]
        geo = segment.geometrie
        coord = geo[len(geo) // 2]
        seg_time = _compute_segment_time(idx, abfahrtszeit, segment_eta_list)
        queries.append(WeatherQuery(koordinate=coord, zeitpunkt=seg_time))

    samples = await provider.fetch_weather(queries)

    if len(samples) != len(queries):
        raise RuntimeError(
            f"Expected {len(queries)} samples from provider for sampled-detail queries, "
            f"got {len(samples)} — provider dropped an entry"
        )

    # Key samples by query coordinate+time for robust lookup
    query_to_sample: dict[tuple[Coordinate, datetime], WeatherSample] = {}
    for query, sample in zip(queries, samples, strict=False):
        query_to_sample[(query.koordinate, query.zeitpunkt)] = sample

    assignment = _nearest_sample_assignment(distances, sampled_indices)

    result: list[WeatherSample] = []
    for seg_idx in range(len(segment_eta_list)):
        sampled_seg = assignment[seg_idx]
        sampled_segment, _ = segment_eta_list[sampled_seg]
        geo = sampled_segment.geometrie
        src_coord = geo[len(geo) // 2]
        src_time = _compute_segment_time(sampled_seg, abfahrtszeit, segment_eta_list)
        src_sample = query_to_sample[(src_coord, src_time)]
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


async def _fetch_low(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[WeatherSample]:
    """Low-detail: samples every ``LOW_DETAIL_SAMPLE_SPACING_M`` metres along the route."""
    return await _fetch_sampled(
        provider, segment_eta_list, abfahrtszeit, LOW_DETAIL_SAMPLE_SPACING_M
    )


async def _fetch_medium(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    abfahrtszeit: datetime,
) -> list[WeatherSample]:
    """Medium-detail: samples every ``MEDIUM_DETAIL_SAMPLE_SPACING_M`` metres along the route."""
    return await _fetch_sampled(
        provider, segment_eta_list, abfahrtszeit, MEDIUM_DETAIL_SAMPLE_SPACING_M
    )


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
