"""Hilfsfunktionen für den `charging_infrastructure`-Zugriff.

Stellt globale Provider-Instanzen und praktische Abfragen bereit.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .providers import LocalFileChargingStationProvider

if TYPE_CHECKING:
    from typing import Literal

    from tripplanner.geo import Coordinate


# Default-Provider mit lokaler Datei
_DEFAULT_PROVIDER: LocalFileChargingStationProvider | None = None


def init_charging_infrastructure(
    data_path: Path | None = None,
) -> None:
    """Initialisiert den globalen Provider mit der lokalen Datei (falls nicht gesetzt)."""
    global _DEFAULT_PROVIDER  # noqa: PLW0603
    if _DEFAULT_PROVIDER is None:
        if data_path is None:
            data_path = Path(__file__).parent.parent.parent / "data" / "supercharger_snapshot.json"
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
        init_charging_infrastructure()
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_in_radius(coordinate, radius_km, country)


async def get_charging_stations_along_route(
    route: Any,
    search_radius_km: float = 2.0,
) -> dict[int, list[Any]]:
    """Hilfsfunktion für die Abfrage entlang einer Route."""
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure()
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_along_route(route, search_radius_km)
