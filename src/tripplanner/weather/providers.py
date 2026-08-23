"""Wetter-Provider und HTTP-Client für Open-Meteo Forecast API.

Implementiert:
- `OpenMeteoClient`: HTTP-Client für die Open-Meteo Forecast API
- `WeatherProvider`: Protocol für Wetter-Datenprovider
- `OpenMeteoProvider`: Implementierung des Providers über Open-Meteo
- `FakeWeatherProvider`: Fake-Provider für Unit-Tests
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

import httpx

from tripplanner.geo import Coordinate
from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample

# Offizielle Open-Meteo hourly-Parameter gemäß Plan
HOURLY_PARAMS = [
    "temperature_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "snowfall",
    "surface_pressure",
    "relative_humidity_2m",
    "shortwave_radiation",
    "cloud_cover",
]

# Umrechnungsfaktor km/h → m/s
_KMH_TO_MPS = 1000.0 / 3600.0


class WeatherProvider(Protocol):
    """Interface für Wetter-Datenprovider (kann durch Fake ersetzt werden)."""

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Abruf von Wetterdaten für mehrere Abfragepunkte.

        Args:
            queries: Liste von Wetterabfragen (Koordinate + Zeitpunkt).

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
        """Neuabfrage bereits abgefragter Punkte mit aktualisiertem Zeitpunkt.

        Diese Methode ist zentral für die iterative Zeit-/Wetterauflösung.
        Die Implementierung darf intern Caching nutzen (z. B. auf `koordinate` + `zeitpunkt`-Tupel),
        um unnötige API-Calls zu vermeiden.

        Args:
            original_queries: Die ursprünglichen Abfragen (unverändert).
            updated_queries: Die aktualisierten Abfragen mit neuen Zeitpunkten,
                             gleiche Koordinaten wie original_queries.

        Returns:
            Liste von WeatherSample für die updated_queries.
        """
        ...


class OpenMeteoClient:
    """HTTP-Client für Open-Meteo Forecast API."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    TIMEOUT_S = 15.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Initialisiert den Client.

        Args:
            client: Optionaler httpx.AsyncClient. Wenn None, wird ein neuer Client erstellt.
        """
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)

    async def fetch_forecast(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[OpenMeteoResponse]:
        """Abruf von Wetterdaten für mehrere Standorte + Zeitpunkte.

        Args:
            queries: Liste von Wetterabfragen (Koordinate + Zeitpunkt).

        Returns:
            Liste von OpenMeteoResponse, in gleicher Reihenfolge wie queries.
            Bei fehlenden Daten für einen Punkt wird None zurückgegeben.
        """
        if not queries:
            return []

        # Gruppieren nach Koordinate (doppelte Standorte sparen API-Calls)
        coords: dict[tuple[float, float], list[tuple[int, WeatherQuery]]] = {}
        for idx, q in enumerate(queries):
            key = (q.koordinate[0], q.koordinate[1])
            coords.setdefault(key, []).append((idx, q))

        results: list[OpenMeteoResponse | None] = [None] * len(queries)

        for (lat, lon), entries in coords.items():
            # Zeitbereich: earliest bis latest Zeitpunkt für diesen Standort
            times = [q.zeitpunkt.isoformat() for _, q in entries]
            time_min = min(times)
            time_max = max(times)

            url = (
                f"{self.BASE_URL}?"
                f"latitude={lat}&longitude={lon}&"
                f"hourly={','.join(HOURLY_PARAMS)}&"
                f"start_date={time_min[:10]}&"
                f"end_date={time_max[:10]}"
            )

            resp = await self._client.get(url)
            resp.raise_for_status()
            data = OpenMeteoResponse(**resp.json())

            # Zeitindex lookup für jeden Query-Punkt
            time_to_idx = {t: i for i, t in enumerate(data.hourly["time"])}
            for idx, q in entries:
                # Snap to the hour — Open-Meteo returns hourly data (:00 only)
                # Strip timezone suffix to match Open-Meteo's "YYYY-MM-DDTHH:MM" format
                snapped = q.zeitpunkt.replace(minute=0, second=0, microsecond=0)
                time_idx = time_to_idx.get(snapped.isoformat(timespec="minutes").split("+")[0])
                if time_idx is not None:
                    results[idx] = data

        return [r for r in results if r is not None]

    async def close(self) -> None:
        """Schließt den HTTP-Client."""
        await self._client.aclose()


class OpenMeteoProvider:
    """Wetter-Provider über Open-Meteo Forecast API.

    Nutzt intern `OpenMeteoClient` für HTTP-Calls und implementiert
    Caching für `refetch_weather` (Dictionary `CacheKey = Tuple[Coordinate, datetime]`).
    """

    def __init__(self, client: OpenMeteoClient | None = None) -> None:
        """Initialisiert den Provider.

        Args:
            client: Optionaler OpenMeteoClient. Wenn None, wird ein neuer Client erstellt.
        """
        self._client = client or OpenMeteoClient()
        self._cache: dict[tuple[Coordinate, datetime], WeatherSample] = {}

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Abruf von Wetterdaten für mehrere Abfragepunkte.

        Args:
            queries: Liste von Wetterabfragen (Koordinate + Zeitpunkt).

        Returns:
            Liste von Wetterdaten, in gleicher Reihenfolge wie queries.
        """
        # Prüfen, ob alle Queries im Cache liegen
        uncached_queries: list[WeatherQuery] = []
        results: list[WeatherSample | None] = [None] * len(queries)

        for idx, query in enumerate(queries):
            key = (query.koordinate, query.zeitpunkt)
            if key in self._cache:
                results[idx] = self._cache[key]
            else:
                uncached_queries.append(query)

        if uncached_queries:
            # API-Calls für alle Koordinaten in einem Batch
            responses = await self._client.fetch_forecast(uncached_queries)

            for resp in responses:
                # Extrahiere alle Samples aus der Response
                # Jede Response enthält data für eine Koordinate
                # Wir brauchen die Indexe aller queries für diese Koordinate
                _COORD_TOLERANCE = 0.1  # ~11 km at equator
                for _idx, query in enumerate(uncached_queries):
                    # Open-Meteo rounds coordinates (13.405 -> 13.4), use tolerance
                    q_lat, q_lon = query.koordinate
                    r_lat, r_lon = resp.latitude, resp.longitude
                    # Match if within 0.1 degrees (about 11km at equator)
                    if (
                        abs(q_lat - r_lat) < _COORD_TOLERANCE
                        and abs(q_lon - r_lon) < _COORD_TOLERANCE
                    ):
                        sample = _extract_sample_from_response(resp, query.zeitpunkt)
                        if sample is not None:
                            key = (query.koordinate, query.zeitpunkt)
                            self._cache[key] = sample
                            # Finde den korrekten Index in results
                            for i, q in enumerate(queries):
                                if (
                                    q.koordinate == query.koordinate
                                    and q.zeitpunkt == query.zeitpunkt
                                ):
                                    results[i] = sample
                                    break

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Neuabfrage bereits abgefragter Punkte mit aktualisiertem Zeitpunkt.

        Args:
            original_queries: Die ursprünglichen Abfragen (unverändert).
            updated_queries: Die aktualisierten Abfragen mit neuen Zeitpunkten,
                             gleiche Koordinaten wie original_queries.

        Returns:
            Liste von WeatherSample für die updated_queries.
        """
        # Prüfen, ob die neuen Queries im Cache liegen
        results: list[WeatherSample | None] = [None] * len(updated_queries)

        for idx, query in enumerate(updated_queries):
            key = (query.koordinate, query.zeitpunkt)
            if key in self._cache:
                results[idx] = self._cache[key]

        # Für nicht-gecachte Queries neu abfragen
        uncached = [q for q in updated_queries if results[updated_queries.index(q)] is None]

        if uncached:
            samples = await self.fetch_weather(uncached)
            for sample in samples:
                key = (sample.koordinate, sample.zeitpunkt)
                self._cache[key] = sample
                for idx, query in enumerate(updated_queries):
                    if (
                        query.koordinate == sample.koordinate
                        and query.zeitpunkt == sample.zeitpunkt
                    ):
                        results[idx] = sample
                        break

        return [r for r in results if r is not None]

    async def close(self) -> None:
        """Schließt den HTTP-Client."""
        await self._client.close()


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
                koordinate=q.koordinate,
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


def _extract_sample_from_response(
    response: OpenMeteoResponse,
    zeitpunkt: datetime,
) -> WeatherSample | None:
    """Extrahiert ein WeatherSample aus einer OpenMeteoResponse.

    Args:
        response: OpenMeteoResponse mit hourly-Daten.
        zeitpunkt: Gewünschter Zeitpunkt.

    Returns:
        WeatherSample oder None, wenn der Zeitpunkt nicht gefunden wird.
    """
    time_to_idx = {t: i for i, t in enumerate(response.hourly["time"])}
    # Open-Meteo returns hourly data on the hour (:00). Snap query time to
    # the nearest hour and strip timezone suffix to match Open-Meteo format.
    snapped = zeitpunkt.replace(minute=0, second=0, microsecond=0)
    iso_hour = snapped.isoformat(timespec="minutes").split("+")[0]
    time_idx = None
    if iso_hour in time_to_idx:
        time_idx = time_to_idx[iso_hour]

    if time_idx is None:
        return None

    hourly = response.hourly

    def get_value(key: str, default: float = 0.0) -> float:
        """Hilfsfunktion zum Extrahieren eines Werts mit Typkonvertierung."""
        value = hourly.get(key, [default] * len(response.hourly.get("time", [])))[time_idx]
        if value is None:
            return default
        return float(value)

    # Windgeschwindigkeit von km/h in m/s umrechnen
    wind_speed_kmh = get_value("wind_speed_10m", 0.0)
    wind_speed_ms = wind_speed_kmh * _KMH_TO_MPS

    return WeatherSample(
        koordinate=(response.latitude, response.longitude),
        zeitpunkt=zeitpunkt,
        temperatur_c=get_value("temperature_2m", 0.0),
        windgeschwindigkeit_ms=wind_speed_ms,
        windrichtung_deg=get_value("wind_direction_10m", 0.0),
        niederschlag_mm=get_value("precipitation", 0.0),
        schneefall_cm=get_value("snowfall", 0.0),
        luftdruck_hpa=get_value("surface_pressure", 1013.25),
        luftfeuchtigkeit_pct=get_value("relative_humidity_2m", 0.0),
        globalstrahlung_wm2=get_value("shortwave_radiation", 0.0),
        bewoelkung_pct=get_value("cloud_cover", 0.0),
    )
