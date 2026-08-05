"""Routenberechnung über GraphHopper.

Das routing-Modul berechnet Straßenrouten zwischen Start, Ziel und Zwischenstopps.
Es kennt **nicht** die Energieverbrauchsdaten, Ladeplanung oder Wetterbedingungen -
diese werden erst in nachgelagerten Modulen (`energy`, `optimization`) bearbeitet.

Exportiert:
- Route, RouteSegment: Pydantic-Modelle für Routen und Segmente
- Coordinate: Typ-Alias für (lat, lon) Koordinaten
- GraphHopperResponse, GraphHopperPath: Internes Response-Mapping
- RoutingProvider: Interface für Routing-Anbieter
- FakeRoutingProvider: Fake-Implementierung für Tests
- GraphHopperClient: HTTP-Client für GraphHopper API
"""

from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.models import (
    Coordinate,
    GraphHopperPath,
    GraphHopperResponse,
    Route,
    RouteSegment,
)
from tripplanner.routing.providers import FakeRoutingProvider, RoutingProvider

__all__ = [
    "Coordinate",
    "FakeRoutingProvider",
    "GraphHopperClient",
    "GraphHopperPath",
    "GraphHopperResponse",
    "Route",
    "RouteSegment",
    "RoutingProvider",
]
