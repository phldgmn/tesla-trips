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

    def test_upsert_pricing_stores_tiers(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """upsert_pricing speichert alle übergebenen Tiers für eine Station."""
        tmp_db.replace_all_stations(sample_db_records)

        tmp_db.upsert_pricing(
            3506,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "EUR",
                    "amount": 0.45,
                    "unit": "kWh",
                    "idle_fee_text": None,
                },
                {
                    "tier_label": "Charging Fees for Other EV",
                    "time_label": None,
                    "currency": "EUR",
                    "amount": 0.55,
                    "unit": "kWh",
                    "idle_fee_text": "0.50 EUR/min idle",
                },
            ],
        )

        pricing = tmp_db.load_pricing({3506})
        assert len(pricing[3506]) == 2
        amounts = {row["tier_label"]: row["amount"] for row in pricing[3506]}
        assert amounts["Charging Fees for Tesla Owner"] == pytest.approx(0.45)
        assert amounts["Charging Fees for Other EV"] == pytest.approx(0.55)

    def test_upsert_pricing_replaces_previous_tiers(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """Ein erneuter upsert_pricing-Aufruf ersetzt (statt ergänzt) die Tiers."""
        tmp_db.replace_all_stations(sample_db_records)
        tier = {
            "tier_label": "Charging Fees for Tesla Owner",
            "time_label": None,
            "currency": "EUR",
            "amount": 0.45,
            "unit": "kWh",
            "idle_fee_text": None,
        }
        tmp_db.upsert_pricing(3506, [tier])
        tmp_db.upsert_pricing(3506, [tier])

        pricing = tmp_db.load_pricing({3506})
        assert len(pricing[3506]) == 1

    def test_get_pricing_recency_omits_never_priced_stations(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """Stationen ohne Preisdaten fehlen im Ergebnis von get_pricing_recency."""
        tmp_db.replace_all_stations(sample_db_records)
        tmp_db.upsert_pricing(
            3506,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "EUR",
                    "amount": 0.45,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )

        recency = tmp_db.get_pricing_recency({3506, 5678})

        assert 3506 in recency
        assert recency[3506].tzinfo is not None
        assert 5678 not in recency

    def test_enqueue_pricing_refresh_is_idempotent(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """Ein bereits eingereihter Eintrag wird nicht doppelt gezählt."""
        tmp_db.replace_all_stations(sample_db_records)

        first = tmp_db.enqueue_pricing_refresh([3506, 5678])
        second = tmp_db.enqueue_pricing_refresh([3506, 9012])

        assert first == 2
        assert second == 1  # 3506 bereits vorhanden, nur 9012 ist neu
        assert len(tmp_db.load_pricing_queue()) == 3

    def test_dequeue_pricing_refresh_removes_entry(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """dequeue_pricing_refresh entfernt genau die angegebene Station."""
        tmp_db.replace_all_stations(sample_db_records)
        tmp_db.enqueue_pricing_refresh([3506, 5678])

        tmp_db.dequeue_pricing_refresh(3506)

        remaining = {row["supercharge_info_id"] for row in tmp_db.load_pricing_queue()}
        assert remaining == {5678}

    def test_load_pricing_queue_orders_never_priced_first(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """Nie gescrapte Stationen stehen vor Stationen mit (auch alten) Preisdaten."""
        tmp_db.replace_all_stations(sample_db_records)
        tmp_db.upsert_pricing(
            5678,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "DKK",
                    "amount": 4.5,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        tmp_db.enqueue_pricing_refresh([5678, 3506])  # 5678 has pricing, 3506 does not

        queue = tmp_db.load_pricing_queue()

        assert [row["supercharge_info_id"] for row in queue] == [3506, 5678]
        assert queue[0]["pricing_last_updated_utc"] is None
        assert queue[1]["pricing_last_updated_utc"] is not None

    def test_load_pricing_queue_orders_stale_by_oldest_first(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """Unter Stationen mit Preisdaten steht die aelteste zuerst."""
        tmp_db.replace_all_stations(sample_db_records)
        tier = {
            "tier_label": "Charging Fees for Tesla Owner",
            "time_label": None,
            "currency": "EUR",
            "amount": 0.4,
            "unit": "kWh",
            "idle_fee_text": None,
        }
        tmp_db.upsert_pricing(3506, [tier])  # scraped second (newer)
        tmp_db.upsert_pricing(5678, [tier])  # scraped after 3506 too, but we
        # overwrite 3506's timestamp below to be clearly the newest, so 5678
        # (older) sorts first.
        conn = tmp_db._conn
        assert conn is not None
        conn.execute(
            "UPDATE charging_pricing SET last_updated_utc = ? WHERE supercharge_info_id = ?",
            ("2020-01-01T00:00:00+00:00", 5678),
        )
        conn.commit()
        tmp_db.enqueue_pricing_refresh([3506, 5678])

        queue = tmp_db.load_pricing_queue()

        assert [row["supercharge_info_id"] for row in queue] == [5678, 3506]

    def test_load_pricing_queue_respects_limit(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """`limit` begrenzt die Anzahl zurückgegebener Warteschlangen-Einträge."""
        tmp_db.replace_all_stations(sample_db_records)
        tmp_db.enqueue_pricing_refresh([3506, 5678, 9012])

        queue = tmp_db.load_pricing_queue(limit=2)

        assert len(queue) == 2

    def test_find_station_by_supercharge_info_id(
        self, tmp_db: SQLiteDatabase, sample_db_records: list[dict[str, Any]]
    ) -> None:
        """find_station_by_supercharge_info_id findet die Station per interner ID."""
        tmp_db.replace_all_stations(sample_db_records)

        found = tmp_db.find_station_by_supercharge_info_id(5678)
        missing = tmp_db.find_station_by_supercharge_info_id(999999)

        assert found is not None
        assert found["tesla_location_id"] == "tesla-dk-copenhagen"
        assert missing is None

    def test_parse_iso_utc_roundtrip(self, tmp_db: SQLiteDatabase) -> None:
        """parse_iso_utc parst ISO-Strings (inkl. Zulu-Suffix) als UTC-datetime."""
        parsed = SQLiteDatabase.parse_iso_utc("2024-01-01T00:00:00Z")
        assert parsed.tzinfo is not None
        assert parsed.year == 2024
