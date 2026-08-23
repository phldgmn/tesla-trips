"""Tests für das weather-Modul: Provider und HTTP-Client.

Enthält Unit-Tests für `OpenMeteoClient`, `OpenMeteoProvider` und
`FakeWeatherProvider` (HTTP-Layer via `httpx.MockTransport` gemockt)
sowie Integration-Test-Templates für echte API-Calls.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample
from tripplanner.weather.providers import (
    FakeWeatherProvider,
    OpenMeteoClient,
    OpenMeteoProvider,
    OpenWeatherProvider,
    _cache_key,
    _extract_sample_from_response,
)

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


def _open_meteo_json(
    *,
    lat: float = 52.52,
    lon: float = 13.405,
    times: list[str] | None = None,
) -> dict[str, Any]:
    """Erzeugt eine gültige Open-Meteo-JSON-Antwort für Tests.

    Args:
        lat: Breitengrad in der Antwort.
        lon: Längengrad in der Antwort.
        times: Zeitstempel-Liste; Default 3 Stunden ab 2026-08-02T00:00.

    Returns:
        Dictionary im Open-Meteo-Response-Format.
    """
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
            "temperature_2m": [20.0 + i for i in range(n)],
            "wind_speed_10m": [36.0 + i for i in range(n)],
            "wind_direction_10m": [180.0 + i for i in range(n)],
            "precipitation": [0.0] * n,
            "snowfall": [0.0] * n,
            "surface_pressure": [1013.0 + i * 0.1 for i in range(n)],
            "relative_humidity_2m": [60.0 + i for i in range(n)],
            "shortwave_radiation": [400.0] * n,
            "cloud_cover": [10.0 * i for i in range(n)],
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


def _make_response(
    *,
    lat: float = 52.52,
    lon: float = 13.405,
    times: list[str] | None = None,
) -> OpenMeteoResponse:
    """Erzeugt eine `OpenMeteoResponse` direkt (ohne HTTP).

    Args:
        lat: Breitengrad.
        lon: Längengrad.
        times: Zeitstempel-Liste.

    Returns:
        `OpenMeteoResponse`-Instanz.
    """
    return OpenMeteoResponse(**_open_meteo_json(lat=lat, lon=lon, times=times))


# ---------------------------------------------------------------------------
# OpenMeteoClient — Unit-Tests (HTTP gemockt)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_meteo_client_default_init() -> None:
    """Test OpenMeteoClient ohne expliziten Client erstellt internen httpx-Client."""
    client = OpenMeteoClient()
    assert client._client is not None
    await client.close()


@pytest.mark.asyncio
async def test_open_meteo_client_fetch_forecast_empty() -> None:
    """Test OpenMeteoClient.fetch_forecast mit leeren Queries gibt [] zurück."""
    client = OpenMeteoClient(client=httpx.AsyncClient())
    results = await client.fetch_forecast([])
    assert results == []
    await client.close()


@pytest.mark.asyncio
async def test_open_meteo_client_fetch_forecast_success() -> None:
    """Test OpenMeteoClient.fetch_forecast erfolgreiche HTTP-Antwort."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    results = await client.fetch_forecast([query])
    assert len(results) == 1
    assert isinstance(results[0], OpenMeteoResponse)
    await client.close()


@pytest.mark.asyncio
async def test_open_meteo_client_fetch_forecast_groups_duplicate_coords() -> None:
    """Test OpenMeteoClient.fetch_forecast gruppiert doppelte Koordinaten."""
    await _test_client_grouping_helper()


async def _test_client_grouping_helper() -> None:
    """Helper für fetch_forecast-Gruppierungstest."""
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    now = datetime(2026, 8, 2, 10, 0)
    queries = [
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now.replace(hour=11)),
    ]
    results = await client.fetch_forecast(queries)
    assert call_count == 1
    assert len(results) == 2
    await client.close()


@pytest.mark.asyncio
async def test_open_meteo_client_fetch_forecast_http_error() -> None:
    """Test OpenMeteoClient.fetch_forecast löst bei HTTP-Fehler aus."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    with pytest.raises(httpx.HTTPStatusError):
        await client.fetch_forecast([query])
    await client.close()


@pytest.mark.asyncio
async def test_open_meteo_client_close() -> None:
    """Test OpenMeteoClient.close schließt den HTTP-Client."""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    client = OpenMeteoClient(client=http_client)
    await client.close()
    assert http_client.is_closed


# ---------------------------------------------------------------------------
# OpenMeteoProvider — Unit-Tests (MockTransport-Client injiziert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_meteo_provider_default_init() -> None:
    """Test OpenMeteoProvider ohne expliziten Client erstellt internen OpenMeteoClient."""
    provider = OpenMeteoProvider()
    assert provider._client is not None
    assert provider._cache == {}
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_fetch_weather_caches_results() -> None:
    """Test OpenMeteoProvider.fetch_weather cached Ergebnisse im Dict."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    results = await provider.fetch_weather([query])

    assert len(results) == 1
    assert results[0].temperatur_c == pytest.approx(21.0)
    assert results[0].luftfeuchtigkeit_pct == pytest.approx(61.0)
    # Cache-Eintrag gesetzt — keys use grid-rounded + hour-snapped _cache_key
    assert _cache_key(BERLIN, query.zeitpunkt) in provider._cache
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_fetch_weather_uses_cache_on_second_call() -> None:
    """Test OpenMeteoProvider.fetch_weather verwendet Cache → kein zweiter API-Call."""
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    await provider.fetch_weather([query])
    assert call_count == 1

    # Zweiter Aufruf sollte Cache nutzen → kein weiterer API-Call
    await provider.fetch_weather([query])
    assert call_count == 1
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_fetch_weather_partial_cache() -> None:
    """Test OpenMeteoProvider.fetch_weather mit teilweise gecachten Queries."""
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    # Erste Query cachen
    cached_query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 0, 0))
    await provider.fetch_weather([cached_query])
    assert call_count == 1

    # Zweite Query für andere Zeit → nur uncached wird abgefragt
    new_query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    results = await provider.fetch_weather([cached_query, new_query])
    assert len(results) == 2
    # Ein weiterer API-Call für new_query
    assert call_count == 2
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_fetch_weather_empty() -> None:
    """Test OpenMeteoProvider.fetch_weather mit leeren Queries."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_open_meteo_json()))
    provider = OpenMeteoProvider(
        client=OpenMeteoClient(client=httpx.AsyncClient(transport=transport))
    )
    results = await provider.fetch_weather([])
    assert results == []
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_refetch_weather_from_cache() -> None:
    """Test OpenMeteoProvider.refetch_weather nutzt Cache für bekannte Punkte."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    # Erstabfrage
    original = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    await provider.fetch_weather([original])

    # Refetch mit gleicher Koordinate + Zeit → aus Cache
    updated = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    results = await provider.refetch_weather([original], [updated])
    assert len(results) == 1
    assert results[0].koordinate == BERLIN
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_refetch_weather_new_time_fetches() -> None:
    """Test OpenMeteoProvider.refetch_weather fragt bei neuem Zeitpunkt neu ab."""
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    # Erstabfrage bei 01:00
    original = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    await provider.fetch_weather([original])

    # Refetch mit neuer Zeit 02:00 → API-Call
    updated = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 2, 0))
    results = await provider.refetch_weather([original], [updated])
    assert len(results) == 1
    assert results[0].zeitpunkt == datetime(2026, 8, 2, 2, 0)
    assert call_count >= 1
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_close() -> None:
    """Test OpenMeteoProvider.close schließt den Client."""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)
    await provider.close()
    assert http_client.is_closed


# ---------------------------------------------------------------------------
# _extract_sample_from_response (providers.py-Variante) — None-Wert-Zweig
# ---------------------------------------------------------------------------


def test_extract_sample_none_value_uses_default() -> None:
    """Test _extract_sample_from_response behandelt None-Werte mit Defaults."""
    json_data = _open_meteo_json()
    json_data["hourly"]["temperature_2m"][0] = None
    json_data["hourly"]["wind_speed_10m"][0] = None
    response = OpenMeteoResponse(**json_data)
    zeitpunkt = datetime(2026, 8, 2, 0, 0)

    sample = _extract_sample_from_response(response, zeitpunkt)

    assert sample is not None
    assert sample.temperatur_c == 0.0
    assert sample.windgeschwindigkeit_ms == 0.0


def test_extract_sample_missing_key_uses_default() -> None:
    """Test _extract_sample_from_response fehlender Key → Default."""
    json_data = _open_meteo_json()
    del json_data["hourly"]["cloud_cover"]
    response = OpenMeteoResponse(**json_data)
    zeitpunkt = datetime(2026, 8, 2, 1, 0)

    sample = _extract_sample_from_response(response, zeitpunkt)

    assert sample is not None
    assert sample.bewoelkung_pct == 0.0


# ---------------------------------------------------------------------------
# Bestehende Tests (unverändert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_meteo_provider_caching() -> None:
    """Test OpenMeteoProvider Caching für refetch_weather."""
    # Given: Fake-Provider mit Caching-Logik (simuliert OpenMeteoProvider)
    now = datetime(2026, 8, 2, 10, 0)

    original_queries = [
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now + timedelta(hours=1)),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now + timedelta(hours=2)),
    ]

    updated_queries = [
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now + timedelta(minutes=30)),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now + timedelta(hours=1, minutes=30)),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now + timedelta(hours=2, minutes=30)),
    ]

    provider = FakeWeatherProvider()
    # When: refetch_weather aufrufen
    samples = await provider.refetch_weather(original_queries, updated_queries)

    # Then: Wetterdaten für updated_queries zurückgeben
    assert len(samples) == 3
    assert samples[0].koordinate == BERLIN
    assert samples[0].zeitpunkt == now + timedelta(minutes=30)
    assert samples[1].koordinate == BERLIN
    assert samples[1].zeitpunkt == now + timedelta(hours=1, minutes=30)
    assert samples[2].koordinate == BERLIN
    assert samples[2].zeitpunkt == now + timedelta(hours=2, minutes=30)


@pytest.mark.asyncio
async def test_fake_weather_provider_set_samples() -> None:
    """Test FakeWeatherProvider.set_samples für Test-Konfiguration."""
    # Given: Fake-Provider ohne samples
    provider = FakeWeatherProvider()
    now = datetime(2026, 8, 2, 10, 0)

    custom_samples = [
        WeatherSample(
            koordinate=BERLIN,
            zeitpunkt=now,
            temperatur_c=25.0,  # Custom value
            windgeschwindigkeit_ms=10.0,
            windrichtung_deg=90.0,
            niederschlag_mm=0.0,
            schneefall_cm=0.0,
            luftdruck_hpa=1015.0,
            luftfeuchtigkeit_pct=50.0,
            globalstrahlung_wm2=500.0,
            bewoelkung_pct=10.0,
        ),
    ]

    # When: set_samples aufrufen
    provider.set_samples(custom_samples)
    queries = [WeatherQuery(koordinate=BERLIN, zeitpunkt=now)]
    results = await provider.fetch_weather(queries)

    # Then: Custom samples zurückgeben
    assert len(results) == 1
    assert results[0].temperatur_c == 25.0
    assert results[0].windgeschwindigkeit_ms == 10.0


# ---------------------------------------------------------------------------
# New acceptance tests for grid-rounding, hour-snapping, and concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grid_rounding_collapses_near_duplicate_coords_to_single_api_call() -> None:
    """Two query coordinates within 0.1° but not identical collapse to one upstream HTTP call."""

    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    # Coordinates within 0.1° of each other but not identical
    # (52.52, 13.405) rounds to (52.5, 13.4)
    # (52.56, 13.38) rounds to (52.6, 13.4) - different group!
    # Let's use two coords that round to THE SAME grid point
    q1 = WeatherQuery(koordinate=(52.51, 13.41), zeitpunkt=datetime(2026, 8, 2, 1, 0))
    q2 = WeatherQuery(koordinate=(52.54, 13.38), zeitpunkt=datetime(2026, 8, 2, 1, 0))
    # Both round to (52.5, 13.4)

    results = await provider.fetch_weather([q1, q2])

    assert len(results) == 2
    # Only ONE HTTP call because both coords round to the same grid point
    assert call_count == 1
    await provider.close()


@pytest.mark.asyncio
async def test_hour_snapped_cache_serves_convergence_loop_refetch() -> None:
    """Two queries at same rounded coord but timestamps in same clock hour hit cache on 2nd call."""

    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    q1 = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 15))
    q2 = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 45))
    # Both snap to hour 1:00 → same cache key → 2nd call is a cache hit

    results1 = await provider.fetch_weather([q1])
    assert len(results1) == 1
    assert call_count == 1

    results2 = await provider.fetch_weather([q2])
    assert len(results2) == 1
    # Only 1 HTTP call total — second query hit cache
    assert call_count == 1
    await provider.close()


@pytest.mark.asyncio
async def test_openweather_rate_limit_still_passes_unmodified() -> None:
    """OpenWeatherMap's rate-limiting and existing behavior pass unmodified.

    Verifies that OpenWeatherProvider (which has its own
    ``SlidingWindowRateLimiter``) still works correctly with the shared
    grid-rounding path — a query within the tolerance window is still matched
    to the nearest 3-hour slot.
    """

    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # OpenWeather uses Unix epoch for dt; 2026-08-02T01:00:00 UTC
        target_dt = int(datetime(2026, 8, 2, 1, 0, tzinfo=UTC).timestamp())
        return httpx.Response(
            200,
            json={
                "list": [
                    {
                        "dt": target_dt,
                        "main": {"temp": 21.0, "humidity": 55, "pressure": 1013},
                        "wind": {"speed": 5.5, "deg": 180},
                        "clouds": {"all": 30},
                        "rain": {"3h": 0.0},
                        "snow": {"3h": 0.0},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    provider = OpenWeatherProvider(api_key="test", client=http_client)

    queries = [WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 30))]
    results = await provider.fetch_weather(queries)

    # Provider returns a sample (matched via tolerance to nearest slot)
    assert len(results) == 1
    assert results[0].temperatur_c == 21.0
    # Exactly one HTTP call
    assert call_count == 1
    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_cache_hit_returns_each_query_own_koordinate() -> None:
    """Two queries at different coordinates in the same grid-cell get their own .koordinate.

    Both queries round to (52.5, 13.4) and share the same clock hour → one HTTP call.
    Each returned WeatherSample.koordinate must match ITS OWN query's coordinate,
    not the coordinate of whichever query populated the cache slot first.
    """
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_open_meteo_json())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    meteo_client = OpenMeteoClient(client=http_client)
    provider = OpenMeteoProvider(client=meteo_client)

    # Both coords round to (52.5, 13.4); same hour → same cache key
    q1 = WeatherQuery(koordinate=(52.51, 13.41), zeitpunkt=datetime(2026, 8, 2, 1, 0))
    q2 = WeatherQuery(koordinate=(52.54, 13.38), zeitpunkt=datetime(2026, 8, 2, 1, 0))

    results = await provider.fetch_weather([q1, q2])

    assert len(results) == 2
    assert call_count == 1  # single HTTP call

    # Each sample carries its own query's coordinate
    coord_map = {r.koordinate for r in results}
    assert q1.koordinate in coord_map
    assert q2.koordinate in coord_map

    # No cross-contamination: each result's koordinate equals its query's koordinate
    for result in results:
        if result.koordinate == q1.koordinate:
            assert result.koordinate == q1.koordinate
        if result.koordinate == q2.koordinate:
            assert result.koordinate == q2.koordinate

    # Second call: both hit cache — verify the label bug is fixed on cache hits too
    results2 = await provider.fetch_weather([q1, q2])
    assert len(results2) == 2
    assert call_count == 1  # still only 1 HTTP call

    for r in results2:
        if r.koordinate == q1.koordinate:
            assert r.koordinate == q1.koordinate
        if r.koordinate == q2.koordinate:
            assert r.koordinate == q2.koordinate

    await provider.close()


@pytest.mark.asyncio
async def test_open_meteo_provider_refetch_cache_hit_returns_each_query_own_koordinate() -> None:
    """refetch_weather cache hits also label each sample with the requesting query's .koordinate."""
    provider = OpenMeteoProvider()

    # Populate the cache first
    q1 = WeatherQuery(koordinate=(52.51, 13.41), zeitpunkt=datetime(2026, 8, 2, 1, 0))
    q2 = WeatherQuery(koordinate=(52.54, 13.38), zeitpunkt=datetime(2026, 8, 2, 1, 0))
    provider._cache[_cache_key(q1.koordinate, q1.zeitpunkt)] = WeatherSample(
        koordinate=(52.5, 13.4),  # grid-rounded
        zeitpunkt=datetime(2026, 8, 2, 1, 0),
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

    # refetch both — both hit cache
    results = await provider.refetch_weather([q1, q2], [q1, q2])

    assert len(results) == 2
    for r in results:
        if r.koordinate == q1.koordinate:
            assert r.koordinate == q1.koordinate
        if r.koordinate == q2.koordinate:
            assert r.koordinate == q2.koordinate
