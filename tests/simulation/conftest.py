"""Tests für das simulation-Modul: Konfiguration und Fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tripplanner.charging_infrastructure.models import (
    ChargingStation,
    ConnectorType,
    StallType,
)
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization.models import ChargingPlan, ChargingStop
from tripplanner.routing.models import Coordinate, Route, RouteSegment

# Fixe Koordinaten für Tests
BERLIN_COORD: Coordinate = (52.5200, 13.4050)
FRANKFURT_COORD: Coordinate = (50.1109, 8.6821)
LEIPZIG_COORD: Coordinate = (51.3397, 12.3731)


def make_route_segment(
    segment_index: int,
    geometrie: list[Coordinate],
    length_m: float,
    strassenklasse: str = "MOTORWAY",
) -> RouteSegment:
    """Hilfsfunktion zur Erstellung von RouteSegment-Instanzen."""
    return RouteSegment(
        segment_index=segment_index,
        geometrie=geometrie,
        length_m=length_m,
        strassenklasse=strassenklasse,
        speed_limit_kmh=120,
        bearing_deg=45.0,
    )


def make_segment_energy_result(
    segment_index: int,
    energiebedarf_kwh: float,
    drive_time_s: float,
    speed_ms: float,
    segment_length_m: float,
) -> SegmentEnergyResult:
    """Hilfsfunktion zur Erstellung von SegmentEnergyResult-Instanzen."""
    return SegmentEnergyResult(
        segment_index=segment_index,
        energiebedarf_kwh=energiebedarf_kwh,
        rekuperation_kwh=0.0,
        energiebedarf_brutto_kwh=energiebedarf_kwh,
        speed_ms=speed_ms,
        drive_time_s=drive_time_s,
        segment_length_m=segment_length_m,
    )


def make_charging_station(
    station_id: str,
    name: str,
    coordinate: Coordinate,
) -> ChargingStation:
    """Hilfsfunktion zur Erstellung von ChargingStation-Instanzen."""
    return ChargingStation(
        station_id=station_id,
        name=name,
        coordinate=coordinate,
        stalls={StallType.V3: 4},
        max_ladeleistung_kw=150.0,
        connector_types=[ConnectorType.CCS2],
        country="DE",
    )


def make_charging_stop(
    station: ChargingStation,
    segment_index: int,
    arrival_soc_pct: float,
    target_soc_pct: float,
    estimated_charge_duration_s: int,
    arrival_time: datetime,
) -> ChargingStop:
    """Hilfsfunktion zur Erstellung von ChargingStop-Instanzen."""
    return ChargingStop(
        station=station,
        segment_index=segment_index,
        arrival_soc_pct=arrival_soc_pct,
        target_soc_pct=target_soc_pct,
        geschaetzte_ladedauer_s=estimated_charge_duration_s,
        arrival_time=arrival_time,
        departure_time=arrival_time + timedelta(seconds=estimated_charge_duration_s),
    )


# Fixtures fuer Route ohne Ladehalt
@pytest.fixture
def route_no_charging() -> Route:
    """Route mit 3 Segmenten ohne Ladehalt (insgesamt ca. 150 km)."""
    return Route(
        segments=[
            make_route_segment(
                segment_index=0,
                geometrie=[BERLIN_COORD, (51.5, 12.0), LEIPZIG_COORD],
                length_m=50_000,
            ),
            make_route_segment(
                segment_index=1,
                geometrie=[LEIPZIG_COORD, (51.0, 10.0), (50.5, 9.0)],
                length_m=60_000,
            ),
            make_route_segment(
                segment_index=2,
                geometrie=[(50.5, 9.0), FRANKFURT_COORD],
                length_m=40_000,
            ),
        ],
        gesamtlaenge_m=150_000,
        geometrie=[BERLIN_COORD, LEIPZIG_COORD, FRANKFURT_COORD],
    )


@pytest.fixture
def energy_results_no_charging() -> list[SegmentEnergyResult]:
    """Energieergebnisse fuer 3 Segmente ohne Ladehalt."""
    return [
        make_segment_energy_result(
            segment_index=0,
            energiebedarf_kwh=8.5,
            drive_time_s=1500,
            speed_ms=33.33,
            segment_length_m=50_000,
        ),
        make_segment_energy_result(
            segment_index=1,
            energiebedarf_kwh=9.2,
            drive_time_s=1800,
            speed_ms=33.33,
            segment_length_m=60_000,
        ),
        make_segment_energy_result(
            segment_index=2,
            energiebedarf_kwh=7.8,
            drive_time_s=1200,
            speed_ms=33.33,
            segment_length_m=40_000,
        ),
    ]


@pytest.fixture
def plan_no_charging() -> ChargingPlan:
    """ChargingPlan ohne Ladehalte."""
    return ChargingPlan(
        ladehalte=[],
        gesamtreisezeit_s=4500,
    )


# Fixtures fuer Route mit einem Ladehalt
@pytest.fixture
def route_with_charging() -> Route:
    """Route mit 3 Segmenten und einem Ladehalt in Mitte."""
    return Route(
        segments=[
            make_route_segment(
                segment_index=0,
                geometrie=[BERLIN_COORD, LEIPZIG_COORD],
                length_m=50_000,
            ),
            make_route_segment(
                segment_index=1,
                geometrie=[LEIPZIG_COORD, (50.5, 9.0)],
                length_m=60_000,
            ),
            make_route_segment(
                segment_index=2,
                geometrie=[(50.5, 9.0), FRANKFURT_COORD],
                length_m=40_000,
            ),
        ],
        gesamtlaenge_m=150_000,
        geometrie=[BERLIN_COORD, LEIPZIG_COORD, FRANKFURT_COORD],
    )


@pytest.fixture
def energy_results_with_charging() -> list[SegmentEnergyResult]:
    """Energieergebnisse fuer 3 Segmente mit Ladehalt."""
    return [
        make_segment_energy_result(
            segment_index=0,
            energiebedarf_kwh=8.5,
            drive_time_s=1500,
            speed_ms=33.33,
            segment_length_m=50_000,
        ),
        make_segment_energy_result(
            segment_index=1,
            energiebedarf_kwh=9.2,
            drive_time_s=1800,
            speed_ms=33.33,
            segment_length_m=60_000,
        ),
        make_segment_energy_result(
            segment_index=2,
            energiebedarf_kwh=7.8,
            drive_time_s=1200,
            speed_ms=33.33,
            segment_length_m=40_000,
        ),
    ]


@pytest.fixture
def charging_station_leipzig() -> ChargingStation:
    """Ladestation bei Leipzig."""
    return make_charging_station(
        station_id="leipzig-1",
        name="Tesla Supercharger Leipzig",
        coordinate=LEIPZIG_COORD,
    )


@pytest.fixture
def plan_with_charging(
    charging_station_leipzig: ChargingStation,
) -> ChargingPlan:
    """ChargingPlan mit einem Ladehalt in Leipzig."""
    base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
    return ChargingPlan(
        ladehalte=[
            make_charging_stop(
                station=charging_station_leipzig,
                segment_index=1,
                arrival_soc_pct=30.0,
                target_soc_pct=60.0,
                estimated_charge_duration_s=1800,
                arrival_time=base_time + timedelta(seconds=1500),
            ),
        ],
        gesamtreisezeit_s=6300,
    )


# Fixtures fuer kleine Route (2 Segmente, 10 km)
@pytest.fixture
def route_small() -> Route:
    """Kleine Route mit 2 Segmenten (ca. 10 km)."""
    return Route(
        segments=[
            make_route_segment(
                segment_index=0,
                geometrie=[BERLIN_COORD, (52.0, 13.0)],
                length_m=5000,
            ),
            make_route_segment(
                segment_index=1,
                geometrie=[(52.0, 13.0), LEIPZIG_COORD],
                length_m=5000,
            ),
        ],
        gesamtlaenge_m=10_000,
        geometrie=[BERLIN_COORD, LEIPZIG_COORD],
    )


@pytest.fixture
def energy_results_small() -> list[SegmentEnergyResult]:
    """Energieergebnisse fuer kleine Route."""
    return [
        make_segment_energy_result(
            segment_index=0,
            energiebedarf_kwh=1.0,
            drive_time_s=300,
            speed_ms=16.67,
            segment_length_m=5000,
        ),
        make_segment_energy_result(
            segment_index=1,
            energiebedarf_kwh=1.2,
            drive_time_s=360,
            speed_ms=13.89,
            segment_length_m=5000,
        ),
    ]
