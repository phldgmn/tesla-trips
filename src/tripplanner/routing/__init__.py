"""Routenberechnung über GraphHopper.

Das routing-Modul berechnet road routes zwischen Start, Ziel und Zwischenstopps.
Es kennt **nicht** die Energieconsumptionsdaten, charging planning oder weatherbedingungen -
diese werden erst in nachgelagerten Modulen (`energy`, `optimization`) bearbeitet.

Exportiert:
- Route, Routesegment: Pydantic-modele für Routen und segmente
- Coordinate: Typ-Alias für (lat, lon) Koordinaten
- GraphHopperResponse, GraphHopperPath: Internes Response-Mapping
- Routingprovider: Interface für Routing-Anbieter
- GraphHopperRoutingprovider: implementation über GraphHopper HTTP API
- GraphHopperClient: HTTP-Client für GraphHopper API
- Ferrysegment: Erkannte, zusammenhaengende Faehrverbindung in einer Route
- detect_ferries: Gruppiert FERRY-segmente einer Route zu Ferrysegment-Eintraegen
"""

from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.ferries import detect_ferries
from tripplanner.routing.models import (
    Coordinate,
    FerrySegment,
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
    "FakeRoutingProvider",
    "FerrySegment",
    "GraphHopperClient",
    "GraphHopperPath",
    "GraphHopperResponse",
    "GraphHopperRoutingProvider",
    "Route",
    "RouteSegment",
    "RoutingProvider",
    "detect_ferries",
]
