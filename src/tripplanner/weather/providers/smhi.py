"""SMHI (Swedish Meteorological and Hydrological Institute) provider."""

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
