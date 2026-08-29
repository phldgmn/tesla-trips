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


def _smhi_json(*, temp: float = 14.2, frozen_part_pct: float = -9.0) -> dict:
    """Builds a minimal SMHI `snow1g` point-forecast response."""
    return {
        "timeSeries": [
            {
                "time": "2026-08-17T14:00:00Z",
                "data": {
                    "air_temperature": temp,
                    "wind_speed": 5.5,
                    "wind_from_direction": 310.0,
                    "relative_humidity": 58.0,
                    "air_pressure_at_mean_sea_level": 1010.0,
                    "cloud_area_fraction": 4,
                    "precipitation_amount_median": 1.2,
                    "precipitation_frozen_part": frozen_part_pct,
                },
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
    assert sample.bewoelkung_pct == 50.0  # 4 oktas * 12.5
    # frozen_part_pct = -9 (SMHI's "no precipitation" sentinel) -> 0% frozen.
    assert sample.niederschlag_mm == 1.2
    assert sample.schneefall_cm == 0.0
    assert sample.globalstrahlung_wm2 == 0.0
    await provider.close()


@pytest.mark.asyncio
async def test_smhi_provider_frozen_precipitation_routes_to_schneefall() -> None:
    """`precipitation_frozen_part` = 100 routes all precipitation into `schneefall_cm`."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_smhi_json(frozen_part_pct=100.0))

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert results[0].niederschlag_mm == 0.0
    assert results[0].schneefall_cm == 0.12  # 1.2mm / 10


@pytest.mark.asyncio
async def test_smhi_provider_partial_frozen_precipitation_splits_proportionally() -> None:
    """`precipitation_frozen_part` between 0 and 100 splits rain/snow proportionally."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_smhi_json(frozen_part_pct=50.0))

    provider = SmhiProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    query = WeatherQuery(koordinate=STOCKHOLM, zeitpunkt=datetime(2026, 8, 17, 14, 0))

    results = await provider.fetch_weather([query])

    assert results[0].niederschlag_mm == pytest.approx(0.6)  # 1.2mm * 50%
    assert results[0].schneefall_cm == pytest.approx(0.06)  # 1.2mm * 50% / 10


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
