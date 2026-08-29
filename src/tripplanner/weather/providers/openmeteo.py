"""Open-Meteo Forecast API client and provider."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import httpx

from tripplanner.cache import TTLCache
from tripplanner.geo import Coordinate
from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample
from tripplanner.weather.providers.caching import (
    _cache_deserialize,
    _cache_key,
    _cache_str_key,
)

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


_KMH_TO_MPS = 1000.0 / 3600.0


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
            key = (round(q.koordinate[0], 1), round(q.koordinate[1], 1))
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

    Nutzt intern ``OpenMeteoClient`` für HTTP-Calls und implementiert
    zweistufiges Caching: ein in-memory ``dict`` für schnelle Wiederholungen
    innerhalb eines Prozesses sowie einen persistenten ``TTLCache`` (SQLite),
    der auch über Prozessgrenzen hinweg wirksam ist.
    """

    def __init__(
        self,
        client: OpenMeteoClient | None = None,
        *,
        cache_ttl_seconds: float = 3600.0,
        cache_dir: Path | str | None = None,
    ) -> None:
        """Initialisiert den Provider.

        Args:
            client: Optionaler OpenMeteoClient. Wenn None, wird ein neuer Client erstellt.
            cache_ttl_seconds: TTL für den persistenten Cache in Sekunden
                (Standard: 3600 = 1 Stunde, entsprechend dem stündlichen
                Aktualisierungsrhythmus von Open-Meteo).
            cache_dir: Verzeichnis für die SQLite-Datenbank des persistenten
                Caches. Wenn ``None``, wird der Standardpfad
                ``<TRIPPLANNER_CACHE_DIR>/external_api_cache.sqlite`` verwendet.
        """
        self._client = client or OpenMeteoClient()
        self._cache: dict[tuple[Coordinate, datetime], WeatherSample] = {}
        self._persistent_cache: TTLCache | None = (
            TTLCache(
                namespace="weather_open_meteo",
                ttl_seconds=cache_ttl_seconds,
                db_path=cache_dir,
            )
            if cache_dir is not None
            else None
        )

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Abruf von Wetterdaten für mehrere Abfragepunkte.

        Uses grid-rounded + hour-snapped cache keys so that near-duplicate
        coordinates within 0.1° and timestamps in the same clock hour
        share cache entries.  Returned samples carry the *original* coordinate
        and timestamp from each query.

        Two-tier caching: the in-memory ``_cache`` dict is checked first for
        fast intra-process hits.  On miss the persistent ``_persistent_cache``
        (SQLite-backed TTLCache) is consulted before any HTTP call; a hit
        populates the in-memory layer as well.  On fresh fetch the parsed
        ``WeatherSample`` is stored in both caches.
        """
        uncached_queries: list[tuple[int, WeatherQuery]] = []
        results: list[WeatherSample | None] = [None] * len(queries)

        for idx, query in enumerate(queries):
            ck = _cache_key(query.koordinate, query.zeitpunkt)
            raw = self._cache.get(ck)
            if raw is None and self._persistent_cache is not None:
                # Persistent cache fallback
                str_key = _cache_str_key(query.koordinate, query.zeitpunkt)
                json_str = self._persistent_cache.get(str_key)
                if json_str is not None:
                    raw = _cache_deserialize(json_str)
                    self._cache[ck] = raw

            if raw is not None:
                results[idx] = raw.model_copy(
                    update={"koordinate": query.koordinate, "zeitpunkt": query.zeitpunkt}
                )
            else:
                uncached_queries.append((idx, query))

        if uncached_queries:
            # Extract queries for the HTTP call (preserve original indices in results)
            query_list: list[WeatherQuery] = [q for _, q in uncached_queries]
            responses = await self._client.fetch_forecast(query_list)

            # Build map: rounded_coord -> response (client returns one per unique coord)
            resp_by_coord: dict[tuple[float, float], OpenMeteoResponse] = {}
            for resp in responses:
                rc = (round(resp.latitude, 1), round(resp.longitude, 1))
                resp_by_coord[rc] = resp

            for orig_idx, query in uncached_queries:
                # Find matching response via rounded coord
                rc = _cache_key(query.koordinate, query.zeitpunkt)[0]
                resp_match = resp_by_coord.get(rc)
                if resp_match is None:
                    continue
                sample = _extract_sample_from_response(
                    resp_match, query.zeitpunkt, query.koordinate
                )
                if sample is not None:
                    ck = _cache_key(query.koordinate, query.zeitpunkt)
                    self._cache[ck] = sample
                    if self._persistent_cache is not None:
                        str_key = _cache_str_key(query.koordinate, query.zeitpunkt)
                        self._persistent_cache.set(str_key, sample.model_dump(mode="json"))
                    results[orig_idx] = sample

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Neuabfrage bereits abgefragter Punkte mit aktualisiertem Zeitpunkt.

        Uses grid-rounded + hour-snapped cache keys so that the convergence
        loop's re-runs hit cache for queries at the same rounded coordinate
        and within the same clock hour as the original query.
        """
        # Prüfen, ob die neuen Queries im Cache liegen (grid-rounded + hour-snapped)
        results: list[WeatherSample | None] = [None] * len(updated_queries)

        for idx, query in enumerate(updated_queries):
            ck = _cache_key(query.koordinate, query.zeitpunkt)
            raw = self._cache.get(ck)
            if raw is None and self._persistent_cache is not None:
                # Persistent cache fallback
                str_key = _cache_str_key(query.koordinate, query.zeitpunkt)
                json_str = self._persistent_cache.get(str_key)
                if json_str is not None:
                    raw = _cache_deserialize(json_str)
                    self._cache[ck] = raw
            if raw is not None:
                results[idx] = raw.model_copy(
                    update={"koordinate": query.koordinate, "zeitpunkt": query.zeitpunkt}
                )

        # Für nicht-gecachte Queries neu abfragen
        uncached = [q for q in updated_queries if results[updated_queries.index(q)] is None]

        if uncached:
            samples = await self.fetch_weather(uncached)
            for sample in samples:
                ck = _cache_key(sample.koordinate, sample.zeitpunkt)
                self._cache[ck] = sample
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


def _extract_sample_from_response(
    response: OpenMeteoResponse,
    zeitpunkt: datetime,
    query_koordinate: tuple[float, float] | None = None,
) -> WeatherSample | None:
    """Extrahiert ein WeatherSample aus einer OpenMeteoResponse.

    Args:
        response: OpenMeteoResponse mit hourly-Daten.
        zeitpunkt: Gewünschter Zeitpunkt.
        query_koordinate: Original-Koordinatenpunkt der Abfrage.

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
        koordinate=query_koordinate or (response.latitude, response.longitude),
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
