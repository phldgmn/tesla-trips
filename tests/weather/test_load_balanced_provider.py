"""Unit tests for `tripplanner.weather.providers.LoadBalancedWeatherProvider`.

Covers the resilience contract this class exists for: country-aware
eligibility, round-robin load balancing, automatic failover on provider
failure, and graceful degradation (never raising) when every eligible
provider fails.
"""

from collections.abc import Sequence
from datetime import datetime

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import (
    LoadBalancedWeatherProvider,
    WeatherProviderEntry,
    _cache_key,
)

BERLIN: Coordinate = (52.5200, 13.4050)
COPENHAGEN: Coordinate = (55.6761, 12.5683)
STOCKHOLM: Coordinate = (59.3293, 18.0686)
PARIS: Coordinate = (48.8566, 2.3522)  # outside DE/DK/SE


class _StubProvider:
    """A `WeatherProvider` returning canned samples or raising a canned error."""

    def __init__(
        self,
        *,
        samples: list[WeatherSample] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.samples = samples or []
        self.error = error
        self.calls: list[list[WeatherQuery]] = []
        self.closed = False

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        self.calls.append(list(queries))
        if self.error is not None:
            raise self.error
        wanted = {(q.coordinate, q.zeitpunkt) for q in queries}
        return [s for s in self.samples if (s.coordinate, s.zeitpunkt) in wanted]

    async def refetch_weather(
        self, original_queries: Sequence[WeatherQuery], updated_queries: Sequence[WeatherQuery]
    ) -> list[WeatherSample]:
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        self.closed = True


def _sample(coordinate: Coordinate, zeitpunkt: datetime, temp: float) -> WeatherSample:
    return WeatherSample(
        coordinate=coordinate,
        zeitpunkt=zeitpunkt,
        temperatur_c=temp,
        windgeschwindigkeit_ms=1.0,
        windrichtung_deg=0.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=50.0,
        globalstrahlung_wm2=100.0,
        bewoelkung_pct=10.0,
    )


def test_load_balanced_provider_requires_at_least_one_entry() -> None:
    """An empty entry list is rejected at construction time."""
    with pytest.raises(ValueError, match="at least one provider"):
        LoadBalancedWeatherProvider([])


@pytest.mark.asyncio
async def test_load_balanced_provider_uses_only_country_eligible_provider() -> None:
    """SMHI (SE-only) is never queried for a German coordinate; Open-Meteo is."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    global_provider = _StubProvider(samples=[_sample(BERLIN, zeitpunkt, 20.0)])
    se_only_provider = _StubProvider(samples=[_sample(BERLIN, zeitpunkt, 50.0)])

    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("global", global_provider, None),
            WeatherProviderEntry("se-only", se_only_provider, frozenset({"SE"})),
        ]
    )

    results = await composite.fetch_weather([WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt)])

    assert len(results) == 1
    assert results[0].temperatur_c == 20.0
    assert se_only_provider.calls == []


@pytest.mark.asyncio
async def test_load_balanced_provider_country_restricted_provider_used_in_its_country() -> None:
    """SMHI (SE-only) is queried, and used, for a Swedish coordinate."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    se_only_provider = _StubProvider(samples=[_sample(STOCKHOLM, zeitpunkt, 12.0)])

    composite = LoadBalancedWeatherProvider(
        [WeatherProviderEntry("se-only", se_only_provider, frozenset({"SE"}))]
    )

    results = await composite.fetch_weather(
        [WeatherQuery(coordinate=STOCKHOLM, zeitpunkt=zeitpunkt)]
    )

    assert len(results) == 1
    assert results[0].temperatur_c == 12.0
    assert len(se_only_provider.calls) == 1


@pytest.mark.asyncio
async def test_load_balanced_provider_country_restricted_provider_skipped_outside_country() -> None:
    """DMI (DK-only) is not eligible for a coordinate outside DE/DK/SE; falls back to neutral."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    dk_only_provider = _StubProvider(samples=[])

    composite = LoadBalancedWeatherProvider(
        [WeatherProviderEntry("dk-only", dk_only_provider, frozenset({"DK"}))]
    )

    results = await composite.fetch_weather([WeatherQuery(coordinate=PARIS, zeitpunkt=zeitpunkt)])

    assert len(results) == 1
    assert dk_only_provider.calls == []
    assert results[0].temperatur_c == 15.0  # neutral fallback sentinel


@pytest.mark.asyncio
async def test_load_balanced_provider_round_robins_across_calls() -> None:
    """Repeated calls to the same coordinate rotate the starting candidate."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    provider_a = _StubProvider(samples=[_sample(BERLIN, zeitpunkt, 1.0)])
    provider_b = _StubProvider(samples=[_sample(BERLIN, zeitpunkt, 2.0)])

    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("a", provider_a, None),
            WeatherProviderEntry("b", provider_b, None),
        ]
    )

    await composite.fetch_weather([WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt)])
    # Second call: different (cache-missing) time so it dispatches again.
    zeitpunkt_2 = datetime(2026, 8, 17, 15, 0)
    provider_a.samples.append(_sample(BERLIN, zeitpunkt_2, 1.0))
    provider_b.samples.append(_sample(BERLIN, zeitpunkt_2, 2.0))
    await composite.fetch_weather([WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt_2)])

    # Both providers got a first attempt across the two rotated dispatches.
    assert len(provider_a.calls) == 1
    assert len(provider_b.calls) == 1


@pytest.mark.asyncio
async def test_load_balanced_provider_fails_over_to_next_provider_on_error() -> None:
    """A provider raising `httpx.HTTPStatusError` (e.g. 429) is skipped in favor of the next."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(429, request=request)
    failing = _StubProvider(error=httpx.HTTPStatusError("429", request=request, response=response))
    healthy = _StubProvider(samples=[_sample(BERLIN, zeitpunkt, 7.0)])

    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("failing", failing, None),
            WeatherProviderEntry("healthy", healthy, None),
        ]
    )

    results = await composite.fetch_weather([WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt)])

    assert len(results) == 1
    assert results[0].temperatur_c == 7.0
    assert len(failing.calls) == 1
    assert len(healthy.calls) == 1


@pytest.mark.asyncio
async def test_load_balanced_provider_cooldown_deprioritizes_failed_provider() -> None:
    """A provider that failed is tried after healthy ones until its cooldown expires."""
    zeitpunkt_a = datetime(2026, 8, 17, 14, 0)
    zeitpunkt_b = datetime(2026, 8, 17, 15, 0)
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(429, request=request)
    err = httpx.HTTPStatusError("429", request=request, response=response)
    flaky = _StubProvider(error=err, samples=[_sample(BERLIN, zeitpunkt_b, 3.0)])
    healthy = _StubProvider(
        samples=[_sample(BERLIN, zeitpunkt_a, 9.0), _sample(BERLIN, zeitpunkt_b, 9.0)]
    )

    clock_value = [0.0]
    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("flaky", flaky, None),
            WeatherProviderEntry("healthy", healthy, None),
        ],
        cooldown_seconds=300.0,
        clock=lambda: clock_value[0],
    )

    # First call: "flaky" is tried first (rotation offset 0), fails, "healthy" serves it.
    await composite.fetch_weather([WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt_a)])
    flaky.error = None  # now it *would* succeed, but should still be deprioritized
    flaky.samples = [_sample(BERLIN, zeitpunkt_b, 3.0)]

    # Still within cooldown: "flaky" is moved after "healthy", so "healthy" is tried
    # first for the next coordinate group and satisfies it, "flaky" is never called.
    await composite.fetch_weather([WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt_b)])

    assert len(flaky.calls) == 1  # only the initial failing call
    assert len(healthy.calls) == 2


@pytest.mark.asyncio
async def test_load_balanced_provider_all_fail_uses_neutral_fallback_never_raises() -> None:
    """If every eligible provider fails, a neutral placeholder is used instead of raising."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(503, request=request)
    err = httpx.HTTPStatusError("503", request=request, response=response)
    failing_a = _StubProvider(error=err)
    failing_b = _StubProvider(error=RuntimeError("boom"))

    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("a", failing_a, None),
            WeatherProviderEntry("b", failing_b, None),
        ]
    )

    results = await composite.fetch_weather([WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt)])

    assert len(results) == 1
    assert results[0].temperatur_c == 15.0
    assert results[0].windgeschwindigkeit_ms == 3.0
    assert results[0].coordinate == BERLIN
    assert results[0].zeitpunkt == zeitpunkt


@pytest.mark.asyncio
async def test_load_balanced_provider_caches_successful_results() -> None:
    """A second `fetch_weather` for the same point hits the cache, not the provider."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    provider = _StubProvider(samples=[_sample(BERLIN, zeitpunkt, 5.0)])
    composite = LoadBalancedWeatherProvider([WeatherProviderEntry("p", provider, None)])
    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt)

    first = await composite.fetch_weather([query])
    second = await composite.fetch_weather([query])

    assert first[0].temperatur_c == second[0].temperatur_c == 5.0
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_load_balanced_provider_refetch_weather_serves_unchanged_points_from_cache() -> None:
    """`refetch_weather` reuses the composite's cache for points already resolved."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    provider = _StubProvider(samples=[_sample(BERLIN, zeitpunkt, 6.0)])
    composite = LoadBalancedWeatherProvider([WeatherProviderEntry("p", provider, None)])
    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt)

    await composite.fetch_weather([query])
    results = await composite.refetch_weather([query], [query])

    assert results[0].temperatur_c == 6.0
    assert len(provider.calls) == 1  # second (re)fetch served from cache


@pytest.mark.asyncio
async def test_load_balanced_provider_empty_queries_returns_empty() -> None:
    """An empty query list returns an empty result without dispatching to any provider."""
    provider = _StubProvider()
    composite = LoadBalancedWeatherProvider([WeatherProviderEntry("p", provider, None)])

    assert await composite.fetch_weather([]) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_load_balanced_provider_close_closes_every_provider() -> None:
    """`close()` closes every registered provider exposing a `close()` method."""
    provider_a = _StubProvider()
    provider_b = _StubProvider()
    composite = LoadBalancedWeatherProvider(
        [WeatherProviderEntry("a", provider_a, None), WeatherProviderEntry("b", provider_b, None)]
    )

    await composite.close()

    assert provider_a.closed
    assert provider_b.closed


@pytest.mark.asyncio
async def test_load_balanced_provider_multiple_coordinates_dispatched_independently() -> None:
    """Queries for different coordinates within one call are each routed by their own country."""
    zeitpunkt = datetime(2026, 8, 17, 14, 0)
    global_provider = _StubProvider(
        samples=[_sample(BERLIN, zeitpunkt, 20.0), _sample(COPENHAGEN, zeitpunkt, 18.0)]
    )
    dk_only_provider = _StubProvider(samples=[_sample(COPENHAGEN, zeitpunkt, 50.0)])

    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("global", global_provider, None),
            WeatherProviderEntry("dk-only", dk_only_provider, frozenset({"DK"})),
        ]
    )

    results = await composite.fetch_weather(
        [
            WeatherQuery(coordinate=BERLIN, zeitpunkt=zeitpunkt),
            WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=zeitpunkt),
        ]
    )

    by_coord = {r.coordinate: r for r in results}
    assert by_coord[BERLIN].temperatur_c == 20.0
    assert by_coord[COPENHAGEN].temperatur_c in (18.0, 50.0)  # either eligible provider may win


@pytest.mark.asyncio
async def test_load_balanced_provider_cache_hit_returns_each_query_own_koordinate() -> None:
    """Two queries at different coordinates in the same grid-cell get their own .coordinate.

    Both queries round to (52.5, 13.4) and share the same clock hour → one HTTP call.
    Each returned WeatherSample.coordinate must match ITS OWN query's coordinate,
    not the coordinate of whichever query populated the cache slot first.
    """
    zeitpunkt = datetime(2026, 8, 2, 1, 0)
    coord1: Coordinate = (52.51, 13.41)
    coord2: Coordinate = (52.54, 13.38)

    # Stub returns one sample per query, indexed by (coordinate, zeitpunkt)
    stub = _StubProvider(
        samples=[
            _sample(coord1, zeitpunkt, 20.0),
            _sample(coord2, zeitpunkt, 22.0),
        ]
    )

    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("global", stub, None),
        ]
    )

    q1 = WeatherQuery(coordinate=coord1, zeitpunkt=zeitpunkt)
    q2 = WeatherQuery(coordinate=coord2, zeitpunkt=zeitpunkt)

    results = await composite.fetch_weather([q1, q2])

    assert len(results) == 2
    # Each result carries its own query's coordinate
    result_map = {r.coordinate: r for r in results}
    assert result_map[coord1].coordinate == coord1
    assert result_map[coord2].coordinate == coord2

    # Second call: both hit composite cache — verify the label fix holds
    results2 = await composite.fetch_weather([q1, q2])
    assert len(results2) == 2
    result_map2 = {r.coordinate: r for r in results2}
    assert result_map2[coord1].coordinate == coord1
    assert result_map2[coord2].coordinate == coord2


@pytest.mark.asyncio
async def test_load_balanced_provider_resolve_group_collision_same_cache_key() -> None:
    """_resolve_group handles two pending sub-queries with different coords but same cache key.

    When two pending queries share a rounded grid cell, _resolve_group builds
    `by_key` from the provider's returned samples. Without the fix, `by_key`
    would keep only the last sample and label both indices with that sample's
    coordinate. The fix ensures each result is copied with its own query's
    coordinate and zeitpunkt.
    """
    zeitpunkt = datetime(2026, 8, 2, 1, 0)
    coord1: Coordinate = (52.51, 13.41)
    coord2: Coordinate = (52.54, 13.38)

    stub = _StubProvider(
        samples=[
            _sample(coord1, zeitpunkt, 20.0),
            _sample(coord2, zeitpunkt, 22.0),
        ]
    )

    composite = LoadBalancedWeatherProvider(
        [
            WeatherProviderEntry("global", stub, None),
        ]
    )

    q1 = WeatherQuery(coordinate=coord1, zeitpunkt=zeitpunkt)
    q2 = WeatherQuery(coordinate=coord2, zeitpunkt=zeitpunkt)

    results = await composite.fetch_weather([q1, q2])

    assert len(results) == 2
    # Build a set of (result.coordinate, result.zeitpunkt) pairs
    result_pairs = {(r.coordinate, r.zeitpunkt) for r in results}
    assert (coord1, zeitpunkt) in result_pairs
    assert (coord2, zeitpunkt) in result_pairs

    # Now populate composite's internal cache with a single entry that has
    # a DIFFERENT coordinate, then query both coords again — they'll hit
    # the same cache slot but must return their own original coordinates.

    composite._cache[_cache_key(coord1, zeitpunkt)] = WeatherSample(
        coordinate=(52.5, 13.4),  # grid-rounded (not coord1 or coord2)
        zeitpunkt=zeitpunkt,
        temperatur_c=20.0,
        windgeschwindigkeit_ms=5.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )

    results2 = await composite.fetch_weather([q1, q2])
    assert len(results2) == 2
    result_map2 = {r.coordinate: r for r in results2}
    assert result_map2[coord1].coordinate == coord1
    assert result_map2[coord2].coordinate == coord2
