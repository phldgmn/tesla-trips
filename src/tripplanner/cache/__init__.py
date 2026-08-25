"""Persistent, TTL-backed cache backed by SQLite.

This module is a dependency-free primitive, following the same exception as
`tripplanner.geo` — importable by any ``tripplanner`` submodule without
violating the module-boundary rule documented in
``docs/07-implementierungsplan.md``, Abschnitt 6.2.  Weather and construction
providers may import this directly.

All state lives in a single on-disk SQLite file; cache entries survive across
separate process runs and fresh ``TTLCache`` instantiations.
"""

from tripplanner.cache.store import TTLCache

__all__ = ["TTLCache"]
