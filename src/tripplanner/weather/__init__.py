"""Public interface for the `weather` module.

Re-exports:
- `WeatherProvider`: Protocol for weather data providers
- `fetch_weather_for_route`: fetches weather along a route
- `fetch_weather_by_detail`: detail-level-aware weather fetching
- `OpenMeteoClient` / `OpenMeteoProvider`: Open-Meteo Forecast API (global, keyless)
- `MetNorwayProvider`: MET Norway Locationforecast 2.0 API (global, keyless)
- `OpenWeatherProvider`: OpenWeather forecast API (global, API key,
  client-side rate-limited below the free-tier 60 requests/minute cap)
- `SlidingWindowRateLimiter`: generic async rolling-window rate limiter
- `SmhiProvider`: SMHI meteorological forecasts API (Sweden only, keyless)
- `DmiProvider`: DMI HARMONIE DINI forecast EDR API (Denmark only, keyless)
- `LoadBalancedWeatherProvider` / `WeatherProviderEntry`: country-aware,
  load-balanced composite with automatic failover
- `detect_country`: best-effort DE/DK/SE coordinate classification
- `WeatherDetailLevel`: weather granularity control ("off" | "low" | "medium" | "high")
"""

from tripplanner.weather.coverage import detect_country
from tripplanner.weather.models import (
    OpenMeteoResponse,
    WeatherDetailLevel,
    WeatherQuery,
    WeatherSample,
)
from tripplanner.weather.providers import (
    DmiProvider,
    FakeWeatherProvider,
    LoadBalancedWeatherProvider,
    MetNorwayProvider,
    OpenMeteoClient,
    OpenMeteoProvider,
    OpenWeatherProvider,
    SlidingWindowRateLimiter,
    SmhiProvider,
    WeatherProvider,
    WeatherProviderEntry,
)
from tripplanner.weather.weather import fetch_weather_by_detail, fetch_weather_for_route

__all__ = [
    "DmiProvider",
    "FakeWeatherProvider",
    "LoadBalancedWeatherProvider",
    "MetNorwayProvider",
    "OpenMeteoClient",
    "OpenMeteoProvider",
    "OpenMeteoResponse",
    "OpenWeatherProvider",
    "SlidingWindowRateLimiter",
    "SmhiProvider",
    "WeatherDetailLevel",
    "WeatherProvider",
    "WeatherProviderEntry",
    "WeatherQuery",
    "WeatherSample",
    "detect_country",
    "fetch_weather_by_detail",
    "fetch_weather_for_route",
]
