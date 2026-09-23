"""Unit tests for `tripplanner.weather.providers.OpenWeatherProvider`.

HTTP is mocked via `httpx.MockTransport` — no live network calls (see
AGENTS.md).
"""

from datetime import UTC, datetime
from unittest import mock

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery
from tripplanner.weather.providers import OpenWeatherProvider, SlidingWindowRateLimiter

BERLIN: Coordinate = (52.5200, 13.4050)


def _to_utc_epoch(naive_utc: datetime) -> int:
    """Converts a naive datetime, treated as UTC, to a Unix epoch (matching
    OpenWeather's `dt` field and `OpenWeatherProvider`'s naive-UTC convention)."""
    return int(naive_utc.replace(tzinfo=UTC).timestamp())


def _openweather_json(*, dt: int, temp: float = 18.5) -> dict:
    """Builds a minimal OpenWeather "5 day / 3 hour" forecast response."""
    return {
        "list": [
            {
                "dt": dt,
                "main": {"temp": temp, "pressure": 1015.0, "humidity": 72.0},
                "wind": {"speed": 6.2, "deg": 200.0},
                "clouds": {"all": 55.0},
                "rain": {"3h": 1.5},
            }
        ]
    }


@pytest.mark.asyncio
async def test_openweather_provider_fetch_weather_maps_fields() -> None:
    """Successful response is mapped to a `WeatherSample`, appid/units passed through."""
    target = datetime(2026, 8, 17, 15, 0)
    dt_epoch = _to_utc_epoch(target)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["appid"] == "test-key"
        assert request.url.params["units"] == "metric"
        return httpx.Response(200, json=_openweather_json(dt=dt_epoch))

    provider = OpenWeatherProvider(
        api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=target)

    results = await provider.fetch_weather([query])

    assert len(results) == 1
    sample = results[0]
    assert sample.temperatur_c == 18.5
    assert sample.windgeschwindigkeit_ms == 6.2
    assert sample.windrichtung_deg == 200.0
    assert sample.luftfeuchtigkeit_pct == 72.0
    assert sample.luftdruck_hpa == 1015.0
    assert sample.bewoelkung_pct == 55.0
    assert sample.niederschlag_mm == 0.5  # 1.5mm/3h -> 0.5mm/h
    assert sample.globalstrahlung_wm2 == 0.0
    await provider.close()


@pytest.mark.asyncio
async def test_openweather_provider_matches_nearest_slot_within_tolerance() -> None:
    """A query time between two 3h slots matches the nearest one within tolerance."""
    slot = datetime(2026, 8, 17, 15, 0)
    dt_epoch = _to_utc_epoch(slot)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openweather_json(dt=dt_epoch))

    provider = OpenWeatherProvider(
        api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 16, 0))

    results = await provider.fetch_weather([query])

    assert len(results) == 1
    assert results[0].zeitpunkt == datetime(2026, 8, 17, 16, 0)


@pytest.mark.asyncio
async def test_openweather_provider_beyond_tolerance_returns_no_sample() -> None:
    """A query time far beyond the forecast horizon yields no sample."""
    dt_epoch = _to_utc_epoch(datetime(2026, 8, 17, 15, 0))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openweather_json(dt=dt_epoch))

    provider = OpenWeatherProvider(
        api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 25, 15, 0))

    results = await provider.fetch_weather([query])

    assert results == []


@pytest.mark.asyncio
async def test_openweather_provider_snow_conversion() -> None:
    """`snow.3h` (mm water equivalent) converts to hourly `schneefall_cm`."""
    target = datetime(2026, 8, 17, 15, 0)
    dt_epoch = _to_utc_epoch(target)

    def handler(_request: httpx.Request) -> httpx.Response:
        payload = _openweather_json(dt=dt_epoch)
        payload["list"][0]["snow"] = {"3h": 6.0}
        del payload["list"][0]["rain"]
        return httpx.Response(200, json=payload)

    provider = OpenWeatherProvider(
        api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=target)

    results = await provider.fetch_weather([query])

    assert results[0].niederschlag_mm == 0.0
    assert results[0].schneefall_cm == 0.2  # 6mm/3h -> 2mm/h -> 0.2cm


@pytest.mark.asyncio
async def test_openweather_provider_fetch_weather_empty_queries() -> None:
    """An empty query list short-circuits without any HTTP call."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_openweather_json(dt=0))

    provider = OpenWeatherProvider(
        api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert await provider.fetch_weather([]) == []
    assert calls == 0


@pytest.mark.asyncio
async def test_openweather_provider_http_error_propagates() -> None:
    """A non-2xx response raises `httpx.HTTPStatusError`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Invalid API key"})

    provider = OpenWeatherProvider(
        api_key="bad-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 15, 0))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch_weather([query])


@pytest.mark.asyncio
async def test_openweather_provider_refetch_weather_delegates_to_fetch() -> None:
    """`refetch_weather` ignores `original_queries` and re-fetches `updated_queries`."""
    target = datetime(2026, 8, 17, 15, 0)
    dt_epoch = _to_utc_epoch(target)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openweather_json(dt=dt_epoch))

    provider = OpenWeatherProvider(
        api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    original = [WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 12, 0))]
    updated = [WeatherQuery(coordinate=BERLIN, zeitpunkt=target)]

    results = await provider.refetch_weather(original, updated)

    assert len(results) == 1
    assert results[0].zeitpunkt == target


@pytest.mark.asyncio
async def test_openweather_provider_throttles_below_requests_per_minute() -> None:
    """More unique coordinates than `requests_per_minute` are spread across windows.

    Reproduces the "temporary blocked" OpenWeather account scenario: a
    route with many distinct coordinates must never fire more than
    `requests_per_minute` HTTP requests within any 60s window.
    """
    target = datetime(2026, 8, 17, 15, 0)
    dt_epoch = _to_utc_epoch(target)
    call_times: list[float] = []
    fake_now = [0.0]

    def clock() -> float:
        return fake_now[0]

    def handler(_request: httpx.Request) -> httpx.Response:
        call_times.append(clock())
        return httpx.Response(200, json=_openweather_json(dt=dt_epoch))

    async def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    limiter = SlidingWindowRateLimiter(max_calls=3, period_s=60.0, clock=clock)
    provider = OpenWeatherProvider(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rate_limiter=limiter,
    )
    queries = [WeatherQuery(coordinate=(52.0 + i, 13.0), zeitpunkt=target) for i in range(7)]

    with mock.patch("asyncio.sleep", side_effect=fake_sleep):
        results = await provider.fetch_weather(queries)

    assert len(results) == 7
    assert len(call_times) == 7
    # No 60s window contains more than 3 calls.
    for i in range(len(call_times) - 3):
        assert call_times[i + 3] - call_times[i] >= 60.0
    await provider.close()


@pytest.mark.asyncio
async def test_sliding_window_rate_limiter_allows_burst_then_throttles() -> None:
    """First `max_calls` acquisitions are immediate; the next waits for the window to free up."""
    fake_now = [100.0]

    def clock() -> float:
        return fake_now[0]

    async def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    limiter = SlidingWindowRateLimiter(max_calls=2, period_s=10.0, clock=clock)

    with mock.patch("asyncio.sleep", side_effect=fake_sleep):
        await limiter.acquire()
        await limiter.acquire()
        start = fake_now[0]
        await limiter.acquire()

    assert fake_now[0] - start >= 10.0


@pytest.mark.asyncio
async def test_sliding_window_rate_limiter_rejects_non_positive_max_calls() -> None:
    """`max_calls` must be at least 1."""
    with pytest.raises(ValueError, match="max_calls"):
        SlidingWindowRateLimiter(max_calls=0)
