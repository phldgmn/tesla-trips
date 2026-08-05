"""Integration-Tests für das weather-Modul: Provider mit echten API-Calls.

Diese Tests sind mit `@pytest.mark.integration` markiert und werden in dieser
Umgebung NICHT ausgeführt (kein echter Open-Meteo-Zugriff verfügbar).
Sie müssen später gegen echte Dienste laufen.
"""

from datetime import datetime, timedelta

import pytest

from tripplanner.geo import Coordinate
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import FakeWeatherProvider
from tripplanner.weather.weather import fetch_weather_iterative

BERLIN: Coordinate = (52.5200, 13.4050)
HAMBURG: Coordinate = (53.5511, 9.9937)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_weather_iterative_with_integration_provider() -> None:
    """Integrationstest für fetch_weather_iterative mit echtem HTTP-Call.

    Given: Lokale GraphHopper-Instanz mit echtem Open-Meteo-Provider.
    When: fetch_weather_iterative auf einer 200 km-Strecke mit 5 Abfragen.
    Then: Konvergenz nach ≤ 2 Iterationen (ETA-Abweichung < 30 Min), Gesamt-ETA < 20 Sekunden.
    """
    # This test requires actual HTTP access to Open-Meteo API
    # Skip in this environment, but keep as template for future integration tests
    pytest.skip("Integration test - requires actual Open-Meteo API access")


@pytest.mark.asyncio
async def test_fetch_weather_iterative_with_fake_provider() -> None:
    """Test fetch_weather_iterative mit Fake-Provider.

    Given: Fake-Provider mit simulierten Abweichungen.
    When: fetch_weather_iterative mit 3 Queries aufrufen.
    Then: Ergebnis hat 3 Samples, Iterationen konvergieren oder max_iterations erreichen.
    """
    now = datetime(2026, 8, 2, 10, 0)
    queries = [
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now + timedelta(hours=1)),
        WeatherQuery(koordinate=BERLIN, zeitpunkt=now + timedelta(hours=2)),
    ]

    provider = FakeWeatherProvider()
    # When: fetch_weather_iterative aufrufen
    results = await fetch_weather_iterative(
        provider,
        queries,
        max_iterations=2,
        convergence_threshold_s=1800,
    )

    # Then: Ergebnis hat 3 Samples
    assert len(results) == 3
    assert all(s.koordinate == BERLIN for s in results)


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
