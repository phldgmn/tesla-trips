"""Öffentliche Wetter-Abfrage-Funktionen.

Implementiert:
- `fetch_weather_for_route`: Abruf von Wetterdaten entlang einer Route mit Batching
"""

from collections.abc import Sequence

from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import WeatherProvider


async def fetch_weather_for_route(
    provider: WeatherProvider,
    route_queries: Sequence[WeatherQuery],
    batch_size: int = 20,
) -> list[WeatherSample]:
    """Abruf von Wetterdaten entlang einer Route mit automatischem Batching.

    Args:
        provider: Der zu verwendende Wetterprovider (in Tests: Fake).
        route_queries: Liste von Abfragen (Koordinate + ETA).
        batch_size: Maximale Anzahl Abfragen pro API-Call (Open-Meteo empfiehlt <=50).

    Returns:
        Liste von WeatherSample in gleicher Reihenfolge wie route_queries.
    """
    if not route_queries:
        return []

    results: list[WeatherSample | None] = [None] * len(route_queries)
    remaining = list(route_queries)

    while remaining:
        batch = remaining[:batch_size]
        remaining = remaining[batch_size:]

        samples = await provider.fetch_weather(batch)
        for sample in samples:
            for idx, query in enumerate(route_queries):
                if query.koordinate == sample.koordinate and query.zeitpunkt == sample.zeitpunkt:
                    if results[idx] is None:
                        results[idx] = sample
                    break

    return [r for r in results if r is not None]
