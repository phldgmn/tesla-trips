"""Routenberechnung über GraphHopper.

Das routing-Modul berechnet Straßenrouten zwischen Start, Ziel und Zwischenstopps.
Es kennt **nicht** die Energieverbrauchsdaten, Ladeplanung oder Wetterbedingungen -
diese werden erst in nachgelagerten Modulen (`energy`, `optimization`) bearbeitet.

Exportiert:
- Route, RouteSegment: Pydantic-Modelle für Routen und Segmente
- Coordinate: Typ-Alias für (lat, lon) Koordinaten
- GraphHopperResponse, GraphHopperPath: Internes Response-Mapping
- RoutingProvider: Interface für Routing-Anbieter
- GraphHopperRoutingProvider: Implementierung über GraphHopper HTTP API
- GraphHopperClient: HTTP-Client für GraphHopper API
- FaehrSegment: Erkannte, zusammenhängende Fährverbindung in einer Route
- erkenne_faehren: Gruppiert FERRY-Segmente einer Route zu FaehrSegment-Einträgen
"""

from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.faehren import erkenne_faehren
from tripplanner.routing.models import (
    Coordinate,
    FaehrSegment,
    GraphHopperPath,
    GraphHopperResponse,
    Route,
    RouteSegment,
)
from tripplanner.routing.providers import (
    FakeRoutingProvider,
    GraphHopperRoutingProvider,
    RoutingProvider,
)

__all__ = [
    "Coordinate",
    "FaehrSegment",
    "FakeRoutingProvider",
    "GraphHopperClient",
    "GraphHopperPath",
    "GraphHopperResponse",
    "GraphHopperRoutingProvider",
    "Route",
    "RouteSegment",
    "RoutingProvider",
    "erkenne_faehren",
]
