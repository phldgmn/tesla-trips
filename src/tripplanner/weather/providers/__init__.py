"""Re-exports from weather provider modules.

This file ensures that every name importable from the old flat
``tripplanner.weather.providers`` module remains importable through
the new package structure.
"""

from tripplanner.weather.providers.caching import (
    _cache_deserialize,
    _cache_key,
    _cache_str_key,
)
from tripplanner.weather.providers.composite import (
    LoadBalancedWeatherProvider,
    WeatherProviderEntry,
)
from tripplanner.weather.providers.dmi import (
    DmiProvider,
)
from tripplanner.weather.providers.metno import (
    MetNorwayProvider,
)
from tripplanner.weather.providers.openmeteo import (
    _KMH_TO_MPS,
    HOURLY_PARAMS,
    OpenMeteoClient,
    OpenMeteoProvider,
    _extract_sample_from_response,
)
from tripplanner.weather.providers.openweather import (
    OpenWeatherProvider,
    SlidingWindowRateLimiter,
)
from tripplanner.weather.providers.protocols import (
    FakeWeatherProvider,
    WeatherProvider,
)
from tripplanner.weather.providers.smhi import (
    SmhiProvider,
)

__all__ = [
    "FakeWeatherProvider",
    "DmiProvider",
    "HOURLY_PARAMS",
    "LoadBalancedWeatherProvider",
    "MetNorwayProvider",
    "OpenMeteoClient",
    "OpenMeteoProvider",
    "OpenWeatherProvider",
    "SlidingWindowRateLimiter",
    "SmhiProvider",
    "WeatherProvider",
    "WeatherProviderEntry",
    "_KMH_TO_MPS",
    "_cache_deserialize",
    "_cache_key",
    "_cache_str_key",
    "_extract_sample_from_response",
]
