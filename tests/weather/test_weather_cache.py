"""Persistent TTL-cache tests for OpenMeteoProvider and LoadBalancedWeatherProvider.

Tests verify that the SQLite-backed TTLCache survives across fresh provider
instances (simulating separate process runs) and that TTL expiry correctly
invalidates cached entries.  No live external HTTP calls are made — all
providers are wired through an ``httpx.MockTransport`` that counts actual
API calls.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import (
    FakeWeatherProvider,
    LoadBalancedWeatherProvider,
    OpenMeteoClient,
    OpenMeteoProvider,
    WeatherProviderEntry,
    _cache_deserialize,
    _cache_key,
    _cache_str_key,
)

# ── Helpers (mirroring test_providers.py conventions) ─────────────────────────

BERLIN: Coordinate = (52.5200, 13.4050)


def _open_meteo_json(
    *,
    lat: float = 52.52,
    lon: float = 13.405,
    times: list[str] | None = None,
    temperature: float = 20.0,
    wind_speed: float = 36.0,
) -> dict:
    """Build a valid Open-Meteo JSON response for tests."""
    if times is None:
        times = [f"2026-08-02T{h:02d}:00" for h in range(24)]
    n = len(times)
    return {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Europe/Berlin",
        "timezone_abbreviation": "CEST",
        "elevation": 38.0,
        "hourly": {
            "time": times,
            "temperature_2m": [temperature + i for i in range(n)],
            "wind_speed_10m": [wind_speed + i for i in range(n)],
            "wind_direction_10m": [180.0 + i for i in range(n)],
            "precipitation": [0.0] * n,
            "snowfall": [0.0] * n,
            "surface_pressure": [1013.0 + i * 0.1 for i in range(n)],
            "relative_humidity_2m": [60.0 + i for i in range(n)],
            "shortwave_radiation": [400.0] * n,
            "cloud_cover": [10.0 * (i % 10) for i in range(n)],
        },
        "hourly_units": {
            "time": "iso8601",
            "temperature_2m": "°C",
            "wind_speed_10m": "km/h",
            "wind_direction_10m": "°",
            "precipitation": "mm",
            "snowfall": "cm",
            "surface_pressure": "hPa",
            "relative_humidity_2m": "%",
            "shortwave_radiation": "W/m²",
            "cloud_cover": "%",
        },
    }


# ── Serialization helpers ────────────────────────────────────────────────────


def test_cache_str_key_matches_cache_key() -> None:
    """_cache_str_key serialises the same (rounded, snapped) as _cache_key."""
    coord: Coordinate = (52.5234, 13.4067)
    time_dt = datetime(2026, 8, 15, 10, 33, 45)

    _cache_key(coord, time_dt)
    str_key = _cache_str_key(coord, time_dt)

    # JSON returns lists for tuples, so compare parsed structure
    data = json.loads(str_key)
    assert data[0] == [round(coord[0], 1), round(coord[1], 1)]
    assert data[1] == time_dt.replace(minute=0, second=0, microsecond=0).isoformat(
        timespec="minutes"
    )


def test_cache_deserialize_roundtrip() -> None:
    """WeatherSample -> model_dump -> model_validate recovers the same data."""
    sample = WeatherSample(
        coordinate=(52.52, 13.41),
        zeitpunkt=datetime(2026, 8, 15, 10, 0, 0),
        temperatur_c=25.0,
        windgeschwindigkeit_ms=5.5,
        windrichtung_deg=185.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=25,
    )
    dumped = sample.model_dump(mode="json")
    recovered = _cache_deserialize(dumped)

    assert recovered.temperatur_c == sample.temperatur_c
    assert recovered.windgeschwindigkeit_ms == sample.windgeschwindigkeit_ms
    assert recovered.bewoelkung_pct == sample.bewoelkung_pct
    assert recovered.coordinate == sample.coordinate
    assert recovered.zeitpunkt == sample.zeitpunkt


# ── OpenMeteoProvider persistent cache ───────────────────────────────────────


def _make_provider_with_transport(
    transport: httpx.MockTransport,
    cache_ttl: float,
    cache_dir: str | None,
) -> OpenMeteoProvider:
    """Helper to build an OpenMeteoProvider with a MockTransport."""
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    return OpenMeteoProvider(
        client=meteo_client,
        cache_ttl_seconds=cache_ttl,
        cache_dir=cache_dir,
    )


@pytest.mark.asyncio
async def test_openmeteo_persistent_cache_cross_instance(tmp_path: Path) -> None:
    """Two fresh OpenMeteoProvider instances sharing the same db_path
    must share cached results — the second instance should not call fetch_weather."""
    db_path = str(tmp_path / "weather_cache.sqlite")
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)

    # Instance 1: first fetch populates both caches
    p1 = _make_provider_with_transport(transport, 3600.0, db_path)

    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    results = await p1.fetch_weather([query])
    assert len(results) == 1
    assert call_count == 1

    await p1.close()

    # Instance 2: fresh process, same db_path — should hit persistent cache
    p2 = _make_provider_with_transport(transport, 3600.0, db_path)

    results2 = await p2.fetch_weather([query])
    assert len(results2) == 1
    assert call_count == 1  # persisted across instances — no new HTTP call

    await p2.close()


@pytest.mark.asyncio
async def test_openmeteo_different_coords_trigger_new_call(tmp_path: Path) -> None:
    """A materially different coordinate/time must not hit cache."""
    db_path = str(tmp_path / "weather_cache2.sqlite")
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        lat = float(request.url.params.get("latitude", 52.52))
        lon = float(request.url.params.get("longitude", 13.405))
        return httpx.Response(200, json=_open_meteo_json(lat=lat, lon=lon))

    transport = httpx.MockTransport(handler)
    provider = _make_provider_with_transport(transport, 3600.0, db_path)

    # Two different coords — should each trigger an API call
    q1 = WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    q2 = WeatherQuery(coordinate=(53.55, 9.99), zeitpunkt=datetime(2026, 8, 2, 12, 0))

    results = await provider.fetch_weather([q1, q2])
    assert len(results) == 2
    # Two unique rounded coords → two API calls (not batched by the client)
    assert call_count == 2

    # Same queries again — should hit in-memory cache
    results2 = await provider.fetch_weather([q1, q2])
    assert len(results2) == 2
    assert call_count == 2  # no new calls


@pytest.mark.asyncio
async def test_openmeteo_ttl_expiry_causes_refetch(tmp_path: Path) -> None:
    """Entries older than cache_ttl_seconds must be refetched."""
    db_path = str(tmp_path / "weather_cache3.sqlite")
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    provider = _make_provider_with_transport(transport, 1.0, db_path)

    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    results = await provider.fetch_weather([query])
    assert len(results) == 1
    assert call_count == 1
    await provider.close()

    # Wait for TTL to expire
    await asyncio.sleep(1.5)

    # New instance — should be expired, triggering a new call
    provider2 = _make_provider_with_transport(transport, 1.0, db_path)
    results2 = await provider2.fetch_weather([query])
    assert len(results2) == 1
    assert call_count == 2  # TTL expired, new call made

    await provider2.close()


# ── LoadBalancedWeatherProvider persistent cache ─────────────────────────────


def _make_lb_provider_with_entry(
    sample: WeatherSample,
    cache_ttl: float,
    cache_dir: str | None,
) -> tuple[LoadBalancedWeatherProvider, list[int]]:
    """Build a LoadBalancedWeatherProvider whose single inner provider
    returns *sample* for every query.  Returns (provider, call_counter)."""
    call_count: list[int] = [0]

    class _StubProvider:
        async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
            call_count[0] += 1
            return [sample]

        async def refetch_weather(
            self,
            original_queries: Sequence[WeatherQuery],
            updated_queries: Sequence[WeatherQuery],
        ) -> list[WeatherSample]:
            _ = original_queries
            return await self.fetch_weather(updated_queries)

    entry = WeatherProviderEntry("stub", _StubProvider(), None)
    provider = LoadBalancedWeatherProvider(
        [entry],
        cache_ttl_seconds=cache_ttl,
        cache_dir=cache_dir,
    )
    return provider, call_count


@pytest.mark.asyncio
async def test_lb_persistent_cache_cross_instance(tmp_path: Path) -> None:
    """LoadBalancedWeatherProvider shares cache across fresh instances."""
    db_path = str(tmp_path / "lb_cache.sqlite")
    sample = WeatherSample(
        coordinate=(52.52, 13.41),
        zeitpunkt=datetime(2026, 8, 15, 10, 0),
        temperatur_c=25.0,
        windgeschwindigkeit_ms=5.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20,
    )
    query = WeatherQuery(coordinate=(52.52, 13.41), zeitpunkt=datetime(2026, 8, 15, 10, 0))

    # Instance 1: first fetch
    p1, counter1 = _make_lb_provider_with_entry(sample, 3600.0, db_path)

    results = await p1.fetch_weather([query])
    assert len(results) == 1
    assert counter1[0] == 1
    await p1.close()

    # Instance 2: should hit persistent cache
    p2, counter2 = _make_lb_provider_with_entry(sample, 3600.0, db_path)

    results2 = await p2.fetch_weather([query])
    assert len(results2) == 1
    assert counter2[0] == 0  # persisted!

    await p2.close()


@pytest.mark.asyncio
async def test_lb_in_memory_first_level(tmp_path: Path) -> None:
    """Same instance: in-memory dict avoids SQLite round-trip on repeated calls."""
    db_path = str(tmp_path / "lb_inmem.sqlite")
    sample = WeatherSample(
        coordinate=(52.52, 13.41),
        zeitpunkt=datetime(2026, 8, 15, 10, 0),
        temperatur_c=25.0,
        windgeschwindigkeit_ms=5.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20,
    )
    query = WeatherQuery(coordinate=(52.52, 13.41), zeitpunkt=datetime(2026, 8, 15, 10, 0))

    stub, counter = _make_lb_provider_with_entry(sample, 3600.0, db_path)

    # First call — stub is hit
    await stub.fetch_weather([query])
    assert counter[0] == 1

    # Second call same provider — in-memory cache, stub NOT hit
    await stub.fetch_weather([query])
    assert counter[0] == 1  # still 1


@pytest.mark.asyncio
async def test_lb_different_coords_trigger_new_call(tmp_path: Path) -> None:
    """Different coordinate/time triggers a new fetch."""
    db_path = str(tmp_path / "lb_diff.sqlite")
    sample = WeatherSample(
        coordinate=(52.52, 13.41),
        zeitpunkt=datetime(2026, 8, 15, 10, 0),
        temperatur_c=25.0,
        windgeschwindigkeit_ms=5.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20,
    )
    stub, counter = _make_lb_provider_with_entry(sample, 3600.0, db_path)

    q1 = WeatherQuery(coordinate=(52.52, 13.41), zeitpunkt=datetime(2026, 8, 15, 10, 0))
    q2 = WeatherQuery(coordinate=(52.52, 13.42), zeitpunkt=datetime(2026, 8, 15, 10, 0))
    q3 = WeatherQuery(coordinate=(51.0, 9.0), zeitpunkt=datetime(2026, 8, 15, 10, 0))

    # First call — stub hit once, caches rounded coord (52.5, 13.4)
    await stub.fetch_weather([q1])
    assert counter[0] == 1

    # q2 rounds to same (52.5, 13.4) — in-memory hit
    await stub.fetch_weather([q2])
    assert counter[0] == 1

    # q3 rounds to different coord — new call
    await stub.fetch_weather([q3])
    assert counter[0] == 2


@pytest.mark.asyncio
async def test_lb_ttl_expiry_causes_refetch(tmp_path: Path) -> None:
    """LoadBalancedWeatherProvider respects TTL and refetches expired entries."""
    db_path = str(tmp_path / "lb_ttl.sqlite")
    sample = WeatherSample(
        coordinate=(52.52, 13.41),
        zeitpunkt=datetime(2026, 8, 15, 10, 0),
        temperatur_c=25.0,
        windgeschwindigkeit_ms=5.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20,
    )
    query = WeatherQuery(coordinate=(52.52, 13.41), zeitpunkt=datetime(2026, 8, 15, 10, 0))

    stub1, counter1 = _make_lb_provider_with_entry(sample, 1.0, db_path)

    results = await stub1.fetch_weather([query])
    assert len(results) == 1
    assert counter1[0] == 1
    await stub1.close()

    await asyncio.sleep(1.5)

    stub2, counter2 = _make_lb_provider_with_entry(sample, 1.0, db_path)
    results2 = await stub2.fetch_weather([query])
    assert len(results2) == 1
    assert counter2[0] == 1  # TTL expired

    await stub2.close()


# ── refetch_weather persistence ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openmeteo_refetch_uses_persistent_cache(tmp_path: Path) -> None:
    """refetch_weather falls back to persistent cache on in-memory miss."""
    db_path = str(tmp_path / "refetch_cache.sqlite")
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    provider = _make_provider_with_transport(transport, 3600.0, db_path)

    query = WeatherQuery(coordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))

    # Initial fetch — stub called once, cached
    await provider.fetch_weather([query])
    assert call_count == 1

    # Clear in-memory cache manually to simulate cold start
    provider._cache.clear()

    # refetch_weather — should hit persistent cache, not stub
    r2 = await provider.refetch_weather([query], [query])
    assert len(r2) == 1
    assert call_count == 1  # still 1, persistent hit

    await provider.close()


@pytest.mark.asyncio
async def test_lb_refetch_uses_persistent_cache(tmp_path: Path) -> None:
    """LoadBalancedWeatherProvider.refetch_weather falls back to persistent cache."""
    db_path = str(tmp_path / "lb_refetch.sqlite")
    sample = WeatherSample(
        coordinate=(52.52, 13.41),
        zeitpunkt=datetime(2026, 8, 15, 10, 0),
        temperatur_c=25.0,
        windgeschwindigkeit_ms=5.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20,
    )
    query = WeatherQuery(coordinate=(52.52, 13.41), zeitpunkt=datetime(2026, 8, 15, 10, 0))

    stub, counter = _make_lb_provider_with_entry(sample, 3600.0, db_path)

    r1 = await stub.fetch_weather([query])
    assert len(r1) == 1
    assert counter[0] == 1

    # Clear in-memory cache to simulate cold start
    stub._cache.clear()

    # refetch_weather — persistent cache hit
    r2 = await stub.refetch_weather([query], [query])
    assert len(r2) == 1
    assert counter[0] == 1  # no new call


# ── Default constructor params ──────────────────────────────────────────────


def test_openmeteo_default_cache_params(tmp_path: Path) -> None:
    """OpenMeteoProvider accepts default cache_ttl_seconds and cache_dir."""
    provider = OpenMeteoProvider(cache_dir=tmp_path / "cache.sqlite")
    assert provider._persistent_cache is not None
    assert provider._persistent_cache._ttl == 3600.0


def test_lb_default_cache_params(tmp_path: Path) -> None:
    """LoadBalancedWeatherProvider accepts default cache_ttl_seconds and cache_dir."""
    entry = WeatherProviderEntry("test", FakeWeatherProvider(), None)
    provider = LoadBalancedWeatherProvider([entry], cache_dir=tmp_path / "cache.sqlite")
    assert provider._persistent_cache is not None
    assert provider._persistent_cache._ttl == 3600.0
