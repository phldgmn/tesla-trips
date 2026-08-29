"""MET Norway Locationforecast 2.0 provider."""

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers._shared import (
    _clamp,
    _group_queries_by_coordinate,
    _snap_to_hour_z,
)

logger = logging.getLogger(__name__)


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
