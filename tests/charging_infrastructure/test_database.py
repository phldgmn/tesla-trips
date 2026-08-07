"""Tests für SQLiteDatabase."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tripplanner.charging_infrastructure.database import SQLiteDatabase


@pytest.fixture
def sample_db_records() -> list[dict[str, Any]]:
    """Drei Test-Stationen im Format für replace_all_stations."""
    now = "2024-01-01T00:00:00+00:00"
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


class TestSQLiteDatabase:
    """Tests für SQLiteDatabase Klasse."""

    def test_initialize_creates_tables(self, tmp_db: SQLiteDatabase, tmp_path: Path) -> None:
        """Nach initialize() existieren alle 3 Tabellen."""
        db_path = tmp_path / "test.db"
        assert db_path.exists()

        # Check tables exist by querying sqlite_master
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row["name"] for row in cursor.fetchall()]

        assert {"charging_pricing", "charging_stations", "db_meta"}.issubset(set(tables))

    def test_initialize_is_idempotent(self, tmp_db: SQLiteDatabase) -> None:
        """Mehrmaliges initialize() wirft keinen Fehler."""
        tmp_db.initialize()
        assert tmp_db._initialized
        # Second call should not raise
        tmp_db.initialize()
        assert tmp_db.station_count == 0

    def test_replace_all_stations(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """replace_all_stations fügt Datensätze ein."""
        count = tmp_db.replace_all_stations(sample_db_records)

        assert count == 3
        assert tmp_db.station_count == 3

    def test_replace_all_stations_is_idempotent(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """Double-replace gibt gleiche Anzahl zurück."""
        tmp_db.replace_all_stations(sample_db_records)
        count = tmp_db.replace_all_stations(sample_db_records)

        assert count == 3
        assert tmp_db.station_count == 3

    def test_replace_all_stations_rollback_on_error(self, tmp_db: SQLiteDatabase) -> None:
        """Fehlende Felder führen zu Rollback."""
        bad_records = [
            {
                "supercharge_info_id": 1,
                "site_name": "Station 1",
                "latitude": 1.0,
                "longitude": 1.0,
                "country_code": "DE",
                "stalls_v2": 0,
                "stalls_v3": 0,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 0,
                "power_kilowatt": 0,
                "status": "Operational",
                "connector_types": "[]",
                "ist_24_7": 0,
                "last_updated_utc": "2024-01-01T00:00:00+00:00",
            },
            {
                "supercharge_info_id": 1,  # Duplicate ID - will cause UNIQUE constraint violation
                "site_name": "Station 2",
                "latitude": 2.0,
                "longitude": 2.0,
                "country_code": "DE",
                "stalls_v2": 0,
                "stalls_v3": 0,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 0,
                "power_kilowatt": 0,
                "status": "Operational",
                "connector_types": "[]",
                "ist_24_7": 0,
                "last_updated_utc": "2024-01-01T00:00:00+00:00",
            },
        ]
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.replace_all_stations(bad_records)

        # Count should be 0 since transaction rolled back
        assert tmp_db.station_count == 0

    def test_load_stations(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """load_stations ohne Filter gibt alle Stationen zurück."""
        tmp_db.replace_all_stations(sample_db_records)

        stations = tmp_db.load_stations()

        assert len(stations) == 3
        # Verify structure
        assert "supercharge_info_id" in stations[0]
        assert "site_name" in stations[0]

    def test_load_stations_country_filter(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """load_stations filtert nach Ländercode."""
        tmp_db.replace_all_stations(sample_db_records)

        de_stations = tmp_db.load_stations(country_filter={"DE"})
        dk_stations = tmp_db.load_stations(country_filter={"DK"})

        assert len(de_stations) == 0  # No DE in sample
        assert len(dk_stations) == 1  # Copenhagen is DK

    def test_meta_get_set(self, tmp_db: SQLiteDatabase) -> None:
        """set_meta und get_meta funktionieren."""
        tmp_db.set_meta("test_key", "test_value")

        value = tmp_db.get_meta("test_key")

        assert value == "test_value"

    def test_station_count_property(self, tmp_db: SQLiteDatabase) -> None:
        """station_count gibt korrekte Anzahl zurück."""
        assert tmp_db.station_count == 0

        # Add some stations
        stations = [
            {
                "supercharge_info_id": 1,
                "site_name": "Station 1",
                "latitude": 1.0,
                "longitude": 1.0,
                "country_code": "DE",
                "stalls_v2": 0,
                "stalls_v3": 0,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 0,
                "power_kilowatt": 0,
                "status": "Operational",
                "connector_types": "[]",
                "ist_24_7": 0,
                "last_updated_utc": "2024-01-01T00:00:00+00:00",
            },
            {
                "supercharge_info_id": 2,
                "site_name": "Station 2",
                "latitude": 2.0,
                "longitude": 2.0,
                "country_code": "DE",
                "stalls_v2": 0,
                "stalls_v3": 0,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 0,
                "power_kilowatt": 0,
                "status": "Operational",
                "connector_types": "[]",
                "ist_24_7": 0,
                "last_updated_utc": "2024-01-01T00:00:00+00:00",
            },
        ]
        tmp_db.replace_all_stations(stations)

        assert tmp_db.station_count == 2

    def test_last_refresh_utc(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """last_refresh_utc ist nach replace_all_stations nicht None."""
        tmp_db.replace_all_stations(sample_db_records)

        last_refresh = tmp_db.last_refresh_utc

        assert last_refresh is not None
        assert last_refresh.tzinfo is not None

    def test_close(self, tmp_db: SQLiteDatabase) -> None:
        """close schließt Verbindung ohne Fehler."""
        tmp_db.close()
        # Should not raise
