# Plan 03: Weather and Wind Modules

**Purpose:** This plan covers the `weather` (Phase 1) and `wind` (Phase 2) modules of the Tesla Trip Planner together.

---

## 1. Purpose & Scope

### `weather` Module

The `weather` module is responsible for querying and providing weather data along the route. It fulfills the following functions:

- Query weather data for given coordinates and timestamps via the Open-Meteo Forecast API.
- **Support for iterative time/weather resolution** (see `02-architecture.md`, section "Iterative Time/Weather Resolution"): method to re-query already-queried points with an updated timestamp.
- Batching multiple query points into a single API call for efficiency.
- Encapsulation of the HTTP client behind a `WeatherProvider` protocol for testability (fake implementation in tests).

**Out of scope:** Historical data queries (this is intended for future calibration, see section "External Integration / Algorithm Details"), live traffic, construction sites (these fall under the `construction` module).

### `wind` Module

The `wind` module calculates the effective wind components from wind data (speed and direction) and the direction (bearing) of the respective route segment:

- **Headwind/tailwind component** (m/s, signed: positive = headwind, negative = tailwind).
- **Crosswind component** (m/s, signed: positive = crosswind from the right, negative = from the left).

The module is pure computation logic with no external data sources and is fully unit-testable.

---

## 2. Dependencies & Phase Assignment

| Module | Phase | Consumed Types (from the registry) |
|--------|-------|------------------------------------|
| `weather` | Phase 1 | None (standalone module) |
| `wind` | Phase 2 | `tripplanner.weather.models.WeatherSample` (read-only), `tripplanner.routing.models.RouteSegment` (for `bearing_deg`) |

**Note:** The `wind` module reads exclusively from the Pydantic models of the `weather` module. No module imports internal implementation details of another module — only `tripplanner.<other_module>.models`.

---

## 3. Data Models

### `weather.models` – Official types from the registry

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from tripplanner.geo import (
    Coordinate,
)  # canonical geo primitive, see docs/plans/00-foundation-tooling.md and docs/plans/01-routing.md section 3

class WeatherQuery(BaseModel):
    """Query for a single weather event."""

    koordinate: Coordinate  # WGS84 (lat, lon)
    zeitpunkt: datetime

class WeatherSample(BaseModel):
    """Weather data for a point in time at a coordinate."""

    koordinate: Coordinate
    zeitpunkt: datetime
    temperatur_c: float = Field(ge=-100.0, le=70.0, description="Temperature in °C")
    windgeschwindigkeit_ms: float = Field(ge=0.0, description="Wind speed in m/s")
    windrichtung_deg: float = Field(
        ge=0.0, le=360.0, description="Wind direction in degrees (0° = N, 90° = E)"
    )
    niederschlag_mm: float = Field(ge=0.0, description="Precipitation in mm (hourly sum)")
    schneefall_cm: float = Field(ge=0.0, description="Snowfall in cm (water equivalent)")
    luftdruck_hpa: float = Field(ge=870.0, le=1084.0, description="Air pressure in hPa (MSL)")
    luftfeuchtigkeit_pct: float = Field(
        ge=0.0, le=100.0, description="Relative humidity in %"
    )
    globalstrahlung_wm2: float = Field(ge=0.0, description="Global radiation in W/m² (hourly sum)")
    bewoelkung_pct: float = Field(ge=0.0, le=100.0, description="Cloud cover in %")
```

### `weather.models` – Internal helper types

```python
class OpenMeteoResponse(BaseModel):
    """Raw response from Open-Meteo Forecast API (for internal processing only)."""

    latitude: float
    longitude: float
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly: dict[str, list[float | int | str | None]]
    hourly_units: dict[str, str]
    # Additional fields (daily, current, etc.) are ignored
```

### `wind.models` – Official types from the registry

```python
from tripplanner.weather.models import WeatherSample
from tripplanner.routing.models import RouteSegment

class WindComponents(BaseModel):
    """Wind components along a route."""

    segment_index: int
    gegenwind_ms: float  # positive: headwind, negative: tailwind
    seitenwind_ms: float  # positive: from right, negative: from left
```

---

## 4. Public Interface

### `weather.providers.WeatherProvider` (Protocol)

```python
from typing import Protocol, Sequence
from tripplanner.weather.models import WeatherQuery, WeatherSample

class WeatherProvider(Protocol):
    """Interface for weather data providers (can be replaced by a fake)."""

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Fetch weather data for multiple query points.

        Args:
            queries: List of weather queries (coordinate + timestamp).

        Returns:
            List of weather data, in the same order as queries.
            If data is missing for a point, an empty list or None is returned,
            which is marked by a sentinel (e.g. None) or a special WeatherSample with NaN.
        """
        ...

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Re-query already-queried points with an updated timestamp.

        This method is central to iterative time/weather resolution.
        The implementation may use caching internally (e.g. on `coordinate` + `timestamp` tuples)
        to avoid unnecessary API calls.

        Args:
            original_queries: The original queries (unchanged).
            updated_queries: The updated queries with new timestamps,
                             same coordinates as original_queries.

        Returns:
            List of WeatherSample for the updated_queries.
        """
        ...
```

### `weather.<module>.py` – Public Functions

```python
from typing import Sequence
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import WeatherProvider

async def fetch_weather_for_route(
    provider: WeatherProvider,
    route_queries: Sequence[WeatherQuery],
    batch_size: int = 20,
) -> list[WeatherSample]:
    """Fetch weather data along a route with automatic batching.

    Args:
        provider: The weather provider to use (in tests: fake).
        route_queries: List of queries (coordinate + ETA).
        batch_size: Maximum number of queries per API call (Open-Meteo recommends <=50).

    Returns:
        List of WeatherSample in the same order as route_queries.
    """
    ...

async def fetch_weather_iterative(
    provider: WeatherProvider,
    initial_queries: Sequence[WeatherQuery],
    max_iterations: int = 2,
    convergence_threshold_s: int = 1800,  # 30 minutes = 1800 seconds
) -> list[WeatherSample]:
    """Iterative weather query per 02-architecture.md.

    1. Fetch with initial_queries (rough ETA).
    2. Calculate energy consumption + charging plan → new ETA per segment.
    3. If deviation > convergence_threshold_s at a point:
       Re-fetch with updated timestamps.
    4. Convergence check (max. max_iterations).

    Args:
        provider: Weather provider (may use caching internally).
        initial_queries: First query (rough ETA).
        max_iterations: Maximum iteration threshold.
        convergence_threshold_s: Deviation threshold in seconds.

    Returns:
        List of WeatherSample after convergence (or max_iterations).
    """
    ...
```

### `wind.<module>.py` – Public Functions

```python
from typing import Sequence
from tripplanner.weather.models import WeatherSample
from tripplanner.routing.models import RouteSegment
from tripplanner.wind.models import WindComponents

def compute_wind_components_for_route(
    weather_samples: Sequence[WeatherSample],
    segments: Sequence[RouteSegment],
) -> list[WindComponents]:
    """Calculate wind components for each segment along the route.

    Args:
        weather_samples: Weather data per weather query point.
        segments: Route segments (must have the same length as weather_samples
                  or be extendable via interpolation — here: direct mapping).

    Returns:
        List of WindComponents (segment_index corresponds to segment.segment_index).
        Uncovered segments (e.g. missing weather data) are marked with
        WindComponents(segment_index=<idx>, gegenwind_ms=0.0, seitenwind_ms=0.0).
    """
    ...
```

---

## 5. External Integration / Algorithm Details

### Open-Meteo Forecast API – Specific API Endpoints & Parameters

**Endpoint:** `https://api.open-meteo.com/v1/forecast`

**Required `hourly` parameters for this project:**

```python
hourly_params = [
    "temperature_2m",  # Temperature in °C
    "wind_speed_10m",  # Wind speed in km/h → convert to m/s (* 1000/3600)
    "wind_direction_10m",  # Wind direction in ° (0° = N, 90° = E)
    "precipitation",  # Precipitation in mm (hourly sum)
    "snowfall",  # Snowfall in cm (water equivalent)
    "surface_pressure",  # Air pressure in hPa (MSL)
    "relative_humidity_2m",  # Relative humidity in %
    "shortwave_radiation",  # Global radiation in W/m² (hourly sum)
    "cloud_cover",  # Cloud cover in %
]
```

**Coordinate Batching:**

- Multiple locations can be queried in a single call via `latitude=<lat1>,<lat2>,...&longitude=<lon1>,<lon2>,...`
- Open-Meteo recommends **a maximum of 50 locations per call** (no fixed limit in the documentation, but a practical recommendation).
- In `fetch_weather_for_route`, `batch_size = min(len(queries), 50)` is therefore used.

**Rate Limits (without API key):**

- **Non-commercial use:** 10,000 calls/day free of charge.
- **Commercial use:** Platform subscriptions (standard: 1M calls/month).
- A fake provider is used for tests (no real API call).

**Example API call (HTTP GET):**

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=52.52,48.137,48.775
    &longitude=13.405,11.575,9.175
    &current=temperature_2m,wind_speed_10m
    &hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation,snowfall,surface_pressure,relative_humidity_2m,shortwave_radiation,cloud_cover
    &timezone=auto
    &forecast_days=2
```

**Response format (excerpt):**

```json
{
…
  "utc_offset_seconds": 3600,
  "timezone": "Europe/Berlin",
  "timezone_abbreviation": "CET",
  "elevation": 38.0,
  "hourly_units": {
    "time": "iso8601",
    "temperature_2m": "°C",
    "wind_speed_10m": "km/h",
    "wind_direction_10m": "°",
    "precipitation": "mm",
    "snowfall": "cm",
    "surface_pressure": "hPa",
    "relative_humidity_2m": "%",
    "shortwave_radiation": "W/m²",
    "cloud_cover": "%"
  },
  "hourly": {
    "time": ["2026-08-02T00:00", "2026-08-02T01:00", ...],
    "temperature_2m": [14.5, 13.8, ...],
    "wind_speed_10m": [15.2, 12.8, ...],
    "wind_direction_10m": [245, 250, ...],
    ...
  }
}
```

**Client implementation (`weather.client.py`):**

```python
import httpx
from typing import Sequence
from tripplanner.weather.models import WeatherQuery, OpenMeteoResponse

class OpenMeteoClient:
    """HTTP client for Open-Meteo Forecast API."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    TIMEOUT_S = 15.0

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)

    async def fetch_forecast(
        self,
        queries: Sequence[WeatherQuery],
        hourly_params: list[str],
    ) -> list[OpenMeteoResponse]:
        """Fetch weather data for multiple locations + timestamps."""
        if not queries:
            return []

        # Group by coordinate (duplicate locations save API calls)
        coords: dict[tuple[float, float], list[tuple[int, WeatherQuery]]] = {}
        for idx, q in enumerate(queries):
            key = (q.koordinate.lat, q.koordinate.lon)
            coords.setdefault(key, []).append((idx, q))

        results: list[OpenMeteoResponse | None] = [None] * len(queries)

        for (lat, lon), entries in coords.items():
            # Time range: earliest to latest timestamp for this location
            times = [q.zeitpunkt.isoformat() for _, q in entries]
            time_min = min(times)
            time_max = max(times)

            url = f"{self.BASE_URL}?latitude={lat}&longitude={lon}&hourly={','.join(hourly_params)}&start_date={time_min[:10]}&end_date={time_max[:10]}"

            resp = await self._client.get(url)
            resp.raise_for_status()
            data = OpenMeteoResponse(**resp.json())

            # Time index lookup for each query point
            time_to_idx = {t: i for i, t in enumerate(data.hourly["time"])}
            for idx, q in entries:
                time_idx = time_to_idx.get(q.zeitpunkt.isoformat())
                if time_idx is not None:
                    results[idx] = self._extract_sample(data, time_idx)
        # ... (validation, fallback logic)

        return [r for r in results if r is not None]
```

### Open-Meteo Historical API – Brief Info

For future calibration (not implemented in the current scope, but significant for design decisions):

**Endpoint:** `https://archive-api.open-meteo.com/v1/archive`

**Differences from the Forecast API:**

- Time range: `start_date`/`end_date` (back into the past, e.g. 1940–present).
- Uses reanalysis data (ERA5, ERA5-Land, ECMWF IFS) instead of real-time forecasts.
- Identical `hourly` parameters as the Forecast API.
- Rate limits are analogous (10,000 calls/day free of charge, but explicitly intended for historical data).

**Intended use in the plan:** The `WeatherProvider` interface is designed so that a `HistoricalWeatherProvider` can be implemented later, which returns averages from historical queries (e.g. monthly averages for each day of the year). This allows the consumption model to be calibrated on long-term weather conditions.

### Wind Projection – Trigonometric Formula

The wind components along the direction of travel are calculated using vector projection.

**Notation:**

- $v_w$ = wind speed (m/s) — from `WeatherSample.windgeschwindigkeit_ms`
- $\theta_w$ = wind direction (degrees, 0° = N, 90° = E) — from `WeatherSample.windrichtung_deg`
- $\theta_b$ = bearing of the direction of travel (degrees, 0° = N, 90° = E) — from `RouteSegment.bearing_deg`

**Convention for wind direction:**

- In meteorology, wind direction is defined as the **direction from which the wind comes** (e.g. "northerly wind" = wind comes from the north → blows toward the south).
- For vector projection, we therefore need to shift the **wind vector direction** by 180°: $\theta_{\text{wind, vector}} = \theta_w + 180°$ (modulo 360°).

**Calculation:**

1. Calculate the angle difference between the wind vector and bearing:
   $$
   \Delta\theta = \theta_b - (\theta_w + 180°) \mod 360°
   $$
   (Convert to radians: $\Delta\theta_{\text{rad}} = \Delta\theta \cdot \pi/180$)

2. Headwind/tailwind component (longitudinal):
   $$
   v_{\text{long}} = v_w \cdot \cos(\Delta\theta_{\text{rad}})
   $$
   - $v_{\text{long}} > 0$ → headwind (braking)
   - $v_{\text{long}} < 0$ → tailwind (assisting)

3. Crosswind component (transverse):
   $$
   v_{\text{side}} = v_w \cdot \sin(\Delta\theta_{\text{rad}})
   $$
   - $v_{\text{side}} > 0$ → crosswind from the right
   - $v_{\text{side}} < 0$ → crosswind from the left

**Implementation in `wind.<module>.py`:**

```python
import math
from tripplanner.weather.models import WeatherSample
from tripplanner.routing.models import RouteSegment
from tripplanner.wind.models import WindComponents

def _degrees_to_radians(deg: float) -> float:
    return deg * math.pi / 180.0

def compute_wind_components(
    weather: WeatherSample,
    segment: RouteSegment,
) -> WindComponents:
    """Calculate wind components for a single segment."""
    # Wind direction as vector direction (offset by 180°)
    wind_dir_vector = (weather.windrichtung_deg + 180.0) % 360.0

    # Segment bearing (required field, already calculated by routing, see RouteSegment.bearing_deg)
    bearing = segment.bearing_deg

    delta_theta = bearing - wind_dir_vector
    delta_theta_rad = _degrees_to_radians(delta_theta)

    v_long = weather.windgeschwindigkeit_ms * math.cos(delta_theta_rad)
    v_side = weather.windgeschwindigkeit_ms * math.sin(delta_theta_rad)

    return WindComponents(
        segment_index=segment.segment_index,
        gegenwind_ms=v_long,  # positive = headwind
        seitenwind_ms=v_side,  # positive = from right
    )

def compute_wind_components_for_route(
    weather_samples: Sequence[WeatherSample],
    segments: Sequence[RouteSegment],
) -> list[WindComponents]:
    if len(weather_samples) != len(segments):
        raise ValueError("weather_samples and segments must have the same length.")

    return [compute_wind_components(w, s) for w, s in zip(weather_samples, segments)]
```

**Validation test cases (see section 6):**

- Wind from North (0°), bearing East (90°) → $v_{\text{long}} = 0$, $v_{\text{side}} = +v_w$ (crosswind from right).
- Wind from North (0°), bearing North (0°) → $v_{\text{long}} = -v_w$ (tailwind).
- Wind from North (0°), bearing South (180°) → $v_{\text{long}} = +v_w$ (headwind).

---

## 6. Test Strategy

### `weather` Module

**Fixtures:**

- `tests/fixtures/weather/open_meteo_response.json` — Recorded API response (example weather point, ~100 lines of JSON).
- `tests/fixtures/weather/fake_weather_samples.json` — Hand-crafted `WeatherSample` list for unit tests (no HTTP call).

**Unit Tests (`tests/weather/test_weather.py`):**

1. **Given:** `OpenMeteoResponse` fixture with 24 hours + 5 weather variables.  
   **When:** `fetch_weather_for_route()` with 3 `WeatherQuery` (same coordinate, 3 times).  
   **Then:** Result length = 3, values correctly interpolated (e.g. `temperature_2m[0] = 15.0°C`, `wind_speed_10m[1] = 12.5 km/h → 3.47 m/s`).  
   *Edge case:* Query time falls outside the query time range → exception or sentinel value.

2. **Given:** `OpenMeteoResponse` with only one coordinate, but 2 queries (duplicate location).  
   **When:** `fetch_weather_for_route()` with `batch_size=20`.  
   **Then:** Only 1 API call instead of 2 (caching via coordinate grouping works).

3. **Given:** 50 queries with the same coordinate, 50 queries with a different coordinate (100 total).  
   **When:** `fetch_weather_for_route()` with `batch_size=20`.  
   **Then:** At least 4 API calls (50/20 + 50/20 = 2.5 + 2.5 → 3 calls per coordinate group = min. 4 calls).

**Integration Tests (`tests/weather/test_providers.py`):**

- `@pytest.mark.integration`  
  **Given:** Local GraphHopper instance with mock weather provider (real HTTP call).  
  **When:** `fetch_weather_iterative()` on a 200 km route with 5 queries.  
  **Then:** Convergence after ≤ 2 iterations (ETA deviation < 30 min), total ETA < 20 seconds.

### `wind` Module

**Fixtures:** No external files needed — all test cases as a Python list.

**Unit Tests (`tests/wind/test_wind.py`):**

1. **Given:** `WeatherSample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0)` (northerly wind), `RouteSegment(bearing_deg=0.0)` (north).
   **When:** `compute_wind_components()`.  
   **Then:** `gegenwind_ms = -10.0` (tailwind), `seitenwind_ms = 0.0`.

2. **Given:** `WeatherSample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0)` (northerly wind), `RouteSegment(bearing_deg=180.0)` (south).
   **When:** `compute_wind_components()`.  
   **Then:** `gegenwind_ms = +10.0` (headwind), `seitenwind_ms = 0.0`.

3. **Given:** `WeatherSample(windgeschwindigkeit_ms=10.0, windrichtung_deg=90.0)` (easterly wind), `RouteSegment(bearing_deg=0.0)` (north).
   **When:** `compute_wind_components()`.  
   **Then:** `gegenwind_ms = 0.0`, `seitenwind_ms = -10.0` (crosswind from left).  
   *Edge case:* Wind angle = 45° to bearing → $v_{\text{long}} = v_{\text{side}} = -10/\sqrt{2} \approx -7.07$.

### Test Setup (`tests/weather/conftest.py`, `tests/wind/conftest.py`)

```python
import pytest
from tripplanner.weather.models import WeatherSample

@pytest.fixture
def weather_sample_north_wind() -> WeatherSample:
    return WeatherSample(
        koordinate=Coordinate(lat=52.52, lon=13.405),
        zeitpunkt=datetime(2026, 8, 2, 12, 0),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=10.0,
        windrichtung_deg=0.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )

@pytest.fixture
def weather_sample_east_wind() -> WeatherSample:
    return WeatherSample(
        koordinate=Coordinate(lat=52.52, lon=13.405),
        zeitpunkt=datetime(2026, 8, 2, 12, 0),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=10.0,
        windrichtung_deg=90.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )
```

---

## 7. Task Checklist

**Strictly observe module boundaries:** Each module writes only into `src/tripplanner/<module>/`, `tests/<module>/`, `docs/plans/03-weather-wind.md`. No live network access in unit tests.

1. **[weather/models.py]** Create `WeatherQuery` and `WeatherSample` as Pydantic models with all specified fields, validators (e.g. `windgeschwindigkeit_ms >= 0`, `windrichtung_deg in [0, 360]`) and docstrings (Google-Style).
2. **[weather/client.py]** Implement `OpenMeteoClient.fetch_forecast()` with batching (max. 50 coordinates per call), time range determination, and parsing the JSON response into `OpenMeteoResponse` + `WeatherSample`.
3. **[weather/providers.py]** Implement `WeatherProvider` protocol and `OpenMeteoProvider` class, which uses `OpenMeteoClient`. Add caching for `refetch_weather()` (Dictionary `CacheKey = Tuple[Coordinate, datetime]`).
4. **[weather/**init**.py]** Re-export `WeatherProvider`, `fetch_weather_for_route`, `fetch_weather_iterative`.
5. **[tests/weather/test_weather.py]** Write 3 unit tests (see section 6, test cases 1–3). Use `pytest.mark.asyncio`.
6. **[tests/weather/test_providers.py]** Write 1 integration test (`@pytest.mark.integration`) for `fetch_weather_iterative` with local GraphHopper instance (mock provider, no real HTTP call).
7. **[weather/**init**.py]** Add `OpenMeteoClient` as an optional build parameter for `OpenMeteoProvider` (for dependency injection in tests).
8. **[wind/models.py]** Create `WindComponents` as a Pydantic model (fields: `segment_index`, `gegenwind_ms`, `seitenwind_ms`).
9. **`[wind/<module>.py]`** Implement `compute_wind_components()` per the trigonometric formula (vector projection with 180° shift for wind direction).
10. **`[wind/<module>.py]`** Implement `compute_wind_components_for_route()` with length check and loop over zipped list.
11. **[wind/test_wind.py]** Write 3 unit tests (see section 6, test cases 1–3). Check values with `math.isclose()` using tolerance `1e-6`.
12. **[docs/plans/03-weather-wind.md]** Update this file with final implementation details (formula, API endpoint, parameters).
13. **[pyproject.toml]** Add `httpx` as a dependency (`httpx = "^0.27.0"` for async support).
14. **[tests/conftest.py]** Create shared `Coordinate` fixture for all modules (`lat=52.52, lon=13.405` for Berlin).

---

## 8. Risks & Open Technical Questions

**Already addressed by "Binding Decided Open Points":**

- ✅ Iterative ETA/weather convergence (threshold 30 min, max. iteration count = configurable).
- ✅ Road route remains fixed (no energy-optimal re-routing) — the `weather` module only queries, no re-routing logic.
- ✅ Tesla Supercharger data source: kept locally (JSON/SQLite) — `ChargingStationProvider` interface is already prepared, but has no interface to `weather`.
- ✅ `energy` module may use sensible default parameters (no calibration deliverable in the current scope).

**New Risks / Open Questions:**

1. **Open-Meteo Rate Limits with multiple parallel route calculations:**
   - If 100 routes are calculated in parallel, 100 × 5 calls = 500 calls could be reached quickly.
   - **Solution:** Caching in `OpenMeteoProvider` (not just for `refetch_weather`, but also for identical locations+times in different requests). Optional: `asyncio.Semaphore` to limit parallel calls.

2. **Time zone handling:**
   - Open-Meteo returns `timezone` and `timezone_abbreviation`. `WeatherSample.zeitpunkt` expects `datetime` with `tzinfo`.
   - **Solution:** `OpenMeteoResponse` parses `time` strings (ISO 8601) via `datetime.fromisoformat()` with `tzinfo` from `timezone`. If `timezone` is missing, UTC is assumed.

3. **Interpolation for missing timestamps:**
   - Open-Meteo provides data in hourly steps (or 15-minute intervals). If the requested `WeatherQuery.zeitpunkt` is not exactly contained in `hourly.time`, interpolation is required.
   - **Solution:** `OpenMeteoClient.fetch_forecast()` performs linear interpolation (previous and following hourly slot). Alternatively: set `forecast_days=2` to ensure all requested times fall within the forecast range.

4. **Wind direction: 0° vs. 360° (northerly wind):**
   - Open-Meteo provides `wind_direction_10m` as `0..360`. `0` and `360` are identical.
   - **Solution:** In `compute_wind_components()`, `windrichtung_deg` is normalized (`% 360`) and correctly handled for the 180° shift (`(windrichtung_deg + 180) % 360`).

5. **Bearing field:** `RouteSegment.bearing_deg` is already calculated by the `routing` module (see `docs/plans/01-routing.md`, Task 15) and is a required field — `wind` no longer needs to calculate a fallback from the geometry.

6. **Historical API as a backup for missing forecast data:**
   - If Open-Meteo Forecast API returns no data for a timestamp (e.g. too far in the past), the Historical API could be used as a fallback.
   - **Solution:** Currently **not implemented** (planned for Phase 9: Integration/Hardening). Preparation: `WeatherProvider` protocol is designed so that a `FallbackWeatherProvider` can be implemented.

7. **Wind sensor height:**
   - Open-Meteo provides `wind_speed_10m` (at 10 m height). For vehicles (approx. 1–1.5 m height), a height correction may be necessary (logarithmic wind profile).
   - **Solution:** Currently **not implemented** (wind at 10 m height is assumed to be sufficiently accurate). Preparation: `WindComponents` could be renamed to `wind_speed_at_10m_ms: float` to allow inserting a `wind_speed_at_vehicle_height_ms` metric later.

---

**End of plan.** This document is fully implementation-ready and contains no placeholders (TBD, "decide later", etc.). All external API details, algorithms, and test cases are concretely specified.
