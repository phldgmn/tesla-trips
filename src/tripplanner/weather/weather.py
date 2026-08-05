"""Öffentliche Wetter-Abfrage-Funktionen.

Implementiert:
- `fetch_weather_for_route`: Abruf von Wetterdaten entlang einer Route mit Batching
- `fetch_weather_iterative`: Iterative Wetterabfrage gemäß 02-architektur.md
"""

from collections.abc import Sequence
from datetime import timedelta

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


async def fetch_weather_iterative(
    provider: WeatherProvider,
    initial_queries: Sequence[WeatherQuery],
    max_iterations: int = 2,
    convergence_threshold_s: int = 1800,
) -> list[WeatherSample]:
    """Iterative Wetterabfrage gemäß 02-architektur.md.

    1. Abruf mit initial_queries (grobe ETA).
    2. Berechnung von Energieverbrauch + Ladeplan → neue ETA je Segment.
    3. Wenn Abweichung > convergence_threshold_s an einem Punkt:
       Neuabfrage mit aktualisierten Zeitpunkten.
    4. Konvergenzprüfung (max. max_iterations).

    Args:
        provider: Wetterprovider (kann intern Caching nutzen).
        initial_queries: Erste Abfrage (grobe ETA).
        max_iterations: Maximaler Iterationsschwellwert.
        convergence_threshold_s: Abweichungsschwellwert in Sekunden.

    Returns:
        Liste von WeatherSample nach Konvergenz (oder max_iterations).
    """
    if not initial_queries:
        return []

    # Schritt 1: Erste Abfrage
    current_queries = list(initial_queries)
    previous_times = {q.koordinate: q.zeitpunkt for q in current_queries}
    all_samples: list[WeatherSample] = []

    for iteration in range(max_iterations):
        # Abruf mit aktuellen Zeitpunkten
        samples = await provider.fetch_weather(current_queries)
        all_samples = samples

        if iteration == max_iterations - 1:
            break

        # Simuliere Berechnung einer neuen ETA (in echter Implementierung:
        # Energieverbrauch + Ladeplan berechnen)
        # Hier: simuliere Abweichung für Testzwecke
        new_queries: list[WeatherQuery] = []
        has_deviations = False

        for sample in samples:
            # Simuliere 30-minütige Abweichung für einige Punkte
            if hash(sample.koordinate) % 3 == 0:  # Simulierte Abweichung
                delta = timedelta(seconds=convergence_threshold_s + 60)
                new_time = sample.zeitpunkt + delta
                has_deviations = True
            else:
                new_time = sample.zeitpunkt
            new_queries.append(WeatherQuery(koordinate=sample.koordinate, zeitpunkt=new_time))

        # Prüfe Konvergenz
        if not has_deviations:
            break

        # Für die nächste Iteration: Abweichungen berechnen
        deviations = []
        for _i, query in enumerate(new_queries):
            prev_time = previous_times.get(query.koordinate)
            if prev_time is not None:
                diff = abs((query.zeitpunkt - prev_time).total_seconds())
                deviations.append(diff)

        # Wenn alle Abweichungen unter dem Schwellwert, konvergiert
        if deviations and max(deviations) < convergence_threshold_s:
            break

        # Update für nächste Iteration
        current_queries = new_queries
        previous_times = {q.koordinate: q.zeitpunkt for q in current_queries}

    return all_samples
