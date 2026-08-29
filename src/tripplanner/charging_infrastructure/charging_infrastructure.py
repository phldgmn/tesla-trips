"""Hilfsfunktionen für den `charging_infrastructure`-Zugriff.

Stellt globale Provider-Instanzen und praktische Abfragen bereit.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import ChargingStation, ChargingStationProvider
from .providers import (
    LocalFileChargingStationProvider,
    TeslaChargingStationProvider,
)

if TYPE_CHECKING:
    from typing import Literal

    from tripplanner.geo import Coordinate


# Default-Provider (initialisiert bei erstem Zugriff)
_DEFAULT_PROVIDER: ChargingStationProvider | None = None


def init_charging_infrastructure(
    data_path: Path | None = None,
    provider_type: Literal["local_file", "tesla_db"] = "tesla_db",
) -> None:
    """Initialisiert den globalen Provider.

    Args:
        data_path: Pfad zur Datenquelle.
            - Bei provider_type='local_file': Pfad zur JSON-Datei
            - Bei provider_type='tesla_db': Pfad zur SQLite-DB
        provider_type: Art des Providers.
            - 'local_file': Lokaler JSON-basierter Provider
            - 'tesla_db': SQLite-DB mit supercharge.info-API-Refresh
    """
    global _DEFAULT_PROVIDER  # noqa: PLW0603
    if _DEFAULT_PROVIDER is not None:
        return

    if provider_type == "tesla_db":
        _DEFAULT_PROVIDER = TeslaChargingStationProvider(db_path=data_path)
    else:
        if data_path is None:
            data_path = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "data"
                / "supercharger_snapshot.json"
            )
        _DEFAULT_PROVIDER = LocalFileChargingStationProvider(data_path)


async def get_charging_stations_in_radius(
    coordinate: Coordinate,
    radius_km: float = 5.0,
    country: Literal["DE", "DK", "SE"] | None = None,
) -> list[Any]:
    """Hilfsfunktion für die übliche Abfrage nach Supercharger in der Nähe einer Koordinate.

    Nutzt den globalen Provider.

    Args:
        coordinate: (lat, lon) als Tuple (WGS84)
        radius_km: Suchradius in Kilometern (Flugdistanz)
        country: Optionaler Länderfilter (DE/DK/SE)

    Returns:
        Liste von ChargingStation, sortiert nach Distanz (aufsteigend)
    """
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure(provider_type="tesla_db")
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_in_radius(coordinate, radius_km, country)


async def get_charging_stations_along_route(
    route: Any,
    search_radius_km: float = 2.0,
) -> dict[int, list[Any]]:
    """Hilfsfunktion für die Abfrage entlang einer Route."""
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure(provider_type="tesla_db")
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_along_route(route, search_radius_km)


def get_all_charging_stations() -> list[ChargingStation]:
    """Liefert alle Supercharger-Stationen aus der Datenbank.

    Returns:
        Liste aller ChargingStation-Eintraege.
    """
    # _DEFAULT_PROVIDER wird nur gelesen, nicht zugewiesen
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure(provider_type="tesla_db")
    assert isinstance(_DEFAULT_PROVIDER, TeslaChargingStationProvider)
    return _DEFAULT_PROVIDER.get_all_stations()


async def refresh_supercharger_station(
    slug: str,
) -> ChargingStation | None:
    """Aktualisiert eine einzelne Station von der Tesla API.

    Args:
        slug: tesla_location_id (location_url_slug)

    Returns:
        Aktualisierte ChargingStation oder None bei Fehler.
    """
    # _DEFAULT_PROVIDER wird nur gelesen, nicht zugewiesen
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure(provider_type="tesla_db")
    assert isinstance(_DEFAULT_PROVIDER, TeslaChargingStationProvider)
    return await _DEFAULT_PROVIDER.refresh_single_station(slug)
