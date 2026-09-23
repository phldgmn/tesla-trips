"""DMI (Danish Meteorological Institute) provider."""

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
        coordinate=query.coordinate,
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
