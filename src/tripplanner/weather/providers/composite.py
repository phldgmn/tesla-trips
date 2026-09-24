"""Load-balanced composite weather provider."""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import httpx

from tripplanner.cache import TTLCache
from tripplanner.geo import Coordinate
from tripplanner.weather.coverage import detect_country
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers.caching import (
    _cache_deserialize,
    _cache_key,
    _cache_str_key,
)
from tripplanner.weather.providers.protocols import WeatherProvider

logger = logging.getLogger(__name__)


def _neutral_weather_sample(query: WeatherQuery) -> WeatherSample:
    """Builds a conservative placeholder sample when every provider failed.

    Used exclusively by `LoadBalancedWeatherProvider` as a last resort so a
    total weather-provider outage degrades trip accuracy instead of
    blocking trip calculation. Values are a deliberately mild, generic
    Northern-European estimate - distinct from `FakeWeatherProvider`'s
    defaults so degraded-mode production output is never mistaken for test
    fixture data.

    Args:
        query: The query the sample answers.

    Returns:
        A `WeatherSample` with neutral placeholder values.
    """
    return WeatherSample(
        coordinate=query.coordinate,
        timestamp=query.timestamp,
        temperature_c=15.0,
        wind_speed_ms=3.0,
        wind_direction_deg=180.0,
        precipitation_mm=0.0,
        snowfall_cm=0.0,
        pressure_hpa=1013.25,
        humidity_pct=70.0,
        solar_radiation_wm2=200.0,
        cloudiness_pct=50.0,
    )


class WeatherProviderEntry(NamedTuple):
    """One weather provider registered with `LoadBalancedWeatherProvider`."""

    name: str
    """Human-readable provider name, used in logs and cooldown tracking."""

    provider: WeatherProvider
    """The underlying provider implementation."""

    countries: frozenset[str] | None
    """Country codes (`"DE"`/`"DK"`/`"SE"`) this provider is restricted to,
    or `None` for global coverage (eligible for every coordinate, including
    ones outside the three focus countries)."""


class LoadBalancedWeatherProvider:
    """Composite `WeatherProvider` with country-aware load balancing and failover.

    For each queried coordinate:

    1. `detect_country` narrows the candidate providers to those whose
       `countries` include the coordinate's country (or are `None`, i.e.
       global).
    2. Candidates rotate round-robin across calls (spreading load instead
       of always hitting the same provider first); a provider that failed
       recently is deprioritized (moved after healthy candidates, not
       excluded) for `cooldown_seconds`, so a rate-limited provider gets a
       rest without permanently losing its turn.
    3. Candidates are tried in that order until one returns a sample for a
       given point; a provider raising (HTTP error, timeout, malformed
       response) is logged and skipped in favor of the next candidate.
    4. If every eligible candidate fails for a point, a neutral placeholder
       sample is used (`_neutral_weather_sample`) instead of raising - a
       full weather-provider outage degrades trip accuracy, it never blocks
       trip calculation. This is the fix for the "a single failing
       provider must never break routing" requirement.

    Successful results are cached per grid-rounded coordinate and snapped hour,
    lifetime of this instance, serving both `fetch_weather` and
    `refetch_weather` (the iterative ETA/weather resolution described in
    `docs/03-modulspezifikationen.md` §3) without re-querying providers for
    points already resolved.
    """

    def __init__(  # noqa: PLR0913
        self,
        entries: Sequence[WeatherProviderEntry],
        *,
        cooldown_seconds: float = 300.0,
        max_concurrency: int = 30,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl_seconds: float = 3600.0,
        cache_dir: Path | str | None = None,
    ) -> None:
        """Initializes the composite provider.

        Args:
            entries: Registered providers with their country restrictions.
                Must be non-empty.
            cooldown_seconds: How long a failed provider is deprioritized
                (moved after healthy candidates, not excluded) before being
                retried at normal priority again.
            max_concurrency: Maximum number of coordinate groups resolved
                concurrently. Routes have one weather query per segment, so
                a long route can mean hundreds of distinct coordinates;
                resolving them one at a time would multiply per-request
                network latency by the segment count. Bounded concurrency
                keeps wall-clock time reasonable without opening an
                unbounded number of simultaneous connections to any single
                provider.
            clock: Monotonic time source; overridable in tests.
            cache_ttl_seconds: TTL für den persistenten Cache in Sekunden
                (Standard: 3600 = 1 Stunde).
            cache_dir: Verzeichnis für die SQLite-Datenbank des persistenten
                Caches. Wenn ``None``, wird der Standardpfad
                ``<TRIPPLANNER_CACHE_DIR>/external_api_cache.sqlite`` verwendet.

        Raises:
            ValueError: If `entries` is empty.
        """
        if not entries:
            raise ValueError("LoadBalancedWeatherProvider requires at least one provider.")
        self._entries = list(entries)
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._rotation = 0
        self._unhealthy_until: dict[str, float] = {}
        self._cache: dict[tuple[Coordinate, datetime], WeatherSample] = {}
        self._persistent_cache: TTLCache | None = (
            TTLCache(
                namespace="weather_load_balanced",
                ttl_seconds=cache_ttl_seconds,
                db_path=cache_dir,
            )
            if cache_dir is not None
            else None
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        """Fetches weather for `queries`, dispatching per coordinate as described above.

        Coordinate groups are resolved concurrently (bounded by
        `max_concurrency`) rather than one at a time, so a long route
        (many distinct coordinates) does not multiply per-group network
        latency by the number of segments.
        """
        if not queries:
            return []

        results: list[WeatherSample | None] = [None] * len(queries)
        pending_indices: list[int] = []

        # Persistent-cache fallback for in-memory misses: one bulk lookup in a
        # worker thread instead of a blocking SQLite round trip per sample.
        persisted: dict[str, object] = {}
        if self._persistent_cache is not None:
            missing_keys = [
                _cache_str_key(q.coordinate, q.timestamp)
                for q in queries
                if _cache_key(q.coordinate, q.timestamp) not in self._cache
            ]
            if missing_keys:
                persisted = await asyncio.to_thread(self._persistent_cache.get_many, missing_keys)

        for idx, query in enumerate(queries):
            ck = _cache_key(query.coordinate, query.timestamp)
            cached = self._cache.get(ck)
            if cached is None:
                json_str = persisted.get(_cache_str_key(query.coordinate, query.timestamp))
                if json_str is not None:
                    cached = _cache_deserialize(json_str)
                    self._cache[ck] = cached
            if cached is not None:
                results[idx] = cached.model_copy(
                    update={"coordinate": query.coordinate, "timestamp": query.timestamp}
                )
            else:
                pending_indices.append(idx)

        groups: dict[Coordinate, list[int]] = {}
        for idx in pending_indices:
            rounded = _cache_key(queries[idx].coordinate, queries[idx].timestamp)[0]
            groups.setdefault(rounded, []).append(idx)

        async def resolve_bounded(coordinate: Coordinate, group_indices: list[int]) -> None:
            async with self._semaphore:
                await self._resolve_group(coordinate, group_indices, queries, results)

        await asyncio.gather(
            *(resolve_bounded(coordinate, idxs) for coordinate, idxs in groups.items())
        )

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-fetches weather for `updated_queries`.

        `original_queries` is accepted to satisfy the `WeatherProvider`
        protocol; the composite's own `(coordinate, timestamp)` cache
        (populated by any prior `fetch_weather`/`refetch_weather` call)
        already serves unchanged points, so no separate handling is needed.
        """
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        """Closes every registered provider that exposes a `close()` method."""
        for entry in self._entries:
            close = getattr(entry.provider, "close", None)
            if close is not None:
                await close()

    async def _resolve_group(
        self,
        coordinate: Coordinate,
        group_indices: list[int],
        queries: Sequence[WeatherQuery],
        results: list[WeatherSample | None],
    ) -> None:
        """Resolves every query index in `group_indices` (all sharing `coordinate`)."""
        country = detect_country(coordinate)
        eligible = [e for e in self._entries if e.countries is None or country in e.countries]
        pending = list(group_indices)
        to_persist: dict[str, object] = {}

        for entry in self._ordered_candidates(eligible):
            if not pending:
                break
            sub_queries = [queries[i] for i in pending]
            samples = await self._try_provider(entry, sub_queries, coordinate)
            if samples is None:
                continue
            by_key = {_cache_key(s.coordinate, s.timestamp): s for s in samples}
            still_pending: list[int] = []
            for i in pending:
                ck = _cache_key(queries[i].coordinate, queries[i].timestamp)
                sample = by_key.get(ck)
                if sample is None:
                    still_pending.append(i)
                else:
                    results[i] = sample.model_copy(
                        update={
                            "coordinate": queries[i].coordinate,
                            "timestamp": queries[i].timestamp,
                        }
                    )
                    # Store with original query time for this index
                    self._cache[ck] = sample
                    to_persist[_cache_str_key(queries[i].coordinate, queries[i].timestamp)] = (
                        sample.model_dump(mode="json")
                    )
            pending = still_pending

        if to_persist and self._persistent_cache is not None:
            await asyncio.to_thread(self._persistent_cache.set_many, to_persist)

        if pending:
            logger.error(
                "All eligible weather providers failed for %s; using neutral fallback "
                "for %d point(s).",
                coordinate,
                len(pending),
            )
            for i in pending:
                results[i] = _neutral_weather_sample(queries[i])

    async def _try_provider(
        self,
        entry: WeatherProviderEntry,
        sub_queries: Sequence[WeatherQuery],
        coordinate: Coordinate,
    ) -> list[WeatherSample] | None:
        """Calls `entry.provider.fetch_weather`, absorbing every failure mode.

        Returns `None` (and marks `entry` unhealthy) on any error; the
        caller then moves on to the next candidate.
        """
        try:
            samples = await entry.provider.fetch_weather(sub_queries)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Weather provider %s: HTTP %s for %s.",
                entry.name,
                exc.response.status_code,
                coordinate,
            )
            self._mark_unhealthy(entry.name)
            return None
        except httpx.HTTPError as exc:
            logger.warning(
                "Weather provider %s: request failed for %s: %s.", entry.name, coordinate, exc
            )
            self._mark_unhealthy(entry.name)
            return None
        except Exception:
            logger.exception(
                "Weather provider %s: unexpected error for %s.", entry.name, coordinate
            )
            self._mark_unhealthy(entry.name)
            return None

        self._mark_healthy(entry.name)
        return samples

    def _ordered_candidates(
        self, entries: Sequence[WeatherProviderEntry]
    ) -> list[WeatherProviderEntry]:
        """Round-robin-rotates `entries`, then sorts healthy ones before cooling-down ones."""
        if not entries:
            return []
        offset = self._rotation % len(entries)
        self._rotation += 1
        rotated = list(entries[offset:]) + list(entries[:offset])
        now = self._clock()
        healthy = [e for e in rotated if self._unhealthy_until.get(e.name, 0.0) <= now]
        unhealthy = [e for e in rotated if e not in healthy]
        return healthy + unhealthy

    def _mark_unhealthy(self, name: str) -> None:
        """Deprioritizes provider `name` for `cooldown_seconds`."""
        self._unhealthy_until[name] = self._clock() + self._cooldown_seconds

    def _mark_healthy(self, name: str) -> None:
        """Clears any cooldown for provider `name`."""
        self._unhealthy_until.pop(name, None)
