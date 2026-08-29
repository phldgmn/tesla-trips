"""Tests for ``fetch_weather_by_detail`` and its distance-spaced sampling/interpolation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tripplanner.geo import Coordinate
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.weather.providers import FakeWeatherProvider
from tripplanner.weather.weather import (
    HIGH_DETAIL_SAMPLE_SPACING_M,
    LONG_STOP_THRESHOLD,
    LOW_DETAIL_SAMPLE_SPACING_M,
    MEDIUM_DETAIL_SAMPLE_SPACING_M,
    _compute_segment_time,
    _cumulative_midpoint_distances_m,
    _interpolate_angle_deg,
    _interpolate_samples,
    _interpolation_brackets,
    _long_stop_boundary_indices,
    _sample_indices_by_spacing,
    fetch_weather_by_detail,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

ABFAHRTSZEIT = datetime(2026, 8, 15, 8, 0, 0)


def _make_segment(
    index: int,
    start: Coordinate,
    end: Coordinate,
    laenge_m: float = 50_000.0,
) -> RouteSegment:
    """Create a minimal ``RouteSegment`` for testing."""
    return RouteSegment(
        segment_index=index,
        geometrie=[start, end],
        laenge_m=laenge_m,
        strassenklasse="MOTORWAY",
        bearing_deg=0.0,
    )


def _make_route(segment_count: int) -> tuple[Route, list[RouteSegment]]:
    """Create a route with *segment_count* segments along a straight line."""
    coords = [(52.0 + i * 0.1, 13.0 - i * 0.1) for i in range(segment_count + 1)]
    segments = [_make_segment(i, coords[i], coords[i + 1]) for i in range(segment_count)]
    route = Route(
        segments=segments,
        gesamtlaenge_m=sum(s.laenge_m for s in segments),
        geometrie=coords,
    )
    return route, segments


def _make_segment_eta(
    segments: list[RouteSegment],
    duration_seconds: int = 3600,
) -> list[tuple[RouteSegment, timedelta]]:
    """Pair each segment with an accumulated timedelta starting from zero."""
    acc = timedelta(0)
    return [(seg, acc) for seg in segments] or []


def _make_sample(coordinate: Coordinate, zeitpunkt: datetime, temperatur_c: float) -> WeatherSample:
    """Builds a distinguishable `WeatherSample` for interpolation/fan-out assertions."""
    return WeatherSample(
        koordinate=coordinate,
        zeitpunkt=zeitpunkt,
        temperatur_c=temperatur_c,
        windgeschwindigkeit_ms=5.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )


def _weather_sample(**overrides: object) -> WeatherSample:
    """Builds a `WeatherSample` with sane defaults, overridable per field."""
    defaults: dict[str, object] = {
        "koordinate": (52.0, 13.0),
        "zeitpunkt": ABFAHRTSZEIT,
        "temperatur_c": 10.0,
        "windgeschwindigkeit_ms": 5.0,
        "windrichtung_deg": 90.0,
        "niederschlag_mm": 1.0,
        "schneefall_cm": 0.0,
        "luftdruck_hpa": 1000.0,
        "luftfeuchtigkeit_pct": 50.0,
        "globalstrahlung_wm2": 200.0,
        "bewoelkung_pct": 30.0,
    }
    defaults.update(overrides)
    return WeatherSample(**defaults)  # type: ignore[arg-type]


def _expected_sampled_indices(
    segment_eta: list[tuple[RouteSegment, timedelta]],
    spacing_m: float,
) -> list[int]:
    """Reference computation of which segment indices a spacing level should query."""
    distances = _cumulative_midpoint_distances_m(segment_eta)
    sampled = set(_sample_indices_by_spacing(distances, spacing_m))
    sampled |= _long_stop_boundary_indices(segment_eta, LONG_STOP_THRESHOLD)
    return sorted(sampled)


# ── _cumulative_midpoint_distances_m ────────────────────────────────────────


class TestCumulativeMidpointDistances:
    """Pure unit tests for the along-route midpoint-distance helper."""

    def test_uniform_segment_lengths(self) -> None:
        """Five 50km segments: midpoints at 25, 75, 125, 175, 225 km."""
        _route, segments = _make_route(5)
        segment_eta = _make_segment_eta(segments)
        distances = _cumulative_midpoint_distances_m(segment_eta)
        assert distances == [25_000.0, 75_000.0, 125_000.0, 175_000.0, 225_000.0]

    def test_empty(self) -> None:
        assert _cumulative_midpoint_distances_m([]) == []


# ── _sample_indices_by_spacing ───────────────────────────────────────────────


class TestSampleIndicesBySpacing:
    """Pure unit tests for the distance-spaced index picker."""

    def test_always_includes_first_and_last(self) -> None:
        assert _sample_indices_by_spacing([0.0, 10.0, 20.0], spacing_m=1_000.0) == [0, 2]

    def test_spacing_below_route_length_adds_intermediate_points(self) -> None:
        # 10 points spaced 10m apart; spacing 25m -> thresholds at 25, 50, 75.
        distances = [float(i * 10) for i in range(10)]
        assert _sample_indices_by_spacing(distances, spacing_m=25.0) == [0, 3, 6, 9]

    def test_empty(self) -> None:
        assert _sample_indices_by_spacing([], spacing_m=100.0) == []

    def test_single_segment(self) -> None:
        assert _sample_indices_by_spacing([50.0], spacing_m=100.0) == [0]


# ── _long_stop_boundary_indices ──────────────────────────────────────────────


class TestLongStopBoundaryIndices:
    """Pure unit tests for the long-stop arrival/departure forcing helper."""

    def test_no_long_stop_returns_empty(self) -> None:
        _route, segments = _make_route(5)
        segment_eta = [(seg, timedelta(minutes=30)) for seg in segments]
        assert _long_stop_boundary_indices(segment_eta, LONG_STOP_THRESHOLD) == set()

    def test_long_stop_forces_arrival_and_departure_indices(self) -> None:
        _route, segments = _make_route(5)
        durations = [timedelta(hours=1)] * 5
        durations[2] = timedelta(hours=6)
        segment_eta = list(zip(segments, durations, strict=False))
        assert _long_stop_boundary_indices(segment_eta, LONG_STOP_THRESHOLD) == {2, 3}

    def test_long_stop_at_last_segment_only_forces_arrival(self) -> None:
        _route, segments = _make_route(3)
        durations = [timedelta(hours=1), timedelta(hours=1), timedelta(hours=6)]
        segment_eta = list(zip(segments, durations, strict=False))
        assert _long_stop_boundary_indices(segment_eta, LONG_STOP_THRESHOLD) == {2}

    def test_exactly_at_threshold_is_not_a_long_stop(self) -> None:
        _route, segments = _make_route(2)
        durations = [LONG_STOP_THRESHOLD, timedelta(hours=1)]
        segment_eta = list(zip(segments, durations, strict=False))
        assert _long_stop_boundary_indices(segment_eta, LONG_STOP_THRESHOLD) == set()

    def test_multiple_long_stops(self) -> None:
        _route, segments = _make_route(6)
        durations = [timedelta(hours=1)] * 6
        durations[1] = timedelta(hours=5)
        durations[4] = timedelta(hours=8)
        segment_eta = list(zip(segments, durations, strict=False))
        assert _long_stop_boundary_indices(segment_eta, LONG_STOP_THRESHOLD) == {1, 2, 4, 5}


# ── _interpolation_brackets ──────────────────────────────────────────────────


class TestInterpolationBrackets:
    """Pure unit tests for the along-route interpolation-bracket helper."""

    def test_empty_sampled_returns_identity_clamp(self) -> None:
        assert _interpolation_brackets([0.0, 1.0, 2.0], []) == [
            (0, 0, 0.0),
            (1, 1, 0.0),
            (2, 2, 0.0),
        ]

    def test_single_sampled_clamps_everything(self) -> None:
        assert _interpolation_brackets([0.0, 1.0, 2.0], [1]) == [
            (1, 1, 0.0),
            (1, 1, 0.0),
            (1, 1, 0.0),
        ]

    def test_full_coverage_resolves_to_own_value(self) -> None:
        """When every segment is sampled, each resolves to its own value.

        `bisect_right` brackets an exact match with the *next* sampled point
        rather than looping it onto itself, but `t=0` at that bracket still
        evaluates to exactly that point's own value.
        """
        distances = [0.0, 5.0, 10.0]
        sampled = [0, 1, 2]
        values = [10.0, 20.0, 30.0]
        brackets = _interpolation_brackets(distances, sampled)
        for i, (left, right, t) in enumerate(brackets):
            interpolated = values[left] + (values[right] - values[left]) * t
            assert interpolated == pytest.approx(values[i])

    def test_midpoint_between_two_samples(self) -> None:
        assert _interpolation_brackets([0.0, 5.0, 10.0], [0, 2]) == [
            (0, 2, 0.0),
            (0, 2, 0.5),
            (2, 2, 0.0),
        ]

    def test_before_first_sample_clamps_hard(self) -> None:
        brackets = _interpolation_brackets([-5.0, 0.0, 10.0], [1, 2])
        assert brackets[0] == (1, 1, 0.0)

    def test_after_last_sample_clamps_hard(self) -> None:
        brackets = _interpolation_brackets([0.0, 10.0, 20.0], [0, 1])
        assert brackets[2] == (1, 1, 0.0)

    def test_single_segment(self) -> None:
        assert _interpolation_brackets([0.0], [0]) == [(0, 0, 0.0)]


# ── _interpolate_angle_deg ───────────────────────────────────────────────────


class TestInterpolateAngleDeg:
    """Pure unit tests for the circular angle interpolation helper."""

    def test_halfway_no_wraparound(self) -> None:
        assert _interpolate_angle_deg(10.0, 30.0, 0.5) == pytest.approx(20.0)

    def test_wraparound_takes_short_way(self) -> None:
        """350 deg interpolated toward 10 deg must cross 0, not 180 the long way.

        The result lands at the 0/360 boundary; floating-point rounding can
        yield either exact value (see `_interpolate_angle_deg` docstring).
        """
        result = _interpolate_angle_deg(350.0, 10.0, 0.5)
        assert result == pytest.approx(0.0, abs=1e-9) or result == pytest.approx(360.0, abs=1e-9)

    def test_t_zero_returns_left_angle(self) -> None:
        assert _interpolate_angle_deg(45.0, 200.0, 0.0) == pytest.approx(45.0)

    def test_t_one_returns_right_angle(self) -> None:
        assert _interpolate_angle_deg(45.0, 200.0, 1.0) == pytest.approx(200.0)

    def test_exactly_opposite_angles_never_raises(self) -> None:
        """0/180 at t=0.5 is direction-undefined but must return a finite angle."""
        result = _interpolate_angle_deg(0.0, 180.0, 0.5)
        assert 0.0 <= result <= 360.0


# ── _interpolate_samples ─────────────────────────────────────────────────────


class TestInterpolateSamples:
    """Pure unit tests for the `WeatherSample` interpolation helper."""

    def test_t_zero_returns_left_object(self) -> None:
        left, right = _weather_sample(temperatur_c=10.0), _weather_sample(temperatur_c=20.0)
        assert _interpolate_samples(left, right, 0.0) is left

    def test_t_one_returns_right_object(self) -> None:
        left, right = _weather_sample(temperatur_c=10.0), _weather_sample(temperatur_c=20.0)
        assert _interpolate_samples(left, right, 1.0) is right

    def test_same_object_shortcuts_without_copy(self) -> None:
        sample = _weather_sample()
        assert _interpolate_samples(sample, sample, 0.7) is sample

    def test_midpoint_linearly_interpolates_scalar_fields(self) -> None:
        left = _weather_sample(
            temperatur_c=10.0,
            windgeschwindigkeit_ms=2.0,
            niederschlag_mm=0.0,
            schneefall_cm=0.0,
            luftdruck_hpa=1000.0,
            luftfeuchtigkeit_pct=40.0,
            globalstrahlung_wm2=100.0,
            bewoelkung_pct=20.0,
        )
        right = _weather_sample(
            temperatur_c=20.0,
            windgeschwindigkeit_ms=8.0,
            niederschlag_mm=4.0,
            schneefall_cm=2.0,
            luftdruck_hpa=1020.0,
            luftfeuchtigkeit_pct=80.0,
            globalstrahlung_wm2=300.0,
            bewoelkung_pct=60.0,
        )
        mid = _interpolate_samples(left, right, 0.25)
        assert mid.temperatur_c == pytest.approx(12.5)
        assert mid.windgeschwindigkeit_ms == pytest.approx(3.5)
        assert mid.niederschlag_mm == pytest.approx(1.0)
        assert mid.schneefall_cm == pytest.approx(0.5)
        assert mid.luftdruck_hpa == pytest.approx(1005.0)
        assert mid.luftfeuchtigkeit_pct == pytest.approx(50.0)
        assert mid.globalstrahlung_wm2 == pytest.approx(150.0)
        assert mid.bewoelkung_pct == pytest.approx(30.0)

    def test_wind_direction_interpolates_circularly(self) -> None:
        left = _weather_sample(windrichtung_deg=350.0)
        right = _weather_sample(windrichtung_deg=10.0)
        mid = _interpolate_samples(left, right, 0.5)
        assert mid.windrichtung_deg == pytest.approx(
            0.0, abs=1e-6
        ) or mid.windrichtung_deg == pytest.approx(360.0, abs=1e-6)


# ── fetch_weather_by_detail — high detail ────────────────────────────────────


class TestFetchHigh:
    """High-detail tests: distance-spaced sampling with interpolation between points."""

    @pytest.mark.asyncio
    async def test_high_query_indices_match_spacing_computation(self) -> None:
        for n_segments in [5, 10, 20, 50]:
            _route, segments = _make_route(n_segments)
            segment_eta = _make_segment_eta(segments)
            provider = FakeWeatherProvider()

            await fetch_weather_by_detail(
                provider=provider,
                segment_eta_list=segment_eta,
                abfahrtszeit=ABFAHRTSZEIT,
                detail="high",
            )

            expected_indices = _expected_sampled_indices(segment_eta, HIGH_DETAIL_SAMPLE_SPACING_M)
            expected_coords = {
                segment_eta[idx][0].geometrie[len(segment_eta[idx][0].geometrie) // 2]
                for idx in expected_indices
            }
            actual_coords = {q.koordinate for q in provider.fetch_weather_calls[0]}
            assert actual_coords == expected_coords

    @pytest.mark.asyncio
    async def test_high_query_count_independent_of_polyline_density(self) -> None:
        """Regression (2026-08-29 bug report): a dense polyline (many short raw
        routing-provider segments over a short real distance, as produced by
        curvy roads) must not blow up the query count -- distance spacing, not
        one-query-per-segment, bounds it. Previously "high" queried EVERY
        segment, so a curvy 100km route with 500 tiny 200m segments queried
        500 points instead of ~2 (100km/60km); this caused 429s from weather
        providers on long routes.
        """
        dense_segments = [
            _make_segment(
                i, (52.0, 13.0 + i * 0.001), (52.0, 13.0 + (i + 1) * 0.001), laenge_m=200.0
            )
            for i in range(500)
        ]
        segment_eta = _make_segment_eta(dense_segments)
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        n_queries = len(provider.fetch_weather_calls[0])
        total_distance_m = sum(s.laenge_m for s in dense_segments)
        max_expected = total_distance_m / HIGH_DETAIL_SAMPLE_SPACING_M + 2
        assert n_queries <= max_expected
        assert n_queries < len(dense_segments)

    @pytest.mark.asyncio
    async def test_high_single_batched_provider_call(self) -> None:
        """All queries go out in one `fetch_weather` invocation, however many points."""
        _route, segments = _make_route(10)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        expected_indices = _expected_sampled_indices(segment_eta, HIGH_DETAIL_SAMPLE_SPACING_M)
        assert len(result) == 10
        assert len(provider.fetch_weather_calls) == 1
        assert len(provider.fetch_weather_calls[0]) == len(expected_indices)

    @pytest.mark.asyncio
    async def test_high_first_last_always_sampled(self) -> None:
        _route, segments = _make_route(12)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        queried_indices: set[int] = set()
        for seg_idx, (seg, _) in enumerate(segment_eta):
            for q in provider.fetch_weather_calls[0]:
                if q.koordinate == seg.geometrie[len(seg.geometrie) // 2]:
                    queried_indices.add(seg_idx)
                    break
        assert 0 in queried_indices
        assert len(segment_eta) - 1 in queried_indices

    @pytest.mark.asyncio
    async def test_high_fanout_length_equals_segment_count(self) -> None:
        _route, segments = _make_route(5)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_high_sample_has_segment_own_timestamp(self) -> None:
        _route, segments = _make_route(3)
        durations = [timedelta(hours=1), timedelta(hours=2), timedelta(hours=0.5)]
        eta_list = list(zip(segments, durations, strict=False))
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=eta_list,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        for i, sample in enumerate(result):
            expected_time = _compute_segment_time(i, ABFAHRTSZEIT, eta_list)
            assert sample.zeitpunkt == expected_time


# ── fetch_weather_by_detail — low detail ─────────────────────────────────────


class TestFetchLow:
    """Low-detail tests: distance-spaced sampling with interpolation between points."""

    @pytest.mark.asyncio
    async def test_low_single_batched_provider_call(self) -> None:
        """All queries go out in one `fetch_weather` invocation, however many points."""
        _route, segments = _make_route(10)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        expected_indices = _expected_sampled_indices(segment_eta, LOW_DETAIL_SAMPLE_SPACING_M)
        assert len(result) == 10
        assert len(provider.fetch_weather_calls) == 1
        assert len(provider.fetch_weather_calls[0]) == len(expected_indices)

    @pytest.mark.asyncio
    async def test_low_returned_length_equals_segment_count(self) -> None:
        _route, segments = _make_route(7)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        assert len(result) == 7

    @pytest.mark.asyncio
    async def test_low_sample_has_segment_own_coordinate(self) -> None:
        _route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        for i, sample in enumerate(result):
            seg = segment_eta[i][0]
            expected_coord = seg.geometrie[len(seg.geometrie) // 2]
            assert sample.koordinate == expected_coord

    @pytest.mark.asyncio
    async def test_low_sample_has_segment_own_timestamp(self) -> None:
        _route, segments = _make_route(3)
        durations = [timedelta(hours=1), timedelta(hours=2), timedelta(hours=0.5)]
        eta_list = list(zip(segments, durations, strict=False))
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=eta_list,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        for i, sample in enumerate(result):
            expected_time = _compute_segment_time(i, ABFAHRTSZEIT, eta_list)
            assert sample.zeitpunkt == expected_time

    @pytest.mark.asyncio
    async def test_low_varies_across_a_long_trip(self) -> None:
        """Regression (2026-08-29 bug report): a long trip must not broadcast one
        point's reading to the entire route -- e.g. Gummersbach (DE) to Hagfors
        (SE) is ~1400km/~18h and always showed identical 18C/0.2mm/31km/h SSW.
        """
        _route, segments = _make_route(20)  # 20 * 50km = 1000km
        segment_eta = _make_segment_eta(segments)
        expected_indices = _expected_sampled_indices(segment_eta, LOW_DETAIL_SAMPLE_SPACING_M)
        assert len(expected_indices) > 1, "fixture must exercise multi-point sampling"

        seed_samples = []
        for idx in expected_indices:
            seg, _ = segment_eta[idx]
            coord = seg.geometrie[len(seg.geometrie) // 2]
            t = _compute_segment_time(idx, ABFAHRTSZEIT, segment_eta)
            seed_samples.append(_make_sample(coord, t, temperatur_c=float(idx)))
        provider = FakeWeatherProvider(samples=seed_samples)

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        distinct_temperatures = {sample.temperatur_c for sample in result}
        assert len(distinct_temperatures) > 1

    @pytest.mark.asyncio
    async def test_low_interpolates_smoothly_between_two_samples(self) -> None:
        """Segments between two sampled points get a gradient, not a step function."""
        _route, segments = _make_route(10)
        segment_eta = _make_segment_eta(segments)
        distances = _cumulative_midpoint_distances_m(segment_eta)
        expected_indices = _expected_sampled_indices(segment_eta, LOW_DETAIL_SAMPLE_SPACING_M)
        assert len(expected_indices) >= 2

        seed_samples = []
        for idx in expected_indices:
            seg, _ = segment_eta[idx]
            coord = seg.geometrie[len(seg.geometrie) // 2]
            t = _compute_segment_time(idx, ABFAHRTSZEIT, segment_eta)
            seed_samples.append(_make_sample(coord, t, temperatur_c=float(idx)))
        provider = FakeWeatherProvider(samples=seed_samples)

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        first, second = expected_indices[0], expected_indices[1]
        for seg_idx in range(first, second + 1):
            frac = (distances[seg_idx] - distances[first]) / (distances[second] - distances[first])
            expected_temp = float(first) + frac * (float(second) - float(first))
            assert result[seg_idx].temperatur_c == pytest.approx(expected_temp)

    @pytest.mark.asyncio
    async def test_long_stop_forces_fresh_point_at_departure(self) -> None:
        """A stop longer than `LONG_STOP_THRESHOLD` must not be smoothed over.

        A 20-segment, 1000km route where segment 9 carries a 6h wait (e.g. an
        overnight stay at a mandatory waypoint) must query fresh weather right
        after the wait (segment 10) instead of interpolating from whatever
        distance-spaced sample happens to be next.
        """
        _route, segments = _make_route(20)
        durations = [timedelta(hours=1)] * 20
        durations[9] = timedelta(hours=6)
        segment_eta = list(zip(segments, durations, strict=False))

        expected_indices = _expected_sampled_indices(segment_eta, LOW_DETAIL_SAMPLE_SPACING_M)
        assert 9 in expected_indices
        assert 10 in expected_indices

        seed_samples = []
        for idx in expected_indices:
            seg, _ = segment_eta[idx]
            coord = seg.geometrie[len(seg.geometrie) // 2]
            t = _compute_segment_time(idx, ABFAHRTSZEIT, segment_eta)
            # Departure (post-wait) weather is drastically different from arrival.
            temp = -10.0 if idx >= 10 else 25.0
            seed_samples.append(_make_sample(coord, t, temperatur_c=temp))
        provider = FakeWeatherProvider(samples=seed_samples)

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        assert result[9].temperatur_c == pytest.approx(25.0)
        assert result[10].temperatur_c == pytest.approx(-10.0)


# ── fetch_weather_by_detail — medium detail ──────────────────────────────────


class TestFetchMedium:
    """Medium-detail tests: distance-spaced sampling with interpolation between points."""

    @pytest.mark.asyncio
    async def test_medium_query_indices_match_spacing_computation(self) -> None:
        for n_segments in [5, 10, 20, 50]:
            _route, segments = _make_route(n_segments)
            segment_eta = _make_segment_eta(segments)
            provider = FakeWeatherProvider()

            await fetch_weather_by_detail(
                provider=provider,
                segment_eta_list=segment_eta,
                abfahrtszeit=ABFAHRTSZEIT,
                detail="medium",
            )

            expected_indices = _expected_sampled_indices(
                segment_eta, MEDIUM_DETAIL_SAMPLE_SPACING_M
            )
            expected_coords = {
                segment_eta[idx][0].geometrie[len(segment_eta[idx][0].geometrie) // 2]
                for idx in expected_indices
            }
            actual_coords = {q.koordinate for q in provider.fetch_weather_calls[0]}
            assert actual_coords == expected_coords

    @pytest.mark.asyncio
    async def test_medium_query_count_independent_of_polyline_density(self) -> None:
        """Regression (2026-08-29 bug report): a dense polyline (many short raw
        routing-provider segments over a short real distance, as produced by
        curvy roads) must not blow up the query count -- distance spacing, not
        segment-index stride, bounds it. Previously "every N-th segment" meant
        a curvy 100km route with 500 tiny 200m segments queried ~100 points
        (500/5) instead of ~3 (100km/40km); this caused 429s from weather
        providers on long routes.
        """
        dense_segments = [
            _make_segment(
                i, (52.0, 13.0 + i * 0.001), (52.0, 13.0 + (i + 1) * 0.001), laenge_m=200.0
            )
            for i in range(500)
        ]
        segment_eta = _make_segment_eta(dense_segments)
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        n_queries = len(provider.fetch_weather_calls[0])
        total_distance_m = sum(s.laenge_m for s in dense_segments)
        max_expected = total_distance_m / MEDIUM_DETAIL_SAMPLE_SPACING_M + 2
        assert n_queries <= max_expected
        assert n_queries < len(dense_segments)

    @pytest.mark.asyncio
    async def test_medium_first_last_always_sampled(self) -> None:
        _route, segments = _make_route(12)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        queried_indices: set[int] = set()
        for seg_idx, (seg, _) in enumerate(segment_eta):
            for q in provider.fetch_weather_calls[0]:
                if q.koordinate == seg.geometrie[len(seg.geometrie) // 2]:
                    queried_indices.add(seg_idx)
                    break
        assert 0 in queried_indices
        assert len(segment_eta) - 1 in queried_indices

    @pytest.mark.asyncio
    async def test_medium_fanout_length_equals_segment_count(self) -> None:
        _route, segments = _make_route(10)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_medium_interpolates_between_sampled_points(self) -> None:
        """Verify segments between two sampled points get a linear gradient.

        Uses 1km segments scaled so the sampled set is deterministic:
        distances 0.5..11.5km with a 4.5km-equivalent spacing samples
        {0, 5, 10, 11} (see `TestSampleIndicesBySpacing`), exercised here
        end-to-end against the module's real `MEDIUM_DETAIL_SAMPLE_SPACING_M`
        by scaling segment length so the ratio matches.
        """
        scale = MEDIUM_DETAIL_SAMPLE_SPACING_M / 4.5
        segments = [
            _make_segment(
                i,
                (52.0 + i * 0.01, 13.0),
                (52.0 + (i + 1) * 0.01, 13.0),
                laenge_m=1.0 * scale,
            )
            for i in range(12)
        ]
        segment_eta = _make_segment_eta(segments)
        expected_indices = _expected_sampled_indices(segment_eta, MEDIUM_DETAIL_SAMPLE_SPACING_M)
        assert expected_indices == [0, 5, 10, 11]

        distances = _cumulative_midpoint_distances_m(segment_eta)
        seed_samples = []
        for idx in expected_indices:
            seg, _ = segment_eta[idx]
            coord = seg.geometrie[len(seg.geometrie) // 2]
            t = _compute_segment_time(idx, ABFAHRTSZEIT, segment_eta)
            seed_samples.append(_make_sample(coord, t, temperatur_c=float(idx)))
        provider = FakeWeatherProvider(samples=seed_samples)

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        assert len(result) == 12
        # Segments strictly between two sampled points (e.g. 6, 7, 8, 9
        # between samples 5 and 10) must show a strictly increasing gradient,
        # not a step held at either endpoint's value.
        between = [result[i].temperatur_c for i in range(6, 10)]
        assert between == sorted(between)
        assert between[0] > 5.0
        assert between[-1] < 10.0
        for seg_idx in range(6, 10):
            frac = (distances[seg_idx] - distances[5]) / (distances[10] - distances[5])
            expected_temp = 5.0 + frac * (10.0 - 5.0)
            assert result[seg_idx].temperatur_c == pytest.approx(expected_temp)
            expected_coord = segments[seg_idx].geometrie[len(segments[seg_idx].geometrie) // 2]
            assert result[seg_idx].koordinate == expected_coord


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Empty, single-segment, and off-level edge cases."""

    @pytest.mark.asyncio
    async def test_empty_high_returns_empty(self) -> None:
        _route, _ = _make_route(0)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=[],
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        assert result == []
        assert len(provider.fetch_weather_calls) == 0

    @pytest.mark.asyncio
    async def test_empty_low_returns_empty(self) -> None:
        result = await fetch_weather_by_detail(
            provider=FakeWeatherProvider(),
            segment_eta_list=[],
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_medium_returns_empty(self) -> None:
        result = await fetch_weather_by_detail(
            provider=FakeWeatherProvider(),
            segment_eta_list=[],
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_single_segment_high(self) -> None:
        _route, segments = _make_route(1)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        assert len(result) == 1
        assert len(provider.fetch_weather_calls) == 1
        assert len(provider.fetch_weather_calls[0]) == 1

    @pytest.mark.asyncio
    async def test_single_segment_low(self) -> None:
        _route, segments = _make_route(1)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        assert len(result) == 1
        assert len(provider.fetch_weather_calls) == 1

    @pytest.mark.asyncio
    async def test_single_segment_medium(self) -> None:
        _route, segments = _make_route(1)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        assert len(result) == 1
        assert len(provider.fetch_weather_calls) == 1

    @pytest.mark.asyncio
    async def test_off_level_raises_for_nonempty(self) -> None:
        _route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        with pytest.raises(ValueError, match='"off"'):
            await fetch_weather_by_detail(
                provider=provider,
                segment_eta_list=segment_eta,
                abfahrtszeit=ABFAHRTSZEIT,
                detail="off",
            )

    @pytest.mark.asyncio
    async def test_unknown_detail_level_raises(self) -> None:
        _route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        with pytest.raises(ValueError, match="Unknown detail"):
            await fetch_weather_by_detail(
                provider=provider,
                segment_eta_list=segment_eta,
                abfahrtszeit=ABFAHRTSZEIT,
                detail="super_high",  # type: ignore[arg-type]
            )
