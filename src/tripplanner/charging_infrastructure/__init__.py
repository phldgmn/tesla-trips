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
- Hilfsfunktionen: `provider_session` (kurzlebiger Provider fuer Skripte/CLI)
"""

from .charging_infrastructure import provider_session
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
    "parse_pricing_tiers",
    "provider_session",
    "select_owner_rate_for_time",
]
