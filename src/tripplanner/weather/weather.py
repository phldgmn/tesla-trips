"""Public weather query functions.

Implementiert:
- ``fetch_weather_for_route``: Abruf von Wetterdaten entlang einer Route mit Batching
- ``fetch_weather_by_detail``: Detail-level-aware weather fetching with nearest-neighbor fan-out
"""

from __future__ import annotations

import bisect
import math
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
    pass

# Target spacing (metres) between representative weather sample points along
# the route. Deliberately distance-based, not segment-index-based: a
# `RouteSegment` is one raw routing-provider polyline edge, and its length
# varies wildly with route geometry (a few metres on tight urban curves, up
# to kilometres on straight motorway stretches). Sampling every N-th
# *segment* therefore samples every N-th *point on the polyline*, so a long,
# curvy route with a dense polyline produced far more queries than a short,
# straight one covering the same distance -- on multi-hundred-km trips this
# ballooned into thousands of queries (even for "high", which used to query
# EVERY segment) and triggered weather-provider 429s. Spacing by along-route
# distance keeps the query count proportional to trip length regardless of
# polyline density, and lets "low"/"medium" (previously a single point for
# the whole trip, or a segment-index stride, see the 2026-08-29 bug report)
# vary sensibly across long trips while staying cheap. All three active
# levels (`\"high\"`/`\"medium\"`/`\"low\"`) share the same one-shot,
# never-refetching mechanism (`_fetch_sampled`) and only differ in spacing.
HIGH_DETAIL_SAMPLE_SPACING_M: float = 60_000.0
MEDIUM_DETAIL_SAMPLE_SPACING_M: float = 120_000.0
LOW_DETAIL_SAMPLE_SPACING_M: float = 200_000.0

LONG_STOP_THRESHOLD: timedelta = timedelta(hours=4)
"""Duration above which a segment's `segment_eta_list` time is treated as a
"long stop" for `\"low\"`/`\"medium\"`/`\"high\"` detail. That duration
covers both a segment's own travel time and any charging/waypoint-wait time
incurred while there (see `_step_9_update_eta` in `trip_input.pipeline`), so
a duration past this threshold means the vehicle sat still for a while (e.g.
an overnight wait at a mandatory `Waypoint`) -- long enough that conditions
may have genuinely changed. `_long_stop_boundary_indices` forces a fresh
sample right before and right after such a stop instead of interpolating
stale pre-stop conditions across the whole stationary period."""


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


def _long_stop_boundary_indices(
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    threshold: timedelta,
) -> set[int]:
    """Finds segment indices that must be freshly sampled around a long stop.

    A segment's duration in *segment_eta_list* covers both its own travel
    time and any charging/waypoint-wait time incurred while there (see
    `_step_9_update_eta` in `trip_input.pipeline`), so a duration exceeding
    *threshold* marks a long stationary stop. Both the segment at which the
    stop happens (its own ETA timestamp is unaffected by the wait, i.e.
    pre-stop/arrival conditions) and the immediately following segment
    (whose ETA timestamp already includes the full wait via
    `_compute_segment_time`, i.e. post-stop/departure conditions) are
    returned, so the wait becomes a hard interpolation boundary instead of
    being smoothed over by whatever samples happen to bracket it by
    distance.

    Args:
        segment_eta_list: Pairs of ``(segment, elapsed_timedelta)`` where the
            timedelta is time spent at/traversing that segment.
        threshold: Minimum duration to classify a segment as a long stop.

    Returns:
        Segment indices to force into the sampled set.
    """
    last_index = len(segment_eta_list) - 1
    indices: set[int] = set()
    for idx, (_, duration) in enumerate(segment_eta_list):
        if duration > threshold:
            indices.add(idx)
            if idx < last_index:
                indices.add(idx + 1)
    return indices


def _interpolation_brackets(
    distances_m: Sequence[float],
    sampled_indices: Sequence[int],
) -> list[tuple[int, int, float]]:
    """Finds the two sampled indices to interpolate each segment between.

    Args:
        distances_m: Along-route distance to each segment's midpoint (see
            `_cumulative_midpoint_distances_m`); its length is the segment
            count.
        sampled_indices: Segment indices that were actually queried.

    Returns:
        A list of length ``len(distances_m)``; entry *i* is
        ``(left, right, t)`` where *left*/*right* are sampled indices
        bracketing segment *i* by along-route distance and *t* in ``[0, 1]``
        is segment *i*'s fractional position between them (``0`` at *left*,
        ``1`` at *right*). Segments outside the sampled range, or exactly at
        a sampled point, clamp to the nearest edge sample (``left == right``,
        ``t = 0``).

    Examples:
        >>> _interpolation_brackets([0.0, 5.0, 10.0], [0, 2])
        [(0, 2, 0.0), (0, 2, 0.5), (2, 2, 0.0)]
        >>> _interpolation_brackets([0.0], [0])
        [(0, 0, 0.0)]
    """
    if not sampled_indices:
        return [(i, i, 0.0) for i in range(len(distances_m))]

    sampled = sorted(sampled_indices)
    sampled_distances = [distances_m[i] for i in sampled]

    result: list[tuple[int, int, float]] = []
    for distance in distances_m:
        pos = bisect.bisect_right(sampled_distances, distance)
        if pos == 0:
            result.append((sampled[0], sampled[0], 0.0))
        elif pos == len(sampled):
            result.append((sampled[-1], sampled[-1], 0.0))
        else:
            left, right = sampled[pos - 1], sampled[pos]
            span = distances_m[right] - distances_m[left]
            t = (distance - distances_m[left]) / span if span > 0 else 0.0
            result.append((left, right, t))
    return result


def _interpolate_angle_deg(a_deg: float, b_deg: float, t: float) -> float:
    """Interpolates a circular angle (e.g. wind direction) between two values.

    Plain linear interpolation breaks at the 0/360 wraparound (350
    interpolated toward 10 would cross 180 the long way instead of 0);
    averaging the (cos, sin) unit vectors instead always takes the short way
    around.

    Args:
        a_deg: Angle at ``t=0``, degrees.
        b_deg: Angle at ``t=1``, degrees.
        t: Interpolation fraction in ``[0, 1]``.

    Returns:
        The interpolated angle in ``[0, 360]`` degrees (floating-point
        rounding right at the wraparound boundary can yield exactly ``360.0``
        instead of ``0.0`` -- callers comparing against ``0`` should treat
        the two as equivalent). For near-exactly-opposite angles (e.g. 0 and
        180 at ``t=0.5``) the direction is inherently undefined and the
        result is sensitive to floating-point noise in `math.sin`/`math.cos`;
        it never raises, but the exact value returned is not guaranteed.
    """
    a_rad, b_rad = math.radians(a_deg), math.radians(b_deg)
    x = (1.0 - t) * math.cos(a_rad) + t * math.cos(b_rad)
    y = (1.0 - t) * math.sin(a_rad) + t * math.sin(b_rad)
    if x == 0.0 and y == 0.0:
        return a_deg % 360.0
    return math.degrees(math.atan2(y, x)) % 360.0


def _interpolate_samples(left: WeatherSample, right: WeatherSample, t: float) -> WeatherSample:
    """Linearly interpolates two `WeatherSample`s (circularly for wind direction).

    Args:
        left: Sample at ``t=0``.
        right: Sample at ``t=1``.
        t: Interpolation fraction; clamped to ``left``/``right`` verbatim at
            the ``t <= 0``/``t >= 1`` edges.

    Returns:
        A new `WeatherSample` with every numeric field interpolated.
        ``coordinate``/``zeitpunkt`` are copied from *left* and are expected
        to be overwritten by the caller with the target segment's own
        values.
    """
    if left is right or t <= 0.0:
        return left
    if t >= 1.0:
        return right
    return left.model_copy(
        update={
            "temperatur_c": left.temperatur_c + (right.temperatur_c - left.temperatur_c) * t,
            "windgeschwindigkeit_ms": left.windgeschwindigkeit_ms
            + (right.windgeschwindigkeit_ms - left.windgeschwindigkeit_ms) * t,
            "windrichtung_deg": _interpolate_angle_deg(
                left.windrichtung_deg, right.windrichtung_deg, t
            ),
            "niederschlag_mm": left.niederschlag_mm
            + (right.niederschlag_mm - left.niederschlag_mm) * t,
            "schneefall_cm": left.schneefall_cm + (right.schneefall_cm - left.schneefall_cm) * t,
            "luftdruck_hpa": left.luftdruck_hpa + (right.luftdruck_hpa - left.luftdruck_hpa) * t,
            "luftfeuchtigkeit_pct": left.luftfeuchtigkeit_pct
            + (right.luftfeuchtigkeit_pct - left.luftfeuchtigkeit_pct) * t,
            "globalstrahlung_wm2": left.globalstrahlung_wm2
            + (right.globalstrahlung_wm2 - left.globalstrahlung_wm2) * t,
            "bewoelkung_pct": left.bewoelkung_pct
            + (right.bewoelkung_pct - left.bewoelkung_pct) * t,
        }
    )


async def fetch_weather_by_detail(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    departure_time: datetime,
    detail: WeatherDetailLevel,
) -> list[WeatherSample]:
    """Fetch weather samples according to the requested detail level.

    Returns exactly one ``WeatherSample`` per entry in *segment_eta_list* in the
    same order.  The function never returns more or fewer samples than there are
    segments.

    Args:
        provider: Weather provider to use for fetching data.
        segment_eta_list: Pairs of ``(segment, elapsed_timedelta)`` where the
            timedelta is the accumulated ETA since *departure_time* at the start
            of that segment.
        departure_time: Departure time of the trip (naive or timezone-aware
            ``datetime``).
        detail: Target granularity level (``low``, ``medium``, or
            ``high``, differing only in sample spacing -- see
            `HIGH_DETAIL_SAMPLE_SPACING_M`/`MEDIUM_DETAIL_SAMPLE_SPACING_M`/
            `LOW_DETAIL_SAMPLE_SPACING_M`). ``off`` is **not** handled
            here — callers must guard against it before invoking this
            function.

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
        return await _fetch_high(provider, segment_eta_list, departure_time)
    elif detail == "low":
        return await _fetch_low(provider, segment_eta_list, departure_time)
    elif detail == "medium":
        return await _fetch_medium(provider, segment_eta_list, departure_time)
    else:
        raise ValueError(f"Unknown detail level: {detail!r}")


def _compute_segment_time(
    seg_idx: int,
    departure_time: datetime,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
) -> datetime:
    """Compute the ETA timestamp for the given segment index.

    The timestamp is ``departure_time + sum(durations of all preceding segments)``.
    """
    elapsed = timedelta(0)
    for i in range(seg_idx):
        _, duration = segment_eta_list[i]
        elapsed += duration
    return departure_time + elapsed


async def _fetch_high(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    departure_time: datetime,
) -> list[WeatherSample]:
    """High-detail: samples every ``HIGH_DETAIL_SAMPLE_SPACING_M`` metres along the route."""
    return await _fetch_sampled(
        provider, segment_eta_list, departure_time, HIGH_DETAIL_SAMPLE_SPACING_M
    )


async def _fetch_sampled(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    departure_time: datetime,
    spacing_m: float,
) -> list[WeatherSample]:
    """Shared "low"/"medium"/"high" implementation: spaced sampling with interpolation.

    Queries one representative point roughly every *spacing_m* metres along
    the route (always including the first and last segment, plus a fresh
    arrival/departure pair around any stop longer than `LONG_STOP_THRESHOLD`,
    see `_long_stop_boundary_indices`). Every other segment's weather is
    then linearly interpolated between the two along-route-nearest sampled
    points (circularly for wind direction, see `_interpolate_samples`)
    rather than copied from whichever one is nearest, so weather varies
    smoothly along the route instead of jumping at each sample boundary.

    Args:
        provider: Weather provider to use for fetching data.
        segment_eta_list: Pairs of ``(segment, elapsed_timedelta)`` in route
            order.
        departure_time: Departure time of the trip.
        spacing_m: Target distance between consecutive sampled points.

    Returns:
        A list of length ``len(segment_eta_list)`` with one interpolated
        `WeatherSample` per segment, each carrying that segment's own
        coordinate and timestamp.

    Raises:
        RuntimeError: If the provider returns a different number of samples
            than queries (a provider dropped an entry).
    """
    distances = _cumulative_midpoint_distances_m(segment_eta_list)
    sampled_indices = sorted(
        set(_sample_indices_by_spacing(distances, spacing_m))
        | _long_stop_boundary_indices(segment_eta_list, LONG_STOP_THRESHOLD)
    )

    queries: list[WeatherQuery] = []
    for idx in sampled_indices:
        segment, _ = segment_eta_list[idx]
        geo = segment.geometrie
        coord = geo[len(geo) // 2]
        seg_time = _compute_segment_time(idx, departure_time, segment_eta_list)
        queries.append(WeatherQuery(coordinate=coord, zeitpunkt=seg_time))

    samples = await provider.fetch_weather(queries)

    if len(samples) != len(queries):
        raise RuntimeError(
            f"Expected {len(queries)} samples from provider for sampled-detail queries, "
            f"got {len(samples)} — provider dropped an entry"
        )

    sample_by_index = dict(zip(sampled_indices, samples, strict=True))
    brackets = _interpolation_brackets(distances, sampled_indices)

    result: list[WeatherSample] = []
    for seg_idx in range(len(segment_eta_list)):
        left_idx, right_idx, t = brackets[seg_idx]
        interpolated = _interpolate_samples(
            sample_by_index[left_idx], sample_by_index[right_idx], t
        )
        segment, _ = segment_eta_list[seg_idx]
        seg_geo = segment.geometrie
        seg_coord = seg_geo[len(seg_geo) // 2]
        seg_time = _compute_segment_time(seg_idx, departure_time, segment_eta_list)
        result.append(
            interpolated.model_copy(
                update={"coordinate": seg_coord, "zeitpunkt": seg_time},
            )
        )
    return result


async def _fetch_low(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    departure_time: datetime,
) -> list[WeatherSample]:
    """Low-detail: samples every ``LOW_DETAIL_SAMPLE_SPACING_M`` metres along the route."""
    return await _fetch_sampled(
        provider, segment_eta_list, departure_time, LOW_DETAIL_SAMPLE_SPACING_M
    )


async def _fetch_medium(
    provider: WeatherProvider,
    segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
    departure_time: datetime,
) -> list[WeatherSample]:
    """Medium-detail: samples every ``MEDIUM_DETAIL_SAMPLE_SPACING_M`` metres along the route."""
    return await _fetch_sampled(
        provider, segment_eta_list, departure_time, MEDIUM_DETAIL_SAMPLE_SPACING_M
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
                if query.coordinate == sample.coordinate and query.zeitpunkt == sample.zeitpunkt:
                    if results[idx] is None:
                        results[idx] = sample
                    break

    return [r for r in results if r is not None]
