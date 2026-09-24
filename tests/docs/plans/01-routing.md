# Implementation Plan: `routing` Module (Phase 1)

## 1. Purpose & Scope

The `routing` module calculates one or more road routes between a start point, destination, and any intermediate stops (mandatory waypoints) using GraphHopper. It does **not** know energy consumption data, charging planning, or weather conditions — these are handled later by downstream modules (`energy`, `optimization`).

**Scope:**

- HTTP client for GraphHopper server with Docker setup (local instance)
- Calculation of a single route for given waypoints (start → intermediate stops → destination)
- Extraction of all segment information relevant to downstream modules: geometry, length, road class, speed limit, elevation (if available)
- Treatment of intermediate stops as mandatory waypoints that must be traversed in order

**Out of Scope:**

- No own OSM data processing — exclusively use GraphHopper as data source
- No energy-aware routing optimization (no elevation-based cost function in current scope)
- No multiple route alternatives with energy comparison (deferred / not now, see "Open Points" in `06-open-points-contradictions.md`)
- No live traffic data (explicitly not part of the project)

## 2. Dependencies & Phase Assignment

**Phase:** Phase 1 (independent data source modules, parallelizable)

**External Types (read-only, exactly as defined in the register):**

- `tripplanner.trip_input.models.TripRequest` (start coordinate, destination coordinate, zwischenstopps: list[Waypoint], abfahrtszeit, fahrzeugprofil: VehicleProfile, praeferenzen)
- `tripplanner.trip_input.models.Waypoint` (koordinate, aufenthaltsdauer: timedelta | None)
- `tripplanner.routing.models.Route` (segments collection)
- `tripplanner.routing.models.RouteSegment` (segment_index, geometrie/coordinates, laenge_m, strassenklasse, oberflaeche, tempolimit_kmh, steigung_rohdaten, bearing_deg)

**Note on Vehicle Profile:** The `VehicleProfile` from `trip_input` is currently **not** used to influence GraphHopper routing, since GraphHopper requires its own vehicle profile (`profile`) and individual Tesla-specific parameters only become active in Phase 2 (`energy`). In the future, the vehicle profile could be integrated via `custom_model`, but it is intentionally not implemented at present (no elevation penalty in the routing itself — only speed limit weighting).

## 3. Data Models

```python
# src/tripplanner/routing/models.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Tuple
from datetime import timedelta

# Represents a coordinate (latitude, longitude)
Coordinate = Tuple[float, float]  # (lat, lon)

class RouteSegment(BaseModel):
    """A segment of the route with all attributes relevant to downstream modules."""

    segment_index: int = Field(..., description="Zero-based index of this segment in the route")
    geometrie: List[Coordinate] = Field(
        ..., description="List of (lat, lon) coordinates describing the segment"
    )
    laenge_m: float = Field(..., gt=0, description="Length of the segment in meters")
    strassenklasse: str = Field(
        ..., description="Road class (MOTORWAY, TRUNK, PRIMARY, SECONDARY, TRACK, etc.)"
    )
    oberflaeche: str | None = Field(
        default=None,
        description="Road surface from GraphHopper Path detail `surface` (e.g. asphalt, gravel, dirt), None if not available. Consumed by `energy` for the rolling resistance factor (see docs/plans/06-energy.md, section 5.1.1).",
    )
    tempolimit_kmh: int | None = Field(
        default=None, ge=0, description="Speed limit in km/h (None if not available)"
    )
    steigung_rohdaten: float | None = Field(
        default=None, ge=-100, le=100, description="Slope in percentage (None if not available)"
    )
    bearing_deg: float = Field(
        ...,
        ge=0.0,
        lt=360.0,
        description="Direction of travel (bearing) at segment start in degrees (0=N, 90=E), calculated by the routing module from segment start/end coordinate (forward azimuth, WGS84 great circle). Consumed by `wind` for wind component projection.",
    )

class Route(BaseModel):
    """The complete calculated route with metadata."""

    segments: List[RouteSegment] = Field(
        ..., description="List of all route segments in travel direction"
    )
    gesamtlaenge_m: float = Field(..., gt=0, description="Total route length in meters")
    geometrie: List[Coordinate] = Field(
        ..., description="Full route geometry as a list of coordinates"
    )
    bbox: Tuple[float, float, float, float] | None = Field(
        default=None, description="Bounding box [min_lat, min_lon, max_lat, max_lon] (optional)"
    )

class GraphHopperResponse(BaseModel):
    """Internal representation of a GraphHopper /route API response (for internal processing only)."""

    paths: List[GraphHopperPath]
    info: GraphHopperInfo

class GraphHopperPath(BaseModel):
    """A path (usually only one) from the GraphHopper response."""

    distance: float  # Meters
    time: int  # Milliseconds
    points: str  # Encoded polyline (points_encoded=True)
    points_encoded: bool = True
    details: dict[str, list[str | float]] = Field(
        default_factory=dict
    )  # Details like road_class, max_speed, average_slope
    instructions: list = Field(default_factory=list)

class GraphHopperInfo(BaseModel):
    """Meta-information about the GraphHopper response."""

    copyright: list[str]
    hints: list[dict] = Field(default_factory=list)
    took: int  # Milliseconds
```

**Additional helper types (not in the register, but necessary):**

- `Coordinate`: Tuple[float, float] — (latitude, longitude). **Consolidation note:** This alias is identical to the `tripplanner.geo.Coordinate` primitive in `docs/plans/00-foundation-tooling.md`. `routing` defines it locally here since `routing` is the first module in the pipeline; once `tripplanner.geo` exists in Phase 0, `routing.models` will import from there instead of redefining locally (no functional difference, only a single source of truth).
- `GraphHopperResponse`/`GraphHopperPath` — For internal processing only, no cross-module interface
- **Coordinate convention (mandatory for the entire project):** All `Coordinate` tuples are `(lat, lon)`, never `(lon, lat)`. Conversion to GeoJSON order `(lon, lat)` happens exclusively at the serialization boundary to the frontend (see `docs/plans/08-simulation-visualization-api.md`, section 5.2), not in domain models.

## 4. Public Interface

```python
# src/tripplanner/routing/__init__.py
from .models import Route, RouteSegment, Coordinate, GraphHopperResponse, GraphHopperPath
from .providers import RoutingProvider, FakeRoutingProvider
from .client import GraphHopperClient

__all__ = [
    "Route",
    "RouteSegment",
    "Coordinate",
    "GraphHopperResponse",
    "GraphHopperPath",
    "RoutingProvider",
    "FakeRoutingProvider",
    "GraphHopperClient",
]
```

```python
# src/tripplanner/routing/providers.py
from abc import ABC, abstractmethod
from typing import Protocol
from tripplanner.trip_input.models import TripRequest
from tripplanner.routing.models import Route

class RoutingProvider(Protocol):
    """Interface for routing providers. Enables fake implementations for testing."""

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Calculates a route for the given TripRequest."""
        ...

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        ziel: Coordinate,
        zwischenstopps: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Calculates a route with explicit intermediate stops."""
        ...

class GraphHopperRoutingProvider:
    """Concrete implementation via GraphHopper HTTP API."""

    def __init__(self, client: GraphHopperClient, use_custom_model: bool = False):
        self.client = client
        self.use_custom_model = use_custom_model

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Calculates a route for a TripRequest (including intermediate stops)."""
        # Convert TripRequest → GraphHopper parameters
        # Call self.client.route(...)
        # Map GraphHopperResponse → Route
        ...

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        ziel: Coordinate,
        zwischenstopps: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Calculates a route with intermediate stops via GraphHopper."""
        # Call client.route with points=[start] + [zwischenstopps] + [ziel]
        ...
```

```python
# src/tripplanner/routing/client.py
from typing import List, Tuple
from httpx import AsyncClient
from tripplanner.routing.models import GraphHopperResponse, GraphHopperPath

Coordinate = Tuple[float, float]

class GraphHopperClient:
    """HTTP client for GraphHopper API. Handles authentication, request/response mapping."""

    def __init__(self, base_url: str = "http://localhost:8989", api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = AsyncClient(base_url=base_url, timeout=60.0)

    async def route(
        self,
        points: List[Coordinate],
        profile: str = "car",
        elevation: bool = False,
        details: List[str] | None = None,
        custom_model: dict | None = None,
    ) -> GraphHopperResponse:
        """
        GraphHopper /route HTTP endpoint.

        Args:
            points: List of [lon, lat] coordinates (at least 2)
            profile: GraphHopper profile (e.g. "car", "bike", "foot", or custom)
            elevation: If True, include elevation in polyline
            details: List of desired path details (e.g. ["road_class", "max_speed", "average_slope", "surface"])
            custom_model: Optional custom_model JSON for individual vehicle profile

        Returns:
            GraphHopperResponse with decoded polyline and details

        Raises:
            ValueError: If fewer than 2 points are provided
            httpx.HTTPStatusError: On HTTP errors (4xx/5xx)
        """
        # Convert points: (lat, lon) → [lon, lat]
        gh_points = [[lon, lat] for lat, lon in points]
        payload = {"point": gh_points, "profile": profile, "elevation": elevation}

        if details:
            payload["details"] = details

        if custom_model:
            payload["custom_model"] = custom_model

        response = await self._client.post("/route", json=payload)
        response.raise_for_status()

        return GraphHopperResponse.model_validate(response.json())

    async def close(self) -> None:
        """Closes the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "GraphHopperClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
```

## 5. External Integration / Algorithm Details

### GraphHopper Docker Setup

**Version:** 11.0 (current stable release, October 14, 2025)

**Docker image:** `israelhikingmap/graphhopper` or `harelmazor/graphhopper` (both actively maintained)

**Docker Compose example (for local development server):**

```yaml
version: "3.8"
services:
  graphhopper:
    image: israelhikingmap/graphhopper:11.0
    container_name: tesla-trips-graphhopper
    ports:
      - "8989:8989"
    volumes:
      - ./data:/data
    environment:
      - GRAPHHOPPER_JAVA_OPTS=-Xms1g -Xmx4g
    restart: unless-stopped
```

**OSM data source:** Geofabrik extracts

- Germany: `germany-latest.osm.pbf` (~4.5 GB)
- Denmark: `denmark-latest.osm.pbf` (from `europe/denmark.html`)
- Sweden: `sweden-latest.osm.pbf` (~772 MB)

**Extract method for DE/DK/SE:**

```bash
# Option 1: Download individual countries
wget https://download.geofabrik.de/europe/germany-latest.osm.pbf
wget https://download.geofabrik.de/europe/denmark-latest.osm.pbf
wget https://download.geofabrik.de/europe/sweden-latest.osm.pbf

# Option 2: Europe full extract + osmconvert for region clipping
# Bounding box: west=-10, south=47, east=34, north=71 (approx DE/DK/SE)
wget https://download.geofabrik.de/europe-latest.osm.pbf
osmconvert europe-latest.osm.pbf -b=-10,47,34,71 -o=de-dk-se.osm.pbf
```

**GraphHopper with Custom Model:**

```yaml
# In config.yml of GraphHopper container
profiles:
  - name: tesla_model3
    vehicle: car
    custom_model_files: [tesla_model3.json]

custom_models.directory: /data/models
```

**Tesla Model 3 Custom Model JSON (`tesla_model3.json`):**

```json
{
  "speed": [
    {
      "if": "road_class == MOTORWAY",
      "limit_to": 130
    },
    {
      "if": "true",
      "limit_to": 100
    }
  ],
  "priority": [
    {
      "if": "road_class == MOTORWAY",
      "multiply_by": 1.0
    }
  ],
  "distance_influence": 0
}
```

*Justification:*

- Adjust speed limits: On highways in DE/DK/SE typical 130 km/h instead of default (usually 120 km/h for car), restricted to 100 km/h in cities.
- No motorway avoidance — Tesla Model 3 is allowed to use highways.
- `distance_influence: 0` → prefers fastest route (no forced preference for shorter paths at equal travel time).
- Elevation: Currently **not** included via `custom_model` — only in Phase 2 (`energy`) by researching elevations from elevation data.

### GraphHopper `/route` HTTP API Parameters

**Endpoint:** `POST /route`

**Relevant parameters (for this module):**

| Parameter | Type | Required | Description |
| ----------- | ----- | --------------- | -------------- |
| `point` | array[lon, lat] | Yes | At least 2 coordinates (start, [intermediate stops], destination) |
| `profile` | string | Yes | GraphHopper profile name (e.g. "car", "tesla_model3") |
| `elevation` | boolean | No | If `true`, include elevation in polyline (for slope calculation) |
| `points_encoded` | boolean | No | Default: `true` (polyline encode), `false` → GeoJSON |
| `details` | array[string] | No | Desired path details: `["road_class", "max_speed", "average_slope", "max_slope", "surface"]` |

**Relevant Path Details (for downstream modules):**

| Detail | Type | Description |
| -------- | ----- | -------------- |
| `road_class` | string | MOTORWAY, TRUNK, PRIMARY, SECONDARY, TRACK, STEPS, CYCLEWAY, FOOTWAY, OTHER |
| `max_speed` | number | Speed limit in km/h (0 = no limit, -1 = not available) |
| `average_slope` | number | average slope in percentage (100 * Δh / d) |
| `max_slope` | number | maximum slope in the segment in percentage (along the segment) |
| `surface` | string | PAVED, GRAVEL, DIRT, GRASS, etc. (for later rolling resistance calculation) |

**Example Request (Python/httpx):**

```python
payload = {
    "point": [[lon1, lat1], [lon2, lat2], [lon3, lat3]],  # [lon, lat] order!
    "profile": "car",
    "elevation": True,
    "details": ["road_class", "max_speed", "average_slope", "surface"],
}
response = await client.post("/route", json=payload)
```

**Example Response Snippet (path details):**

```json
{
  "paths": [{
    "distance": 12345.678,
    "time": 543210,
    "points_encoded": true,
    "points": "o}`jH...",
    "details": {
      "road_class": ["PRIMARY", "SECONDARY", "TRACK"],
      "max_speed": [100, 80, 50],
      "average_slope": [1.2, -0.5, 3.8]
    }
  }]
}
```

### Slope Calculation from GraphHopper Details

- **`average_slope`** is already provided in percentage (signed decimal), needs no further calculation.
- **`max_slope`** is the maximum slope within the segment (more relevant for recuperation).
- If `elevation: true` is set, the raw polyline with elevation information can also be retrieved (for later more precise calculation).

## 6. Test Strategy

### Unit Tests (Mock/Fake, without GraphHopper Server)

**Fixtures:**

- `tests/fixtures/routing/graphhopper_response_basic.json`: Minimal response without details
- `tests/fixtures/routing/graphhopper_response_with_details.json`: Response with `road_class`, `max_speed`, `average_slope`, `surface` details
- `tests/fixtures/routing/expected_route_model.json`: Expected Pydantic `Route` object
- `tests/fixtures/routing/waypoints_testcase.json`: Example `TripRequest` with intermediate stops

**Test Cases:**

1. **Given:** Valid `TripRequest` without intermediate stops  
   **When:** `berechne_route()` is called  
   **Then:** `Route` object is returned, `gesamtlaenge_m` > 0, `segments` ≥ 1, `tempolimit_kmh` and `strassenklasse` are set (or `None` if not available)

2. **Given:** `TripRequest` with 2 intermediate stops  
   **When:** Route is calculated  
   **Then:** Route contains segments in correct order Start → Stop1 → Stop2 → Destination, Total length sum of segment lengths (within 1% tolerance)

3. **Given:** GraphHopper response with `average_slope` details  
   **When:** `RouteSegment` is extracted  
   **Then:** `steigung_rohdaten` is correctly set (or `None` if `average_slope` is missing), `tempolimit_kmh` ≥ 0 or `None`, `strassenklasse` is a valid value from `["MOTORWAY", "TRUNK", "PRIMARY", "SECONDARY", "TRACK", "OTHER"]`

### Integration Tests (against local GraphHopper Server)

**Fixture:** Local GraphHopper container (via `pytest-docker` or manual start) with `germany-latest.osm.pbf`

**Test Cases:**

1. **Given:** Start/End in Germany (e.g. Berlin → Hamburg)  
   **When:** `GraphHopperClient.route()` is called  
   **Then:** HTTP Status 200, response contains valid polyline, `distance` ≈ 250 km (within ±5 km tolerance)

2. **Given:** Route with intermediate stop in Denmark (Berlin → Copenhagen → Malmö)  
   **When:** Route calculated  
   **Then:** Route contains at least 3 segments (Start→Stop, Stop→Destination), `gesamtlaenge_m` ≈ 750 km (within tolerance)

**Marker:** `@pytest.mark.integration`

**Test File Structure:**

```
tests/routing/
├── test_routing.py          # Unit Tests (fake provider)
├── test_providers.py        # Integration Tests (against GraphHopper)
└── conftest.py              # Fixture: graphhopper_client, example_trip_request
```

## 7. Task Checklist

- [ ] **Task 1:** Create module skeleton (`src/tripplanner/routing/`, `tests/routing/`, `docs/plans/01-routing.md` already exists). No need to create `pyproject.toml` entries for `rasterio` dependency since `rasterio` is only needed by `elevation` module. Add `httpx` (already included in root `pyproject.toml`).

- [ ] **Task 2:** Implement `src/tripplanner/routing/models.py` with `RouteSegment`, `Route`, `GraphHopperResponse`, `GraphHopperPath`. Define `Coordinate = Tuple[float, float]`. Add validators (`gt=0` for lengths, `ge=-100, le=100` for slope).

- [ ] **Task 3:** Implement `src/tripplanner/routing/client.py` with `GraphHopperClient.route()`. Implement request mapping (Python `Coordinate` → GraphHopper `[lon, lat]`), parameter passing (`elevation`, `details`), response parsing (`GraphHopperResponse.model_validate(response.json())`). Implement context manager (`__aenter__`/`__aexit__`).

- [ ] **Task 4:** Implement `src/tripplanner/routing/providers.py` with `RoutingProvider` Protocol and `GraphHopperRoutingProvider`. Implement `berechne_route()` (convert `TripRequest` → `GraphHopperClient.route()` with points, profile="car", elevation=True, details=["road_class","max_speed","average_slope","surface"]). Implement `berechne_route_mit_waypoints()` (explicit waypoint list).

- [ ] **Task 5:** Implement mapping logic between `GraphHopperPath` and `Route`. Decode polyline (GraphHopper `points` → list of `Coordinate`), extract `details` into segment attributes (`road_class` → `strassenklasse`, `max_speed` → `tempolimit_kmh`, `average_slope` → `steigung_rohdaten`, `surface` → `oberflaeche`). Generate `Route.gesamtlaenge_m = sum(s.laenge_m for s in segments)`.

- [ ] **Task 6:** Create unit tests in `tests/routing/test_routing.py`. Test mapping from `GraphHopperResponse` → `Route`. Test handling of missing details (`details` empty → `tempolimit_kmh=None`, `steigung_rohdaten=None`).

- [ ] **Task 7:** Create integration tests in `tests/routing/test_providers.py`. Test `GraphHopperClient.route()` against local GraphHopper server (Docker container). Test `GraphHopperRoutingProvider.berechne_route()` with `TripRequest` (Berlin → Hamburg).

- [ ] **Task 8:** Create fixtures: `tests/fixtures/routing/graphhopper_response_basic.json`, `tests/fixtures/routing/graphhopper_response_with_details.json`, `tests/fixtures/routing/expected_route_model.json`, `tests/fixtures/routing/waypoints_testcase.json`. Use real GraphHopper responses from Docker test or synthetic examples.

- [ ] **Task 9:** Write docstrings for all public functions per Google style (httpx, pydantic, ruff-D rules). Example: `GraphHopperClient.route()`: Args/Returns/Raises as described in the plan schema.

- [ ] **Task 10:** Implement `src/tripplanner/routing/__init__.py` with exports of `Route`, `RouteSegment`, `Coordinate`, `GraphHopperResponse`, `GraphHopperPath`, `RoutingProvider`, `FakeRoutingProvider`, `GraphHopperClient`.

- [ ] **Task 11:** Create `docker-compose.yml` for local GraphHopper (version 11.0, volume `./data:/data`, ports `8989:8989`, JVM options `-Xms1g -Xmx4g`). Write README snippet for start: `docker-compose up -d`, wait for log "GraphHopper is starting..." (approx. 2–5 minutes).

- [ ] **Task 12:** Implement `FakeRoutingProvider` (for unit tests without GraphHopper). Return dummy `Route` containing `RouteSegment` with synthetic data (lengths ≈ 100 km, `tempolimit_kmh=100`, `strassenklasse="PRIMARY"`, `oberflaeche="asphalt"`, `steigung_rohdaten=1.5`).

- [ ] **Task 13:** Run `ruff check src/tripplanner/routing/ tests/routing/` (select E,F,I,UP,B,SIM,PL,RUF) and fix all messages. Run `mypy src/tripplanner/routing/` with `--strict` and fix type check errors (full type annotations, no `Any` fallbacks).

- [ ] **Task 14:** Write `docs/plans/01-routing.md` completely (this plan). Check that all sections (Purpose & Scope, Dependencies, Data Models, Public Interface, External Integration, Test Strategy, Task Checklist, Risks) are included and no "TBD" placeholders remain.

- [ ] **Task 15:** Add `RouteSegment.bearing_deg` calculation in `providers.py`/`routing.py`: forward azimuth from first and last coordinate of `segment.geometrie` (formula: `atan2(sin(Δlon)·cos(lat2), cos(lat1)·sin(lat2) − sin(lat1)·cos(lat2)·cos(Δlon))`, normalized to `[0, 360)`). Unit test with known cardinal directions (North/East/South/West).

## 8. Risks & Open Technical Questions

1. **Polyline decoding:** GraphHopper uses the same polyline encoding as Google Maps (Encoded Polyline Algorithm). Using an established library (`polyline` PyPI package) is recommended. If not available, implement decoding per the official algorithm.

2. **Edge cases with `max_speed`:** GraphHopper returns `max_speed: 0` for streets without signage (e.g. residential streets in DE) or `-1` if not known. The `routing` module must either treat these values as `None` (no limit) or interpret them as a typical default speed (the `energy` module will decide later for calculation). In the `routing` module, `0` or `-1` is stored as `tempolimit_kmh=None`.

3. **Elevation details without elevation data:** If GraphHopper was started with `elevation: true` but no DEM data is available for the route, `average_slope` may return `null` or `0`. In the `routing` module, this is treated as `steigung_rohdaten=None`.

4. **Multiple routes in GraphHopper response:** The response can contain multiple paths (with `alt=true` parameter). The `routing` module currently uses **only the first path** (`paths[0]`). If multiple routes are desired (deferred / not now), the module must be extended.

5. **Traffic forecasts:** GraphHopper can consider live traffic (via `weighting=shortest` with `traffic=true`). This is not currently planned (traffic is "not part of this project"), so `weighting=fastest` without traffic data is used.

6. **Speed limit interpolation:** If `max_speed` is only available per segment (per edge) but a `RouteSegment` consists of multiple edges (for long road sections), an average speed can be calculated. For the current scope, the first or average `max_speed` of the segment is used.

7. **Cross-border routing (DE/DK/SE):** GraphHopper supports cross-border routing out of the box as long as the OSM data is contiguous (Germany, Denmark, Sweden are included in the Europe extract). No additional action needed.

8. **Missing road classes:** If a road has no `road_class` (e.g. private driveways), GraphHopper returns `"OTHER"`. The module accepts this value.

9. **GraphHopper container startup time:** The first start after `docker-compose up` can take 2–10 minutes (OSM import). CI/CD pipelines must implement a wait logic (polling on the `/health` endpoint).

10. **Reproducibility:** GraphHopper uses an internal graph cache (`/data/graph-cache`). For reproducible tests (same OSM file → same route), it must be ensured that no external changes (e.g. Waze traffic updates) occur. In practice, the route is reproducible for the same OSM file.
