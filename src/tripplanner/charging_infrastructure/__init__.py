"""Öffentliches API des `charging_infrastructure`-Moduls.

Exports:
- Datenmodelle: `ChargingStation`, `ChargingPricingTier`,
  `ChargingStationWithPricing`, `StallType`, `ConnectorType`
- Provider: `ChargingStationProvider`,
  `LocalFileChargingStationProvider`, `FakeChargingStationProvider`,
  `TeslaChargingStationProvider`
- HTTP-Clients: `SuperchargeInfoClient`, `TeslaLocationsClient`
  (curl_cffi), `SafariTeslaClient` (echtes Safari via AppleScript, Default),
  `NodriverTeslaClient` (nodriver / Chromium via CDP, Legacy),
  `create_tesla_client` (Factory, Default: safari), `CurlError` (gemeinsamer
  Fehlertyp aller Tesla-Transports)
- Pricing: `parse_pricing_tiers`, `select_owner_rate_for_time`,
  `PricingParseError`, `CachedPricing`, `PricingQueueDrainResult`
- Hilfsfunktionen: `init_charging_infrastructure`,
  `get_charging_stations_in_radius`, `get_charging_stations_along_route`
"""

from .charging_infrastructure import (
    get_all_charging_stations,
    get_charging_stations_along_route,
    get_charging_stations_in_radius,
    init_charging_infrastructure,
    refresh_supercharger_station,
)
from .client import (
    CurlError,
    NodriverTeslaClient,
    SafariTeslaClient,
    SuperchargeInfoClient,
    TeslaLocationsClient,
    create_tesla_client,
)
from .models import (
    ChargingPricingTier,
    ChargingStation,
    ChargingStationProvider,
    ChargingStationWithPricing,
    ConnectorType,
    StallType,
)
from .pricing import PricingParseError, parse_pricing_tiers, select_owner_rate_for_time
from .providers import (
    CachedPricing,
    FakeChargingStationProvider,
    LocalFileChargingStationProvider,
    PricingQueueDrainResult,
    TeslaChargingStationProvider,
)

__all__ = [
    "CachedPricing",
    "ChargingPricingTier",
    "ChargingStation",
    "ChargingStationProvider",
    "ChargingStationWithPricing",
    "ConnectorType",
    "CurlError",
    "FakeChargingStationProvider",
    "LocalFileChargingStationProvider",
    "NodriverTeslaClient",
    "PricingParseError",
    "PricingQueueDrainResult",
    "SafariTeslaClient",
    "StallType",
    "SuperchargeInfoClient",
    "TeslaChargingStationProvider",
    "TeslaLocationsClient",
    "create_tesla_client",
    "get_all_charging_stations",
    "get_charging_stations_along_route",
    "get_charging_stations_in_radius",
    "init_charging_infrastructure",
    "parse_pricing_tiers",
    "refresh_supercharger_station",
    "select_owner_rate_for_time",
]
