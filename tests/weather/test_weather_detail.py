"""Tests for ``fetch_weather_by_detail`` and ``_nearest_sample_assignment``."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from tripplanner.geo import Coordinate
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.weather.providers import FakeWeatherProvider
from tripplanner.weather.weather import (
    MEDIUM_DETAIL_SEGMENT_STRIDE,
    _compute_segment_time,
    _nearest_sample_assignment,
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


# ── _nearest_sample_assignment unit tests ─────────────────────────────────────


class TestNearestSampleAssignment:
    """Pure-integer unit tests for the nearest-sample assignment helper."""

    def test_empty_sampled(self) -> None:
        """No sampled indices returns identity mapping."""
        assert _nearest_sample_assignment(5, []) == [0, 1, 2, 3, 4]

    def test_single_sampled(self) -> None:
        """Single sampled index assigns all segments to it."""
        assert _nearest_sample_assignment(5, [2]) == [2, 2, 2, 2, 2]

    def test_exact_cover(self) -> None:
        """Every segment is a sampled index."""
        assert _nearest_sample_assignment(3, [0, 1, 2]) == [0, 1, 2]

    def test_tie_breaks_lower(self) -> None:
        """Ties break to the lower index."""
        assert _nearest_sample_assignment(7, [0, 6]) == [0, 0, 0, 0, 6, 6, 6]

    def test_medium_12_segments(self) -> None:
        """12 segments, stride-5 sampling: sampled {0, 5, 10, 11}.

        Manual trace:
            seg 0: dist(0,0)=0, dist(5,0)=5  -> 0
            seg 1: dist(0,1)=1, dist(5,1)=4  -> 0
            seg 2: dist(0,2)=2, dist(5,2)=3  -> 0
            seg 3: dist(0,3)=3, dist(5,3)=2  -> 5
            seg 4: dist(0,4)=4, dist(5,4)=1  -> 5
            seg 5: dist(5,5)=0  -> 5
            seg 6: dist(5,6)=1, dist(10,6)=4  -> 5
            seg 7: dist(5,7)=2, dist(10,7)=3  -> 5
            seg 8: dist(5,8)=3, dist(10,8)=2  -> 10
            seg 9: dist(10,9)=1  -> 10
            seg 10: dist(10,10)=0  -> 10
            seg 11: dist(10,11)=1, dist(11,11)=0  -> 11
        """
        assignment = _nearest_sample_assignment(12, [0, 5, 10, 11])
        assert assignment == [0, 0, 0, 5, 5, 5, 5, 5, 10, 10, 10, 11]

    def test_single_segment(self) -> None:
        """One segment, one sample."""
        assert _nearest_sample_assignment(1, [0]) == [0]


# ── fetch_weather_by_detail — high detail ────────────────────────────────────


class TestFetchHigh:
    """High-detail tests: one query per segment, identical to _step_5 today."""

    @pytest.mark.asyncio
    async def test_high_one_query_per_segment(self) -> None:
        route, segments = _make_route(5)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        assert len(result) == 5
        assert len(provider.fetch_weather_calls) == 1
        assert len(provider.fetch_weather_calls[0]) == 5

    @pytest.mark.asyncio
    async def test_high_query_coordinates_are_midpoints(self) -> None:
        route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            route=route,
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
        route, segments = _make_route(3)
        durations = [timedelta(hours=1), timedelta(hours=2), timedelta(hours=0.5)]
        eta_list = list(zip(segments, durations, strict=False))
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            route=route,
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


class TestFetchLow:
    """Low-detail tests: one representative query broadcast to all segments."""

    @pytest.mark.asyncio
    async def test_low_single_provider_call(self) -> None:
        route, segments = _make_route(10)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        assert len(result) == 10
        assert len(provider.fetch_weather_calls) == 1
        assert len(provider.fetch_weather_calls[0]) == 1

    @pytest.mark.asyncio
    async def test_low_returned_length_equals_segment_count(self) -> None:
        route, segments = _make_route(7)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        assert len(result) == 7

    @pytest.mark.asyncio
    async def test_low_sample_has_segment_own_coordinate(self) -> None:
        route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
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
        route, segments = _make_route(3)
        durations = [timedelta(hours=1), timedelta(hours=2), timedelta(hours=0.5)]
        eta_list = list(zip(segments, durations, strict=False))
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=eta_list,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        for i, sample in enumerate(result):
            expected_time = _compute_segment_time(i, ABFAHRTSZEIT, eta_list)
            assert sample.zeitpunkt == expected_time


# ── fetch_weather_by_detail — medium detail ──────────────────────────────────


class TestFetchMedium:
    """Medium-detail tests: sampled every N-th segment, fan-out via nearest."""

    @pytest.mark.asyncio
    async def test_medium_query_count_bound(self) -> None:
        for n_segments in [5, 10, 20, 50]:
            route, segments = _make_route(n_segments)
            segment_eta = _make_segment_eta(segments)
            provider = FakeWeatherProvider()

            await fetch_weather_by_detail(
                provider=provider,
                route=route,
                segment_eta_list=segment_eta,
                abfahrtszeit=ABFAHRTSZEIT,
                detail="medium",
            )

            n_queries = len(provider.fetch_weather_calls[0])
            max_allowed = math.ceil(n_segments / MEDIUM_DETAIL_SEGMENT_STRIDE) + 1
            assert n_queries <= max_allowed

    @pytest.mark.asyncio
    async def test_medium_first_last_always_sampled(self) -> None:
        route, segments = _make_route(12)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        await fetch_weather_by_detail(
            provider=provider,
            route=route,
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
        route, segments = _make_route(10)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_medium_nearest_neighbor_assignment(self) -> None:
        """Verify nearest-neighbor assignment by weather values, not just coordinates.

        stride=5 -> sampled indices {0, 5, 10, 11}.
        We seed the provider with 4 distinguishable samples (one per sampled query)
        carrying unique temperatur_c. Then assert that each segment's returned sample
        has the temperatur_c of its correctly-assigned nearest sample.

        Fan-out mapping (12 segments, sampled [0,5,10,11]):
            seg 0-2   -> 0 (temperatur_c=0.0)
            seg 3-7   -> 5 (temperatur_c=5.0)
            seg 8-10  -> 10 (temperatur_c=10.0)
            seg 11    -> 11 (temperatur_c=11.0)
        """
        route, segments = _make_route(12)
        segment_eta = _make_segment_eta(segments)

        # Build 4 distinguishable samples for the 4 sampled queries
        sampled_indices = sorted({0, 5, 10, 11})
        # Compute what coordinates/times the provider will be queried with
        sampled_samples: list[WeatherSample] = []
        for idx in sampled_indices:
            seg, _ = segment_eta[idx]
            geo = seg.geometrie
            coord = geo[len(geo) // 2]
            t = _compute_segment_time(idx, ABFAHRTSZEIT, segment_eta)
            # temperatur_c uniquely identifies which sample this segment's weather came from
            sampled_samples.append(
                WeatherSample(
                    koordinate=coord,
                    zeitpunkt=t,
                    temperatur_c=float(idx),
                    windgeschwindigkeit_ms=5.0,
                    windrichtung_deg=180.0,
                    niederschlag_mm=0.0,
                    schneefall_cm=0.0,
                    luftdruck_hpa=1013.25,
                    luftfeuchtigkeit_pct=60.0,
                    globalstrahlung_wm2=400.0,
                    bewoelkung_pct=20.0,
                )
            )
        provider = FakeWeatherProvider(samples=sampled_samples)

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        assert len(result) == 12

        assignment = _nearest_sample_assignment(12, sampled_indices)
        for seg_idx, sample in enumerate(result):
            assigned_src = assignment[seg_idx]
            expected_temp = float(assigned_src)
            assert sample.temperatur_c == expected_temp, (
                f"seg {seg_idx} should get sample from src {assigned_src} "
                f"(temp={expected_temp}), got {sample.temperatur_c}"
            )
            # Also verify coordinate override happened
            expected_coord = segments[seg_idx].geometrie[len(segments[seg_idx].geometrie) // 2]
            assert sample.koordinate == expected_coord


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Empty, single-segment, and off-level edge cases."""

    @pytest.mark.asyncio
    async def test_empty_high_returns_empty(self) -> None:
        route, _ = _make_route(0)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
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
            route=Route(segments=[], gesamtlaenge_m=0, geometrie=[]),
            segment_eta_list=[],
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_medium_returns_empty(self) -> None:
        result = await fetch_weather_by_detail(
            provider=FakeWeatherProvider(),
            route=Route(segments=[], gesamtlaenge_m=0, geometrie=[]),
            segment_eta_list=[],
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_single_segment_high(self) -> None:
        route, segments = _make_route(1)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="high",
        )

        assert len(result) == 1
        assert len(provider.fetch_weather_calls) == 1
        assert len(provider.fetch_weather_calls[0]) == 1

    @pytest.mark.asyncio
    async def test_single_segment_low(self) -> None:
        route, segments = _make_route(1)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="low",
        )

        assert len(result) == 1
        assert len(provider.fetch_weather_calls) == 1

    @pytest.mark.asyncio
    async def test_single_segment_medium(self) -> None:
        route, segments = _make_route(1)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        result = await fetch_weather_by_detail(
            provider=provider,
            route=route,
            segment_eta_list=segment_eta,
            abfahrtszeit=ABFAHRTSZEIT,
            detail="medium",
        )

        assert len(result) == 1
        assert len(provider.fetch_weather_calls) == 1

    @pytest.mark.asyncio
    async def test_off_level_raises_for_nonempty(self) -> None:
        route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        with pytest.raises(ValueError, match='"off"'):
            await fetch_weather_by_detail(
                provider=provider,
                route=route,
                segment_eta_list=segment_eta,
                abfahrtszeit=ABFAHRTSZEIT,
                detail="off",
            )

    @pytest.mark.asyncio
    async def test_unknown_detail_level_raises(self) -> None:
        route, segments = _make_route(3)
        segment_eta = _make_segment_eta(segments)
        provider = FakeWeatherProvider()

        with pytest.raises(ValueError, match="Unknown detail"):
            await fetch_weather_by_detail(
                provider=provider,
                route=route,
                segment_eta_list=segment_eta,
                abfahrtszeit=ABFAHRTSZEIT,
                detail="super_high",  # type: ignore[arg-type]
            )
