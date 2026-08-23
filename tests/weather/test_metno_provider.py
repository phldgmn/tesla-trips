"""Unit tests for `tripplanner.weather.providers.MetNorwayProvider`.

HTTP is mocked via `httpx.MockTransport` — no live network calls (see
AGENTS.md).
"""

from datetime import datetime

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery
from tripplanner.weather.providers import MetNorwayProvider

BERLIN: Coordinate = (52.5200, 13.4050)


def _metno_json(*, temp: float = 12.3, snow_symbol: bool = False) -> dict:
    """Builds a minimal MET Norway Locationforecast 2.0 response."""
    return {
        "properties": {
            "timeseries": [
                {
                    "time": "2026-08-17T14:00:00Z",
                    "data": {
                        "instant": {
                            "details": {
                                "air_temperature": temp,
                                "wind_speed": 4.5,
                                "wind_from_direction": 270.0,
                                "relative_humidity": 65.0,
                                "air_pressure_at_sea_level": 1008.0,
                                "cloud_area_fraction": 40.0,
                            }
                        },
                        "next_1_hours": {
                            "summary": {"symbol_code": "lightsnow" if snow_symbol else "cloudy"},
                            "details": {"precipitation_amount": 2.0},
                        },
                    },
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_metno_provider_fetch_weather_maps_fields() -> None:
    """Successful response is mapped to a `WeatherSample` with converted fields."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "User-Agent" in request.headers
        assert request.headers["User-Agent"] != ""
        return httpx.Response(200, json=_metno_json())

    provider = MetNorwayProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert len(results) == 1
    sample = results[0]
    assert sample.temperatur_c == 12.3
    assert sample.windgeschwindigkeit_ms == 4.5
    assert sample.windrichtung_deg == 270.0
    assert sample.luftfeuchtigkeit_pct == 65.0
    assert sample.luftdruck_hpa == 1008.0
    assert sample.bewoelkung_pct == 40.0
    assert sample.niederschlag_mm == 2.0
    assert sample.schneefall_cm == 0.0
    assert sample.globalstrahlung_wm2 == 0.0
    await provider.close()


@pytest.mark.asyncio
async def test_metno_provider_snow_symbol_routes_to_schneefall() -> None:
    """A `next_1_hours` snow symbol routes precipitation into `schneefall_cm`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_metno_json(snow_symbol=True))

    provider = MetNorwayProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert results[0].niederschlag_mm == 0.0
    assert results[0].schneefall_cm == 0.2
    await provider.close()


@pytest.mark.asyncio
async def test_metno_provider_missing_hour_returns_no_sample() -> None:
    """A query time absent from `timeseries` yields no sample for that query."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_metno_json())

    provider = MetNorwayProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 20, 9, 0))

    results = await provider.fetch_weather([query])

    assert results == []
    await provider.close()


@pytest.mark.asyncio
async def test_metno_provider_fetch_weather_empty_queries() -> None:
    """An empty query list short-circuits without any HTTP call."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_metno_json())

    provider = MetNorwayProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await provider.fetch_weather([]) == []
    assert calls == 0
    await provider.close()


@pytest.mark.asyncio
async def test_metno_provider_http_error_propagates() -> None:
    """A non-2xx response raises `httpx.HTTPStatusError` (caller's responsibility to handle)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    provider = MetNorwayProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch_weather([query])
    await provider.close()


@pytest.mark.asyncio
async def test_metno_provider_refetch_weather_delegates_to_fetch() -> None:
    """`refetch_weather` ignores `original_queries` and re-fetches `updated_queries`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_metno_json())

    provider = MetNorwayProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    original = [WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 12, 0))]
    updated = [WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 17, 14, 0))]

    results = await provider.refetch_weather(original, updated)

    assert len(results) == 1
    assert results[0].zeitpunkt == datetime(2026, 8, 17, 14, 0)
    await provider.close()


@pytest.mark.asyncio
async def test_metno_provider_default_init_creates_client() -> None:
    """Constructing without an injected client creates a usable internal client."""
    provider = MetNorwayProvider()
    assert provider._client is not None
    await provider.close()
