"""Unit tests for `tripplanner.weather.providers.SmhiProvider`.

HTTP is mocked via `httpx.MockTransport` — no live network calls (see
AGENTS.md).
"""

from datetime import datetime

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery
from tripplanner.weather.providers import SmhiProvider

STOCKHOLM: Coordinate = (59.3293, 18.0686)


def _smhi_param(name: str, value: float, unit: str = "") -> dict:
    """Builds one SMHI `timeSeries[].parameters[]` entry."""
    return {"name": name, "levelType": "hl", "level": 2, "unit": unit, "values": [value]}


def _smhi_json(*, temp: float = 14.2, pcat: int = 0) -> dict:
    """Builds a minimal SMHI `pmp3g` point-forecast response."""
    return {
        "timeSeries": [
            {
                "validTime": "2026-08-17T14:00:00Z",
                "parameters": [
                    _smhi_param("t", temp, "Cel"),
                    _smhi_param("ws", 5.5, "m/s"),
                    _smhi_param("wd", 310.0, "degree"),
                    _smhi_param("r", 58.0, "percent"),
                    _smhi_param("msl", 1010.0, "hPa"),
                    _smhi_param("tcc_mean", 4, "octas"),
                    _smhi_param("pmedian", 1.2, "kg/m2/h"),
                    _smhi_param("pcat", pcat, "category"),
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_smhi_provider_fetch_weather_maps_fields() -> None:
    """Successful response is mapped to a `WeatherSample` with converted fields."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_smhi_json())

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert len(results) == 1
    sample = results[0]
    assert sample.temperatur_c == 14.2
    assert sample.windgeschwindigkeit_ms == 5.5
    assert sample.windrichtung_deg == 310.0
    assert sample.luftfeuchtigkeit_pct == 58.0
    assert sample.luftdruck_hpa == 1010.0
    assert sample.bewoelkung_pct == 50.0  # 4 octas * 12.5
    assert sample.niederschlag_mm == 1.2
    assert sample.schneefall_cm == 0.0
    assert sample.globalstrahlung_wm2 == 0.0
    await provider.close()


@pytest.mark.asyncio
async def test_smhi_provider_snow_category_routes_to_schneefall() -> None:
    """`pcat` category 1 (snow) routes precipitation into `schneefall_cm`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_smhi_json(pcat=1))

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert results[0].niederschlag_mm == 0.0
    assert results[0].schneefall_cm == 0.12  # 1.2mm / 10


@pytest.mark.asyncio
async def test_smhi_provider_missing_hour_returns_no_sample() -> None:
    """A query time absent from `timeSeries` yields no sample for that query."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_smhi_json())

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 20, 9, 0))

    results = await provider.fetch_weather([query])

    assert results == []


@pytest.mark.asyncio
async def test_smhi_provider_fetch_weather_empty_queries() -> None:
    """An empty query list short-circuits without any HTTP call."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_smhi_json())

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await provider.fetch_weather([]) == []
    assert calls == 0


@pytest.mark.asyncio
async def test_smhi_provider_http_error_propagates() -> None:
    """A non-2xx response raises `httpx.HTTPStatusError`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service unavailable"})

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch_weather([query])


@pytest.mark.asyncio
async def test_smhi_provider_refetch_weather_delegates_to_fetch() -> None:
    """`refetch_weather` ignores `original_queries` and re-fetches `updated_queries`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_smhi_json())

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    original = [WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 17, 12, 0))]
    updated = [WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 17, 14, 0))]

    results = await provider.refetch_weather(original, updated)

    assert len(results) == 1
    assert results[0].zeitpunkt == datetime(2026, 8, 17, 14, 0)
