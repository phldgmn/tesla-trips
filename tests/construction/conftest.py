"""Tests für construction-Modul: Fixtures und Konfiguration."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_construction_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Give every construction test its own throwaway cache directory.

    ConstructionProviderImpl uses a persistent SQLite-backed TTLCache whose
    default location is ``.cache/construction_cache.sqlite`` (resolved via
    ``TRIPPLANNER_CACHE_DIR`` env var when ``config.cache_dir`` is ``None``).
    Without isolation, cache entries from one test leak into the next, causing
    order-dependent / intermittent failures (e.g. STRtree call counts,
    concurrency assertions).

    This fixture monkeypatches ``TRIPPLANNER_CACHE_DIR`` to ``tmp_path`` so
    every test gets a unique cache location.  Tests that already pass an
    explicit ``cache_dir`` in their ``ConstructionProviderConfig`` are
    unaffected — that explicit value always takes precedence.
    """
    monkeypatch.setenv("TRIPPLANNER_CACHE_DIR", str(tmp_path))
