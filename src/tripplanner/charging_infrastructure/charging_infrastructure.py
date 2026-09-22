"""Hilfsfunktionen für den `charging_infrastructure`-Zugriff außerhalb der API.

Die FastAPI-App bezieht ihren Provider über Dependency Injection
(`trip_input.app._lifespan`). Für Skripte/CLI stellt `provider_session`
einen kurzlebigen Provider bereit, der beim Verlassen geschlossen wird.
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
    """Erzeugt einen Provider und gibt dessen Ressourcen beim Verlassen frei.

    Args:
        data_path: Pfad zur Datenquelle.
            - Bei provider_type='local_file': Pfad zur JSON-Datei
            - Bei provider_type='tesla_db': Pfad zur SQLite-DB
        provider_type: Art des Providers.
            - 'local_file': Lokaler JSON-basierter Provider
            - 'tesla_db': SQLite-DB mit supercharge.info-API-Refresh
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
