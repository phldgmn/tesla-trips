# Implementation Plan: `elevation` Module (Phase 1)

---

## 1. Purpose & Scope

**Purpose:** The `elevation` module extracts the elevation profile along a given route and derives the uphill/downhill gradient profile per segment from it.

**Concrete deliverables:**

- Elevation value (in meters) per route point (from DEM tiles)
- Slope in percent per segment, calculated from elevation difference and horizontal distance
- Handling of tile boundaries (automatic combination of multiple DEM tiles)
- Handling of invalid elevation values (e.g. sea areas → 0 m)

**Delimitation from other modules:**

- No weather or construction-site integration: pure geometry/elevation only
- No energy computation: the module supplies raw data (elevation, slope) that `energy` consumes
- No rerouting: route points come from `routing`; the module does not change the route

**Explicitly NOT in scope:**

- Downloading/caching DEM data (upstream data pipeline, see section 5)
- Live updates of the DEM stack (static data set only)
- Interpolation between route points (point values only, no intermediate values)
- Computation crossing UTM zone boundaries (assumption: Western Europe, a single uniform UTM zone, or WGS84->UTM transformation before the call)

---

## 2. Dependencies & Phase Assignment

**Phase:** Phase 1 (data-source modules)

**Dependencies on other modules (consumed models.py types):**

- `tripplanner.routing.models.Route`: consumes `Route.segments` to extract coordinates
- `tripplanner.routing.models.RouteSegment`: uses `RouteSegment.geometrie` for coordinates
- No imported implementation details — import only `tripplanner.routing.models`

**Consumed external data sources:**

- Copernicus DEM GLO-30 (Cloud Optimized GeoTIFFs, EPSG:4326 WGS84 or UTM zones)
- Local DEM tiles (either in the repo or in the `data/elevation/` directory)

**Produced data models for other modules:**

- `tripplanner.elevation.models.ElevationPoint` (defined in the registry)
- `tripplanner.elevation.models.SegmentGradient` (defined in the registry)

**Phase dependency:** The module is independent of the other Phase 1 modules (`routing`, `weather`, `construction`, `charging_infrastructure`) — it processes the route geometry from `routing.models` but needs no other data. It is a PREREQUISITE for `wind` (needs the elevation profile list) and `energy` (needs `SegmentGradient`).

---

## 3. Data Models

All Pydantic models in `src/tripplanner/elevation/models.py`:

```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Tuple
from datetime import datetime
from geographiclib.geodesic import Geodesic

class ElevationPoint(BaseModel):
    """Elevation value at a coordinate."""

    koordinate: Tuple[float, float] = Field(description="latitude, longitude (WGS84, degrees)")
    hoehe_m: float = Field(
        ge=-100,
        le=9000,
        description="elevation above sea level in meters (invalid values: -9999 → unpopulated)",
    )

    @field_validator("koordinate")
    @classmethod
    def validate_koordinate(cls, v: Tuple[float, float]) -> Tuple[float, float]:
        lat, lon = v
        if not (-90 <= lat <= 90):
            raise ValueError("latitude must be between -90 and 90 degrees")
        if not (-180 <= lon <= 180):
            raise ValueError("longitude must be between -180 and 180 degrees")
        return v

class SegmentGradient(BaseModel):
    """Uphill/downhill gradient per segment from elevation difference and horizontal distance."""

    segment_index: int = Field(ge=0, description="index of the RouteSegment (0-based)")
    steigung_prozent: float = Field(
        description="slope in percent (positive = uphill, negative = downhill)"
    )
    hoehendifferenz_m: float = Field(
        description="elevation difference between the segment's start and end point in meters"
    )
    horizontale_distanz_m: float = Field(
        description="horizontal distance (not along the route, but great-circle projection) in meters"
    )

    @field_validator("steigung_prozent", "hoehendifferenz_m", "horizontale_distanz_m")
    @classmethod
    def validate_values(cls, v: float) -> float:
        if v < 0 and "steigung" not in cls.__name__:
            # hoehendifferenz_m and horizontale_distanz_m can be negative (downhill)
            return v
        # steigung_prozent can be negative (downhill)
        return v
```

**Additional internal types (not in the registry, but internal to the module):**

```python
class DEMTileKey(BaseModel):
    """Key for a DEM tile (coordinate bbox + CRS reference)."""

    min_lat: float = Field(ge=-90, le=90)
    max_lat: float = Field(ge=-90, le=90)
    min_lon: float = Field(ge=-180, le=180)
    max_lon: float = Field(ge=-180, le=180)
    crs_epsg: int = Field(default=4326, description="EPSG code of the CRS")

class DEMTile(BaseModel):
    """In-memory representation of a DEM tile with metadata."""

    key: DEMTileKey
    raster_data: bytes  # Raw GeoTIFF data (for caching only, do not export)
    transform: List[float] = Field(
        description="affine transform matrix [a, b, c, d, e, f] for pixel->world"
    )
    width: int
    height: int
    nodata_value: float = Field(default=-9999)
```

---

## 4. Public Interface

**File structure:**

```
src/tripplanner/elevation/
├── __init__.py        # exports: ElevationProvider, get_elevation_profile, calculate_segment_gradients
├── models.py           # see section 3
├── elevation.py        # core logic (public functions + provider interface)
└── providers.py        # DEMDataSourceProtocol + GeoTIFFDataSource + FakeDataSource
```

**Public API in `src/tripplanner/elevation/elevation.py`:**

```python
from typing import Protocol, List, Tuple
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.elevation.models import ElevationPoint, SegmentGradient

class DEMDataSourceProtocol(Protocol):
    """Protocol for DEM data sources (for testability)."""

    def get_elevation(self, lat: float, lon: float) -> float:
        """Query the elevation value at a coordinate. -9999 = invalid."""
        ...

    def get_elevations_batch(self, coordinates: List[Tuple[float, float]]) -> List[float]:
        """Elevation values for multiple coordinates (optimized for batch lookup)."""
        ...

class ElevationProvider:
    """Main provider class for elevation data."""

    def __init__(self, data_source: DEMDataSourceProtocol):
        self.data_source = data_source

    def get_elevation_profile(
        self, route: Route, sampling_distance_m: float = 100.0
    ) -> List[ElevationPoint]:
        """
        Extracts the elevation profile along the route.

        Args:
            route: The route with segments (from routing.models)
            sampling_distance_m: sampling distance in meters (default: 100 m)

        Returns:
            List of ElevationPoint for every sample point (incl. start/end of each segment)
        """
        ...

    def calculate_segment_gradients(
        self, elevation_points: List[ElevationPoint], route: Route
    ) -> List[SegmentGradient]:
        """
        Computes the uphill/downhill gradient per segment from elevation difference and horizontal distance.

        Args:
            elevation_points: ElevationPoints in route order (start->destination)
            route: The original route (for segment geometry)

        Returns:
            List of SegmentGradient (one per segment)
        """
        ...

def calculate_horizontal_distance(
    coord1: Tuple[float, float], coord2: Tuple[float, float]
) -> float:
    """
    Computes the horizontal distance between two coordinates (WGS84).

    Args:
        coord1: (latitude, longitude) point 1
        coord2: (latitude, longitude) point 2

    Returns:
        Horizontal distance in meters (not along the route!)
    """
    ...
```

**Exports in `src/tripplanner/elevation/__init__.py`:**

```python
from .models import ElevationPoint, SegmentGradient
from .elevation import ElevationProvider, calculate_horizontal_distance

__all__ = [
    "ElevationPoint",
    "SegmentGradient",
    "ElevationProvider",
    "calculate_horizontal_distance",
]
```

---

## 5. External Integration / Algorithm Details

### 5.1 DEM Data Source: Copernicus DEM GLO-30

**Source:** AWS Open Data Registry `copernicus-dem-30m` (bucket: `copernicus-dem-30m.s3.eu-central-1.amazonaws.com`)

**Rationale for AWS S3 (not OpenTopography API):**

- Free, unrestricted use (Copernicus Open License)
- No API key registration needed
- Cloud Optimized GeoTIFFs (COG) directly usable
- Region `eu-central-1` close to the project location (lower latency)
- Read access without an AWS account (public bucket ACL)

**File format & tile schema:**

- Format: Cloud Optimized GeoTIFF (COG), LZW compression
- Resolution: 30 m × 30 m (GLO-30 Public)
- CRS: WGS84 / EPSG:4326 (geodetic) or UTM zones (derived)
- Tile size: 1° × 1° (width × length)
- File name schema: `Copernicus_DSM_COG_{ZONENCODE}_{LAT}_{ZONENCODE}_{LON}_DEMSUB.tif`
  - Example: `Copernicus_DSM_COG30_N50_00_E008_00_DEM.tif` (50°N, 8°E)
- Nodata value: `-9999` (invalid data, e.g. oceans)

**rasterio workflow for elevation lookup:**

```python
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.crs import CRS
import numpy as np

def load_dem_tile(tile_path: str) -> rasterio.DatasetReader:
    """Loads a DEM tile and validates the CRS."""
    src = rasterio.open(tile_path)
    if src.crs != CRS.from_epsg(4326):
        # Transform UTM zones to WGS84 (only once at load time)
        transform, width, height = calculate_default_transform(
            src.crs, CRS.from_epsg(4326), src.width, src.height, *src.bounds
        )
        # Reprojection into a temporary array (or lazy loading)
        # For production code: lazy via WarpedVRT
    return src

def sample_elevation(src: rasterio.DatasetReader, lat: float, lon: float) -> float:
    """
    Extracts the elevation value at a coordinate.

    Args:
        src: An open rasterio.DatasetReader
        lat: latitude (WGS84)
        lon: longitude (WGS84)

    Returns:
        Elevation value in meters or -9999 (nodata)
    """
    # Check whether the coordinate lies within the bounds
    if not (
        src.bounds.left <= lon <= src.bounds.right and src.bounds.bottom <= lat <= src.bounds.top
    ):
        return -9999  # Outside the tile

    # Conversion world -> pixel (row, col)
    row, col = src.index(lon, lat)  # rasterio index() takes (x, y) = (lon, lat) for EPSG:4326

    # Reading the value (band 1 = elevation)
    band_data = src.read(1, window=((row, row + 1), (col, col + 1)))
    value = band_data[0, 0]

    # Nodata check
    if np.isnan(value) or value == src.nodata:
        return -9999
    return value
```

**Tile boundary handling:**

- Coordinates within a segment may span multiple tiles
- Algorithm: determine all relevant tile keys for the segment's bbox
- If a coordinate lies outside the current tile, try the neighboring tile
- Priority: exact tile > neighboring tile > -9999 (undefined)

**Data pipeline model (local preparation):**

1. Manually download DEM tiles for Western Europe (or script `scripts/fetch_dem_tiles.py`)
2. Storage location: `data/elevation/copernicus/` (in .gitignore)
3. Index file `data/elevation/tile_index.json` with bbox metadata (optional, for fast tile lookup)
4. Caching: `functools.lru_cache` for `rasterio.open()` (avoid expensive opening)

---

### 5.2 Slope Calculation

**Mathematical formula (verified via web search):**

$$\text{Slope (\%)} = \frac{\text{Elevation Difference}\ [\text{m}]}{\text{Horizontal Distance}\ [\text{m}]} \times 100$$

**Horizontal distance** is the great-circle projection (not along the route!):

- Given: Two coordinates $(lat_1, lon_1)$ and $(lat_2, lon_2)$
- Compute geodetic distance with `geographiclib` (exact on WGS84 spheroid)

```python
from geographiclib.geodesic import Geodesic

def calculate_horizontal_distance(
    coord1: Tuple[float, float], coord2: Tuple[float, float]
) -> float:
    """
    Computes the horizontal distance between two WGS84 coordinates.

    Args:
        coord1: (latitude, longitude)
        coord2: (latitude, longitude)

    Returns:
        Horizontal distance in meters (not along the route!)
    """
    lat1, lon1 = coord1
    lat2, lon2 = coord2

    geod = Geodesic.WGS84
    inv = geod.Inverse(lat1, lon1, lat2, lon2)
    return inv["s12"]  # Distance in meters
```

**Algorithm in `calculate_segment_gradients()`:**

```python
def calculate_segment_gradients(
    self, elevation_points: List[ElevationPoint], route: Route
) -> List[SegmentGradient]:
    """Computes slope per segment."""
    gradients: List[SegmentGradient] = []

    # Route segments must correlate with ElevationPoints
    # Assumption: elevation_points contains start+end of each segment in order

    point_idx = 0
    for seg_idx, segment in enumerate(route.segments):
        if point_idx + 1 >= len(elevation_points):
            break

        start_point = elevation_points[point_idx]
        end_point = elevation_points[point_idx + 1]

        # Elevation difference (end - start; positive = uphill, negative = downhill)
        dh = end_point.hoehe_m - start_point.hoehe_m

        # Compute horizontal distance (not route length!)
        horizontal_dist = calculate_horizontal_distance(
            start_point.koordinate, end_point.koordinate
        )

        if horizontal_dist == 0:
            gradient_pct = 0.0  # Avoid division by zero
        else:
            gradient_pct = (dh / horizontal_dist) * 100

        gradients.append(
            SegmentGradient(
                segment_index=seg_idx,
                steigung_prozent=gradient_pct,
                hoehendifferenz_m=dh,
                horizontale_distanz_m=horizontal_dist,
            )
        )

        point_idx += 1  # Next segment starts at this segment's end point

    return gradients
```

**Note:** The formula is identical to the USGS definition (<https://www.usgs.gov/educational-resources/determine-percent-slope-and-angle-slope>). For angle: $\text{Slope}^\circ = \arctan(\text{Slope\%} / 100)$ — needed in the `energy` module for incline angles.

---

### 5.3 Fake Implementation for Tests (`providers.py`)

```python
from typing import List, Tuple
from tripplanner.elevation.providers import DEMDataSourceProtocol

class FakeDataSource(DEMDataSourceProtocol):
    """Synthetic DEM data for unit tests (no real file I/O)."""
    
    def __init__(self, baseline_elevation: float = 100.0, noise_range: float = 5.0):
        self.baseline = baseline_elevation
        self.noise = noise_range
    
    def get_elevation(self, lat: float, lon: float) -> float:
        # Deterministic based on coordinates (not random!)
        hash_val = hash((round(lat, 5), round(lon, 5))) % 1000
        noise = (hash_val / 1000.0 - 0.5) * 2 * self.noise  # -noise..+noise
        return self.baseline + noise
    
    def get_elevations_batch(self, coordinates: List[Tuple[float, float]]) -> List[float]:
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]
```

---

## 6. Test Strategy

### 6.1 Fixtures (in `tests/fixtures/elevation/`)

**Files:**

1. `copernicus_dem_test_tile.tif` — small synthetic GeoTIFF (5×5 pixels, 10m resolution, 30m reference)
   - Coordinates: 47.0°N, 8.0°E to 47.00015°N, 8.00015°E (approx. 11m × 11m)
   - Values: linear slope from 100m (top-left) to 120m (bottom-right)
2. `tile_index.json` — bbox metadata for test tile
3. `route_segment_example.json` — small RouteSegment example with 4 coordinate points

**Example `route_segment_example.json`:**

```json
{
  "segment_index": 0,
  "koordinaten": [
    [47.0, 8.0],
    [47.00005, 8.00005],
    [47.00010, 8.00010],
    [47.00015, 8.00015]
  ],
  "laenge_m": 22.5,
  "strassenklasse": "A",
  "tempolimit_kmh": 120
}
```

### 6.2 Concrete Test Cases

**Test 1: Single Elevation Lookup (Unit)**

**Given:** `FakeDataSource` with `baseline=150.0`, `noise=2.0`
**When:** `provider.get_elevation(47.0, 8.0)` is called
**Then:** Result is in the range `[148.0, 152.0]` (± noise_range)

```python
def test_fake_data_source_elevation(fake_data_source: DEMDataSourceProtocol):
    """Tests deterministic elevation query."""
    elevation = fake_data_source.get_elevation(47.0, 8.0)
    assert 148.0 <= elevation <= 152.0
```

**Test 2: Slope Calculation with Real GeoTIFF (Integration)**

**Given:** Test GeoTIFF with linear slope (100m → 120m over 11m distance → ~181.8%)
**When:** `ElevationProvider.calculate_segment_gradients()` called with 2 points (start/end)
**Then:** `steigung_prozent ≈ 181.8` (within ±1% tolerance due to raster resolution)

```python
def test_segment_gradient_with_real_dem(
    real_dem_provider: ElevationProvider, route_with_two_points: Route
):
    """Integration test with real DEM tile."""
    points = real_dem_provider.get_elevation_profile(
        route_with_two_points, sampling_distance_m=10.0
    )
    gradients = real_dem_provider.calculate_segment_gradients(points, route_with_two_points)

    assert len(gradients) == 1
    assert gradients[0].hoehendifferenz_m == pytest.approx(20.0, abs=1.0)  # 120-100=20
    assert gradients[0].steigung_prozent == pytest.approx(181.8, abs=2.0)  # 20/11*100
```

**Test 3: Edge Case — Tile Boundary Crossing (Integration)**

**Given:** Route that crosses exactly over a tile boundary (e.g. 47.00015°N)
**When:** `get_elevation_profile()` with real DEM
**Then:** No `IndexError` or `ValueError`, valid elevation values returned (also -9999 is acceptable for ocean)

```python
def test_tile_boundary_crossing(real_dem_provider: ElevationProvider):
    """Tests handling of tile boundaries."""
    coords = [(47.0000, 8.0000), (47.00016, 8.0000)]  # exactly at tile boundary
    # Simulate route with these points
    route = mock_route_with_coords(coords)
    
    points = real_dem_provider.get_elevation_profile(route)
    assert len(points) == 2
    assert -9999 <= points[0].hoehe_m <= 5000  # valid range check
    assert -9999 <= points[1].hoehe_m <= 5000
```

### 6.3 Unit vs. Integration Test Delimitation

| Test | Type | Decorator | Description |
| ------ | ----- | ----------- | -------------- |
| `test_fake_data_source_elevation` | Unit | - | FakeDataSource with deterministic values |
| `test_fake_data_source_batch` | Unit | - | Batch lookup with multiple coordinates |
| `test_horizontal_distance_calculation` | Unit | - | `calculate_horizontal_distance()` with known coordinates |
| `test_segment_gradient_calculation` | Unit | - | Manual creation of ElevationPoints, compute slope |
| `test_real_dem_elevation_lookup` | Integration | `@pytest.mark.integration` | Read real GeoTIFF file, validate coordinates |
| `test_tile_boundary_crossing` | Integration | `@pytest.mark.integration` | Route over tile boundary, edge cases |

---

## 7. Task Checklist

**Prerequisite:** `docs/plans/01-routing.md` is implemented (route models are available)

- [ ] **Task 1:** Define data models (`src/tripplanner/elevation/models.py`)
  - [ ] `ElevationPoint` with validators (lat/lon range, hoehe_m limits)
  - [ ] `SegmentGradient` with validators (no division by zero in tests)
  - [ ] `__init__.py` for exports
  - **Acceptance:** `mypy src/tripplanner/elevation/models.py` without errors

- [ ] **Task 2:** Implement fake data source (`src/tripplanner/elevation/providers.py`)
  - [ ] `DEMDataSourceProtocol` (abc.Protocol)
  - [ ] `FakeDataSource` with deterministic noise function
  - [ ] `get_elevation` and `get_elevations_batch` methods
  - **Acceptance:** `pytest tests/elevation/test_providers.py -v` → 100% coverage

- [ ] **Task 3:** Implement `calculate_horizontal_distance`
  - [ ] Use of `geographiclib.geodesic.Geodesic.WGS84.Inverse`
  - [ ] Handling of identical coordinates (return 0.0)
  - [ ] Docstring with formula and example (Google-Style)
  - **Acceptance:** Manual test: distance (0°,0°) → (0°,1°) ≈ 111.32 km

- [ ] **Task 4:** `ElevationProvider` core logic (`elevation.py`)
  - [ ] `__init__(self, data_source: DEMDataSourceProtocol)`
  - [ ] Implement `get_elevation_profile(route, sampling_distance_m)`
  - [ ] Implement `calculate_segment_gradients(elevation_points, route)`
  - [ ] Error handling: empty route, incomplete elevation_points
  - **Acceptance:** Unit tests with FakeDataSource, no external calls

- [ ] **Task 5:** Prepare test fixtures
  - [ ] Create synthetic 5×5 GeoTIFF (`tests/fixtures/elevation/copernicus_dem_test_tile.tif`)
  - [ ] Generate `tile_index.json` with metadata
  - [ ] `route_segment_example.json` with 4 coordinates
  - **Acceptance:** `rasterio.open("copernicus_dem_test_tile.tif").crs == EPSG:4326`

- [ ] **Task 6:** Integration test with real GeoTIFF
  - [ ] `conftest.py` with fixtures: `real_dem_provider()` (loads test tile once)
  - [ ] Implement `test_segment_gradient_with_real_dem`
  - [ ] Implement `test_tile_boundary_crossing`
  - [ ] Apply `@pytest.mark.integration` decorator
  - **Acceptance:** `pytest -m integration` → 2 tests pass

- [ ] **Task 7:** Write documentation (Google-Style, ruff D-rules)
  - [ ] Add all class, method, function docstrings
  - [ ] Type hints in all signatures (including `*args`/`**kwargs`)
  - [ ] Examples in docstrings (where sensible)
  - **Acceptance:** `ruff check src/tripplanner/elevation/` → no D1xx/D2xx errors

- [ ] **Task 8:** Check `rasterio` installation + write optional DEM fetcher
  - [ ] Run `uv add rasterio`, `rasterio.__version__` ≥ 1.3.0
  - [ ] Check if `rasterio.env.Env.default_credentials` is set (for S3 access)
  - [ ] Optional: create `scripts/fetch_dem_tiles.py` (download for DE/DK/SE)
  - **Acceptance:** `python -c "import rasterio; print(rasterio.__version__)"` → 1.3.x+

- [ ] **Task 9:** Enforce test coverage (pytest-cov)
  - [ ] `pytest --cov=src/tripplanner/elevation --cov-report=term-missing --cov-fail-under=85`
  - [ ] All public functions/at least 90% of internal logic covered
  - [ ] FakeDataSource and integration tests combined for 85%+ coverage
  - **Acceptance:** CLI output shows `85%` or higher

- [ ] **Task 10:** Check linting and typing
  - [ ] `ruff check src/tripplanner/elevation/ tests/elevation/` (select=E,F,I,UP,B,SIM,PL,RUF,D)
  - [ ] `mypy src/tripplanner/elevation/` with `--strict` → no errors
  - [ ] `pre-commit run --all-files` if hook system is set up
  - **Acceptance:** No red lines in lint output

- [ ] **Task 11:** Validate integration with `routing` module
  - [ ] Small end-to-end scenario: `Route` from `routing` → `elevation.get_elevation_profile` → `calculate_segment_gradients`
  - [ ] Test route with 3 segments (start→destination incl. waypoint)
  - [ ] Validation: `len(segments) == len(gradients)`
  - **Acceptance:** `pytest tests/elevation/test_elevation.py::test_integration_with_routing` passes

- [ ] **Task 12:** Optimize caching and performance (optional, not strictly required for Phase 1)
  - [ ] `functools.lru_cache` for `rasterio.open()` (maxsize=4 for Western Europe)
  - [ ] Optimize batch lookup (numpy vectorized operations where possible)
  - [ ] Profiling with `pytest-benchmark` for 100+ coordinates
  - **Acceptance:** Time for 100 elevation lookups ≤ 100ms (on SSD)

---

## 8. Risks & Open Technical Questions

**Already covered by "Decided Open Points" (from docs/06-open-points-contradictions.md):**

- No dependency on live network in unit tests (FakeDataSource covers this)
- Local DEM data source is accepted (no crawler needed)

**Open technical questions (not governed by project specifications):**

1. **UTM zone transformation:** Should the module automatically perform WGS84→UTM, or should it be assumed?
   - **Decision:** WGS84 (EPSG:4326) directly with `rasterio.index()` is sufficient for 30m resolution (error < 1cm for Europe). UTM projection only on demand with `pyproj.Transformer`.

2. **DEM caching strategy:** Should the module have its own cache layer (e.g. SQLite with GeoTIFF blobs)?
   - **Decision:** No dedicated cache in Phase 1 — `rasterio` already uses internal VRT caching. If performance issues arise: `rasterio.vrt.WarpedVRT` for lazy reprojection.

3. **Duplicate coordinate processing:** Should `get_elevation_profile` implement duplicate debouncing (if the route frequently traverses the same coordinate)?
   - **Decision:** No — `sampling_distance_m=100` guarantees sufficient distance to avoid duplicates. If needed: `set` debouncing in the `routing` module (not here).

4. **Nodata handling for sea areas:** Should `-9999` (nodata) be replaced by interpolation?
   - **Decision:** No — retain `-9999` as a marker. The `energy` module can then decide whether to reject or interpolate sea segments.

5. **Large European routes (more than 4 tiles):** How efficient is the dynamic tile lookup?
   - **Solution:** Initial MVP with sequential tile opening. If performance bottleneck: tile index with quadtree (not in Phase 1).

6. **rasterio architectural binding:** Is `rasterio` accepted as a fixed dependency?
   - **Decision:** Yes — `rasterio` is the standard for GeoTIFF in Python and is explicitly mentioned in `docs/02-architecture.md`. Alternative (`rioxarray`) would be overkill.

**Additional risk:**

- **File size:** Copernicus DEM GLO-30 for all of Europe is approximately 500 GB. **Solution:** Download only DE/DK/SE tiles (approx. 20–30 GB), enforce `.gitignore` for `data/elevation/`.
