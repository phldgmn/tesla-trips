"""Provider-Factory für Produktion.

Liefert eine vorkonfigurierte Menge von Produktion-Providern, die
für die Reiseplanung benötigt werden: Routing, Elevation, Weather,
Construction und Charging.

Konfiguration erfolgt ausschließlich über Umgebungsvariablen.
Optional können Constructor-Überschreibungen für elevation_data_source
und construction_config übergeben werden, um Phase-B/C/D-Implementierungen
später einzubinden, ohne diesen Code erneut ändern zu müssen.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.construction.providers import (
    ConstructionProviderConfig,
    ConstructionProviderImpl,
)
from tripplanner.elevation.elevation import ElevationProvider
from tripplanner.elevation.providers import DEMDataSourceProtocol, FakeDataSource
from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.providers import GraphHopperRoutingProvider
from tripplanner.weather.providers import OpenMeteoProvider


class ProductionProviders(NamedTuple):
    """Produktions-Provider-Tuple: routing, elevation, weather, construction, charging.

    All 5 production providers needed for trip planning.
    """

    routing: GraphHopperRoutingProvider
    elevation_provider: ElevationProvider
    weather: OpenMeteoProvider
    construction: ConstructionProviderImpl
    charging: TeslaChargingStationProvider


async def build_production_providers(
    *,
    elevation_data_source: DEMDataSourceProtocol | None = None,
    construction_provider: ConstructionProviderImpl | None = None,
) -> ProductionProviders:
    """Erstellt alle Produktion-Provider für die Reiseplanung.

    Args:
        elevation_data_source: Optionaler DEMDataSourceProtocol für ElevationProvider.
            Wenn None, wird ein temporärer FakeDataSource verwendet bis Phase C.
            CopernicusDEMDataSource implementiert.
        construction_provider: Optionaler ConstructionProviderImpl. Wenn None,
            wird ein neuer Provider mit leeren Credentials erstellt. Phase D wird
            dies ersetzen, um echte Credentials aus credentials.local.yaml zu laden.

    Returns:
        ProductionProviders mit allen benötigten Providern.

    Note:
        Phase C/D werden die Override-Parameter verwenden, um echte
        CopernicusDEMDataSource und ConstructionProviderImpl mit realen
        Credentials zu liefern. Diese Factory ist für Phase A als
        Vorlage konzipiert.
    """
    # Routing: GraphHopper über Umgebungsvariable
    gh_url = os.environ.get("GRAPHHOPPER_URL", "http://localhost:8989")
    routing = GraphHopperRoutingProvider(client=GraphHopperClient(base_url=gh_url))

    # Elevation: temporär FakeDataSource (Phase C liefert CopernicusDEMDataSource)
    if elevation_data_source is None:
        # TODO: Phase C - replace with CopernicusDEMDataSource()
        elevation_data_source = FakeDataSource()
    elevation = ElevationProvider(data_source=elevation_data_source)

    # Weather: OpenMeteoProvider ohne Auth (free API)
    weather = OpenMeteoProvider()

    # Construction: mit konfigurierbarem Provider (Phase D lädt Credentials aus YAML)
    if construction_provider is None:
        # Konstruiere leere Config (keine Anmeldeinformationen, Phase D wird dies laden)
        # Die ConstructionProviderImpl-Implementierung (Phase D) überspringt Länder
        # ohne Credentials stattdessen mit leerer Liste, nicht mit einem Exception.
        construction = ConstructionProviderImpl(config=ConstructionProviderConfig(tv_api_key=""))
    else:
        construction = construction_provider

    # Charging: Tesla Supercharger SQLite DB
    db_path_env = os.environ.get("TESLA_SUPERCHARGER_DB_PATH", None)
    if db_path_env is not None:
        charging = TeslaChargingStationProvider(db_path=Path(db_path_env))
    else:
        charging = TeslaChargingStationProvider()

    return ProductionProviders(
        routing=routing,
        elevation_provider=elevation,
        weather=weather,
        construction=construction,
        charging=charging,
    )


async def close_production_providers(providers: ProductionProviders) -> None:
    """Schließt alle asynchronen Ressourcen der Produktion-Provider.

    Args:
        providers: ProductionProviders-Tuple von build_production_providers().
    """
    await providers.routing.client.close()
    await providers.weather.close()
    # construction ist async context manager (wird in Phase D closed)
    # charging ist synchronous (TeslaChargingStationProvider schließt SQLite)
    providers.charging.close()
