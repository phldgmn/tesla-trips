"""Tests for ``fetch_weather_by_detail`` and ``_nearest_sample_assignment``."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tripplanner.geo import Coordinate
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.weather.providers import FakeWeatherProvider
from tripplanner.weather.weather import (
    LOW_DETAIL_SAMPLE_SPACING_M,
    MEDIUM_DETAIL_SAMPLE_SPACING_M,
    _compute_segment_time,
    _cumulative_midpoint_distances_m,
    _nearest_sample_assignment,
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


# ── _cumulative_midpoint_distances_m / _sample_indices_by_spacing / _nearest_sample_assignment ──


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


class TestNearestSampleAssignment:
    """Pure unit tests for the along-route-distance nearest-sample helper."""

    def test_empty_sampled(self) -> None:
        """No sampled indices returns identity mapping."""
        assert _nearest_sample_assignment([0.0, 1.0, 2.0, 3.0, 4.0], []) == [0, 1, 2, 3, 4]

    def test_single_sampled(self) -> None:
        """Single sampled index assigns all segments to it."""
        assert _nearest_sample_assignment([0.0, 1.0, 2.0, 3.0, 4.0], [2]) == [2, 2, 2, 2, 2]

    def test_exact_cover(self) -> None:
        """Every segment is a sampled index."""
        assert _nearest_sample_assignment([0.0, 1.0, 2.0], [0, 1, 2]) == [0, 1, 2]

    def test_tie_breaks_lower(self) -> None:
        """Ties break to the lower index."""
        distances = [float(i) for i in range(7)]
        assert _nearest_sample_assignment(distances, [0, 6]) == [0, 0, 0, 0, 6, 6, 6]

    def test_uneven_spacing(self) -> None:
        """12 unit-spaced points, sampled {0, 5, 10, 11}.

        Manual trace:
            0: dist(0,0)=0, dist(5,0)=5  -> 0
            1: dist(0,1)=1, dist(5,1)=4  -> 0
            2: dist(0,2)=2, dist(5,2)=3  -> 0
            3: dist(0,3)=3, dist(5,3)=2  -> 5
            4: dist(0,4)=4, dist(5,4)=1  -> 5
            5: dist(5,5)=0  -> 5
            6: dist(5,6)=1, dist(10,6)=4  -> 5
            7: dist(5,7)=2, dist(10,7)=3  -> 5
            8: dist(5,8)=3, dist(10,8)=2  -> 10
            9: dist(10,9)=1  -> 10
            10: dist(10,10)=0  -> 10
            11: dist(10,11)=1, dist(11,11)=0  -> 11
        """
        distances = [float(i) for i in range(12)]
        assignment = _nearest_sample_assignment(distances, [0, 5, 10, 11])
        assert assignment == [0, 0, 0, 5, 5, 5, 5, 5, 10, 10, 10, 11]

    def test_single_segment(self) -> None:
        """One segment, one sample."""
        assert _nearest_sample_assignment([0.0], [0]) == [0]


# ── fetch_weather_by_detail — high detail ────────────────────────────────────


class TestFetchHigh:
    """High-detail tests: one query per segment, identical to _step_5 today."""

    @pytest.mark.asyncio
    async def test_high_one_query_per_segment(self) -> None:
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
        assert len(provider.fetch_weather_calls) == 1
        assert len(provider.fetch_weather_calls[0]) == 5

    @pytest.mark.asyncio
    async def test_high_query_coordinates_are_midpoints(self) -> None:
        _route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        queries = provider.fetch_weather_calls[0]
        for i, (seg, _) in enumerate(segment_eta):
            expected_coord = seg.geometrie[len(seg.geometrie) // 2]
            assert queries[i].koordinate == expected_coord

    @pytest.mark.asyncio
    async def test_high_query_timestamps_accumulate(self) -> None:
        _route, segments = _make_route(3)
        durations = [timedelta(hours=1), timedelta(hours=2), timedelta(hours=0.5)]
        eta_list = list(zip(segments, durations, strict=False))
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            segment_eta_list=eta_list,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        queries = provider.fetch_weather_calls[0]
        expected_times = [
            ABFAHRTSZEIT,
            ABFAHRTSZEIT + timedelta(hours=1),
            ABFAHRTSZEIT + timedelta(hours=3),
        ]
        for i, exp in enumerate(expected_times):
            assert queries[i].zeitpunkt == exp


# ── fetch_weather_by_detail — low detail ─────────────────────────────────────


def _make_sample(coordinate: Coordinate, zeitpunkt: datetime, temperatur_c: float) -> WeatherSample:
    """Builds a distinguishable `WeatherSample` for fan-out assertions."""
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


def _expected_sampled_indices(
    segment_eta: list[tuple[RouteSegment, timedelta]],
    spacing_m: float,
) -> list[int]:
    """Reference computation of which segment indices a spacing level should query."""
    distances = _cumulative_midpoint_distances_m(segment_eta)
    return _sample_indices_by_spacing(distances, spacing_m)


class TestFetchLow:
    """Low-detail tests: distance-spaced sampling, fanned out to every segment."""

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


# ── fetch_weather_by_detail — medium detail ──────────────────────────────────


class TestFetchMedium:
    """Medium-detail tests: distance-spaced sampling, fan-out via nearest."""

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
    async def test_medium_nearest_neighbor_assignment(self) -> None:
        """Verify fan-out uses the along-route-nearest sampled point's weather.

        Uses 1km segments with a 4.5km spacing override reproduced here via
        direct construction so the sampled set is deterministic: distances
        0.5..11.5km with spacing 4.5km samples {0, 5, 10, 11} (see
        `TestNearestSampleAssignment.test_uneven_spacing`), exercised here
        end-to-end through `_sample_indices_by_spacing` against the module's
        real `MEDIUM_DETAIL_SAMPLE_SPACING_M` by scaling segment length so
        the ratio matches (40 000m spacing / 5 segment stride equivalent).
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
        distances = _cumulative_midpoint_distances_m(segment_eta)
        assignment = _nearest_sample_assignment(distances, expected_indices)
        for seg_idx, sample in enumerate(result):
            expected_temp = float(assignment[seg_idx])
            assert sample.temperatur_c == expected_temp, (
                f"seg {seg_idx} should get sample from src {assignment[seg_idx]} "
                f"(temp={expected_temp}), got {sample.temperatur_c}"
            )
            expected_coord = segments[seg_idx].geometrie[len(segments[seg_idx].geometrie) // 2]
            assert sample.koordinate == expected_coord


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
