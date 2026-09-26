"""Protocol and fake for weather providers."""

from collections.abc import Sequence
from typing import Protocol

from tripplanner.weather.models import WeatherQuery, WeatherSample


class WeatherProvider(Protocol):
    """Interface for weather data provider (can be replaced with Fake)."""

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Fetch weather data for multiple query points.

        Args:
            queries: List of weather queries (coordinate + timestamp).

        Returns:
            List of weather data, in the same order as queries.
            When data is missing for a point, an empty list or
            None is returned, this is indicated by a sentinel (e.g. None)
        """
        ...

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Refetch already-fetched points with updated timestamp.

        This method is central to the iterative ETA/weather resolution.
        The implementation may use internal caching (e.g. on (coordinate + timestamp) tuples),
        to avoid unnecessary API calls.

        Args:
            original_queries: The original queries (unchanged).
            updated_queries: The updated queries with new timestamps,
                             gleiche Koordinaten wie original_queries.

        Returns:
            List of WeatherSample for the updated queries.
        """
        ...


class FakeWeatherProvider:
    """Fake provider for unit tests.

    Returns predefined weather data without making real API calls.
    """

    def __init__(self, samples: list[WeatherSample] | None = None) -> None:
        """Initialize the fake provider.

        Args:
            samples: List of WeatherSample to return.
                     Wenn None, werden Dummy-data generiert.
        """
        self._samples = samples or []
        self.fetch_weather_calls: list[Sequence[WeatherQuery]] = []
        self.refetch_weather_calls: list[tuple[Sequence[WeatherQuery], Sequence[WeatherQuery]]] = []

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Returns predefined weather data."""
        self.fetch_weather_calls.append(queries)

        if self._samples:
            return self._samples[: len(queries)]
        # Dummy-data generieren
        return [
            WeatherSample(
                coordinate=q.coordinate,
                timestamp=q.timestamp,
                temperature_c=20.0,
                wind_speed_ms=5.0,
                wind_direction_deg=180.0,
                precipitation_mm=0.0,
                snowfall_cm=0.0,
                pressure_hpa=1013.25,
                humidity_pct=60.0,
                solar_radiation_wm2=400.0,
                cloudiness_pct=20.0,
            )
            for q in queries
        ]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Returns predefined weather data."""
        self.refetch_weather_calls.append((original_queries, updated_queries))
        return await self.fetch_weather(updated_queries)

    def set_samples(self, samples: list[WeatherSample]) -> None:
        """Set the weather data to return."""
        self._samples = samples
