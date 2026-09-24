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
    "/snow1g/version/1/geotype/point/lon/{lon}/lat/{lat}/data.json"
)


class SmhiProvider:
    """Weather provider using SMHI's free, keyless forecast API (Sweden only).

    Uses the `snow1g` point-forecast API. SMHI deprecated the previous
    `pmp3g` v2 API on 2026-03-31 (HTTP 404 on every request since); `snow1g`
    v1 is its replacement, same domain/auth (keyless), but with a renamed
    `time` field (was `validTime`), a flat `data` object (was a `parameters`
    list of `{name, values}` entries), and renamed parameters (see
    `_extract_smhi_sample`). Sweden-only coverage (per SMHI: "only locations
    close to Sweden can be added"); `LoadBalancedWeatherProvider` restricts
    this provider to coordinates `detect_country` classifies as `"SE"`.
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
            by_time = {entry.get("time"): entry for entry in response.json().get("timeSeries", [])}
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
    """Extracts a `WeatherSample` from an SMHI `snow1g` `timeSeries` lookup."""
    entry = by_time.get(_snap_to_hour_z(query.timestamp))
    if entry is None:
        return None

    data = entry.get("data", {})
    precipitation_mm = float(data.get("precipitation_amount_median", 0.0))
    # `precipitation_frozen_part` is a percentage of the precipitation that's
    # frozen (0-100); SMHI uses -9 as a sentinel for "no precipitation at
    # all", which must not be treated as -900% frozen.
    frozen_part_pct = float(data.get("precipitation_frozen_part", 0.0))
    frozen_fraction = _clamp(frozen_part_pct, 0.0, 100.0) / 100.0 if frozen_part_pct >= 0 else 0.0

    return WeatherSample(
        coordinate=query.coordinate,
        timestamp=query.timestamp,
        temperature_c=float(data.get("air_temperature", 0.0)),
        wind_speed_ms=float(data.get("wind_speed", 0.0)),
        wind_direction_deg=_clamp(float(data.get("wind_from_direction", 0.0)), 0.0, 360.0),
        precipitation_mm=precipitation_mm * (1.0 - frozen_fraction),
        snowfall_cm=(precipitation_mm * frozen_fraction) / 10.0,
        pressure_hpa=_clamp(
            float(data.get("air_pressure_at_mean_sea_level", 1013.25)), 870.0, 1084.0
        ),
        humidity_pct=_clamp(float(data.get("relative_humidity", 0.0)), 0.0, 100.0),
        solar_radiation_wm2=0.0,  # not exposed by the snow1g point forecast
        cloudiness_pct=_clamp(float(data.get("cloud_area_fraction", 0.0)) * 12.5, 0.0, 100.0),
    )
