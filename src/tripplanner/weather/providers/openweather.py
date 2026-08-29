"""OpenWeather 5 day / 3 hour forecast provider."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers._shared import _clamp, _group_queries_by_coordinate

logger = logging.getLogger(__name__)


_OPENWEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5/forecast"
# Forecast slots are 3h apart; accept the nearest one within half that
# window plus margin so a query near a slot boundary still resolves.
_OPENWEATHER_MATCH_TOLERANCE = timedelta(hours=1, minutes=30)
# OpenWeather's free tier hard-blocks the API key after sustained bursts above
# 60 requests/minute (see https://openweathermap.org/appid - "Calls per
# minute"). Default to a margin below that so normal jitter/retries never
# tip the account over the provider's own limit.
_OPENWEATHER_DEFAULT_REQUESTS_PER_MINUTE = 50


class SlidingWindowRateLimiter:
    """Async sliding-window rate limiter shared by concurrent callers.

    `acquire()` blocks until fewer than `max_calls` calls have started
    within the trailing `period_s` seconds, then reserves a slot. Safe to
    share across concurrently-running coroutines (e.g. the coordinate
    groups `LoadBalancedWeatherProvider` resolves in parallel).
    """

    def __init__(
        self,
        max_calls: int,
        period_s: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initializes the limiter.

        Args:
            max_calls: Maximum number of calls allowed within `period_s`.
            period_s: Length of the rolling window in seconds.
            clock: Monotonic time source; overridable in tests.
        """
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1.")
        self._max_calls = max_calls
        self._period_s = period_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()

    async def acquire(self) -> None:
        """Waits, if necessary, until a slot within the rolling window is free."""
        while True:
            async with self._lock:
                now = self._clock()
                while self._timestamps and now - self._timestamps[0] >= self._period_s:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    return
                wait_s = self._period_s - (now - self._timestamps[0])
            await asyncio.sleep(max(wait_s, 0.0))



class OpenWeatherProvider:
    """Weather provider using OpenWeather's free "5 day / 3 hour" forecast API.

    Global coverage. Requires an API key (`credentials.local.yaml`:
    `weather.openweather.key`, or env var `OPENWEATHER_API_KEY`; see
    `tripplanner.trip_input.providers_factory`). Forecast resolution is 3
    hours, so matching uses the nearest available slot within
    `_OPENWEATHER_MATCH_TOLERANCE` instead of exact-hour matching.

    Client-side rate-limited to `requests_per_minute` (default
    `_OPENWEATHER_DEFAULT_REQUESTS_PER_MINUTE`, below OpenWeather's free-tier
    60 rpm cap) to avoid the API key being temporarily blocked by
    OpenWeather for exceeding that limit.
    """

    BASE_URL = _OPENWEATHER_BASE_URL
    TIMEOUT_S = 15.0

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        *,
        requests_per_minute: int = _OPENWEATHER_DEFAULT_REQUESTS_PER_MINUTE,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        """Initializes the provider.

        Args:
            api_key: OpenWeather API key (sent as the `appid` query parameter).
            client: Optional pre-configured `httpx.AsyncClient` for tests.
            requests_per_minute: Client-side cap on HTTP requests per rolling
                60s window, enforced before every request. Ignored if
                `rate_limiter` is given.
            rate_limiter: Optional pre-configured limiter (e.g. to share one
                across multiple `OpenWeatherProvider` instances using the
                same API key, or to inject a fake clock in tests).
        """
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(requests_per_minute)

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        """Fetches weather for `queries`, one rate-limited HTTP request per unique coordinate."""
        if not queries:
            return []

        results: list[WeatherSample | None] = [None] * len(queries)
        for coordinate, entries in _group_queries_by_coordinate(queries).items():
            lat, lon = coordinate
            await self._rate_limiter.acquire()
            response = await self._client.get(
                self.BASE_URL,
                params={"lat": lat, "lon": lon, "appid": self._api_key, "units": "metric"},
            )
            response.raise_for_status()
            entries_by_time = _openweather_entries_by_time(response.json())
            for idx, query in entries:
                sample = _extract_openweather_sample(entries_by_time, query)
                if sample is not None:
                    results[idx] = sample

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-fetches weather for `updated_queries` (no internal caching)."""
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        await self._client.aclose()



def _openweather_entries_by_time(payload: dict[str, Any]) -> dict[datetime, dict[str, Any]]:
    """Indexes an OpenWeather forecast response's `list[]` by naive-UTC timestamp."""
    result: dict[datetime, dict[str, Any]] = {}
    for entry in payload.get("list", []):
        dt = entry.get("dt")
        if dt is None:
            continue
        result[datetime.fromtimestamp(dt, tz=UTC).replace(tzinfo=None)] = entry
    return result


def _extract_openweather_sample(
    entries_by_time: dict[datetime, dict[str, Any]], query: WeatherQuery
) -> WeatherSample | None:
    """Finds the nearest OpenWeather 3-hour slot to `query.zeitpunkt` and maps it.

    Returns `None` if no slot is within `_OPENWEATHER_MATCH_TOLERANCE` (e.g.
    the queried time is beyond the 5-day forecast horizon).
    """
    if not entries_by_time:
        return None
    nearest = min(entries_by_time, key=lambda dt: abs(dt - query.zeitpunkt))
    if abs(nearest - query.zeitpunkt) > _OPENWEATHER_MATCH_TOLERANCE:
        return None

    entry = entries_by_time[nearest]
    main = entry.get("main", {})
    wind = entry.get("wind", {})
    rain_3h = float(entry.get("rain", {}).get("3h", 0.0))
    snow_3h = float(entry.get("snow", {}).get("3h", 0.0))

    return WeatherSample(
        koordinate=query.koordinate,
        zeitpunkt=query.zeitpunkt,
        temperatur_c=float(main.get("temp", 0.0)),
        windgeschwindigkeit_ms=float(wind.get("speed", 0.0)),
        windrichtung_deg=_clamp(float(wind.get("deg", 0.0)), 0.0, 360.0),
        niederschlag_mm=rain_3h / 3.0,
        schneefall_cm=(snow_3h / 3.0) / 10.0,
        luftdruck_hpa=_clamp(float(main.get("pressure", 1013.25)), 870.0, 1084.0),
        luftfeuchtigkeit_pct=_clamp(float(main.get("humidity", 0.0)), 0.0, 100.0),
        globalstrahlung_wm2=0.0,  # not exposed by the 2.5 forecast API
        bewoelkung_pct=_clamp(float(entry.get("clouds", {}).get("all", 0.0)), 0.0, 100.0),
    )
