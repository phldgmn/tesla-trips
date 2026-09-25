"""SQLite-databank-Manager für Tesla Supercharger-data.

Verwaltet die lokale SQLite-databank für Supercharger-data aus der
supercharge.info-API. Enthaelt Schema-Definitionen, CRUD-Zugriff und
Transaktionslogik für Stations- und Pricing-data.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
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
    """Verwaltet eine lokale SQLite-databank für Supercharger-data.

    Threading model: every public method opens its own short-lived
    connection (`_connect`) and closes it before returning, so instances are
    safe to call from any thread (e.g. via `asyncio.to_thread`). Writes run
    inside `with conn:` and are therefore atomic (commit on success, rollback
    on error). WAL mode lets readers proceed while a writer holds the lock;
    concurrent writers wait up to `busy_timeout`.
    """

    def __init__(self, db_path: Path) -> None:
        """Initialisiert die databank mit dem Pfad zur Datei.

        Args:
            db_path: Pfad zur SQLite-databankdatei.
        """
        self._db_path = db_path
        self._initialized: bool = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Opens a per-operation connection with pragmas and `row_factory` set."""
        conn = sqlite3.connect(str(self._db_path), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            _ = conn.execute("PRAGMA busy_timeout=5000")
            _ = conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """Erstellt Tabellen und Indizes (idempotent).

        Erstellt die Tabellen charging_stations, charging_pricing und db_meta
        sofern sie nicht existieren. Setzt den (persistenten) WAL-Modus.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn, conn:
            _ = conn.execute("PRAGMA journal_mode=WAL")
            self._create_schema(conn)
        self._initialized = True

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        """Legt alle Tabellen und Indizes an, sofern sie fehlen."""
        _ = conn.execute("""
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

        _ = conn.execute("""
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

        _ = conn.execute("""
            CREATE TABLE IF NOT EXISTS db_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        _ = conn.execute("""
            CREATE TABLE IF NOT EXISTS charging_pricing_queue (
                supercharge_info_id INTEGER PRIMARY KEY,
                enqueued_utc TEXT NOT NULL,
                FOREIGN KEY (supercharge_info_id) REFERENCES charging_stations(supercharge_info_id)
                    ON DELETE CASCADE
            )
        """)

        _ = conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stations_coord
            ON charging_stations (latitude, longitude)
        """)
        _ = conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stations_country
            ON charging_stations (country_code)
        """)
        _ = conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stations_status
            ON charging_stations (status)
        """)
        _ = conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stations_slug
            ON charging_stations (tesla_location_id)
        """)
        _ = conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pricing_station
            ON charging_pricing (supercharge_info_id)
        """)
        _ = conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pricing_queue_enqueued
            ON charging_pricing_queue (enqueued_utc)
        """)

    def _ensure_initialized(self) -> None:
        """Stellt sicher, dass die databank initialisiert ist."""
        if not self._initialized:
            self.initialize()

    def load_stations(self, country_filter: set[str] | None = None) -> list[dict[str, Any]]:
        """Laedt alle Stationen aus der databank.

        Args:
            country_filter: Optionales Set von Laendercodes für Filterung.

        Returns:
            Liste von Station-Dicts mit allen Spalten.
        """
        self._ensure_initialized()
        with self._connect() as conn:
            if country_filter:
                placeholders = ",".join("?" * len(country_filter))
                cur = conn.execute(
                    f"SELECT * FROM charging_stations WHERE country_code IN ({placeholders})",
                    list(country_filter),
                )
            else:
                cur = conn.execute("SELECT * FROM charging_stations")

            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def load_pricing(
        self, supercharge_info_ids: set[int] | None = None
    ) -> dict[int, list[dict[str, Any]]]:
        """Laedt Preisdaten aus der databank.

        Args:
            supercharge_info_ids: Optionales Set von Station-IDs für Filterung.

        Returns:
            Dict mapping supercharge_info_id auf Liste von Pricing-Dicts.
        """
        self._ensure_initialized()
        with self._connect() as conn:
            if supercharge_info_ids:
                placeholders = ",".join("?" * len(supercharge_info_ids))
                cur = conn.execute(
                    f"SELECT * FROM charging_pricing WHERE supercharge_info_id IN ({placeholders})",
                    list(supercharge_info_ids),
                )
            else:
                cur = conn.execute("SELECT * FROM charging_pricing")

            rows = cur.fetchall()
            result: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                data = dict(row)
                sid = data["supercharge_info_id"]
                if sid not in result:
                    result[sid] = []
                result[sid].append(data)
            return result

    def replace_all_stations(self, stations: list[dict[str, Any]]) -> int:
        """Ersetzt alle Stationen durch neue data.

        Führt alle Änderungen in einer Transaktion aus.

        Args:
            stations: Liste von Station-Dicts mit allen requireden Schlüsseln.

        Returns:
            Anzahl der eingefügten Stationen.
        """
        self._ensure_initialized()
        with self._connect() as conn, conn:
            # Clear pricing first (due to FK)
            _ = conn.execute("DELETE FROM charging_pricing")
            # Then stations
            _ = conn.execute("DELETE FROM charging_stations")

            for station in stations:
                _ = conn.execute(
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
            _ = conn.execute(
                """
                INSERT OR REPLACE INTO db_meta (key, value)
                VALUES ('last_full_refresh_utc', ?)
                """,
                (now_utc,),
            )

            return len(stations)

    def update_station(self, station: dict[str, Any]) -> bool:
        """Aktualisiert eine einzelne Station anhand ihrer tesla_location_id.

        Fuegt die Station ein, falls sie noch nicht existiert (UPSERT).

        Args:
            station: Station-Dict mit allen requireden Schluesseln.

        Returns:
            True wenn aktualisiert, False wenn eingefuegt.
        """
        self._ensure_initialized()
        with self._connect() as conn, conn:
            now_utc = datetime.now(UTC).isoformat()
            station["last_updated_utc"] = now_utc
            slug = station.get("tesla_location_id", "")

            # Pruefen ob vorhanden
            cursor = conn.execute(
                "SELECT id, supercharge_info_id FROM charging_stations WHERE tesla_location_id = ?",
                (slug,),
            )
            row = cursor.fetchone()
            exists = row is not None

            if exists:
                _ = conn.execute(
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
                _ = conn.execute(
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
            return exists

    def update_tesla_location_id(self, supercharge_info_id: int, slug: str) -> None:
        """Ueberschreibt die `tesla_location_id` (Slug) einer bestehenden Station.

        Wird uses, wenn sich ein zuvor gespeicherter Slug als falsch
        herausstellt - z. B. wenn supercharge.info fuer `locationId` einen
        stale numerischen Platzhalter statt Teslas echtem
        `location_url_slug` liefert (siehe
        `TeslaChargingStationprovider._resolve_numeric_slug`). Anders als
        `update_station()` (das per `tesla_location_id` sucht) identifiziert
        dies die Station ueber die stabile `supercharge_info_id`.

        Args:
            supercharge_info_id: Interne Station-ID.
            slug: Der neue, aufgeloeste `location_url_slug`.
        """
        self._ensure_initialized()
        with self._connect() as conn, conn:
            _ = conn.execute(
                "UPDATE charging_stations SET tesla_location_id = ?, last_updated_utc = ? "
                "WHERE supercharge_info_id = ?",
                (slug, datetime.now(UTC).isoformat(), supercharge_info_id),
            )

    def find_station_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Findet eine Station anhand ihrer tesla_location_id.

        Args:
            slug: tesla_location_id (location_url_slug).

        Returns:
            Station-Dict oder None.
        """
        self._ensure_initialized()
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM charging_stations WHERE tesla_location_id = ?",
                (slug,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def find_station_by_supercharge_info_id(
        self, supercharge_info_id: int
    ) -> dict[str, Any] | None:
        """Findet eine Station anhand ihrer supercharge_info_id.

        Fallback fuer `find_station_by_slug`, wenn eine Station keine
        `tesla_location_id` besitzt (z. B. Stationen ausserhalb DE/DK/SE, die
        nur ueber die supercharge.info-API bekannt sind - siehe
        `ChargingStation.station_id`-Fallback in
        `TeslaChargingStationprovider._db_record_to_charging_station`).

        Args:
            supercharge_info_id: Die interne Station-ID.

        Returns:
            Station-Dict oder None.
        """
        self._ensure_initialized()
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM charging_stations WHERE supercharge_info_id = ?",
                (supercharge_info_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def upsert_pricing(self, supercharge_info_id: int, tiers: list[dict[str, Any]]) -> None:
        """Fügt oder ersetzt Preisdaten für eine Station.

        Args:
            supercharge_info_id: Die Station-ID.
            tiers: Liste von Pricing-Dicts.
        """
        self._ensure_initialized()
        with self._connect() as conn, conn:
            _ = conn.execute(
                "DELETE FROM charging_pricing WHERE supercharge_info_id = ?",
                (supercharge_info_id,),
            )

            now_utc = datetime.now(UTC).isoformat()
            for tier in tiers:
                _ = conn.execute(
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

    @staticmethod
    def parse_iso_utc(value: str) -> datetime:
        """Parses an ISO-8601 timestamp into a timezone-aware UTC `datetime`.

        Args:
            value: An ISO-8601 timestamp string, as stored via `datetime.
                isoformat()`.
        """
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC)

    def get_pricing_recency(
        self, supercharge_info_ids: set[int] | None = None
    ) -> dict[int, datetime]:
        """Liefert je Station den timestamp der zuletzt gespeicherten Preisdaten.

        Stationen ohne jegliche `charging_pricing`-Zeilen (noch nie gescraped)
        fehlen im Ergebnis-Dict, statt eines `None`-Werts - der caller prueft
        Abwesenheit ueber `station_id not in result`.

        Args:
            supercharge_info_ids: Optionales Set von Station-IDs fuer Filterung.

        Returns:
            Dict mapping supercharge_info_id -> timestamp der juengsten
            Preiszeile (ueber alle Tiers dieser Station).
        """
        self._ensure_initialized()
        with self._connect() as conn:
            if supercharge_info_ids:
                placeholders = ",".join("?" * len(supercharge_info_ids))
                cur = conn.execute(
                    f"""
                    SELECT supercharge_info_id, MAX(last_updated_utc) AS last_updated_utc
                    FROM charging_pricing
                    WHERE supercharge_info_id IN ({placeholders})
                    GROUP BY supercharge_info_id
                    """,
                    list(supercharge_info_ids),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT supercharge_info_id, MAX(last_updated_utc) AS last_updated_utc
                    FROM charging_pricing
                    GROUP BY supercharge_info_id
                    """
                )
            return {
                row["supercharge_info_id"]: self.parse_iso_utc(row["last_updated_utc"])
                for row in cur.fetchall()
            }

    def enqueue_pricing_refresh(self, supercharge_info_ids: Iterable[int]) -> int:
        """Fuegt Stationen zur Preis-Scrape-Warteschlange hinzu (idempotent).

        Bereits vorhandene Eintraege behalten ihren urspruenglichen
        `enqueued_utc`-timestamp (`INSERT OR IGNORE`).

        Args:
            supercharge_info_ids: Station-IDs, die zur Warteschlange
                hinzugefuegt werden sollen.

        Returns:
            Anzahl der tatsaechlich new hinzugefuegten Eintraege.
        """
        self._ensure_initialized()
        with self._connect() as conn, conn:
            now_utc = datetime.now(UTC).isoformat()
            added = 0
            for supercharge_info_id in supercharge_info_ids:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO charging_pricing_queue (supercharge_info_id, enqueued_utc)
                    VALUES (?, ?)
                    """,
                    (supercharge_info_id, now_utc),
                )
                added += cursor.rowcount
            return added

    def dequeue_pricing_refresh(self, supercharge_info_id: int) -> None:
        """Entfernt eine Station aus der Preis-Scrape-Warteschlange.

        Wird sowohl nach einem erfolgreichen Scrape als auch nach einem
        endgueltig fehlgeschlagenen Versuch aufgerufen (siehe
        `TeslaChargingStationprovider.drain_pricing_queue`) - ein weiterhin
        veralteter Preis wird bei der naechsten Routen-Finalisierung erneut
        eingereiht, statt hier endlos zu blockieren.

        Args:
            supercharge_info_id: Die zu entfernende Station-ID.
        """
        self._ensure_initialized()
        with self._connect() as conn, conn:
            _ = conn.execute(
                "DELETE FROM charging_pricing_queue WHERE supercharge_info_id = ?",
                (supercharge_info_id,),
            )

    def load_pricing_queue(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Liefert die Preis-Scrape-Warteschlange in Prioritaets-Reihenfolge.

        Reihenfolge: Stationen ohne jegliche Preisdaten zuerst (
        `pricing_last_updated_utc IS NULL`), danach die mit den aeltesten
        Preisdaten zuerst (aufsteigend nach `pricing_last_updated_utc`).

        Args:
            limit: Optionale Obergrenze fuer die Anzahl zurueckgegebener
                Eintraege.

        Returns:
            Liste von Dicts mit `supercharge_info_id`, `tesla_location_id`,
            `site_name`, `country_code`, `pricing_last_updated_utc` (str oder
            None).
        """
        self._ensure_initialized()
        with self._connect() as conn:
            sql = """
                SELECT
                    q.supercharge_info_id AS supercharge_info_id,
                    s.tesla_location_id AS tesla_location_id,
                    s.site_name AS site_name,
                    s.country_code AS country_code,
                    (
                        SELECT MAX(p.last_updated_utc)
                        FROM charging_pricing p
                        WHERE p.supercharge_info_id = q.supercharge_info_id
                    ) AS pricing_last_updated_utc
                FROM charging_pricing_queue q
                JOIN charging_stations s ON s.supercharge_info_id = q.supercharge_info_id
                ORDER BY (pricing_last_updated_utc IS NOT NULL), pricing_last_updated_utc ASC,
                    q.enqueued_utc ASC
            """
            if limit is not None:
                cur = conn.execute(sql + " LIMIT ?", (limit,))
            else:
                cur = conn.execute(sql)
            return [dict(row) for row in cur.fetchall()]

    def get_meta(self, key: str) -> str | None:
        """Liest einen Meta-Wert aus der databank.

        Args:
            key: Der Meta-Schlüssel.

        Returns:
            Der Meta-Wert oder None, wenn nicht gefunden.
        """
        self._ensure_initialized()
        with self._connect() as conn:
            cur = conn.execute("SELECT value FROM db_meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Speichert einen Meta-Wert in der databank.

        Args:
            key: Der Meta-Schlüssel.
            value: Der Meta-Wert.
        """
        self._ensure_initialized()
        with self._connect() as conn, conn:
            _ = conn.execute(
                "INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    def close(self) -> None:
        """No-op kept for API compatibility: connections are per operation."""

    @property
    def station_count(self) -> int:
        """Gibt die Anzahl der Stationen in der databank zurück."""
        self._ensure_initialized()
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM charging_stations")
            row = cur.fetchone()
            return row[0] if row else 0

    @property
    def last_refresh_utc(self) -> datetime | None:
        """Gibt das Datum der letzten vollstaendigen Aktualisierung zurück."""
        value = self.get_meta("last_full_refresh_utc")
        if value is None:
            return None
        return self.parse_iso_utc(value)
