"""Weather providers and HTTP clients for the `weather` module.

Implements:
- `WeatherProvider`: Protocol for weather data providers
- `OpenMeteoClient` / `OpenMeteoProvider`: Open-Meteo Forecast API (global, keyless)
- `MetNorwayProvider`: MET Norway Locationforecast 2.0 API (global, keyless)
- `OpenWeatherProvider`: OpenWeather 5 day / 3 hour forecast API (global, API key)
- `SmhiProvider`: SMHI meteorological forecasts API (Sweden only, keyless)
- `DmiProvider`: DMI HARMONIE DINI forecast EDR API (Denmark only, keyless)
- `LoadBalancedWeatherProvider`: country-aware, load-balanced composite with
  automatic failover across the providers above
- `FakeWeatherProvider`: fake provider for unit tests
"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, Protocol

import httpx

from tripplanner.geo import Coordinate
from tripplanner.weather.coverage import detect_country
from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Shared helpers for the providers below
# ---------------------------------------------------------------------------


def _group_queries_by_coordinate(
    queries: Sequence[WeatherQuery],
) -> dict[Coordinate, list[tuple[int, WeatherQuery]]]:
    """Groups `queries` by exact coordinate, preserving each query's index.

    Args:
        queries: Queries in caller order.

    Returns:
        A dict from coordinate to `(original_index, query)` pairs, used by
        every provider below to issue one HTTP request per unique location
        instead of one per `(coordinate, time)` pair.
    """
    groups: dict[Coordinate, list[tuple[int, WeatherQuery]]] = {}
    for idx, query in enumerate(queries):
        groups.setdefault(query.koordinate, []).append((idx, query))
    return groups


def _snap_to_hour_z(zeitpunkt: datetime) -> str:
    """Formats `zeitpunkt` snapped to the hour as `YYYY-MM-DDTHH:MM:SSZ`.

    Matches the on-the-hour, UTC, `Z`-suffixed timestamp format used by MET
    Norway, SMHI, and DMI. `zeitpunkt` is treated as naive-UTC, the same
    project-wide convention `_extract_sample_from_response` above relies on.
    """
    return zeitpunkt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamps `value` to `[lo, hi]` (defensive against out-of-range upstream data)."""
    return max(lo, min(hi, value))


def _neutral_weather_sample(query: WeatherQuery) -> WeatherSample:
    """Builds a conservative placeholder sample when every provider failed.

    Used exclusively by `LoadBalancedWeatherProvider` as a last resort so a
    total weather-provider outage degrades trip accuracy instead of
    blocking trip calculation. Values are a deliberately mild, generic
    Northern-European estimate - distinct from `FakeWeatherProvider`'s
    defaults so degraded-mode production output is never mistaken for test
    fixture data.

    Args:
        query: The query the sample answers.

    Returns:
        A `WeatherSample` with neutral placeholder values.
    """
    return WeatherSample(
        koordinate=query.koordinate,
        zeitpunkt=query.zeitpunkt,
        temperatur_c=15.0,
        windgeschwindigkeit_ms=3.0,
        windrichtung_deg=180.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=70.0,
        globalstrahlung_wm2=200.0,
        bewoelkung_pct=50.0,
    )


# ---------------------------------------------------------------------------
# MET Norway (Locationforecast 2.0) - global, keyless
# ---------------------------------------------------------------------------

_METNO_BASE_URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
_METNO_USER_AGENT = (
    "tesla-trip-planner/0.1 "
    "(+https://github.com/phldgmn/tesla-trips; "
    "contact: 2805818+phldgmn@users.noreply.github.com)"
)


class MetNorwayProvider:
    """Weather provider using the MET Norway Locationforecast 2.0 API.

    Global coverage ("forecasts for any location on earth" per the
    service's data model docs), though the Nordic/Arctic region gets the
    highest-resolution source data. Free and keyless, but requires a
    unique `User-Agent` identifying the calling application - the Terms of
    Service reject a missing/generic `User-Agent` with 403 - and truncates
    coordinates to 4 decimals.
    """

    BASE_URL = _METNO_BASE_URL
    TIMEOUT_S = 15.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Initializes the provider.

        Args:
            client: Optional pre-configured `httpx.AsyncClient` (used by
                tests to inject a `MockTransport`). When `None`, a new
                client carrying the required `User-Agent` header is created.
        """
        self._client = client or httpx.AsyncClient(
            timeout=self.TIMEOUT_S, headers={"User-Agent": _METNO_USER_AGENT}
        )

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        """Fetches weather for `queries`, one HTTP request per unique coordinate."""
        if not queries:
            return []

        results: list[WeatherSample | None] = [None] * len(queries)
        for coordinate, entries in _group_queries_by_coordinate(queries).items():
            lat, lon = coordinate
            response = await self._client.get(
                self.BASE_URL, params={"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}
            )
            response.raise_for_status()
            timeseries = response.json().get("properties", {}).get("timeseries", [])
            by_time = {entry.get("time"): entry for entry in timeseries}
            for idx, query in entries:
                sample = _extract_metno_sample(by_time, query)
                if sample is not None:
                    results[idx] = sample

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-fetches weather for `updated_queries` (no internal caching)."""
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        await self._client.aclose()


def _metno_precipitation(entry: dict[str, Any]) -> tuple[float, bool]:
    """Reads the best-available precipitation figure and a snow heuristic.

    MET Norway only publishes short-range precipitation summaries
    (`next_1_hours`, falling back to `next_6_hours` divided by 6 further
    out in the forecast horizon) and has no dedicated snowfall-amount
    field, so a `symbol_code` containing "snow" is used as a heuristic to
    route the precipitation amount into `schneefall_cm` instead of
    `niederschlag_mm`.

    Args:
        entry: A single `timeseries[]` element.

    Returns:
        `(precipitation_mm, is_snow)`.
    """
    data = entry.get("data", {})
    next_1h = data.get("next_1_hours")
    if next_1h is not None:
        amount = float(next_1h.get("details", {}).get("precipitation_amount", 0.0))
        symbol = next_1h.get("summary", {}).get("symbol_code", "")
        return amount, "snow" in symbol
    next_6h = data.get("next_6_hours")
    if next_6h is not None:
        amount = float(next_6h.get("details", {}).get("precipitation_amount", 0.0)) / 6.0
        symbol = next_6h.get("summary", {}).get("symbol_code", "")
        return amount, "snow" in symbol
    return 0.0, False


def _extract_metno_sample(by_time: dict[Any, Any], query: WeatherQuery) -> WeatherSample | None:
    """Extracts a `WeatherSample` from a MET Norway `timeseries` lookup.

    Args:
        by_time: `timeseries[].time` -> raw timeseries entry.
        query: The query to answer.

    Returns:
        `None` if `query.zeitpunkt` (snapped to the hour) has no matching entry.
    """
    entry = by_time.get(_snap_to_hour_z(query.zeitpunkt))
    if entry is None:
        return None

    instant = entry.get("data", {}).get("instant", {}).get("details", {})
    precipitation_mm, is_snow = _metno_precipitation(entry)

    return WeatherSample(
        koordinate=query.koordinate,
        zeitpunkt=query.zeitpunkt,
        temperatur_c=float(instant.get("air_temperature", 0.0)),
        windgeschwindigkeit_ms=float(instant.get("wind_speed", 0.0)),
        windrichtung_deg=_clamp(float(instant.get("wind_from_direction", 0.0)), 0.0, 360.0),
        niederschlag_mm=0.0 if is_snow else precipitation_mm,
        schneefall_cm=(precipitation_mm / 10.0) if is_snow else 0.0,
        luftdruck_hpa=_clamp(
            float(instant.get("air_pressure_at_sea_level", 1013.25)), 870.0, 1084.0
        ),
        luftfeuchtigkeit_pct=_clamp(float(instant.get("relative_humidity", 0.0)), 0.0, 100.0),
        globalstrahlung_wm2=0.0,  # not exposed by Locationforecast 2.0
        bewoelkung_pct=_clamp(float(instant.get("cloud_area_fraction", 0.0)), 0.0, 100.0),
    )


# ---------------------------------------------------------------------------
# OpenWeather - global, requires an API key
# ---------------------------------------------------------------------------

_OPENWEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5/forecast"
# Forecast slots are 3h apart; accept the nearest one within half that
# window plus margin so a query near a slot boundary still resolves.
_OPENWEATHER_MATCH_TOLERANCE = timedelta(hours=1, minutes=30)


class OpenWeatherProvider:
    """Weather provider using OpenWeather's free "5 day / 3 hour" forecast API.

    Global coverage. Requires an API key (`credentials.local.yaml`:
    `weather.openweather.key`, or env var `OPENWEATHER_API_KEY`; see
    `tripplanner.trip_input.providers_factory`). Forecast resolution is 3
    hours, so matching uses the nearest available slot within
    `_OPENWEATHER_MATCH_TOLERANCE` instead of exact-hour matching.
    """

    BASE_URL = _OPENWEATHER_BASE_URL
    TIMEOUT_S = 15.0

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        """Initializes the provider.

        Args:
            api_key: OpenWeather API key (sent as the `appid` query parameter).
            client: Optional pre-configured `httpx.AsyncClient` for tests.
        """
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        """Fetches weather for `queries`, one HTTP request per unique coordinate."""
        if not queries:
            return []

        results: list[WeatherSample | None] = [None] * len(queries)
        for coordinate, entries in _group_queries_by_coordinate(queries).items():
            lat, lon = coordinate
            response = await self._client.get(
                self.BASE_URL,
                params={"lat": lat, "lon": lon, "appid": self._api_key, "units": "metric"},
            )
            response.raise_for_status()
            entries_by_time = _openweather_entries_by_time(response.json())
            for idx, query in entries:
                sample = _extract_openweather_sample(entries_by_time, query)
                if sample is not None:
                    results[idx] = sample

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-fetches weather for `updated_queries` (no internal caching)."""
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        await self._client.aclose()


def _openweather_entries_by_time(payload: dict[str, Any]) -> dict[datetime, dict[str, Any]]:
    """Indexes an OpenWeather forecast response's `list[]` by naive-UTC timestamp."""
    result: dict[datetime, dict[str, Any]] = {}
    for entry in payload.get("list", []):
        dt = entry.get("dt")
        if dt is None:
            continue
        result[datetime.fromtimestamp(dt, tz=UTC).replace(tzinfo=None)] = entry
    return result


def _extract_openweather_sample(
    entries_by_time: dict[datetime, dict[str, Any]], query: WeatherQuery
) -> WeatherSample | None:
    """Finds the nearest OpenWeather 3-hour slot to `query.zeitpunkt` and maps it.

    Returns `None` if no slot is within `_OPENWEATHER_MATCH_TOLERANCE` (e.g.
    the queried time is beyond the 5-day forecast horizon).
    """
    if not entries_by_time:
        return None
    nearest = min(entries_by_time, key=lambda dt: abs(dt - query.zeitpunkt))
    if abs(nearest - query.zeitpunkt) > _OPENWEATHER_MATCH_TOLERANCE:
        return None

    entry = entries_by_time[nearest]
    main = entry.get("main", {})
    wind = entry.get("wind", {})
    rain_3h = float(entry.get("rain", {}).get("3h", 0.0))
    snow_3h = float(entry.get("snow", {}).get("3h", 0.0))

    return WeatherSample(
        koordinate=query.koordinate,
        zeitpunkt=query.zeitpunkt,
        temperatur_c=float(main.get("temp", 0.0)),
        windgeschwindigkeit_ms=float(wind.get("speed", 0.0)),
        windrichtung_deg=_clamp(float(wind.get("deg", 0.0)), 0.0, 360.0),
        niederschlag_mm=rain_3h / 3.0,
        schneefall_cm=(snow_3h / 3.0) / 10.0,
        luftdruck_hpa=_clamp(float(main.get("pressure", 1013.25)), 870.0, 1084.0),
        luftfeuchtigkeit_pct=_clamp(float(main.get("humidity", 0.0)), 0.0, 100.0),
        globalstrahlung_wm2=0.0,  # not exposed by the 2.5 forecast API
        bewoelkung_pct=_clamp(float(entry.get("clouds", {}).get("all", 0.0)), 0.0, 100.0),
    )


# ---------------------------------------------------------------------------
# SMHI (Swedish Meteorological and Hydrological Institute) - Sweden only, keyless
# ---------------------------------------------------------------------------

_SMHI_BASE_URL_TEMPLATE = (
    "https://opendata-download-metfcst.smhi.se/api/category"
    "/pmp3g/version/2/geotype/point/lon/{lon}/lat/{lat}/data.json"
)
# SMHI `pcat` (precipitation category) codes indicating frozen precipitation.
_SMHI_SNOW_CATEGORIES = frozenset({1, 2})  # 1=snow, 2=snow and rain


class SmhiProvider:
    """Weather provider using SMHI's free, keyless forecast API (Sweden only).

    Uses the `pmp3g` "meteorological forecasts" point API. Sweden-only
    coverage (per SMHI: "only locations close to Sweden can be added");
    `LoadBalancedWeatherProvider` restricts this provider to coordinates
    `detect_country` classifies as `"SE"`.
    """

    TIMEOUT_S = 15.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Initializes the provider.

        Args:
            client: Optional pre-configured `httpx.AsyncClient` for tests.
        """
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        """Fetches weather for `queries`, one HTTP request per unique coordinate."""
        if not queries:
            return []

        results: list[WeatherSample | None] = [None] * len(queries)
        for coordinate, entries in _group_queries_by_coordinate(queries).items():
            lat, lon = coordinate
            url = _SMHI_BASE_URL_TEMPLATE.format(lon=lon, lat=lat)
            response = await self._client.get(url)
            response.raise_for_status()
            by_time = {
                entry.get("validTime"): entry for entry in response.json().get("timeSeries", [])
            }
            for idx, query in entries:
                sample = _extract_smhi_sample(by_time, query)
                if sample is not None:
                    results[idx] = sample

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-fetches weather for `updated_queries` (no internal caching)."""
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        await self._client.aclose()


def _extract_smhi_sample(by_time: dict[Any, Any], query: WeatherQuery) -> WeatherSample | None:
    """Extracts a `WeatherSample` from an SMHI `timeSeries` lookup."""
    entry = by_time.get(_snap_to_hour_z(query.zeitpunkt))
    if entry is None:
        return None

    params = {p["name"]: p["values"][0] for p in entry.get("parameters", []) if p.get("values")}
    precipitation_mm = float(params.get("pmedian", 0.0))
    is_snow = int(params.get("pcat", 0)) in _SMHI_SNOW_CATEGORIES

    return WeatherSample(
        koordinate=query.koordinate,
        zeitpunkt=query.zeitpunkt,
        temperatur_c=float(params.get("t", 0.0)),
        windgeschwindigkeit_ms=float(params.get("ws", 0.0)),
        windrichtung_deg=_clamp(float(params.get("wd", 0.0)), 0.0, 360.0),
        niederschlag_mm=0.0 if is_snow else precipitation_mm,
        schneefall_cm=(precipitation_mm / 10.0) if is_snow else 0.0,
        luftdruck_hpa=_clamp(float(params.get("msl", 1013.25)), 870.0, 1084.0),
        luftfeuchtigkeit_pct=_clamp(float(params.get("r", 0.0)), 0.0, 100.0),
        globalstrahlung_wm2=0.0,  # not exposed by the pmp3g point forecast
        bewoelkung_pct=_clamp(float(params.get("tcc_mean", 0.0)) * 12.5, 0.0, 100.0),
    )


# ---------------------------------------------------------------------------
# DMI (Danish Meteorological Institute) - Denmark only, keyless
# ---------------------------------------------------------------------------

_DMI_COLLECTION_URL = (
    "https://opendataapi.dmi.dk/v1/forecastedr/collections/harmonie_dini_sf/position"
)
_DMI_PARAMETERS = (
    "temperature-2m",
    "wind-speed-10m",
    "wind-dir-10m",
    "relative-humidity-2m",
    "pressure-sealevel",
    "fraction-of-cloud-cover-2m",
    "rain-precipitation-rate",
    "total-snowfall-rate-water-equivalent",
    "downward-short-wave-radiation-flux",
)


class DmiProvider:
    """Weather provider using DMI's free, keyless HARMONIE DINI forecast API.

    Uses the `forecastedr` EDR API, collection `harmonie_dini_sf` (surface
    fields). The model domain covers Denmark, Iceland, the Netherlands, and
    Ireland (per DMI's own collection description) - well beyond Denmark
    alone - but `LoadBalancedWeatherProvider` restricts this provider to
    coordinates `detect_country` classifies as `"DK"`, matching DMI's
    stated focus and avoiding it as an unnecessary extra candidate
    elsewhere.
    """

    TIMEOUT_S = 20.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Initializes the provider.

        Args:
            client: Optional pre-configured `httpx.AsyncClient` for tests.
        """
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        """Fetches weather for `queries`, one EDR position query per unique coordinate."""
        if not queries:
            return []

        results: list[WeatherSample | None] = [None] * len(queries)
        for coordinate, entries in _group_queries_by_coordinate(queries).items():
            lat, lon = coordinate
            response = await self._client.get(
                _DMI_COLLECTION_URL,
                params={
                    "coords": f"POINT({lon} {lat})",
                    "crs": "crs84",
                    "parameter-name": ",".join(_DMI_PARAMETERS),
                    "f": "CoverageJSON",
                },
            )
            response.raise_for_status()
            payload = response.json()
            for idx, query in entries:
                sample = _extract_dmi_sample(payload, query)
                if sample is not None:
                    results[idx] = sample

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-fetches weather for `updated_queries` (no internal caching)."""
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        await self._client.aclose()


def _extract_dmi_sample(payload: dict[str, Any], query: WeatherQuery) -> WeatherSample | None:
    """Extracts a `WeatherSample` from a DMI EDR `position` CoverageJSON response.

    CoverageJSON layout (OGC API - EDR, `f=CoverageJSON`): `domain.axes.t.values`
    holds the ISO timestamps shared by every parameter; `ranges.<parameter>.values`
    holds one value per timestamp at the same index (single point query =>
    one value per `t`, per `axisNames: ["t", "y", "x"]`).
    """
    axes = payload.get("domain", {}).get("axes", {})
    t_values: list[str] = axes.get("t", {}).get("values", [])
    if not t_values:
        return None
    try:
        idx = t_values.index(_snap_to_hour_z(query.zeitpunkt))
    except ValueError:
        return None

    ranges = payload.get("ranges", {})

    def value_at(name: str, default: float) -> float:
        values = ranges.get(name, {}).get("values", [])
        if idx >= len(values) or values[idx] is None:
            return default
        return float(values[idx])

    rain_rate = value_at("rain-precipitation-rate", 0.0)  # kg/m^2/s
    snow_rate = value_at("total-snowfall-rate-water-equivalent", 0.0)  # kg/m^2/s

    return WeatherSample(
        koordinate=query.koordinate,
        zeitpunkt=query.zeitpunkt,
        temperatur_c=value_at("temperature-2m", 273.15) - 273.15,
        windgeschwindigkeit_ms=value_at("wind-speed-10m", 0.0),
        windrichtung_deg=_clamp(value_at("wind-dir-10m", 0.0), 0.0, 360.0),
        niederschlag_mm=rain_rate * 3600.0,
        schneefall_cm=(snow_rate * 3600.0) / 10.0,
        luftdruck_hpa=_clamp(value_at("pressure-sealevel", 101_325.0) / 100.0, 870.0, 1084.0),
        luftfeuchtigkeit_pct=_clamp(value_at("relative-humidity-2m", 0.0), 0.0, 100.0),
        globalstrahlung_wm2=max(0.0, value_at("downward-short-wave-radiation-flux", 0.0)),
        bewoelkung_pct=_clamp(value_at("fraction-of-cloud-cover-2m", 0.0) * 100.0, 0.0, 100.0),
    )


# ---------------------------------------------------------------------------
# LoadBalancedWeatherProvider - country-aware composite with failover
# ---------------------------------------------------------------------------


class WeatherProviderEntry(NamedTuple):
    """One weather provider registered with `LoadBalancedWeatherProvider`."""

    name: str
    """Human-readable provider name, used in logs and cooldown tracking."""

    provider: WeatherProvider
    """The underlying provider implementation."""

    countries: frozenset[str] | None
    """Country codes (`"DE"`/`"DK"`/`"SE"`) this provider is restricted to,
    or `None` for global coverage (eligible for every coordinate, including
    ones outside the three focus countries)."""


class LoadBalancedWeatherProvider:
    """Composite `WeatherProvider` with country-aware load balancing and failover.

    For each queried coordinate:

    1. `detect_country` narrows the candidate providers to those whose
       `countries` include the coordinate's country (or are `None`, i.e.
       global).
    2. Candidates rotate round-robin across calls (spreading load instead
       of always hitting the same provider first); a provider that failed
       recently is deprioritized (moved after healthy candidates, not
       excluded) for `cooldown_seconds`, so a rate-limited provider gets a
       rest without permanently losing its turn.
    3. Candidates are tried in that order until one returns a sample for a
       given point; a provider raising (HTTP error, timeout, malformed
       response) is logged and skipped in favor of the next candidate.
    4. If every eligible candidate fails for a point, a neutral placeholder
       sample is used (`_neutral_weather_sample`) instead of raising - a
       full weather-provider outage degrades trip accuracy, it never blocks
       trip calculation. This is the fix for the "a single failing
       provider must never break routing" requirement.

    Successful results are cached per `(koordinate, zeitpunkt)` for the
    lifetime of this instance, serving both `fetch_weather` and
    `refetch_weather` (the iterative ETA/weather resolution described in
    `docs/03-modulspezifikationen.md` §3) without re-querying providers for
    points already resolved.
    """

    def __init__(
        self,
        entries: Sequence[WeatherProviderEntry],
        *,
        cooldown_seconds: float = 300.0,
        max_concurrency: int = 10,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initializes the composite provider.

        Args:
            entries: Registered providers with their country restrictions.
                Must be non-empty.
            cooldown_seconds: How long a failed provider is deprioritized
                (moved after healthy candidates, not excluded) before being
                retried at normal priority again.
            max_concurrency: Maximum number of coordinate groups resolved
                concurrently. Routes have one weather query per segment, so
                a long route can mean hundreds of distinct coordinates;
                resolving them one at a time would multiply per-request
                network latency by the segment count. Bounded concurrency
                keeps wall-clock time reasonable without opening an
                unbounded number of simultaneous connections to any single
                provider.
            clock: Monotonic time source; overridable in tests.

        Raises:
            ValueError: If `entries` is empty.
        """
        if not entries:
            raise ValueError("LoadBalancedWeatherProvider requires at least one provider.")
        self._entries = list(entries)
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._rotation = 0
        self._unhealthy_until: dict[str, float] = {}
        self._cache: dict[tuple[Coordinate, datetime], WeatherSample] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_weather(self, queries: Sequence[WeatherQuery]) -> list[WeatherSample]:
        """Fetches weather for `queries`, dispatching per coordinate as described above.

        Coordinate groups are resolved concurrently (bounded by
        `max_concurrency`) rather than one at a time, so a long route
        (many distinct coordinates) does not multiply per-group network
        latency by the number of segments.
        """
        if not queries:
            return []

        results: list[WeatherSample | None] = [None] * len(queries)
        pending_indices: list[int] = []
        for idx, query in enumerate(queries):
            cached = self._cache.get((query.koordinate, query.zeitpunkt))
            if cached is not None:
                results[idx] = cached
            else:
                pending_indices.append(idx)

        groups: dict[Coordinate, list[int]] = {}
        for idx in pending_indices:
            groups.setdefault(queries[idx].koordinate, []).append(idx)

        async def resolve_bounded(coordinate: Coordinate, group_indices: list[int]) -> None:
            async with self._semaphore:
                await self._resolve_group(coordinate, group_indices, queries, results)

        await asyncio.gather(
            *(resolve_bounded(coordinate, idxs) for coordinate, idxs in groups.items())
        )

        return [r for r in results if r is not None]

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-fetches weather for `updated_queries`.

        `original_queries` is accepted to satisfy the `WeatherProvider`
        protocol; the composite's own `(koordinate, zeitpunkt)` cache
        (populated by any prior `fetch_weather`/`refetch_weather` call)
        already serves unchanged points, so no separate handling is needed.
        """
        del original_queries
        return await self.fetch_weather(updated_queries)

    async def close(self) -> None:
        """Closes every registered provider that exposes a `close()` method."""
        for entry in self._entries:
            close = getattr(entry.provider, "close", None)
            if close is not None:
                await close()

    async def _resolve_group(
        self,
        coordinate: Coordinate,
        group_indices: list[int],
        queries: Sequence[WeatherQuery],
        results: list[WeatherSample | None],
    ) -> None:
        """Resolves every query index in `group_indices` (all sharing `coordinate`)."""
        country = detect_country(coordinate)
        eligible = [e for e in self._entries if e.countries is None or country in e.countries]
        pending = list(group_indices)

        for entry in self._ordered_candidates(eligible):
            if not pending:
                break
            sub_queries = [queries[i] for i in pending]
            samples = await self._try_provider(entry, sub_queries, coordinate)
            if samples is None:
                continue
            by_key = {(s.koordinate, s.zeitpunkt): s for s in samples}
            still_pending: list[int] = []
            for i in pending:
                key = (queries[i].koordinate, queries[i].zeitpunkt)
                sample = by_key.get(key)
                if sample is None:
                    still_pending.append(i)
                else:
                    results[i] = sample
                    self._cache[key] = sample
            pending = still_pending

        if pending:
            logger.error(
                "All eligible weather providers failed for %s; using neutral fallback "
                "for %d point(s).",
                coordinate,
                len(pending),
            )
            for i in pending:
                results[i] = _neutral_weather_sample(queries[i])

    async def _try_provider(
        self,
        entry: WeatherProviderEntry,
        sub_queries: Sequence[WeatherQuery],
        coordinate: Coordinate,
    ) -> list[WeatherSample] | None:
        """Calls `entry.provider.fetch_weather`, absorbing every failure mode.

        Returns `None` (and marks `entry` unhealthy) on any error; the
        caller then moves on to the next candidate.
        """
        try:
            samples = await entry.provider.fetch_weather(sub_queries)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Weather provider %s: HTTP %s for %s.",
                entry.name,
                exc.response.status_code,
                coordinate,
            )
            self._mark_unhealthy(entry.name)
            return None
        except httpx.HTTPError as exc:
            logger.warning(
                "Weather provider %s: request failed for %s: %s.", entry.name, coordinate, exc
            )
            self._mark_unhealthy(entry.name)
            return None
        except Exception:
            logger.exception(
                "Weather provider %s: unexpected error for %s.", entry.name, coordinate
            )
            self._mark_unhealthy(entry.name)
            return None

        self._mark_healthy(entry.name)
        return samples

    def _ordered_candidates(
        self, entries: Sequence[WeatherProviderEntry]
    ) -> list[WeatherProviderEntry]:
        """Round-robin-rotates `entries`, then sorts healthy ones before cooling-down ones."""
        if not entries:
            return []
        offset = self._rotation % len(entries)
        self._rotation += 1
        rotated = list(entries[offset:]) + list(entries[:offset])
        now = self._clock()
        healthy = [e for e in rotated if self._unhealthy_until.get(e.name, 0.0) <= now]
        unhealthy = [e for e in rotated if e not in healthy]
        return healthy + unhealthy

    def _mark_unhealthy(self, name: str) -> None:
        """Deprioritizes provider `name` for `cooldown_seconds`."""
        self._unhealthy_until[name] = self._clock() + self._cooldown_seconds

    def _mark_healthy(self, name: str) -> None:
        """Clears any cooldown for provider `name`."""
        self._unhealthy_until.pop(name, None)
