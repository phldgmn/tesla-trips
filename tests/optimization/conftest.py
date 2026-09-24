"""Pytest-Fixtures für `optimization`-Tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

import pytest
from tripplanner.charging_infrastructure.models import (
    ChargingStation,
    ConnectorType,
    StallType,
)
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization.models import (
    OptimizationConstraints,
)
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile, Waypoint

# Fixe Koordinaten für Tests
BERLIN_COORD: Final[tuple[float, float]] = (52.5200, 13.4050)
HAMBURG_COORD: Final[tuple[float, float]] = (53.5511, 9.9937)
LEIPZIG_COORD: Final[tuple[float, float]] = (51.3397, 12.3731)


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    """Beispiel-vehicle_profile."""
    return VehicleProfile(
        mass_kg=1706.0,
        drag_coefficient=0.23,
        frontal_area_m2=2.22,
        rolling_resistance_coefficient=0.011,
        battery_capacity_kwh=62.5,
    )


@pytest.fixture
def optimization_constraints() -> OptimizationConstraints:
    """Standard-Optimierungs-Constraints."""
    return OptimizationConstraints(
        min_soc_pct=15.0,
        target_soc_pct=80.0,
        sicherheitsreserve_pct=5.0,
    )


@pytest.fixture
def basic_route_3_segments() -> Route:
    """Route mit 3 kurzen Segmenten (insgesamt ca. 100 km)."""
    return Route(
        segments=[
            RouteSegment(
                segment_index=0,
                geometrie=[BERLIN_COORD, (53.0, 12.0)],
                length_m=50_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=130,
                steigung_rohdaten=0.0,
                bearing_deg=315.0,
            ),
            RouteSegment(
                segment_index=1,
                geometrie=[(53.0, 12.0), (53.3, 11.0)],
                length_m=30_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=120,
                steigung_rohdaten=0.0,
                bearing_deg=280.0,
            ),
            RouteSegment(
                segment_index=2,
                geometrie=[(53.3, 11.0), HAMBURG_COORD],
                length_m=20_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=110,
                steigung_rohdaten=0.0,
                bearing_deg=260.0,
            ),
        ],
        gesamtlaenge_m=100_000,
        geometrie=[BERLIN_COORD, (53.0, 12.0), (53.3, 11.0), HAMBURG_COORD],
    )


@pytest.fixture
def energy_results_3_segments() -> list[SegmentEnergyResult]:
    """Energieergebnisse für 3 Segmente (niedriger consumption)."""
    return [
        SegmentEnergyResult(
            segment_index=0,
            energiebedarf_kwh=3.0,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=3.0,
            speed_ms=30.0,
            drive_time_s=1667,
            segment_length_m=50_000,
        ),
        SegmentEnergyResult(
            segment_index=1,
            energiebedarf_kwh=2.0,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=2.0,
            speed_ms=25.0,
            drive_time_s=1200,
            segment_length_m=30_000,
        ),
        SegmentEnergyResult(
            segment_index=2,
            energiebedarf_kwh=2.0,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=2.0,
            speed_ms=20.0,
            drive_time_s=1000,
            segment_length_m=20_000,
        ),
    ]


@pytest.fixture
def gradients_3_segments() -> list[SegmentGradient]:
    """Gradienten für 3 Segmente (flach)."""
    return [
        SegmentGradient(
            segment_index=0,
            steigung_prozent=0.0,
            hoehendifferenz_m=0.0,
            horizontale_distanz_m=50_000,
        ),
        SegmentGradient(
            segment_index=1,
            steigung_prozent=0.0,
            hoehendifferenz_m=0.0,
            horizontale_distanz_m=30_000,
        ),
        SegmentGradient(
            segment_index=2,
            steigung_prozent=0.0,
            hoehendifferenz_m=0.0,
            horizontale_distanz_m=20_000,
        ),
    ]


@pytest.fixture
def charging_station_leipzig() -> ChargingStation:
    """Ladestation bei Leipzig."""
    return ChargingStation(
        station_id="leipzig_001",
        name="Tesla Supercharger Leipzig",
        coordinate=LEIPZIG_COORD,
        stalls={StallType.V3: 4, StallType.V3_ULTRA: 2},
        max_ladeleistung_kw=1500.0,
        connector_types=[ConnectorType.CCS2, ConnectorType.NACS],
        country="DE",
    )


@pytest.fixture
def route_with_one_station() -> Route:
    """Route mit 3 Segmenten und einer Ladestation im 2. Segment."""
    return Route(
        segments=[
            RouteSegment(
                segment_index=0,
                geometrie=[BERLIN_COORD, (52.5, 12.5)],
                length_m=50_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=130,
                steigung_rohdaten=0.0,
                bearing_deg=310.0,
            ),
            RouteSegment(
                segment_index=1,
                geometrie=[(52.5, 12.5), (53.0, 11.5)],
                length_m=50_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=120,
                steigung_rohdaten=0.0,
                bearing_deg=290.0,
            ),
            RouteSegment(
                segment_index=2,
                geometrie=[(53.0, 11.5), HAMBURG_COORD],
                length_m=50_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=110,
                steigung_rohdaten=0.0,
                bearing_deg=270.0,
            ),
        ],
        gesamtlaenge_m=150_000,
        geometrie=[BERLIN_COORD, (52.5, 12.5), (53.0, 11.5), HAMBURG_COORD],
    )


@pytest.fixture
def energy_results_3_long_segments() -> list[SegmentEnergyResult]:
    """Energieergebnisse für 3 lange Segmente (höherer consumption)."""
    return [
        SegmentEnergyResult(
            segment_index=0,
            energiebedarf_kwh=8.0,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=8.0,
            speed_ms=30.0,
            drive_time_s=1667,
            segment_length_m=50_000,
        ),
        SegmentEnergyResult(
            segment_index=1,
            energiebedarf_kwh=8.0,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=8.0,
            speed_ms=25.0,
            drive_time_s=2000,
            segment_length_m=50_000,
        ),
        SegmentEnergyResult(
            segment_index=2,
            energiebedarf_kwh=8.0,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=8.0,
            speed_ms=20.0,
            drive_time_s=2500,
            segment_length_m=50_000,
        ),
    ]


@pytest.fixture
def route_with_waypoint() -> Route:
    """Route mit einem Zwischenstopp (Segment 1)."""
    return Route(
        segments=[
            RouteSegment(
                segment_index=0,
                geometrie=[BERLIN_COORD, (52.5, 12.5)],
                length_m=50_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=130,
                steigung_rohdaten=0.0,
                bearing_deg=310.0,
            ),
            RouteSegment(
                segment_index=1,
                geometrie=[(52.5, 12.5), (53.0, 11.5)],
                length_m=50_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=120,
                steigung_rohdaten=0.0,
                bearing_deg=290.0,
            ),
            RouteSegment(
                segment_index=2,
                geometrie=[(53.0, 11.5), HAMBURG_COORD],
                length_m=50_000,
                strassenklasse="MOTORWAY",
                speed_limit_kmh=110,
                steigung_rohdaten=0.0,
                bearing_deg=270.0,
            ),
        ],
        gesamtlaenge_m=150_000,
        geometrie=[BERLIN_COORD, (52.5, 12.5), (53.0, 11.5), HAMBURG_COORD],
    )


@pytest.fixture
def waypoint_leipzig() -> Waypoint:
    """Zwischenstopp bei Leipzig mit 15-min-Pause."""
    return Waypoint(
        coordinate=LEIPZIG_COORD,
        stay_duration=timedelta(minutes=15),
    )
