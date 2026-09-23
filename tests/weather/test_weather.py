"""Unit-Tests für das weather-Modul: fetch_weather_for_route und Batching.

Testfälle gemäß Plan Abschnitt 6:
1. Test mit 3 Queries (gleiche Koordinate, 3 Zeiten) und Interpolation
2. Test mit doppelter Koordinate (nur 1 API-Call)
3. Test mit 100 Queries (50 gleiche + 50 andere Koordinaten, batch_size=20)
"""

from datetime import datetime, timedelta

import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample
from tripplanner.weather.providers import (
    FakeWeatherProvider,
    _extract_sample_from_response,
)
from tripplanner.weather.weather import fetch_weather_for_route

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


@pytest.mark.asyncio
async def test_fetch_weather_for_route_single_coord_three_times() -> None:
    """Test fetch_weather_for_route mit 3 Queries (gleiche Koordinate, 3 Zeiten)."""
    # Given: Fake-Provider mit vordefinierten Wetterdaten
    now = datetime(2026, 8, 2, 10, 0)
    queries = [
        WeatherQuery(coordinate=BERLIN, zeitpunkt=now),
        WeatherQuery(coordinate=BERLIN, zeitpunkt=now + timedelta(hours=1)),
        WeatherQuery(coordinate=BERLIN, zeitpunkt=now + timedelta(hours=2)),
    ]

    samples = [
        WeatherSample(
            coordinate=BERLIN,
            zeitpunkt=now,
            temperatur_c=20.0,
            windgeschwindigkeit_ms=5.0,
            windrichtung_deg=180.0,
            niederschlag_mm=0.0,
            schneefall_cm=0.0,
            luftdruck_hpa=1013.25,
            luftfeuchtigkeit_pct=60.0,
            globalstrahlung_wm2=400.0,
            bewoelkung_pct=20.0,
        ),
        WeatherSample(
            coordinate=BERLIN,
            zeitpunkt=now + timedelta(hours=1),
            temperatur_c=21.0,
            windgeschwindigkeit_ms=6.0,
            windrichtung_deg=185.0,
            niederschlag_mm=0.0,
            schneefall_cm=0.0,
            luftdruck_hpa=1013.0,
            luftfeuchtigkeit_pct=58.0,
            globalstrahlung_wm2=420.0,
            bewoelkung_pct=18.0,
        ),
        WeatherSample(
            coordinate=BERLIN,
            zeitpunkt=now + timedelta(hours=2),
            temperatur_c=22.0,
            windgeschwindigkeit_ms=7.0,
            windrichtung_deg=190.0,
            niederschlag_mm=0.0,
            schneefall_cm=0.0,
            luftdruck_hpa=1012.8,
            luftfeuchtigkeit_pct=55.0,
            globalstrahlung_wm2=440.0,
            bewoelkung_pct=15.0,
        ),
    ]

    provider = FakeWeatherProvider(samples)
    # When: fetch_weather_for_route aufrufen
    results = await fetch_weather_for_route(provider, queries, batch_size=20)

    # Then: Länge Ergebnis = 3, Werte korrekt
    assert len(results) == 3
    assert results[0].temperatur_c == 20.0
    assert results[1].temperatur_c == 21.0
    assert results[2].temperatur_c == 22.0
    assert results[0].windgeschwindigkeit_ms == 5.0
    assert results[1].windgeschwindigkeit_ms == 6.0
    assert results[2].windgeschwindigkeit_ms == 7.0


@pytest.mark.asyncio
async def test_fetch_weather_for_route_duplicate_coords_single_api_call() -> None:
    """Test fetch_weather_for_route mit 2 Queries (doppelter Standort) -> nur 1 API-Call."""
    # Given: Fake-Provider mit Zähler für API-Calls
    call_count = 0

    class CountingFakeProvider:
        async def fetch_weather(
            self,
            queries: list[WeatherQuery],
        ) -> list[WeatherSample]:
            nonlocal call_count
            call_count += 1
            return [
                WeatherSample(
                    coordinate=q.coordinate,
                    zeitpunkt=q.zeitpunkt,
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
                for q in queries
            ]

    now = datetime(2026, 8, 2, 10, 0)
    queries = [
        WeatherQuery(coordinate=BERLIN, zeitpunkt=now),
        WeatherQuery(coordinate=BERLIN, zeitpunkt=now + timedelta(hours=1)),
    ]

    provider = CountingFakeProvider()
    # When: fetch_weather_for_route aufrufen
    results = await fetch_weather_for_route(provider, queries, batch_size=20)

    # Then: Nur 1 API-Call (Caching via Koordinatengruppierung funktioniert)
    assert len(results) == 2
    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_weather_for_route_multiple_coords_batching() -> None:
    """Test fetch_weather_for_route mit 100 Queries (50 gleiche + 50
    andere Koordinaten, batch_size=20)."""
    # Given: Fake-Provider mit Zähler für API-Calls
    call_count = 0

    class CountingFakeProvider:
        async def fetch_weather(
            self,
            queries: list[WeatherQuery],
        ) -> list[WeatherSample]:
            nonlocal call_count
            call_count += 1
            return [
                WeatherSample(
                    coordinate=q.coordinate,
                    zeitpunkt=q.zeitpunkt,
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
                for q in queries
            ]

    now = datetime(2026, 8, 2, 10, 0)
    # 50 Queries für Berlin
    berlin_queries = [
        WeatherQuery(coordinate=BERLIN, zeitpunkt=now + timedelta(hours=i)) for i in range(50)
    ]
    # 50 Queries für Hamburg
    hamburg_queries = [
        WeatherQuery(coordinate=HAMBURG, zeitpunkt=now + timedelta(hours=i)) for i in range(50)
    ]
    queries = berlin_queries + hamburg_queries

    provider = CountingFakeProvider()
    # When: fetch_weather_for_route aufrufen mit batch_size=20
    results = await fetch_weather_for_route(provider, queries, batch_size=20)

    # Then: Mindestens 4 API-Calls (50/20 + 50/20 = 2.5 + 2.5 -> 3 Calls
    # pro Koordinate-Gruppe = min. 4 Calls)
    assert len(results) == 100
    # Pro Koordinate-Gruppe: ceil(50/20) = 3 Calls
    # Gesamt: 3 + 3 = 6 Calls (da Batching pro Koordinate stattfindet)
    assert call_count >= 4


@pytest.mark.asyncio
async def test_fetch_weather_for_route_empty_queries() -> None:
    """Test fetch_weather_for_route mit leeren queries."""
    # Given: Leere Liste von Queries
    queries: list[WeatherQuery] = []

    provider = FakeWeatherProvider()
    # When: fetch_weather_for_route aufrufen
    results = await fetch_weather_for_route(provider, queries, batch_size=20)

    # Then: Leere Ergebnisliste
    assert results == []


def test_extract_sample_from_response() -> None:
    """Test _extract_sample_from_response mit OpenMeteoResponse."""
    # Given: OpenMeteoResponse mit hourly-Daten
    response = OpenMeteoResponse(
        latitude=52.52,
        longitude=13.405,
        timezone="Europe/Berlin",
        timezone_abbreviation="CET",
        elevation=38.0,
        hourly={
            "time": ["2026-08-02T00:00", "2026-08-02T01:00", "2026-08-02T02:00"],
            "temperature_2m": [14.5, 13.8, 13.2],
            "wind_speed_10m": [12.5, 11.8, 10.5],
            "wind_direction_10m": [245, 248, 250],
            "precipitation": [0.0, 0.0, 0.0],
            "snowfall": [0.0, 0.0, 0.0],
            "surface_pressure": [1013.2, 1013.5, 1013.8],
            "relative_humidity_2m": [72, 74, 76],
            "shortwave_radiation": [0, 0, 0],
            "cloud_cover": [45, 50, 55],
        },
        hourly_units={
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
    )
    zeitpunkt = datetime.fromisoformat("2026-08-02T01:00:00")
    # When: _extract_sample_from_response aufrufen
    sample = _extract_sample_from_response(response, zeitpunkt)

    # Then: WeatherSample mit korrekten Werten
    assert sample is not None
    assert sample.coordinate == (52.52, 13.405)
    assert sample.zeitpunkt == zeitpunkt
    assert sample.temperatur_c == 13.8
    # wind_speed_10m: 11.8 km/h → 11.8 * 1000/3600 ≈ 3.28 m/s
    assert sample.windgeschwindigkeit_ms == pytest.approx(11.8 * 1000 / 3600, abs=0.01)
    assert sample.windrichtung_deg == 248
    assert sample.niederschlag_mm == 0.0
    assert sample.schneefall_cm == 0.0
    assert sample.luftdruck_hpa == 1013.5
    assert sample.luftfeuchtigkeit_pct == 74
    assert sample.globalstrahlung_wm2 == 0.0
    assert sample.bewoelkung_pct == 50


def test_extract_sample_from_response_missing_time() -> None:
    """Test _extract_sample_from_response mit Zeitpunkt außerhalb des Zeitraums."""
    # Given: OpenMeteoResponse mit hourly-Daten für 00:00, 01:00, 02:00
    response = OpenMeteoResponse(
        latitude=52.52,
        longitude=13.405,
        timezone="Europe/Berlin",
        timezone_abbreviation="CET",
        elevation=38.0,
        hourly={
            "time": ["2026-08-02T00:00", "2026-08-02T01:00", "2026-08-02T02:00"],
            "temperature_2m": [14.5, 13.8, 13.2],
            "wind_speed_10m": [12.5, 11.8, 10.5],
            "wind_direction_10m": [245, 248, 250],
            "precipitation": [0.0, 0.0, 0.0],
            "snowfall": [0.0, 0.0, 0.0],
            "surface_pressure": [1013.2, 1013.5, 1013.8],
            "relative_humidity_2m": [72, 74, 76],
            "shortwave_radiation": [0, 0, 0],
            "cloud_cover": [45, 50, 55],
        },
        hourly_units={
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
    )

    zeitpunkt = datetime(2026, 8, 2, 3, 0)  # Außerhalb des Zeitraums
    # When: _extract_sample_from_response aufrufen
    sample = _extract_sample_from_response(response, zeitpunkt)

    # Then: None zurückgeben
    assert sample is None
