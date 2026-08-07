"""SQLite-Datenbank-Manager für Tesla Supercharger-Daten.

Verwaltet die lokale SQLite-Datenbank für Supercharger-Daten aus der
supercharge.info-API. Enthält Schema-Definitionen, CRUD-Zugriff und
Transaktionslogik für Stations- und Pricing-Daten.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ISO-2 country codes for European countries
_COUNTRY_MAP: dict[str, str] = {
    "Austria": "AT",
    "Belgium": "BE",
    "Bulgaria": "BG",
    "Croatia": "HR",
    "Cyprus": "CY",
    "Czechia": "CZ",
    "Denmark": "DK",
    "Estonia": "EE",
    "Finland": "FI",
    "France": "FR",
    "Germany": "DE",
    "Greece": "GR",
    "Hungary": "HU",
    "Ireland": "IE",
    "Italy": "IT",
    "Latvia": "LV",
    "Lithuania": "LT",
    "Luxembourg": "LU",
    "Malta": "MT",
    "Netherlands": "NL",
    "Poland": "PL",
    "Portugal": "PT",
    "Romania": "RO",
    "Slovakia": "SK",
    "Slovenia": "SI",
    "Spain": "ES",
    "Sweden": "SE",
    "Switzerland": "CH",
    "United Kingdom": "GB",
    "Norway": "NO",
    "Iceland": "IS",
}


class SQLiteDatabase:
    """Verwaltet eine lokale SQLite-Datenbank für Supercharger-Daten."""

    def __init__(self, db_path: Path) -> None:
        """Initialisiert die Datenbank mit dem Pfad zur Datei.

        Args:
            db_path: Pfad zur SQLite-Datenbankdatei.
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._cursor: sqlite3.Cursor | None = None
        self._initialized: bool = False

    def initialize(self) -> None:
        """Öffnet die Datenbankverbindung und erstellt Tabellen.

        Erstellt die Tabellen charging_stations, charging_pricing und db_meta
        sofern sie nicht existieren. Setzt WAL-Modus und Foreign Keys.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        assert self._conn is not None
        self._conn.row_factory = sqlite3.Row
        _ = self._conn.execute("PRAGMA journal_mode=WAL")
        _ = self._conn.execute("PRAGMA busy_timeout=5000")
        _ = self._conn.execute("PRAGMA foreign_keys=ON")
        self._cursor = self._conn.cursor()

        _ = self._conn.execute("""
            CREATE TABLE IF NOT EXISTS charging_stations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supercharge_info_id INTEGER UNIQUE NOT NULL,
                tesla_location_id TEXT,
                site_name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                country_code TEXT NOT NULL,
                stalls_v2 INTEGER NOT NULL DEFAULT 0,
                stalls_v3 INTEGER NOT NULL DEFAULT 0,
                stalls_v3_ultra INTEGER NOT NULL DEFAULT 0,
                stalls_v4 INTEGER NOT NULL DEFAULT 0,
                total_stalls INTEGER NOT NULL,
                power_kilowatt INTEGER NOT NULL,
                status TEXT NOT NULL,
                connector_types TEXT NOT NULL,
                ist_24_7 INTEGER NOT NULL,
                date_opened TEXT,
                last_updated_utc TEXT NOT NULL
            )
        """)

        _ = self._conn.execute("""
            CREATE TABLE IF NOT EXISTS charging_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supercharge_info_id INTEGER NOT NULL,
                tier_label TEXT NOT NULL,
                time_label TEXT,
                currency TEXT NOT NULL,
                amount REAL NOT NULL,
                unit TEXT NOT NULL CHECK(unit IN ('kWh', 'min')),
                idle_fee_text TEXT,
                last_updated_utc TEXT NOT NULL,
                FOREIGN KEY (supercharge_info_id) REFERENCES charging_stations(supercharge_info_id)
                    ON DELETE CASCADE
            )
        """)

        _ = self._conn.execute("""
            CREATE TABLE IF NOT EXISTS db_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        _ = self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stations_coord
            ON charging_stations (latitude, longitude)
        """)
        _ = self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stations_country
            ON charging_stations (country_code)
        """)
        _ = self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stations_status
            ON charging_stations (status)
        """)
        _ = self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pricing_station
            ON charging_pricing (supercharge_info_id)
        """)

        self._initialized = True

    def _ensure_initialized(self) -> None:
        """Stellt sicher, dass die Datenbank initialisiert ist."""
        if not self._initialized:
            self.initialize()

    def load_stations(self, country_filter: set[str] | None = None) -> list[dict[str, Any]]:
        """Lädt alle Stationen aus der Datenbank.

        Args:
            country_filter: Optionales Set von Ländercodes für Filterung.

        Returns:
            Liste von Station-Dicts mit allen Spalten.
        """
        self._ensure_initialized()
        assert self._cursor is not None

        if country_filter:
            placeholders = ",".join("?" * len(country_filter))
            _ = self._cursor.execute(
                f"SELECT * FROM charging_stations WHERE country_code IN ({placeholders})",
                list(country_filter),
            )
        else:
            _ = self._cursor.execute("SELECT * FROM charging_stations")

        rows = self._cursor.fetchall()
        return [dict(row) for row in rows]

    def load_pricing(
        self, supercharge_info_ids: set[int] | None = None
    ) -> dict[int, list[dict[str, Any]]]:
        """Lädt Preisdaten aus der Datenbank.

        Args:
            supercharge_info_ids: Optionales Set von Station-IDs für Filterung.

        Returns:
            Dict mapping supercharge_info_id auf Liste von Pricing-Dicts.
        """
        self._ensure_initialized()
        assert self._cursor is not None

        if supercharge_info_ids:
            placeholders = ",".join("?" * len(supercharge_info_ids))
            _ = self._cursor.execute(
                f"SELECT * FROM charging_pricing WHERE supercharge_info_id IN ({placeholders})",
                list(supercharge_info_ids),
            )
        else:
            _ = self._cursor.execute("SELECT * FROM charging_pricing")

        rows = self._cursor.fetchall()
        result: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            data = dict(row)
            sid = data["supercharge_info_id"]
            if sid not in result:
                result[sid] = []
            result[sid].append(data)
        return result

    def replace_all_stations(self, stations: list[dict[str, Any]]) -> int:
        """Ersetzt alle Stationen durch neue Daten.

        Führt alle Änderungen in einer Transaktion aus.

        Args:
            stations: Liste von Station-Dicts mit allen erforderlichen Schlüsseln.

        Returns:
            Anzahl der eingefügten Stationen.
        """
        self._ensure_initialized()
        assert self._conn is not None

        try:
            _ = self._conn.execute("BEGIN")

            # Clear pricing first (due to FK)
            _ = self._conn.execute("DELETE FROM charging_pricing")
            # Then stations
            _ = self._conn.execute("DELETE FROM charging_stations")

            for station in stations:
                _ = self._conn.execute(
                    """
                    INSERT INTO charging_stations (
                        supercharge_info_id, tesla_location_id, site_name,
                        latitude, longitude, country_code,
                        stalls_v2, stalls_v3, stalls_v3_ultra, stalls_v4,
                        total_stalls, power_kilowatt, status,
                        connector_types, ist_24_7, date_opened, last_updated_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        station["supercharge_info_id"],
                        station.get("tesla_location_id"),
                        station["site_name"],
                        station["latitude"],
                        station["longitude"],
                        station["country_code"],
                        station["stalls_v2"],
                        station["stalls_v3"],
                        station["stalls_v3_ultra"],
                        station["stalls_v4"],
                        station["total_stalls"],
                        station["power_kilowatt"],
                        station["status"],
                        station["connector_types"],
                        station["ist_24_7"],
                        station.get("date_opened"),
                        station["last_updated_utc"],
                    ),
                )

            # Update meta
            now_utc = datetime.now(UTC).isoformat()
            _ = self._conn.execute(
                """
                INSERT OR REPLACE INTO db_meta (key, value)
                VALUES ('last_full_refresh_utc', ?)
                """,
                (now_utc,),
            )

            _ = self._conn.execute("COMMIT")
        except Exception:
            _ = self._conn.execute("ROLLBACK")
            raise

        return len(stations)

    def update_station(self, station: dict[str, Any]) -> bool:
        """Aktualisiert eine einzelne Station anhand ihrer tesla_location_id.

        Fuegt die Station ein, falls sie noch nicht existiert (UPSERT).

        Args:
            station: Station-Dict mit allen erforderlichen Schluesseln.

        Returns:
            True wenn aktualisiert, False wenn eingefuegt.
        """
        self._ensure_initialized()
        assert self._conn is not None

        now_utc = datetime.now(UTC).isoformat()
        station["last_updated_utc"] = now_utc
        slug = station.get("tesla_location_id", "")

        # Pruefen ob vorhanden
        cursor = self._conn.execute(
            "SELECT id, supercharge_info_id FROM charging_stations WHERE tesla_location_id = ?",
            (slug,),
        )
        row = cursor.fetchone()
        exists = row is not None

        try:
            if exists:
                _ = self._conn.execute(
                    """
                    UPDATE charging_stations SET
                        supercharge_info_id = ?,
                        site_name = ?,
                        latitude = ?,
                        longitude = ?,
                        country_code = ?,
                        stalls_v2 = ?,
                        stalls_v3 = ?,
                        stalls_v3_ultra = ?,
                        stalls_v4 = ?,
                        total_stalls = ?,
                        power_kilowatt = ?,
                        status = ?,
                        connector_types = ?,
                        ist_24_7 = ?,
                        date_opened = ?,
                        last_updated_utc = ?
                    WHERE tesla_location_id = ?
                    """,
                    (
                        station["supercharge_info_id"],
                        station["site_name"],
                        station["latitude"],
                        station["longitude"],
                        station["country_code"],
                        station["stalls_v2"],
                        station["stalls_v3"],
                        station["stalls_v3_ultra"],
                        station["stalls_v4"],
                        station["total_stalls"],
                        station["power_kilowatt"],
                        station["status"],
                        station["connector_types"],
                        station["ist_24_7"],
                        station.get("date_opened"),
                        now_utc,
                        slug,
                    ),
                )
            else:
                _ = self._conn.execute(
                    """
                    INSERT INTO charging_stations (
                        supercharge_info_id, tesla_location_id, site_name,
                        latitude, longitude, country_code,
                        stalls_v2, stalls_v3, stalls_v3_ultra, stalls_v4,
                        total_stalls, power_kilowatt, status,
                        connector_types, ist_24_7, date_opened, last_updated_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        station["supercharge_info_id"],
                        station.get("tesla_location_id"),
                        station["site_name"],
                        station["latitude"],
                        station["longitude"],
                        station["country_code"],
                        station["stalls_v2"],
                        station["stalls_v3"],
                        station["stalls_v3_ultra"],
                        station["stalls_v4"],
                        station["total_stalls"],
                        station["power_kilowatt"],
                        station["status"],
                        station["connector_types"],
                        station["ist_24_7"],
                        station.get("date_opened"),
                        now_utc,
                    ),
                )
        except Exception:
            self._conn.rollback()
            raise

        self._conn.commit()
        return exists

    def find_station_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Findet eine Station anhand ihrer tesla_location_id.

        Args:
            slug: tesla_location_id (location_url_slug).

        Returns:
            Station-Dict oder None.
        """
        self._ensure_initialized()
        assert self._cursor is not None
        _ = self._cursor.execute(
            "SELECT * FROM charging_stations WHERE tesla_location_id = ?",
            (slug,),
        )
        row = self._cursor.fetchone()
        return dict(row) if row else None

    def upsert_pricing(self, supercharge_info_id: int, tiers: list[dict[str, Any]]) -> None:
        """Fügt oder ersetzt Preisdaten für eine Station.

        Args:
            supercharge_info_id: Die Station-ID.
            tiers: Liste von Pricing-Dicts.
        """
        self._ensure_initialized()
        assert self._conn is not None

        _ = self._conn.execute(
            "DELETE FROM charging_pricing WHERE supercharge_info_id = ?",
            (supercharge_info_id,),
        )

        for tier in tiers:
            now_utc = datetime.now(UTC).isoformat()
            _ = self._conn.execute(
                """
                INSERT INTO charging_pricing (
                    supercharge_info_id, tier_label, time_label,
                    currency, amount, unit, idle_fee_text, last_updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    supercharge_info_id,
                    tier["tier_label"],
                    tier.get("time_label"),
                    tier["currency"],
                    tier["amount"],
                    tier["unit"],
                    tier.get("idle_fee_text"),
                    now_utc,
                ),
            )

        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        """Liest einen Meta-Wert aus der Datenbank.

        Args:
            key: Der Meta-Schlüssel.

        Returns:
            Der Meta-Wert oder None, wenn nicht gefunden.
        """
        self._ensure_initialized()
        assert self._cursor is not None

        _ = self._cursor.execute("SELECT value FROM db_meta WHERE key = ?", (key,))
        row = self._cursor.fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Speichert einen Meta-Wert in der Datenbank.

        Args:
            key: Der Meta-Schlüssel.
            value: Der Meta-Wert.
        """
        self._ensure_initialized()
        assert self._conn is not None

        _ = self._conn.execute(
            "INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def close(self) -> None:
        """Schließt die Datenbankverbindung."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._cursor = None
            self._initialized = False

    @property
    def station_count(self) -> int:
        """Gibt die Anzahl der Stationen in der Datenbank zurück."""
        self._ensure_initialized()
        assert self._cursor is not None

        _ = self._cursor.execute("SELECT COUNT(*) FROM charging_stations")
        row = self._cursor.fetchone()
        return row[0] if row else 0

    @property
    def last_refresh_utc(self) -> datetime | None:
        """Gibt das Datum der letzten vollständigen Aktualisierung zurück."""
        value = self.get_meta("last_full_refresh_utc")
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC)
