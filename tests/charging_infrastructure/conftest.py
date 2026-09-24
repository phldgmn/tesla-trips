"""Pytest-Konfiguration und Fixtures für `charging_infrastructure`-Tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tripplanner.charging_infrastructure.database import SQLiteDatabase
from tripplanner.charging_infrastructure.models import (
    ChargingStation,
    ConnectorType,
    StallType,
)
from tripplanner.charging_infrastructure.providers import (
    FakeChargingStationProvider,
    LocalFileChargingStationProvider,
    TeslaChargingStationProvider,
)


@pytest.fixture(scope="session")
def event_loop() -> Generator[None, None, None]:
    """Event loop für async Tests."""
    loop = asyncio.new_event_loop()
    yield
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
def sample_route() -> dict[str, Any]:
    """Sample-Route für get_stations_along_route Tests."""
    fixture_path = (
        Path(__file__).parent.parent / "fixtures" / "charging_infrastructure" / "route_sample.json"
    )
    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)
        return data  # type: ignore[no-any-return]


@pytest.fixture
def tmp_db(tmp_path: Path) -> Generator[SQLiteDatabase, None, None]:
    """Erzeugt eine temporäre Datenbankinstanz mit Initialisierung."""
    db_path = tmp_path / "test.db"
    db = SQLiteDatabase(db_path)
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def sample_db_records() -> list[dict[str, Any]]:
    """Drei Test-Stationen im Format für replace_all_stations."""
    now = datetime.now(UTC).isoformat()
    return [
        {
            "supercharge_info_id": 3506,
            "tesla_location_id": "tesla-es-barcelona",
            "site_name": "Barcelona Supercharger",
            "latitude": 41.4175,
            "longitude": 2.1583,
            "country_code": "ES",
            "stalls_v2": 0,
            "stalls_v3": 8,
            "stalls_v3_ultra": 0,
            "stalls_v4": 0,
            "total_stalls": 8,
            "power_kilowatt": 2000,
            "status": "Operational",
            "connector_types": json.dumps(["CCS2", "Tesla"]),
            "ist_24_7": 1,
            "date_opened": "2021-06-15",
            "last_updated_utc": now,
        },
        {
            "supercharge_info_id": 5678,
            "tesla_location_id": "tesla-dk-copenhagen",
            "site_name": "Copenhagen Supercharger",
            "latitude": 55.6761,
            "longitude": 12.5683,
            "country_code": "DK",
            "stalls_v2": 0,
            "stalls_v3": 12,
            "stalls_v3_ultra": 0,
            "stalls_v4": 0,
            "total_stalls": 12,
            "power_kilowatt": 3000,
            "status": "Operational",
            "connector_types": json.dumps(["CCS2", "Tesla"]),
            "ist_24_7": 1,
            "date_opened": "2022-03-20",
            "last_updated_utc": now,
        },
        {
            "supercharge_info_id": 9012,
            "tesla_location_id": "tesla-se-malmo",
            "site_name": "Malmö Supercharger",
            "latitude": 55.6049,
            "longitude": 12.9944,
            "country_code": "SE",
            "stalls_v2": 0,
            "stalls_v3": 10,
            "stalls_v3_ultra": 0,
            "stalls_v4": 0,
            "total_stalls": 10,
            "power_kilowatt": 2500,
            "status": "Operational",
            "connector_types": json.dumps(["CCS2", "Tesla"]),
            "ist_24_7": 0,
            "date_opened": "2022-09-10",
            "last_updated_utc": now,
        },
    ]


@pytest.fixture
def tesla_euro_fixture_path() -> Path:
    """Pfad zur supercharge.info 3-Sites-Test-Fixture."""
    return (
        Path(__file__).parent.parent
        / "fixtures"
        / "charging_infrastructure"
        / "supercharge_info_response_3sites.json"
    )


@pytest.fixture
def tesla_provider_with_db(tmp_path: Path) -> Generator[TeslaChargingStationProvider, None, None]:
    """TeslaChargingStationProvider mit leerer DB im tmp_path."""
    db_path = tmp_path / "test_tesla.db"
    provider = TeslaChargingStationProvider(db_path=db_path)
    yield provider


@pytest.fixture
def tesla_provider_seeded(
    tmp_path: Path,
) -> TeslaChargingStationProvider:
    """TeslaChargingStationProvider mit vorseeded DB (DE, DK, SE Stationen)."""
    db_path = tmp_path / "seeded_tesla.db"
    db = SQLiteDatabase(db_path)
    db.initialize()
    now = datetime.now(UTC).isoformat()

    # Use per-stall power values (supercharge.info convention)
    db.replace_all_stations(
        [
            {
                "supercharge_info_id": 3506,
                "tesla_location_id": None,
                "site_name": "Barcelona, Spain - L'Illa Diagonal",
                "latitude": 41.3895,
                "longitude": 2.1337,
                "country_code": "ES",
                "stalls_v2": 4,
                "stalls_v3": 0,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 4,
                "power_kilowatt": 125,
                "status": "OPEN",
                "connector_types": json.dumps(["ccs2", "type2"]),
                "ist_24_7": 1,
                "date_opened": "2021-07-01",
                "last_updated_utc": now,
            },
            {
                "supercharge_info_id": 5678,
                "tesla_location_id": "kopenhagensupercharger",
                "site_name": "Copenhagen, Denmark",
                "latitude": 55.6761,
                "longitude": 12.5683,
                "country_code": "DK",
                "stalls_v2": 0,
                "stalls_v3": 8,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 8,
                "power_kilowatt": 250,
                "status": "OPEN",
                "connector_types": json.dumps(["ccs2", "type2"]),
                "ist_24_7": 1,
                "date_opened": "2019-06-15",
                "last_updated_utc": now,
            },
            {
                "supercharge_info_id": 9012,
                "tesla_location_id": "malmosupercharger",
                "site_name": "Malmö, Sweden",
                "latitude": 55.5941,
                "longitude": 13.0039,
                "country_code": "SE",
                "stalls_v2": 0,
                "stalls_v3": 8,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 8,
                "power_kilowatt": 250,
                "status": "OPEN",
                "connector_types": json.dumps(["ccs2", "type2"]),
                "ist_24_7": 1,
                "date_opened": "2020-03-20",
                "last_updated_utc": now,
            },
        ]
    )
    db.close()

    provider = TeslaChargingStationProvider(db_path=db_path)
    return provider
