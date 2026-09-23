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
- FerrySegment: Erkannte, zusammenhängende Fährverbindung in einer Route
- detect_ferries: Gruppiert FERRY-Segmente einer Route zu FerrySegment-Einträgen
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
