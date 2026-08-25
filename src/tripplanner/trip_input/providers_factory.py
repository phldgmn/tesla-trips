"""Provider-Factory für Produktion.

Liefert eine vorkonfigurierte Menge von Produktion-Providern, die
für die Reiseplanung benötigt werden: Routing, Elevation, Weather,
Construction und Charging.

Konfiguration erfolgt ausschließlich über Umgebungsvariablen (und, für
Construction-Credentials, optional über `credentials.local.yaml` im
Repo-Root, siehe `_load_local_credentials`). Optional können
Constructor-Überschreibungen für elevation_data_source und
construction_provider übergeben werden, um Phase-C/D-Implementierungen
später einzubinden, ohne diesen Code erneut ändern zu müssen.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.construction.providers import (
    ConstructionProviderConfig,
    ConstructionProviderImpl,
)
from tripplanner.elevation.elevation import ElevationProvider
from tripplanner.elevation.providers import CopernicusDEMDataSource, DEMDataSourceProtocol
from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.providers import GraphHopperRoutingProvider
from tripplanner.weather.providers import (
    DmiProvider,
    LoadBalancedWeatherProvider,
    MetNorwayProvider,
    OpenMeteoProvider,
    OpenWeatherProvider,
    SmhiProvider,
    WeatherProviderEntry,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_CREDENTIALS_PATH = _REPO_ROOT / "credentials.local.yaml"


class ProductionProviders(NamedTuple):
    """Produktions-Provider-Tuple: routing, elevation, weather, construction, charging.

    All 5 production providers needed for trip planning.
    """

    routing: GraphHopperRoutingProvider
    elevation_provider: ElevationProvider
    weather: LoadBalancedWeatherProvider
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
            Wenn None, wird eine CopernicusDEMDataSource verwendet (echte
            Copernicus-DEM-GLO-30-Kacheln via GDAL /vsicurl/ gegen den
            öffentlichen `copernicus-dem-30m`-Bucket, siehe `elevation/providers.py`).
        construction_provider: Optionaler ConstructionProviderImpl. Wenn None,
            wird ein Provider mit echten Credentials aus Umgebungsvariablen
            bzw. `credentials.local.yaml` erstellt (siehe
            `_load_local_credentials`); Länder ohne Credentials werden von
            `ConstructionProviderImpl` selbst übersprungen, nicht hier.

    Returns:
        ProductionProviders mit allen benötigten Providern.

    Note:
        `elevation_data_source` wird primär für Tests überschrieben (z. B. mit
        `FakeDataSource`), damit keine Live-Netzwerkzugriffe in Unit-Tests
        stattfinden (siehe AGENTS.md).
    """
    # Routing: GraphHopper über Umgebungsvariable
    gh_url = os.environ.get("GRAPHHOPPER_URL", "http://localhost:8989")
    routing = GraphHopperRoutingProvider(client=GraphHopperClient(base_url=gh_url))

    # Elevation: CopernicusDEMDataSource (echte Copernicus-DEM-GLO-30-Kacheln)
    if elevation_data_source is None:
        # Persistent, not tempfile.gettempdir(): DEM tiles are large (~5-30MB
        # each) and expensive to re-fetch; data/dem-tiles matches the repo's
        # existing convention for large local data (GraphHopper graph, OSM
        # extracts, see data/). Pre-populate via scripts/fetch_dem_tiles.py.
        default_dem_cache = str(_REPO_ROOT / "data" / "dem-tiles")
        elevation_data_source = CopernicusDEMDataSource(
            cache_dir=Path(os.environ.get("DEM_CACHE_DIR", default_dem_cache)),
        )
    elevation = ElevationProvider(data_source=elevation_data_source)

    local_credentials = _load_local_credentials()

    # Weather: country-aware, load-balanced composite (siehe _build_weather_provider).
    weather = _build_weather_provider(local_credentials)

    # Construction: konfigurierbarer Provider; Default lädt echte Credentials
    # aus Umgebungsvariablen/credentials.local.yaml (siehe _load_local_credentials).
    if construction_provider is None:
        dk_creds = local_credentials.get("DK", {})
        se_creds = local_credentials.get("SE", {})
        construction = ConstructionProviderImpl(
            config=ConstructionProviderConfig(
                dk_client_id=os.environ.get("DK_CLIENT_ID", dk_creds.get("dk_client_id")),
                dk_secret=os.environ.get("DK_SECRET", dk_creds.get("dk_secret")),
                dk_tenant_id=os.environ.get("DK_TENANT_ID", dk_creds.get("dk_tenant_id")),
                tv_api_key=os.environ.get("TV_API_KEY", se_creds.get("tv_api_key")),
                dk_download_url=os.environ.get("DK_DOWNLOAD_URL", dk_creds.get("dk_download_url")),
            )
        )
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


def _build_weather_provider(
    local_credentials: dict[str, dict[str, str | None]],
) -> LoadBalancedWeatherProvider:
    """Builds the production `WeatherProvider`.

    A country-aware, load-balanced composite of every configured real
    weather provider (see `tripplanner.weather.providers.LoadBalancedWeatherProvider`):
    Open-Meteo and MET Norway are always included (global, keyless).
    OpenWeather (global) is included only when an API key is configured
    (`credentials.local.yaml`: `weather.openweather.key`, or env var
    `OPENWEATHER_API_KEY`, which takes precedence). SMHI and DMI are always
    included but restricted to Sweden/Denmark respectively - the composite
    itself enforces this via `WeatherProviderEntry.countries`, so a single
    missing-key provider or a country-restricted provider never blocks
    weather data for the other focus countries.

    Args:
        local_credentials: Result of `_load_local_credentials()`, passed in
            to avoid re-reading `credentials.local.yaml`.

    Returns:
        A `LoadBalancedWeatherProvider` wrapping every available provider.
    """
    entries: list[WeatherProviderEntry] = [
        WeatherProviderEntry("open-meteo", OpenMeteoProvider(), None),
        WeatherProviderEntry("met-norway", MetNorwayProvider(), None),
        WeatherProviderEntry("smhi", SmhiProvider(), frozenset({"SE"})),
        WeatherProviderEntry("dmi", DmiProvider(), frozenset({"DK"})),
    ]

    openweather_key = os.environ.get("OPENWEATHER_API_KEY") or local_credentials.get(
        "OPENWEATHER", {}
    ).get("key")
    if openweather_key:
        entries.append(
            WeatherProviderEntry("openweather", OpenWeatherProvider(api_key=openweather_key), None)
        )

    return LoadBalancedWeatherProvider(entries)


async def close_production_providers(providers: ProductionProviders) -> None:
    """Schließt alle asynchronen Ressourcen der Produktion-Provider.

    Args:
        providers: ProductionProviders-Tuple von build_production_providers().
    """
    await providers.routing.client.close()
    await providers.weather.close()
    await providers.construction.close()
    # charging ist synchronous (TeslaChargingStationProvider schließt SQLite)
    providers.charging.close()


def _load_local_credentials() -> dict[str, dict[str, str | None]]:
    """Loads DK/SE construction and OpenWeather credentials from `credentials.local.yaml`.

    Reads the repo-root `credentials.local.yaml` (gitignored; see
    `_LOCAL_CREDENTIALS_PATH`) via PyYAML `safe_load`. Never crashes when the
    file is absent (e.g. in CI) — returns `{}` instead. DE needs no mapping
    (the Autobahn GmbH API is unauthenticated).

    Returns:
        A dict keyed by country code (`DK`/`SE`) or `OPENWEATHER`, each
        value already using the target config's field names
        (`dk_client_id`, `dk_secret`, `tv_api_key`, `key`) so callers can
        pass them straight through.
    """
    if not _LOCAL_CREDENTIALS_PATH.is_file():
        return {}

    raw: dict[str, Any] = yaml.safe_load(_LOCAL_CREDENTIALS_PATH.read_text()) or {}

    result: dict[str, dict[str, str | None]] = {}
    dk_raw = raw.get("DK")
    if isinstance(dk_raw, dict) and "client_id" in dk_raw and "secret" in dk_raw:
        result["DK"] = {
            "dk_client_id": dk_raw["client_id"],
            "dk_secret": dk_raw["secret"],
            "dk_tenant_id": dk_raw.get("tenant_id"),
            "dk_download_url": dk_raw.get("download_url"),
        }
    se_raw = raw.get("SE")
    if isinstance(se_raw, dict) and "key" in se_raw:
        result["SE"] = {"tv_api_key": se_raw["key"]}
    weather_raw = raw.get("weather")
    if (
        isinstance(weather_raw, dict)
        and "openweather" in weather_raw
        and isinstance(weather_raw["openweather"], dict)
        and "key" in weather_raw["openweather"]
    ):
        result["OPENWEATHER"] = {"key": weather_raw["openweather"]["key"]}
    return result
