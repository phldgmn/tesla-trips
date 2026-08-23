"""Öffentliche Schnittstelle für das weather-Modul.

Re-exportiert:
- `WeatherProvider`: Protocol für Wetter-Datenprovider
- `fetch_weather_for_route`: Abruf von Wetterdaten entlang einer Route
- `OpenMeteoClient`: HTTP-Client für Open-Meteo (optional für DI in Tests)
- `OpenMeteoProvider`: Implementierung des Providers über Open-Meteo
"""

from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample
from tripplanner.weather.providers import (
    FakeWeatherProvider,
    OpenMeteoClient,
    OpenMeteoProvider,
    WeatherProvider,
)
from tripplanner.weather.weather import fetch_weather_for_route

__all__ = [
    "FakeWeatherProvider",
    "OpenMeteoClient",
    "OpenMeteoProvider",
    "OpenMeteoResponse",
    "WeatherProvider",
    "WeatherQuery",
    "WeatherSample",
    "fetch_weather_for_route",
]
