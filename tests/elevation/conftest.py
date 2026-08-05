"""Tests für das elevation-Modul.

Fixtures und shared test utilities.
"""

from pathlib import Path

import pytest

from tripplanner.elevation.elevation import ElevationProvider
from tripplanner.elevation.models import ElevationPoint, SegmentGradient
from tripplanner.elevation.providers import FakeDataSource

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "elevation"


@pytest.fixture
def fake_data_source() -> FakeDataSource:
    """FakeDataSource für deterministische Unit-Tests."""
    return FakeDataSource(baseline_elevation=150.0, noise_range=4.0)


@pytest.fixture
def elevation_provider(fake_data_source: FakeDataSource) -> ElevationProvider:
    """ElevationProvider mit FakeDataSource."""
    return ElevationProvider(data_source=fake_data_source)


@pytest.fixture
def sample_coords() -> list[tuple[float, float]]:
    """Beispiel-Koordinaten für Tests (in Deutschland)."""
    return [
        (48.1351, 11.5820),  # München
        (48.1360, 11.5830),
        (48.1370, 11.5840),
    ]


@pytest.fixture
def elevation_points_from_coords(sample_coords: list[tuple[float, float]]) -> list[ElevationPoint]:
    """ElevationPoints basierend auf sample_coords mit bekannten Höhen."""
    return [
        ElevationPoint(koordinate=coord, hoehe_m=100.0 + idx * 5.0)
        for idx, coord in enumerate(sample_coords)
    ]


@pytest.fixture
def segment_gradients() -> list[SegmentGradient]:
    """Beispiel SegmentGradient für Tests."""
    return [
        SegmentGradient(
            segment_index=0,
            steigung_prozent=5.0,
            hoehendifferenz_m=10.0,
            horizontale_distanz_m=200.0,
        ),
        SegmentGradient(
            segment_index=1,
            steigung_prozent=-3.0,
            hoehendifferenz_m=-6.0,
            horizontale_distanz_m=200.0,
        ),
    ]


class MockRoute:
    """Mock für routing.models.Route für Tests."""

    def __init__(self, segments: list[object]):
        self.segments = segments


class MockSegment:
    """Mock für routing.models.RouteSegment für Tests."""

    def __init__(self, geometrie: list[tuple[float, float]]):
        self.geometrie = geometrie
