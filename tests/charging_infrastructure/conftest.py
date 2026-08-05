"""Pytest-Konfiguration und Fixtures für `charging_infrastructure`-Tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tripplanner.charging_infrastructure.models import (
    ChargingStation,
    ConnectorType,
    StallType,
)
from tripplanner.charging_infrastructure.providers import (
    FakeChargingStationProvider,
    LocalFileChargingStationProvider,
)


@pytest.fixture(scope="session")
def event_loop():
    """Event loop für async Tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_fixture_path() -> Path:
    """Pfad zur Test-Fixture-Datei mit 24 Stationen."""
    return (
        Path(__file__).parent.parent
        / "fixtures"
        / "charging_infrastructure"
        / "tesla_supercharger_snapshot.json"
    )


@pytest.fixture(scope="session")
def local_provider(test_fixture_path: Path) -> LocalFileChargingStationProvider:
    """LocalFileChargingStationProvider geladen mit Test-Fixture."""
    return LocalFileChargingStationProvider(test_fixture_path)


@pytest.fixture(scope="session")
def fake_provider() -> FakeChargingStationProvider:
    """FakeChargingStationProvider mit eingebauten Test-Stationen."""
    return FakeChargingStationProvider()


@pytest.fixture
def sample_station() -> ChargingStation:
    """Einfaches Sample-ChargingStation für Tests."""
    return ChargingStation(
        station_id="test-001",
        name="Test Station",
        coordinate=(52.5, 13.4),
        stalls={StallType.V3: 4, StallType.V2: 2},
        max_ladeleistung_kw=1200.0,
        connector_types=[ConnectorType.CCS2],
        country="DE",
        letzte_datenAktualisierung=datetime.now(UTC),
    )


@pytest.fixture
def sample_route() -> dict:
    """Sample-Route für get_stations_along_route Tests."""
    fixture_path = (
        Path(__file__).parent.parent / "fixtures" / "charging_infrastructure" / "route_sample.json"
    )
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)
