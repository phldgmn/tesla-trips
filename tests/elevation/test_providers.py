"""Unit-Tests für providers.py: FakeDataSource und DEMDataSourceProtocol."""

import pytest

from tripplanner.elevation.elevation import calculate_horizontal_distance
from tripplanner.elevation.providers import FakeDataSource


class TestFakeDataSource:
    """Tests für FakeDataSource."""

    def test_get_elevation_returns_within_noise_range(self) -> None:
        """Höhenwert liegt im erwarteten Bereich ± noise_range."""
        source = FakeDataSource(baseline_elevation=150.0, noise_range=4.0)
        elevation = source.get_elevation(47.0, 8.0)
        assert 148.0 <= elevation <= 152.0

    def test_get_elevation_is_deterministic(self) -> None:
        """Höhenwert ist deterministisch (selbe Koordinate = gleicher Wert)."""
        source = FakeDataSource(baseline_elevation=100.0, noise_range=5.0)
        val1 = source.get_elevation(47.12345, 8.67890)
        val2 = source.get_elevation(47.12345, 8.67890)
        assert val1 == val2

    def test_get_elevation_different_coordinates_different_values(self) -> None:
        """Unterschiedliche Koordinaten liefern unterschiedliche Werte."""
        source = FakeDataSource(baseline_elevation=100.0, noise_range=50.0)
        val1 = source.get_elevation(47.0, 8.0)
        val2 = source.get_elevation(47.1, 8.1)
        # Bei hoher NoiseRange sollten die Werte meistens unterschiedlich sein
        # (es ist theoretisch möglich, aber extrem unwahrscheinlich)
        assert val1 != val2 or abs(val1 - val2) > 0.001

    def test_get_elevations_batch(self) -> None:
        """Batch-Lookup funktioniert korrekt."""
        source = FakeDataSource(baseline_elevation=100.0, noise_range=5.0)
        coords = [(47.0, 8.0), (47.1, 8.1), (47.2, 8.2)]
        elevations = source.get_elevations_batch(coords)

        assert len(elevations) == 3
        for elev in elevations:
            assert 95.0 <= elev <= 105.0

    def test_get_tile_at_returns_tile(self) -> None:
        """get_tile_at gibt synthetische Kachel zurück."""
        source = FakeDataSource()
        tile = source.get_tile_at(47.0, 8.0)

        assert tile is not None
        assert tile.key.min_lat <= 47.0 <= tile.key.max_lat
        assert tile.key.min_lon <= 8.0 <= tile.key.max_lon

    def test_get_tiles_in_bbox_returns_tile(self) -> None:
        """get_tiles_in_bbox gibt synthetische Kacheln zurück."""
        source = FakeDataSource()
        tiles = source.get_tiles_in_bbox(46.0, 48.0, 7.0, 9.0)

        assert len(tiles) >= 1
        tile = tiles[0]
        assert tile.width == 5
        assert tile.height == 5


class TestCalculateHorizontalDistance:
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
