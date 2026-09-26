"""Utility functions for `charging_infrastructure` access outside the API.

The FastAPI app obtains its provider via dependency injection
(`trip_input.app._lifespan`). For scripts/CLI, `provider_session`
provides a short-lived provider that closes on exit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from .models import ChargingStationProvider
from .providers import (
    LocalFileChargingStationProvider,
    TeslaChargingStationProvider,
)

_DEFAULT_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "supercharger_snapshot.json"
)

@contextmanager
def provider_session(
    data_path: Path | None = None,
    provider_type: Literal["local_file", "tesla_db"] = "tesla_db",
) -> Iterator[ChargingStationProvider]:
    """Creates a provider and releases its resources on exit.

    Args:
        data_path: Path to the data source.
            - When provider_type='local_file': path to JSON file
            - When provider_type='tesla_db': path to SQLite DB
        provider_type: Type of provider.
            - 'local_file': Local JSON-based provider
            - 'tesla_db': SQLite DB with supercharge.info API refresh
    """
    provider: ChargingStationProvider
    if provider_type == "tesla_db":
        provider = TeslaChargingStationProvider(db_path=data_path)
    else:
        provider = LocalFileChargingStationProvider(data_path or _DEFAULT_SNAPSHOT_PATH)
    try:
        yield provider
    finally:
        if isinstance(provider, TeslaChargingStationProvider):
            provider.close()
