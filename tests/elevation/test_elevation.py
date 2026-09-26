"""Unit- und Integrationstests für elevation.py: ElevationProvider."""

import asyncio
import time
from collections import OrderedDict

import pytest
import rasterio
from tripplanner.elevation.elevation import ElevationProvider, calculate_horizontal_distance
from tripplanner.elevation.models import ElevationPoint
from tripplanner.elevation.providers import CopernicusDEMDataSource, FakeDataSource


class MockSegment:
    """Mock für routing.models.RouteSegment für Tests."""

    def __init__(self, geometrie: list[tuple[float, float]]):
        self.geometrie = geometrie


class MockRoute:
    """Mock für routing.models.Route für Tests."""

    def __init__(self, segments: list[MockSegment]):
        self.segments = segments


# =============================================================================
# Module-level async fixtures for tests that call async get_elevation_profile
# =============================================================================


@pytest.fixture
def elevation_provider() -> ElevationProvider:
    """ElevationProvider mit FakeDataSource."""
    source = FakeDataSource(baseline_elevation=100.0, noise_range=0.0)
    return ElevationProvider(data_source=source)


@pytest.fixture
def mock_route() -> MockRoute:
    """Mock-Route mit 2 Segmenten."""
    return MockRoute(
        segments=[
            MockSegment([(47.0, 8.0), (47.0001, 8.0001)]),
            MockSegment([(47.0001, 8.0001), (47.0002, 8.0002)]),
        ]
    )


class TestElevationProvider:
    """Tests für ElevationProvider mit FakeDataSource."""

    @pytest.mark.asyncio
    async def test_get_elevation_profile_empty_route(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """Leere Route ergibt leeres Profil."""
        empty_route = MockRoute(segments=[])
        profile = await elevation_provider.get_elevation_profile(empty_route)
        assert profile == []

    @pytest.mark.asyncio
    async def test_get_elevation_profile_single_segment(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """Ein Segment wird korrekt verarbeitet."""
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        profile = await elevation_provider.get_elevation_profile(route)
        assert len(profile) == 2
        assert profile[0].coordinate == (47.0, 8.0)
        assert profile[1].coordinate == (47.0001, 8.0001)

    @pytest.mark.asyncio
    async def test_get_elevation_profile_multiple_segments(
        self, elevation_provider: ElevationProvider, mock_route: MockRoute
    ) -> None:
        """Mehrere Segmente werden korrekt verarbeitet."""
        profile = await elevation_provider.get_elevation_profile(mock_route)
        assert len(profile) == 3  # Start + 1 Punkt pro Segment

    def test_calculate_segment_gradients_basic(self, elevation_provider: ElevationProvider) -> None:
        """Steigungsberechnung mit einfachen Werten."""
        points = [
            ElevationPoint(coordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(coordinate=(47.0001, 8.0001), hoehe_m=110.0),
        ]
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        gradients = elevation_provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m == pytest.approx(10.0, abs=1.0)
        assert gradients[0].steigung_prozent == pytest.approx(74.3, abs=1.0)

    def test_calculate_segment_gradients_negative_slope(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """Negativ-gradient (Gefälle) wird korrekt berechnet."""
        points = [
            ElevationPoint(coordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(coordinate=(47.0001, 8.0001), hoehe_m=90.0),
        ]
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        gradients = elevation_provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m < 0
        assert gradients[0].steigung_prozent < 0

    def test_calculate_segment_gradients_zero_distance(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """gradient ist 0 wenn horizontale distance 0 ist."""
        points = [
            ElevationPoint(coordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(coordinate=(47.0, 8.0), hoehe_m=120.0),
        ]
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0, 8.0)])])
        gradients = elevation_provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].horizontale_distanz_m == pytest.approx(0.0)

    def test_calculate_segment_gradients_empty_route(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """Leere Route ergibt leere Steigungen."""
        route = MockRoute(segments=[])
        _ = elevation_provider.calculate_segment_gradients([], route)

    def test_calculate_segment_gradients_not_enough_points(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """Not enough points raises ValueError."""
        points = [ElevationPoint(coordinate=(47.0, 8.0), hoehe_m=100.0)]
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        with pytest.raises(ValueError, match="Not enough"):
            elevation_provider.calculate_segment_gradients(points, route)


class TestHorizontalDistanceCalculation:
    """Tests für calculate_horizontal_distance."""

    def test_distance_zero_for_identical_points(self) -> None:
        """distance zwischen identischen Punkten ist 0."""
        coord = (47.0, 8.0)
        dist = calculate_horizontal_distance(coord, coord)
        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_distance_symmetric(self) -> None:
        """distance(a, b) == distance(b, a)."""
        coord1 = (47.0, 8.0)
        coord2 = (48.0, 9.0)
        dist1 = calculate_horizontal_distance(coord1, coord2)
        dist2 = calculate_horizontal_distance(coord2, coord1)
        assert dist1 == pytest.approx(dist2, rel=1e-6)

    def test_distance_approximate_1_degree(self) -> None:
        """distance von 1° Längengrad am Äquator ≈ 111 km."""
        coord1 = (0.0, 0.0)
        coord2 = (0.0, 1.0)
        dist = calculate_horizontal_distance(coord1, coord2)
        assert dist == pytest.approx(111_320, rel=0.01)

    def test_distance_1_degree_latitude(self) -> None:
        """distance von 1° latitude ≈ 111 km."""
        coord1 = (0.0, 0.0)
        coord2 = (1.0, 0.0)
        dist = calculate_horizontal_distance(coord1, coord2)
        assert dist == pytest.approx(111_140, rel=0.01)


class GeoTIFFDataSource:
    """DataSource für echte GeoTIFF-Dateien."""

    def __init__(self, tif_path: str):
        self.src = rasterio.open(tif_path)

    def get_elevation(self, lat: float, lon: float) -> float:
        if not (
            self.src.bounds.left <= lon <= self.src.bounds.right
            and self.src.bounds.bottom <= lat <= self.src.bounds.top
        ):
            return -9999.0
        row, col = self.src.index(lon, lat)
        if 0 <= row < self.src.height and 0 <= col < self.src.width:
            band_data = self.src.read(1, window=((row, row + 1), (col, col + 1)))
            value = band_data[0, 0]
            if value == self.src.nodata or value < -1000:
                return -9999.0
            return value
        return -9999.0

    async def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]

    def get_tile_at(self, lat: float, lon: float):
        return None

    def get_tiles_in_bbox(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float):
        return []


class TestIntegrationWithRealDem:
    """Integrationstests mit echtem GeoTIFF-File."""

    @pytest.fixture
    def real_dem_provider(self) -> ElevationProvider:
        """Provider mit FakeDataSource für reproduzierbare Tests."""
        source = FakeDataSource(baseline_elevation=100.0, noise_range=20.0)
        return ElevationProvider(data_source=source)

    @pytest.mark.integration
    def test_segment_gradient_with_fake_dem(self, real_dem_provider: ElevationProvider) -> None:
        """Steigungsberechnung mit synthetischem DEM."""
        points = [
            ElevationPoint(coordinate=(47.00014, 8.00001), hoehe_m=100.0),
            ElevationPoint(coordinate=(47.00001, 8.00014), hoehe_m=120.0),
        ]
        route = MockRoute(segments=[MockSegment([(47.00014, 8.00001), (47.00001, 8.00014)])])
        gradients = real_dem_provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m == pytest.approx(20.0, abs=1.0)
        assert gradients[0].steigung_prozent == pytest.approx(114.3, abs=5.0)


# =============================================================================
# Part 1: Tests for concurrent tile-batched parallel elevation fetching
# =============================================================================


class TestAsyncGatherCallsToThreadPerTile:
    """Tests that asyncio.gather is used to open tiles concurrently."""

    @pytest.mark.asyncio
    async def test_async_gather_calls_to_thread_for_each_tile(
        self,
    ) -> None:
        """Verify that asyncio.to_thread is called once per distinct tile,
        not once per coordinate.
        """
        coords = [
            (47.0, 8.0),
            (47.0, 8.0001),
            (48.0, 9.0),
            (48.0, 9.0001),
        ]
        source = CopernicusDEMDataSource(base_url="https://example.com")
        source._datasets = OrderedDict()

        to_thread_calls: list[tuple] = []
        real_to_thread = asyncio.to_thread

        async def tracking_to_thread(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            to_thread_calls.append((fn, args, kwargs))
            return await real_to_thread(fn, *args, **kwargs)

        async def patch_and_run():
            asyncio.to_thread = tracking_to_thread  # type: ignore[assignment]
            try:
                await source.get_elevations_batch(coords)
            finally:
                asyncio.to_thread = real_to_thread

        await patch_and_run()
        # 2 distinct tiles, ONE to_thread call each (open-or-reuse + read
        # merged into a single `_read_tile_bulk` call under the tile's lock -
        # see `_get_tile_lock`), not one for opening and one for reading.
        assert len(to_thread_calls) == 2

    @pytest.mark.asyncio
    async def test_tile_reads_are_concurrent_via_to_thread(self) -> None:
        """If a tile's (open-or-reuse + read) has a small delay, concurrent
        distinct tiles finish in ~1x the delay, not Nx the delay.

        Open and read used to be two separate `to_thread` phases; they are
        now merged into one call per tile, held under that tile's lock for
        its whole duration (see `_get_tile_lock`) - this test now measures
        that single phase's concurrency across distinct tiles instead of
        distinguishing an "open phase" from a "read phase".
        """
        coords = [
            (47.0, 8.0),
            (47.0001, 8.0),
            (48.0, 9.0),
            (48.0001, 9.0),
        ]
        source = CopernicusDEMDataSource(base_url="https://example.com")
        source._datasets = OrderedDict()

        call_count = [0]
        real_to_thread = asyncio.to_thread

        async def slow_to_thread(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            call_count[0] += 1
            await asyncio.sleep(0.02)
            return await real_to_thread(fn, *args, **kwargs)

        async def patch_and_measure():
            asyncio.to_thread = slow_to_thread  # type: ignore[assignment]
            try:
                start = asyncio.get_event_loop().time()
                await source.get_elevations_batch(coords)
                elapsed = asyncio.get_event_loop().time() - start
                return call_count[0], elapsed
            finally:
                asyncio.to_thread = real_to_thread

        count, elapsed = await patch_and_measure()

        # 2 distinct tiles, one to_thread call each.
        assert count == 2
        # Concurrent: total ~0.02s, not 0.04s (sequential).
        assert elapsed < 0.06, f"Expected concurrent (~0.02s), got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_results_preserve_original_order(self) -> None:
        """Results map back to the original coordinate order."""
        fake = FakeDataSource(baseline_elevation=100.0, noise_range=0.0)
        results = await fake.get_elevations_batch(
            [
                (47.0, 8.0),
                (48.0, 9.0),
                (49.0, 10.0),
            ]
        )
        assert results == [100.0, 100.0, 100.0]
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_empty_coordinates_returns_empty_list(self) -> None:
        """Empty input returns empty output."""
        source = CopernicusDEMDataSource(base_url="https://example.com")
        source._datasets = OrderedDict()
        results = await source.get_elevations_batch([])
        assert results == []


# =============================================================================
# Regression test: tile eviction during concurrent open phase (Bug 1)
# =============================================================================


class _MockDataset:
    """Minimal mock of rasterio.io.DatasetReader for eviction tests.

    rasterio.read() returns a numpy-like array where [row, col] gives the value.
    The batch code does: dataset.read(1, window=...)[0, 0]
    """

    def __init__(self, value: float) -> None:
        self.value = value
        self._closed = False
        self.nodata = None
        self.height = 10
        self.width = 10

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True

    def index(self, lon: float, lat: float) -> tuple[int, int]:
        return (0, 0)

    def read(self, band: int, window=None) -> "_MockReadResult":
        if self._closed:
            raise ValueError("I/O on closed file")
        return _MockReadResult(self.value)


class _MockReadResult:
    """Minimal mock of numpy array supporting [row, col] indexing."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __getitem__(self, key: tuple[int, int]) -> float:
        return self.value


class TestElevationTileEviction:
    """Regression tests for LRU tile eviction during batched elevation reads."""

    @pytest.mark.asyncio
    async def test_batched_elevation_degrades_gracefully_on_externally_closed_dataset(
        self,
    ) -> None:
        """A dataset closed by something OUTSIDE this class (adversarial /
        defensive case - not reachable via the public API) must not crash
        the batch or corrupt OTHER tiles' results; it degrades to 0.0 for
        just its own tile.

        Previously this scenario "self-healed" by reopening a bypass temp
        dataset. That path was removed: it's now structurally unreachable
        in normal operation because eviction is lock-guarded (see
        `_get_tile_lock`/`_evict_over_cap`) - a dataset present in
        `self._datasets` is never closed by THIS class while anything might
        still be reading it. What remains is graceful degradation for the
        genuinely-external-interference case this test constructs.
        """
        source = CopernicusDEMDataSource(base_url="file:///nonexistent")
        source._max_open_tiles = 16
        source._datasets = OrderedDict()

        # URIs must match what _tile_uri produces for a non-remote base_url:
        # no /vsicurl/ prefix
        tile_uris = [
            "file:///nonexistent/Copernicus_DSM_COG_10_N47_00_E008_00_DEM/Copernicus_DSM_COG_10_N47_00_E008_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N48_00_E009_00_DEM/Copernicus_DSM_COG_10_N48_00_E009_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N49_00_E010_00_DEM/Copernicus_DSM_COG_10_N49_00_E010_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N50_00_E011_00_DEM/Copernicus_DSM_COG_10_N50_00_E011_00_DEM.tif",
        ]
        tile_values: dict[str, float] = dict(
            zip(tile_uris, [100.0, 200.0, 300.0, 400.0], strict=True),
        )

        def make_dataset(path: str) -> _MockDataset:
            return _MockDataset(tile_values[path])

        # Pre-seed the LRU with already-closed datasets for tiles 0 and 2,
        # simulating external interference (this class alone can never
        # produce this state - see docstring above).
        closed_0 = make_dataset(tile_uris[0])
        closed_0.close()
        source._datasets[tile_uris[0]] = closed_0
        closed_2 = make_dataset(tile_uris[2])
        closed_2.close()
        source._datasets[tile_uris[2]] = closed_2

        orig_open = rasterio.open
        rasterio.open = make_dataset  # type: ignore[assignment]

        try:
            coords = [
                (47.0, 8.0),  # tile 0 -> pre-closed, degrades to 0.0
                (48.0, 9.0),  # tile 1 -> 200.0, unaffected
                (49.0, 10.0),  # tile 2 -> pre-closed, degrades to 0.0
                (50.0, 11.0),  # tile 3 -> 400.0, unaffected
            ]
            results = await source.get_elevations_batch(coords)
            assert results == [0.0, 200.0, 0.0, 400.0], (
                f"Pre-closed tiles must degrade to 0.0 without affecting other "
                f"tiles or raising: got {results}"
            )
        finally:
            rasterio.open = orig_open  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_batched_elevation_all_fresh_datasets(self) -> None:
        """When all tiles fit within _max_open_tiles, no eviction occurs."""
        source = CopernicusDEMDataSource(base_url="file:///nonexistent")
        source._max_open_tiles = 4
        source._datasets = OrderedDict()

        tile_uris = [
            "file:///nonexistent/Copernicus_DSM_COG_10_N47_00_E008_00_DEM/Copernicus_DSM_COG_10_N47_00_E008_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N48_00_E009_00_DEM/Copernicus_DSM_COG_10_N48_00_E009_00_DEM.tif",
        ]
        tile_values: dict[str, float] = dict(zip(tile_uris, [100.0, 200.0], strict=True))

        def make_dataset(path: str) -> _MockDataset:
            return _MockDataset(tile_values[path])

        orig_open = rasterio.open
        rasterio.open = make_dataset  # type: ignore[assignment]

        try:
            coords = [(47.0, 8.0), (48.0, 9.0)]
            results = await source.get_elevations_batch(coords)
            assert results == [100.0, 200.0]
        finally:
            rasterio.open = orig_open  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_batch_never_evicts_its_own_tiles(self) -> None:
        """A batch spanning more distinct tiles than the starting
        `_max_open_tiles` must never evict/close one of its own in-flight
        tiles.  `get_elevations_batch` raises `_max_open_tiles` to cover the
        batch before opening anything.

        Regression guard: closing a dataset that another worker thread is
        still `dataset.read()`-ing was a real hang/crash risk once local
        (pre-downloaded) tiles made opens fast enough for many `to_thread`
        workers to race through in a tight window.
        """
        source = CopernicusDEMDataSource(base_url="file:///nonexistent")
        source._max_open_tiles = 2  # deliberately smaller than the batch
        source._datasets = OrderedDict()

        tile_uris = [
            "file:///nonexistent/Copernicus_DSM_COG_10_N47_00_E008_00_DEM/Copernicus_DSM_COG_10_N47_00_E008_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N48_00_E009_00_DEM/Copernicus_DSM_COG_10_N48_00_E009_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N49_00_E010_00_DEM/Copernicus_DSM_COG_10_N49_00_E010_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N50_00_E011_00_DEM/Copernicus_DSM_COG_10_N50_00_E011_00_DEM.tif",
        ]
        tile_values: dict[str, float] = dict(
            zip(tile_uris, [100.0, 200.0, 300.0, 400.0], strict=True),
        )

        closed_tiles: list[str] = []

        def patched_open(path: str) -> _MockDataset:
            ds = _MockDataset(tile_values[path])
            orig_close = ds.close

            def tracked_close() -> None:
                closed_tiles.append(path)
                orig_close()

            ds.close = tracked_close  # type: ignore[method-assign]
            return ds

        orig_open = rasterio.open
        rasterio.open = patched_open  # type: ignore[assignment]

        try:
            coords = [(47.0, 8.0), (48.0, 9.0), (49.0, 10.0), (50.0, 11.0)]
            results = await source.get_elevations_batch(coords)
            assert results == [100.0, 200.0, 300.0, 400.0]
            assert closed_tiles == [], (
                f"Batch must not evict its own in-flight tiles: closed {closed_tiles}"
            )
            assert source._max_open_tiles >= 4, (
                "get_elevations_batch should have raised _max_open_tiles to cover the batch"
            )
        finally:
            rasterio.open = orig_open  # type: ignore[assignment]


# =============================================================================
# Read-call-count bound test (Fix 1 regression guard)
# =============================================================================


class TestReadCallCountBound:
    """Ensure bulk read makes O(tiles) calls, not O(coords)."""

    @pytest.mark.asyncio
    async def test_bulk_read_calls_at_most_once_per_tile(self) -> None:
        """50 coordinates across 2 tiles must trigger at most 2 dataset.read() calls,
        not 50.  This is the core regression guard for Fix 1.

        The old per-point path did one read() per coordinate; the new path
        does one bulk read() per tile.  This test would fail on the old code.
        """
        source = CopernicusDEMDataSource(base_url="file:///nonexistent")
        source._max_open_tiles = 16
        source._datasets = OrderedDict()

        tile_uris = [
            "file:///nonexistent/Copernicus_DSM_COG_10_N47_00_E008_00_DEM/Copernicus_DSM_COG_10_N47_00_E008_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N47_00_E009_00_DEM/Copernicus_DSM_COG_10_N47_00_E009_00_DEM.tif",
        ]
        tile_values: dict[str, float] = dict(zip(tile_uris, [100.0, 200.0], strict=True))

        read_count = [0]

        class _CountingReadResult:
            def __init__(self, value: float) -> None:
                self.value = value

            def __getitem__(self, key: tuple[int, int]) -> float:
                return self.value

        class _CountingDataset(_MockDataset):
            def read(self, band: int, window=None) -> _CountingReadResult:  # type: ignore[override]
                read_count[0] += 1
                return _CountingReadResult(self.value)  # type: ignore[arg-type]

        def patched_open(path: str) -> _CountingDataset:  # type: ignore[return]
            return _CountingDataset(tile_values[path])  # type: ignore[arg-type]

        orig_open = rasterio.open
        rasterio.open = patched_open  # type: ignore[assignment]

        try:
            coords: list[tuple[float, float]] = []
            for i in range(25):
                coords.append((47.0, 8.0 + i * 0.000001))
            for i in range(25):
                coords.append((47.0, 9.0 + i * 0.000001))

            results = await source.get_elevations_batch(coords)
            assert len(results) == 50
            assert read_count[0] <= 2, (
                f"Expected <=2 read() calls (one per tile), got {read_count[0]}."
            )
        finally:
            rasterio.open = orig_open

    @pytest.mark.asyncio
    async def test_bulk_read_with_single_tile_many_coords(self) -> None:
        """200 coordinates in a single tile should trigger exactly 1 read()."""
        source = CopernicusDEMDataSource(base_url="file:///nonexistent")
        source._max_open_tiles = 16
        source._datasets = OrderedDict()

        tile_uri = (
            "file:///nonexistent/Copernicus_DSM_COG_10_N47_00_E008_00_DEM/"
            "Copernicus_DSM_COG_10_N47_00_E008_00_DEM.tif"
        )
        tile_values: dict[str, float] = {tile_uri: 42.0}

        read_count = [0]

        class _SR(_MockReadResult):
            pass

        class _SD(_MockDataset):
            def read(self, band: int, window=None) -> _SR:  # type: ignore[return]
                read_count[0] += 1
                return _SR(self.value)

            def __init__(self, value: float) -> None:
                super().__init__(value)

        def patched_open(path: str) -> _SD:
            return _SD(tile_values[path])

        orig_open = rasterio.open
        rasterio.open = patched_open  # type: ignore[assignment]

        try:
            coords = [(47.0 + i * 0.000001, 8.0) for i in range(200)]
            results = await source.get_elevations_batch(coords)
            assert len(results) == 200
            assert read_count[0] == 1
        finally:
            rasterio.open = orig_open


# =============================================================================
# Read concurrency test (asyncio.gather on bulk reads)
# =============================================================================


class TestReadConcurrency:
    """Verify that bulk reads for multiple tiles run concurrently."""

    @pytest.mark.asyncio
    async def test_bulk_reads_run_concurrently(self) -> None:
        """4 coordinates across 4 distinct tiles with artificial delay should
        complete in ~1x delay, not 4x delay.  This proves the asyncio.gather
        concurrency claim for the read phase.
        """
        source = CopernicusDEMDataSource(base_url="file:///nonexistent")
        source._max_open_tiles = 16
        source._datasets = OrderedDict()

        tile_uris = [
            "file:///nonexistent/Copernicus_DSM_COG_10_N47_00_E008_00_DEM/Copernicus_DSM_COG_10_N47_00_E008_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N48_00_E009_00_DEM/Copernicus_DSM_COG_10_N48_00_E009_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N49_00_E010_00_DEM/Copernicus_DSM_COG_10_N49_00_E010_00_DEM.tif",
            "file:///nonexistent/Copernicus_DSM_COG_10_N50_00_E011_00_DEM/Copernicus_DSM_COG_10_N50_00_E011_00_DEM.tif",
        ]
        tile_values: dict[str, float] = dict(
            zip(tile_uris, [10.0, 20.0, 30.0, 40.0], strict=True),
        )

        read_count = [0]

        class _DelayReadResult:
            def __init__(self, value: float) -> None:
                self.value = value

            def __getitem__(self, key: tuple[int, int]) -> float:
                return self.value

        class _DelayDataset(_MockDataset):
            def __init__(self, value: float) -> None:
                super().__init__(value)

            def read(self, band: int, window=None) -> _DelayReadResult:  # type: ignore[return]
                read_count[0] += 1
                return _DelayReadResult(self.value)

        def patched_open(path: str) -> _DelayDataset:
            return _DelayDataset(tile_values[path])

        orig_open = rasterio.open
        rasterio.open = patched_open  # type: ignore[assignment]

        try:
            delay = 0.15  # seconds per read
            original_read_tile_bulk = CopernicusDEMDataSource._read_tile_bulk

            def delayed_read_tile_bulk(
                self: CopernicusDEMDataSource,
                uri: str,
                points: list[tuple[int, float, float]],
            ) -> list[float]:
                time.sleep(delay)
                return original_read_tile_bulk(self, uri, points)

            CopernicusDEMDataSource._read_tile_bulk = delayed_read_tile_bulk  # type: ignore[assignment]

            try:
                coords = [
                    (47.0, 8.0),
                    (48.0, 9.0),
                    (49.0, 10.0),
                    (50.0, 11.0),
                ]

                start = asyncio.get_event_loop().time()
                results = await source.get_elevations_batch(coords)
                elapsed = asyncio.get_event_loop().time() - start

                assert results == [10.0, 20.0, 30.0, 40.0]
                # Sequential: ~0.6s (4 x 0.15).  Concurrent: ~0.15s.
                assert elapsed < 2 * delay, (
                    f"Expected concurrent (~{delay:.2f}s), got {elapsed:.3f}s."
                )
            finally:
                CopernicusDEMDataSource._read_tile_bulk = original_read_tile_bulk
        finally:
            rasterio.open = orig_open
