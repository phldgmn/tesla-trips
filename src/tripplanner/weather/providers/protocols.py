"""Protocol and fake for weather providers."""

from collections.abc import Sequence
from typing import Protocol

from tripplanner.weather.models import WeatherQuery, WeatherSample


class WeatherProvider(Protocol):
    """Interface für Wetter-Datenprovider (kann durch Fake ersetzt werden)."""

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Abruf von Wetterdaten für mehrere Abfragepunkte.

        Args:
            queries: Liste von Wetterabfragen (Koordinate + timestamp).

        Returns:
            Liste von Wetterdaten, in gleicher Reihenfolge wie queries.
            Wird bei fehlenden Daten für einen Punkt eine leere Liste oder
            None zurückgegeben, wird dies durch ein Sentinel (z. B. None)
        """
        ...

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Neuabfrage bereits abgefragter Punkte mit aktualisiertem timestamp.

        Diese Methode ist zentral für die iterative time-/Wetterauflösung.
        Die Implementierung darf intern Caching nutzen (z. B. auf `coordinate` + `timestamp`-Tupel),
        um unnötige API-Calls zu vermeiden.

        Args:
            original_queries: Die ursprünglichen Abfragen (unverändert).
            updated_queries: Die aktualisierten Abfragen mit neuen Zeitpunkten,
                             gleiche Koordinaten wie original_queries.

        Returns:
            Liste von WeatherSample für die updated_queries.
        """
        ...


class FakeWeatherProvider:
    """Fake-Provider für Unit-Tests.

    Gibt vordefinierte Wetterdaten zurück, ohne echte API-Calls.
    """

    def __init__(self, samples: list[WeatherSample] | None = None) -> None:
        """Initialisiert den Fake-Provider.

        Args:
            samples: Liste von WeatherSample, die zurückgegeben werden sollen.
                     Wenn None, werden Dummy-Daten generiert.
        """
        self._samples = samples or []
        self.fetch_weather_calls: list[Sequence[WeatherQuery]] = []
        self.refetch_weather_calls: list[tuple[Sequence[WeatherQuery], Sequence[WeatherQuery]]] = []

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Gibt vordefinierte Wetterdaten zurück."""
        self.fetch_weather_calls.append(queries)

        if self._samples:
            return self._samples[: len(queries)]
        # Dummy-Daten generieren
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
        """Gibt vordefinierte Wetterdaten zurück."""
        self.refetch_weather_calls.append((original_queries, updated_queries))
        return await self.fetch_weather(updated_queries)

    def set_samples(self, samples: list[WeatherSample]) -> None:
        """Setzt die zurückzugebenden Wetterdaten."""
        self._samples = samples
