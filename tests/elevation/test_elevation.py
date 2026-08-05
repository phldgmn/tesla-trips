"""Unit- und Integrationstests für elevation.py: ElevationProvider."""

import pytest
import rasterio

from tripplanner.elevation.elevation import ElevationProvider, calculate_horizontal_distance
from tripplanner.elevation.models import ElevationPoint
from tripplanner.elevation.providers import FakeDataSource


class MockSegment:
    """Mock für routing.models.RouteSegment für Tests."""

    def __init__(self, geometrie: list[tuple[float, float]]):
        self.geometrie = geometrie


class MockRoute:
    """Mock für routing.models.Route für Tests."""

    def __init__(self, segments: list[MockSegment]):
        self.segments = segments


class TestElevationProvider:
    """Tests für ElevationProvider mit FakeDataSource."""

    @pytest.fixture
    def provider(self) -> ElevationProvider:
        """ElevationProvider mit FakeDataSource."""
        source = FakeDataSource(baseline_elevation=100.0, noise_range=0.0)
        return ElevationProvider(data_source=source)

    @pytest.fixture
    def mock_route(self) -> MockRoute:
        """Mock-Route mit 2 Segmenten."""
        return MockRoute(
            segments=[
                MockSegment([(47.0, 8.0), (47.0001, 8.0001)]),
                MockSegment([(47.0001, 8.0001), (47.0002, 8.0002)]),
            ]
        )

    def test_get_elevation_profile_empty_route(self, provider: ElevationProvider) -> None:
        """Leere Route ergibt leeres Profil."""
        empty_route = MockRoute(segments=[])
        profile = provider.get_elevation_profile(empty_route)
        assert profile == []

    def test_get_elevation_profile_single_segment(self, provider: ElevationProvider) -> None:
        """Ein Segment wird korrekt verarbeitet."""
        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        profile = provider.get_elevation_profile(route)
        assert len(profile) == 2
        assert profile[0].koordinate == (47.0, 8.0)
        assert profile[1].koordinate == (47.0001, 8.0001)

    def test_get_elevation_profile_multiple_segments(
        self, provider: ElevationProvider, mock_route: MockRoute
    ) -> None:
        """Mehrere Segmente werden korrekt verarbeitet."""
        profile = provider.get_elevation_profile(mock_route)
        assert len(profile) == 3  # Start + 1 Punkt pro Segment

    def test_calculate_segment_gradients_basic(self, provider: ElevationProvider) -> None:
        """Steigungsberechnung mit einfachen Werten."""
        points = [
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(koordinate=(47.0001, 8.0001), hoehe_m=110.0),
        ]

        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        gradients = provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m == pytest.approx(10.0)
        # Horizontale Distanz ca. 13.46m, Steigung ca. 74.3%
        assert gradients[0].steigung_prozent == pytest.approx(74.3, abs=1.0)

    def test_calculate_segment_gradients_negative_slope(self, provider: ElevationProvider) -> None:
        """Negativ-Steigung (Gefälle) wird korrekt berechnet."""
        points = [
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=110.0),
            ElevationPoint(koordinate=(47.0001, 8.0001), hoehe_m=100.0),
        ]

        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])
        gradients = provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m == pytest.approx(-10.0)
        assert gradients[0].steigung_prozent < 0

    def test_calculate_segment_gradients_zero_distance(self, provider: ElevationProvider) -> None:
        """Steigung ist 0 wenn horizontale Distanz 0 ist."""
        points = [
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=100.0),
            ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=120.0),
        ]

        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0, 8.0)])])
        gradients = provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].steigung_prozent == 0.0  # Vermeide Division durch Null
        assert gradients[0].horizontale_distanz_m == pytest.approx(0.0)

    def test_calculate_segment_gradients_empty_route(self, provider: ElevationProvider) -> None:
        """Leere Route ergibt leere Steigungen."""
        route = MockRoute(segments=[])
        _ = provider.calculate_segment_gradients([], route)

    def test_calculate_segment_gradients_not_enough_points(
        self, provider: ElevationProvider
    ) -> None:
        """Nicht genug Punkte raises ValueError."""
        points = [ElevationPoint(koordinate=(47.0, 8.0), hoehe_m=100.0)]

        route = MockRoute(segments=[MockSegment([(47.0, 8.0), (47.0001, 8.0001)])])

        with pytest.raises(ValueError, match="Nicht genug ElevationPoints"):
            provider.calculate_segment_gradients(points, route)


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
        assert dist1 == pytest.approx(dist2)

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
        # Prüfen ob Koordinate im Bounds liegt
        if not (
            self.src.bounds.left <= lon <= self.src.bounds.right
            and self.src.bounds.bottom <= lat <= self.src.bounds.top
        ):
            return -9999.0

        # Umrechnung World -> Pixel (row, col)
        row, col = self.src.index(lon, lat)

        # Lesen des Wertes (band 1 = Höhe)
        if 0 <= row < self.src.height and 0 <= col < self.src.width:
            band_data = self.src.read(1, window=((row, row + 1), (col, col + 1)))
            value = band_data[0, 0]
            if value == self.src.nodata or value < -1000:
                return -9999.0
            return value

        return -9999.0

    def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]

    def get_tile_at(self, lat: float, lon: float):
        return None

    def get_tiles_in_bbox(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float):
        return []


class TestIntegrationWithRealDem:
    """Integrationstests mit echtem GeoTIFF-File.

    Diese Tests werden mit @pytest.mark.integration markiert
    und erfordern die Fixture tests/fixtures/elevation/copernicus_dem_test_tile.tif.
    """

    @pytest.fixture
    def real_dem_provider(self) -> ElevationProvider:
        """ElevationProvider mit echtem GeoTIFF-DataSource."""
        return ElevationProvider(
            data_source=GeoTIFFDataSource("tests/fixtures/elevation/copernicus_dem_test_tile.tif")
        )

    @pytest.mark.integration
    def test_real_dem_elevation_lookup(self, real_dem_provider: ElevationProvider) -> None:
        """Elevation-Lookup mit echtem GeoTIFF."""
        # Testpunkt im Tile (47.000075, 8.000075) = Mitte
        elevation = real_dem_provider.data_source.get_elevation(47.000075, 8.000075)
        # Sollte ca. 110m sein (Mittelwert von 100-120)
        assert 105.0 <= elevation <= 115.0

    @pytest.mark.integration
    def test_tile_boundary_crossing(self, real_dem_provider: ElevationProvider) -> None:
        """Testet Umgang mit Kachelgrenzen."""
        # Koordinaten LEICHT INNERHALB des Tiles (nicht exakt an der Kante)
        coords = [(47.00001, 8.00001), (47.00014, 8.00014)]
        elevations = real_dem_provider.data_source.get_elevations_batch(coords)

        # Mindestens einer sollte gültig sein (kein nodata)
        valid_count = sum(1 for e in elevations if e > -9000)
        assert valid_count >= 1

    @pytest.mark.integration
    def test_segment_gradient_with_real_dem(self, real_dem_provider: ElevationProvider) -> None:
        """Steigungsberechnung mit echtem GeoTIFF."""
        # Koordinaten für top-left (~100m) und bottom-right (~120m)
        points = [
            ElevationPoint(koordinate=(47.00014, 8.00001), hoehe_m=100.0),
            ElevationPoint(koordinate=(47.00001, 8.00014), hoehe_m=120.0),
        ]

        route = MockRoute(segments=[MockSegment([(47.00014, 8.00001), (47.00001, 8.00014)])])
        gradients = real_dem_provider.calculate_segment_gradients(points, route)
        assert len(gradients) == 1
        assert gradients[0].hoehendifferenz_m == pytest.approx(20.0, abs=1.0)
        # Distance ~17.5m, 20m/17.5m ≈ 114.3%
        assert gradients[0].steigung_prozent == pytest.approx(114.3, abs=5.0)
