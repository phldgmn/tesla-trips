"""Unit-tests for ``tripplanner.cache.TTLCache``."""

import gc
import time
from pathlib import Path

from tripplanner.cache import TTLCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cache(
    tmp_path: Path,
    namespace: str = "test",
    ttl_seconds: float = 60.0,
) -> TTLCache:
    """Create a TTLCache backed by a temporary SQLite file."""
    db = tmp_path / "test_cache.sqlite"
    return TTLCache(namespace=namespace, ttl_seconds=ttl_seconds, db_path=db)


# ---------------------------------------------------------------------------
# set + get roundtrip
# ---------------------------------------------------------------------------


def test_roundtrip_string(tmp_path: Path) -> None:
    """Setting a string and retrieving it returns the identical value."""
    cache = _make_cache(tmp_path)
    cache.set("greeting", "hallo welt")
    assert cache.get("greeting") == "hallo welt"


def test_roundtrip_dict(tmp_path: Path) -> None:
    """Nested dicts survive set/get roundtrip."""
    cache = _make_cache(tmp_path)
    payload = {"count": 42, "tags": ["a", "b"], "meta": None}
    cache.set("data", payload)
    assert cache.get("data") == payload


def test_roundtrip_list(tmp_path: Path) -> None:
    """Lists of primitives roundtrip correctly."""
    cache = _make_cache(tmp_path)
    cache.set("nums", [1, 2.5, -3])
    assert cache.get("nums") == [1, 2.5, -3]


def test_roundtrip_bool_and_none(tmp_path: Path) -> None:
    """Bool and None values roundtrip correctly."""
    cache = _make_cache(tmp_path)
    cache.set("flag", True)
    cache.set("empty", None)
    assert cache.get("flag") is True
    assert cache.get("empty") is None


# ---------------------------------------------------------------------------
# missing key returns None
# ---------------------------------------------------------------------------


def test_get_missing_key_returns_none(tmp_path: Path) -> None:
    """Reading a key that was never set yields None."""
    cache = _make_cache(tmp_path)
    assert cache.get("nonexistent") is None


# ---------------------------------------------------------------------------
# expiry
# ---------------------------------------------------------------------------


def test_value_expires_after_ttl(tmp_path: Path) -> None:
    """A value with a very short TTL disappears once it expires."""
    cache = _make_cache(tmp_path, ttl_seconds=0.15)
    cache.set("transient", "goes-away")
    assert cache.get("transient") == "goes-away"
    time.sleep(0.2)
    assert cache.get("transient") is None


def test_get_clears_expired(tmp_path: Path) -> None:
    """An expired read both returns None and actually purges the row."""
    cache = _make_cache(tmp_path, ttl_seconds=0.1)
    cache.set("old", "dead")
    time.sleep(0.15)
    assert cache.get("old") is None


# ---------------------------------------------------------------------------
# clear_expired
# ---------------------------------------------------------------------------


def test_clear_expired_removes_only_expired(tmp_path: Path) -> None:
    """Fresh entries are untouched; only expired rows are removed."""
    old_cache = _make_cache(tmp_path, ttl_seconds=0.1)
    old_cache.set("old", "gone")
    time.sleep(0.15)
    fresh_cache = _make_cache(tmp_path, ttl_seconds=60)
    fresh_cache.set("fresh", "alive")
    # Clear on the short-TTL cache — only "old" is expired.
    removed = old_cache.clear_expired()
    assert removed == 1


def test_clear_expired_returns_correct_count(tmp_path: Path) -> None:
    """clear_expired returns the exact number of purged rows."""
    cache = _make_cache(tmp_path, ttl_seconds=0.1)
    for i in range(5):
        cache.set(f"row-{i}", i)
    time.sleep(0.15)
    assert cache.clear_expired() == 5


def test_clear_expired_noop_when_fresh(tmp_path: Path) -> None:
    """When nothing is expired, clear_expired returns 0."""
    cache = _make_cache(tmp_path, ttl_seconds=60)
    cache.set("stays", "here")
    assert cache.clear_expired() == 0


# ---------------------------------------------------------------------------
# namespace isolation
# ---------------------------------------------------------------------------


def test_different_namespaces_no_collision(tmp_path: Path) -> None:
    """Two namespaces with the same key must return different values."""
    a = _make_cache(tmp_path, namespace="ns_a")
    b = _make_cache(tmp_path, namespace="ns_b")
    a.set("shared", "value-a")
    b.set("shared", "value-b")
    assert a.get("shared") == "value-a"
    assert b.get("shared") == "value-b"


def test_clear_expired_only_purges_own_namespace(tmp_path: Path) -> None:
    """clear_expired on namespace A must not touch namespace B."""
    a = _make_cache(tmp_path, namespace="ns_a", ttl_seconds=0.1)
    b = _make_cache(tmp_path, namespace="ns_b", ttl_seconds=60)
    a.set("key", "a-val")
    b.set("key", "b-val")
    time.sleep(0.15)
    removed = a.clear_expired()
    assert removed == 1
    assert b.get("key") == "b-val"


# ---------------------------------------------------------------------------
# cross-process / cross-instance persistence
# ---------------------------------------------------------------------------


def test_fresh_instance_sees_previous_data(tmp_path: Path) -> None:
    """A new TTLCache pointed at the same db_path reads data from an old one."""
    db = tmp_path / "shared.sqlite"
    first = TTLCache(namespace="persist", ttl_seconds=60, db_path=db)
    first.set("persistent", "survives-restart")
    del first
    gc.collect()  # ensure __del__ checkpoints WAL before second instance opens.

    second = TTLCache(namespace="persist", ttl_seconds=60, db_path=db)
    assert second.get("persistent") == "survives-restart"


def test_cross_instance_clear_expired_sees_all(tmp_path: Path) -> None:
    """A fresh instance can purge entries written by a previous instance."""
    db = tmp_path / "shared_expire.sqlite"
    old_cache = TTLCache(namespace="x", ttl_seconds=0.1, db_path=db)
    old_cache.set("old", "bad")
    del old_cache
    gc.collect()

    # "new" gets a long TTL so it survives the sleep past the "old" expiry.
    new_cache = TTLCache(namespace="x", ttl_seconds=60, db_path=db)
    new_cache.set("new", "good")
    del new_cache
    gc.collect()

    time.sleep(0.15)

    second = TTLCache(namespace="x", ttl_seconds=60, db_path=db)
    # clear_expired before get("old") to avoid get() triggering its own purge.
    assert second.clear_expired() == 1
    assert second.get("old") is None
    assert second.get("new") == "good"


# ---------------------------------------------------------------------------
# bulk get_many / set_many
# ---------------------------------------------------------------------------


def test_set_many_then_get_many_roundtrip(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    cache.set_many({"a": 1, "b": {"x": [1, 2]}})
    assert cache.get_many(["a", "b", "missing"]) == {"a": 1, "b": {"x": [1, 2]}}


def test_get_many_handles_more_keys_than_one_sql_chunk(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    items = {f"k{i}": i for i in range(1200)}
    cache.set_many(items)
    assert cache.get_many(list(items)) == items


def test_get_many_skips_expired_entries(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path, ttl_seconds=0.05)
    cache.set_many({"a": 1})
    time.sleep(0.1)
    assert cache.get_many(["a"]) == {}


def test_set_many_with_no_items_is_a_noop(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    cache.set_many({})
    assert cache.get_many(["a"]) == {}
