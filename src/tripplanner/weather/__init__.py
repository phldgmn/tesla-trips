"""Public interface for the `weather` module.

Re-exports:
- `WeatherProvider`: Protocol for weather data providers
- `fetch_weather_for_route`: fetches weather along a route
- `OpenMeteoClient` / `OpenMeteoProvider`: Open-Meteo Forecast API (global, keyless)
- `MetNorwayProvider`: MET Norway Locationforecast 2.0 API (global, keyless)
- `OpenWeatherProvider`: OpenWeather forecast API (global, API key)
- `SmhiProvider`: SMHI meteorological forecasts API (Sweden only, keyless)
- `DmiProvider`: DMI HARMONIE DINI forecast EDR API (Denmark only, keyless)
- `LoadBalancedWeatherProvider` / `WeatherProviderEntry`: country-aware,
  load-balanced composite with automatic failover
- `detect_country`: best-effort DE/DK/SE coordinate classification
"""

from tripplanner.weather.coverage import detect_country
from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample
from tripplanner.weather.providers import (
    DmiProvider,
    FakeWeatherProvider,
    LoadBalancedWeatherProvider,
    MetNorwayProvider,
    OpenMeteoClient,
    OpenMeteoProvider,
    OpenWeatherProvider,
    SmhiProvider,
    WeatherProvider,
    WeatherProviderEntry,
)
from tripplanner.weather.weather import fetch_weather_for_route

__all__ = [
    "DmiProvider",
    "FakeWeatherProvider",
    "LoadBalancedWeatherProvider",
    "MetNorwayProvider",
    "OpenMeteoClient",
    "OpenMeteoProvider",
    "OpenMeteoResponse",
    "OpenWeatherProvider",
    "SmhiProvider",
    "WeatherProvider",
    "WeatherProviderEntry",
    "WeatherQuery",
    "WeatherSample",
    "detect_country",
    "fetch_weather_for_route",
]
