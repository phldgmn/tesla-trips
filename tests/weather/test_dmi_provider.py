"""Unit tests for `tripplanner.weather.providers.DmiProvider`.

HTTP is mocked via `httpx.MockTransport` — no live network calls (see
AGENTS.md).
"""

from datetime import datetime

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery
from tripplanner.weather.providers import DmiProvider

COPENHAGEN: Coordinate = (55.6761, 12.5683)

_T_VALUES = ["2026-08-17T13:00:00Z", "2026-08-17T14:00:00Z", "2026-08-17T15:00:00Z"]


def _dmi_coveragejson(
    *,
    temp_k: float = 286.65,
    rain_rate: float = 0.0002,
    snow_rate: float = 0.0,
) -> dict:
    """Builds a minimal DMI EDR `position` CoverageJSON response.

    3 hourly steps; index 1 (the middle one) is the queried target hour.
    """

    def series(mid: float) -> list[float]:
        return [mid - 1.0, mid, mid + 1.0]

    domain = {
        "axes": {
            "t": {"values": _T_VALUES},
            "x": {"values": [12.5683]},
            "y": {"values": [55.6761]},
        }
    }
    ranges = {
        "temperature-2m": {"type": "NdArray", "values": series(temp_k)},
        "wind-speed-10m": {"type": "NdArray", "values": [3.0, 4.5, 5.0]},
        "wind-dir-10m": {"type": "NdArray", "values": [200.0, 210.0, 220.0]},
        "relative-humidity-2m": {"type": "NdArray", "values": [60.0, 62.0, 64.0]},
        "pressure-sealevel": {
            "type": "NdArray",
            "values": [101_200.0, 101_000.0, 100_900.0],
        },
        "fraction-of-cloud-cover-2m": {"type": "NdArray", "values": [0.3, 0.5, 0.7]},
        "rain-precipitation-rate": {"type": "NdArray", "values": [0.0, rain_rate, 0.0]},
        "total-snowfall-rate-water-equivalent": {
            "type": "NdArray",
            "values": [0.0, snow_rate, 0.0],
        },
        "downward-short-wave-radiation-flux": {
            "type": "NdArray",
            "values": [100.0, 250.0, 300.0],
        },
    }
    return {"domain": domain, "ranges": ranges}


@pytest.mark.asyncio
async def test_dmi_provider_fetch_weather_maps_fields() -> None:
    """Successful response is mapped to a `WeatherSample`, Kelvin/Pa/fraction converted."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "harmonie_dini_sf/position" in str(request.url)
        # Grid-rounded to 0.1° before dispatch to upstream API
        assert request.url.params["coords"] == "POINT(12.6 55.7)"
        return httpx.Response(200, json=_dmi_coveragejson())

    provider = DmiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert len(results) == 1
    sample = results[0]
    assert sample.temperatur_c == pytest.approx(13.5)  # 286.65K - 273.15
    assert sample.windgeschwindigkeit_ms == 4.5
    assert sample.windrichtung_deg == 210.0
    assert sample.luftfeuchtigkeit_pct == 62.0
    assert sample.luftdruck_hpa == pytest.approx(1010.0)  # 101000 Pa / 100
    assert sample.bewoelkung_pct == 50.0  # 0.5 fraction * 100
    assert sample.globalstrahlung_wm2 == 250.0
    await provider.close()


@pytest.mark.asyncio
async def test_dmi_provider_rain_rate_conversion() -> None:
    """`rain-precipitation-rate` (kg/m^2/s) converts to hourly `niederschlag_mm`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dmi_coveragejson(rain_rate=0.0005))

    provider = DmiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert results[0].niederschlag_mm == pytest.approx(1.8)  # 0.0005 * 3600


@pytest.mark.asyncio
async def test_dmi_provider_snow_rate_conversion() -> None:
    """`total-snowfall-rate-water-equivalent` (kg/m^2/s) converts to hourly `schneefall_cm`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dmi_coveragejson(snow_rate=0.0001))

    provider = DmiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert results[0].schneefall_cm == pytest.approx(0.036)  # (0.0001 * 3600) / 10


@pytest.mark.asyncio
async def test_dmi_provider_missing_hour_returns_no_sample() -> None:
    """A query time absent from `domain.axes.t.values` yields no sample."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dmi_coveragejson())

    provider = DmiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=datetime(2026, 8, 20, 9, 0))

    results = await provider.fetch_weather([query])

    assert results == []


@pytest.mark.asyncio
async def test_dmi_provider_fetch_weather_empty_queries() -> None:
    """An empty query list short-circuits without any HTTP call."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_dmi_coveragejson())

    provider = DmiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await provider.fetch_weather([]) == []
    assert calls == 0


@pytest.mark.asyncio
async def test_dmi_provider_http_error_propagates() -> None:
    """A non-2xx response (e.g. DMI's own 429 "server busy") raises `httpx.HTTPStatusError`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"status": 429, "error": "Too Many Requests"})

    provider = DmiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch_weather([query])


@pytest.mark.asyncio
async def test_dmi_provider_refetch_weather_delegates_to_fetch() -> None:
    """`refetch_weather` ignores `original_queries` and re-fetches `updated_queries`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dmi_coveragejson())

    provider = DmiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    original = [WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=datetime(2026, 8, 17, 12, 0))]
    updated = [WeatherQuery(coordinate=COPENHAGEN, zeitpunkt=datetime(2026, 8, 17, 14, 0))]

    results = await provider.refetch_weather(original, updated)

    assert len(results) == 1
    assert results[0].zeitpunkt == datetime(2026, 8, 17, 14, 0)
