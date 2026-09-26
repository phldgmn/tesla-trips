"""HTTP client for the supercharge.info REST API and the Tesla Locations API.

Dieses Modul ist ein re-export shim. Alle Symbole wurden in das
``clients/``-Paket ausgelagert:

- ``common``:  ``CurlError``, ``TeslaClient``, ``create_tesla_client``
- ``supercharge_info``: ``SuperchargeInfoClient``
- ``tesla_curl``:   ``TeslaLocationsClient``
- ``safari``:       ``SafariTeslaClient``
- ``nodriver``:     ``NodriverTeslaClient``, ``NodriverBrowserFetcher``
- ``human_flow``:   ``NodriverHumanFlowTeslaClient``
"""

import importlib

from .clients import (
    CurlError,
    NodriverBrowserFetcher,
    NodriverHumanFlowTeslaClient,
    NodriverTeslaClient,
    SafariTeslaClient,
    SuperchargeInfoClient,
    TeslaClient,
    TeslaLocationsClient,
    create_tesla_client,
)

__all__ = [
    "CurlError",
    "NodriverBrowserFetcher",
    "NodriverHumanFlowTeslaClient",
    "NodriverTeslaClient",
    "SafariTeslaClient",
    "SuperchargeInfoClient",
    "TeslaClient",
    "TeslaLocationsClient",
    "create_tesla_client",
    "importlib",
]
