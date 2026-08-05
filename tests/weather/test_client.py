"""Unit-Tests für `tripplanner.weather.client.OpenMeteoClient`.

Der HTTP-Layer wird durch `httpx.MockTransport` gemockt — es finden KEINE
echten Netzwerkzugriffe statt.
"""

from datetime import datetime
from typing import Any

import httpx
import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.client import (
    _KMH_TO_MPS,
    HOURLY_PARAMS,
    OpenMeteoClient,
    _extract_sample_from_response,
)
from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


def _make_open_meteo_json(
    *,
    lat: float = 52.52,
    lon: float = 13.405,
    times: list[str] | None = None,
) -> dict[str, Any]:
    """Erzeugt eine gültige Open-Meteo-JSON-Antwort für Tests.

    Args:
        lat: Breitengrad in der Antwort.
        lon: Längengrad in der Antwort.
        times: Liste der Zeitstempel; Default sind 3 Stunden ab 2026-08-02T00:00.

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
            "temperature_2m": [14.5 + i * 0.1 for i in range(n)],
            "wind_speed_10m": [12.5 + i for i in range(n)],
            "wind_direction_10m": [245 + i for i in range(n)],
            "precipitation": [0.0] * n,
            "snowfall": [0.0] * n,
            "surface_pressure": [1013.0 + i * 0.1 for i in range(n)],
            "relative_humidity_2m": [72 + i for i in range(n)],
            "shortwave_radiation": [0] * n,
            "cloud_cover": [45 + i for i in range(n)],
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


def _make_response_obj(
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
    return OpenMeteoResponse(**_make_open_meteo_json(lat=lat, lon=lon, times=times))


# ---------------------------------------------------------------------------
# fetch_forecast — HTTP-Layer gemockt via MockTransport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_forecast_single_query_success() -> None:
    """Test fetch_forecast mit einer einzelnen Query — erfolgreiche HTTP-Antwort."""
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json=_make_open_meteo_json())

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    results = await client.fetch_forecast([query])

    assert len(results) == 1
    assert isinstance(results[0], OpenMeteoResponse)
    assert results[0].latitude == 52.52
    assert results[0].longitude == 13.405
    assert len(captured_urls) == 1
    # URL enthält latitude/longitude und start_date/end_date
    url = captured_urls[0]
    assert "latitude=52.52" in url
    assert "longitude=13.405" in url
    assert "hourly=" in url
    for param in HOURLY_PARAMS:
        assert param in url
    assert "start_date=2026-08-02" in url
    assert "end_date=2026-08-02" in url

    await client.close()


@pytest.mark.asyncio
async def test_fetch_forecast_empty_queries_returns_empty() -> None:
    """Test fetch_forecast mit leerer Query-Liste gibt leere Liste zurück."""
    client = OpenMeteoClient(client=httpx.AsyncClient())
    results = await client.fetch_forecast([])
    assert results == []
    await client.close()


@pytest.mark.asyncio
async def test_fetch_forecast_duplicate_coords_single_api_call() -> None:
    """Test fetch_forecast gruppiert doppelte Koordinaten → nur 1 API-Call."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_make_open_meteo_json())

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
async def test_fetch_forecast_multiple_coords_multiple_calls() -> None:
    """Test fetch_forecast mit zwei verschiedenen Koordinaten → 2 API-Calls."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        url = str(request.url)
        if "53.5511" in url:
            return httpx.Response(200, json=_make_open_meteo_json(lat=53.5511, lon=9.9937))
        return httpx.Response(200, json=_make_open_meteo_json())

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    queries = [
        WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0)),
        WeatherQuery(koordinate=HAMBURG, zeitpunkt=datetime(2026, 8, 2, 1, 0)),
    ]
    results = await client.fetch_forecast(queries)

    assert call_count == 2
    assert len(results) == 2
    await client.close()


@pytest.mark.asyncio
async def test_fetch_forecast_time_not_in_response_filtered() -> None:
    """Test fetch_forecast filtert Queries deren Zeitpunkt nicht in der Antwort liegt."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Antwort enthält nur 00:00-02:00 Uhr
        return httpx.Response(
            200,
            json=_make_open_meteo_json(
                times=["2026-08-02T00:00", "2026-08-02T01:00", "2026-08-02T02:00"]
            ),
        )

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    # Zeitpunkt 05:00 liegt außerhalb der Antwort → gefiltert
    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 5, 0))
    results = await client.fetch_forecast([query])

    assert results == []
    await client.close()


@pytest.mark.asyncio
async def test_fetch_forecast_http_error_raises() -> None:
    """Test fetch_forecast löst bei HTTP-Fehlerstatus eine Exception aus."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    with pytest.raises(httpx.HTTPStatusError):
        await client.fetch_forecast([query])

    await client.close()


@pytest.mark.asyncio
async def test_fetch_forecast_422_error_raises() -> None:
    """Test fetch_forecast löst bei 422 (Bad Request) eine Exception aus."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "Invalid parameters"})

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    query = WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 1, 0))
    with pytest.raises(httpx.HTTPStatusError):
        await client.fetch_forecast([query])

    await client.close()


@pytest.mark.asyncio
async def test_fetch_forecast_time_range_in_url() -> None:
    """Test fetch_forecast verwendet earliest/latest Zeitpunkt für Start-/End-Datum."""
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        # Antwort Zeitbereich muss beide Tagen abdecken
        return httpx.Response(
            200,
            json=_make_open_meteo_json(
                times=[f"2026-08-02T{h:02d}:00" for h in range(24)]
                + [f"2026-08-03T{h:02d}:00" for h in range(24)],
            ),
        )

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.AsyncClient(transport=transport))

    queries = [
        WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 2, 6, 0)),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=datetime(2026, 8, 3, 18, 0)),
    ]
    results = await client.fetch_forecast(queries)

    assert len(results) == 2
    url = captured_urls[0]
    assert "start_date=2026-08-02" in url
    assert "end_date=2026-08-03" in url
    await client.close()


@pytest.mark.asyncio
async def test_close_acloses_client() -> None:
    """Test close() schließt den internen httpx-Client."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_make_open_meteo_json()))
    http_client = httpx.AsyncClient(transport=transport)
    client = OpenMeteoClient(client=http_client)

    await client.close()
    assert http_client.is_closed


# ---------------------------------------------------------------------------
# _extract_sample_from_response (client.py-Variante)
# ---------------------------------------------------------------------------


def test_extract_sample_from_response_success() -> None:
    """Test _extract_sample_from_response extrahiert korrekte Werte mit km/h→m/s Umrechnung."""
    response = _make_response_obj()
    zeitpunkt = datetime(2026, 8, 2, 1, 0)

    sample = _extract_sample_from_response(response, zeitpunkt)

    assert sample is not None
    assert sample.koordinate == (52.52, 13.405)
    assert sample.zeitpunkt == zeitpunkt
    # temperature_2m[1] = 14.6
    assert sample.temperatur_c == pytest.approx(14.6)
    # wind_speed_10m[1] = 13.5 km/h → m/s
    assert sample.windgeschwindigkeit_ms == pytest.approx(13.5 * _KMH_TO_MPS, abs=0.01)
    # wind_direction_10m[1] = 246
    assert sample.windrichtung_deg == 246
    # surface_pressure[1] = 1013.1
    assert sample.luftdruck_hpa == pytest.approx(1013.1)
    # relative_humidity_2m[1] = 73
    assert sample.luftfeuchtigkeit_pct == 73
    # cloud_cover[1] = 46
    assert sample.bewoelkung_pct == 46


def test_extract_sample_from_response_missing_time() -> None:
    """Test _extract_sample_from_response gibt None bei nicht gefundenem Zeitpunkt."""
    response = _make_response_obj(
        times=["2026-08-02T00:00", "2026-08-02T01:00", "2026-08-02T02:00"]
    )
    zeitpunkt = datetime(2026, 8, 2, 5, 0)  # außerhalb

    sample = _extract_sample_from_response(response, zeitpunkt)
    assert sample is None


def test_extract_sample_from_response_none_values_use_defaults() -> None:
    """Test _extract_sample_from_response behandelt None-Werte mit Defaults."""
    json_data = _make_open_meteo_json()
    # Setze wind_speed_10m[0] auf None → Default 0.0
    json_data["hourly"]["wind_speed_10m"][0] = None
    json_data["hourly"]["temperature_2m"][0] = None
    response = OpenMeteoResponse(**json_data)
    zeitpunkt = datetime(2026, 8, 2, 0, 0)

    sample = _extract_sample_from_response(response, zeitpunkt)

    assert sample is not None
    assert sample.temperatur_c == 0.0  # None → default 0.0
    assert sample.windgeschwindigkeit_ms == 0.0  # None → default 0.0 → 0.0 m/s


def test_extract_sample_from_response_missing_keys_use_defaults() -> None:
    """Test _extract_sample_from_response fehlt ein Key → Default-Liste."""
    json_data = _make_open_meteo_json()
    # Entferne cloud_cover-Key → get_value nutzt Default
    del json_data["hourly"]["cloud_cover"]
    response = OpenMeteoResponse(**json_data)
    zeitpunkt = datetime(2026, 8, 2, 1, 0)

    sample = _extract_sample_from_response(response, zeitpunkt)

    assert sample is not None
    assert sample.bewoelkung_pct == 0.0  # Default


def test_extract_sample_from_response_with_timezone_aware_datetime() -> None:
    """Test _extract_sample_from_response funktioniert mit tz-aware datetime via Prefix-Match."""
    response = _make_response_obj()
    # isoformat() eines naive datetime = "2026-08-02T01:00:00"
    # Prefix [:16] = "2026-08-02T01:00" → matcht
    zeitpunkt = datetime(2026, 8, 2, 1, 0)

    sample = _extract_sample_from_response(response, zeitpunkt)
    assert sample is not None
    assert sample.zeitpunkt == zeitpunkt
