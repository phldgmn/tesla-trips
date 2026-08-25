"""Core ``TTLCache`` implementation backed by SQLite.

Persistent, time-to-live key-value storage with WAL mode for concurrent
read/write safety.  Each operation opens and closes its own connection to
avoid cross-thread / cross-process issues.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any  # noqa: F401

__all__ = ["TTLCache"]


def _default_db_path() -> Path:
    """Return the default SQLite database path for the cache."""
    cache_dir = Path(os.environ.get("TRIPPLANNER_CACHE_DIR", ".cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "external_api_cache.sqlite"


class TTLCache:
    """A persistent, TTL-backed key-value cache using SQLite.

    Entries are JSON-serialized and stored with an expiration timestamp.
    Expired entries are lazily purged on read and eagerly on
    :meth:`clear_expired`.  The cache survives across process boundaries
    because all state lives in a single SQLite file.

    The default database file is ``.cache/external_api_cache.sqlite``
    (parent directories are created automatically).  Use
    ``TRIPPLANNER_CACHE_DIR`` to relocate it.

    Thread/process safety: each public method opens and closes its own
    :class:`sqlite3.Connection` with WAL journal mode enabled, so concurrent
    readers and a single writer never block each other.
    """

    def __init__(
        self,
        namespace: str,
        ttl_seconds: float,
        db_path: Path | str | None = None,
    ) -> None:
        """Initialise a namespaced cache with the given TTL.

        Args:
            namespace: Logical grouping key.  Entries in different namespaces
                never collide even when their keys are identical.
            ttl_seconds: Time-to-live in seconds.  Entries older than this
                are treated as expired.
            db_path: Path to the SQLite database file.  If *None*, defaults
                to ``<TRIPPLANNER_CACHE_DIR>/external_api_cache.sqlite`` (or
                ``.cache/external_api_cache.sqlite``).  Parent directories are
                created if necessary.
        """
        self._namespace: str = namespace
        self._ttl: float = ttl_seconds
        self._db_path: Path = Path(db_path) if db_path is not None else _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create the cache table if it does not already exist."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache_entries ("
                "namespace TEXT NOT NULL, "
                "key TEXT NOT NULL, "
                "value TEXT NOT NULL, "
                "expires_at REAL NOT NULL, "
                "PRIMARY KEY(namespace, key)"
                ")"
            )
            conn.commit()
        finally:
            conn.close()

    def _checkpoint(self, conn: sqlite3.Connection) -> None:
        """Passively checkpoint the WAL so readers can see the latest committed data.

        Errors are swallowed to keep the caller happy (e.g. if no WAL
        exists yet or the DB is locked).
        """
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    # -- public API ----------------------------------------------------------

    def get(self, key: str) -> object | None:
        """Retrieve a value by key.

        Returns *None* when the key is missing or has expired.  Expired
        entries are removed during this call.

        Args:
            key: The cache key to look up.

        Returns:
            The deserialized value, or *None* if absent/expired.
        """
        sql = "SELECT value FROM cache_entries WHERE namespace = ? AND key = ? AND expires_at > ?"
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute(sql, (self._namespace, key, time.time())).fetchone()
        finally:
            conn.close()
        if row is None:
            self.clear_expired()
            return None
        return json.loads(row[0])  # type: ignore[no-any-return]

    def set(self, key: str, value: object) -> None:
        """Store a JSON-serializable value with the current TTL.

        The value is serialised with :func:`json.dumps`; only values that
        satisfy ``json.dumps(value)`` without error are accepted.

        Args:
            key: The cache key to store under.
            value: The value to cache.  Must be JSON-serializable
                (str, int, float, bool, list, dict, or None).
        """
        payload: str = json.dumps(value)
        expires_at: float = time.time() + self._ttl
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(namespace, key, value, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (self._namespace, key, payload, expires_at),
            )
            conn.commit()
            self._checkpoint(conn)
        finally:
            conn.close()

    def clear_expired(self) -> int:
        """Purge all expired entries for this namespace.

        Returns:
            The number of rows removed.
        """
        cutoff: float = time.time()
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND expires_at <= ?",
                (self._namespace, cutoff),
            )
            conn.commit()
            self._checkpoint(conn)
        finally:
            conn.close()
        return cursor.rowcount
