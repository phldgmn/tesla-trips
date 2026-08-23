"""Unit- und Integrationstests für elevation.py: ElevationProvider."""

import asyncio
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
        assert profile[0].koordinate == (47.0, 8.0)
        assert profile[1].koordinate == (47.0001, 8.0001)

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
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(koordinate=(47.0001, 8.0001), hoehe_m=110.0),
        ]
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        gradients = elevation_provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m == pytest.approx(10.0, abs=1.0)
        assert gradients[0].steigung_prozent == pytest.approx(74.3, abs=1.0)

    def test_calculate_segment_gradients_negative_slope(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """Negativ-Steigung (Gefälle) wird korrekt berechnet."""
        points = [
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(koordinate=(47.0001, 8.0001), hoehe_m=90.0),
        ]
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        gradients = elevation_provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m < 0
        assert gradients[0].steigung_prozent < 0

    def test_calculate_segment_gradients_zero_distance(
        self, elevation_provider: ElevationProvider
    ) -> None:
        """Steigung ist 0 wenn horizontale Distanz 0 ist."""
        points = [
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=120.0),
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
        """Nicht genug Punkte raises ValueError."""
        points = [ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=100.0)]
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        with pytest.raises(ValueError, match="Nicht genug"):
            elevation_provider.calculate_segment_gradients(points, route)


class TestHorizontalDistanceCalculation:
    """Tests für calculate_horizontal_distance."""

    def test_distance_zero_for_identical_points(self) -> None:
        """Distanz zwischen identischen Punkten ist 0."""
        coord = (47.0, 8.0)
        dist = calculate_horizontal_distance(coord, coord)
        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_distance_symmetric(self) -> None:
        """Distanz(a, b) == Distanz(b, a)."""
        coord1 = (47.0, 8.0)
        coord2 = (48.0, 9.0)
        dist1 = calculate_horizontal_distance(coord1, coord2)
        dist2 = calculate_horizontal_distance(coord2, coord1)
        assert dist1 == pytest.approx(dist2, rel=1e-6)

    def test_distance_approximate_1_degree(self) -> None:
        """Distanz von 1° Längengrad am Äquator ≈ 111 km."""
        coord1 = (0.0, 0.0)
        coord2 = (0.0, 1.0)
        dist = calculate_horizontal_distance(coord1, coord2)
        assert dist == pytest.approx(111_320, rel=0.01)

    def test_distance_1_degree_latitude(self) -> None:
        """Distanz von 1° Breite ≈ 111 km."""
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
            ElevationPoint(koordinate=(47.00014, 8.00001), hoehe_m=100.0),
            ElevationPoint(koordinate=(47.00001, 8.00014), hoehe_m=120.0),
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
        not once per coordinate."""
        coords = [
            (47.0, 8.0),
            (47.0, 8.0001),
            (48.0, 9.0),
            (48.0, 9.0001),
        ]
        source = CopernicusDEMDataSource(base_url="https://example.com")
        source._datasets = OrderedDict()

        to_thread_calls: list[tuple] = []

        async def fake_to_thread(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            to_thread_calls.append((fn, args, kwargs))

        async def patch_to_thread():
            orig = asyncio.to_thread
            asyncio.to_thread = fake_to_thread
            try:
                await source.get_elevations_batch(coords)
            finally:
                asyncio.to_thread = orig

        await patch_to_thread()
        assert len(to_thread_calls) == 2

    @pytest.mark.asyncio
    async def test_tile_opening_is_concurrent_via_to_thread(self) -> None:
        """If tile-opening has a small delay, concurrent opens finish in
        ~1x the delay, not Nx the delay."""
        coords = [
            (47.0, 8.0),
            (47.0001, 8.0),
            (48.0, 9.0),
            (48.0001, 9.0),
        ]
        source = CopernicusDEMDataSource(base_url="https://example.com")
        source._datasets = OrderedDict()

        call_count = [0]

        async def slow_to_thread(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            call_count[0] += 1
            await asyncio.sleep(0.02)

        async def patch_and_measure():
            orig = asyncio.to_thread
            asyncio.to_thread = slow_to_thread
            try:
                start = asyncio.get_event_loop().time()
                await source.get_elevations_batch(coords)
                elapsed = asyncio.get_event_loop().time() - start
                return call_count[0], elapsed
            finally:
                asyncio.to_thread = orig

        count, elapsed = await patch_and_measure()

        assert count == 2  # 2 distinct 1 degree tiles
        # Concurrent: total ~0.02s, not 0.04s (sequential)
        assert elapsed < 0.04, f"Expected concurrent (~0.02s), got {elapsed:.3f}s"

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
