"""Route calculation via GraphHopper.

The routing module calculates road routes between start, destination, and intermediate stops.
It does **not** know the energy consumption data, charging planning, or weather conditions -
these are processed only in downstream modules (``energy``, ``optimization``).

Exports:
- ``Route``, ``RouteSegment``: Pydantic models for routes and segments
- ``Coordinate``: Type alias for (lat, lon) coordinates
- ``GraphHopperResponse``, ``GraphHopperPath``: Internal response mapping
- ``RoutingProvider``: Interface for routing providers
- ``GraphHopperRoutingProvider``: implementation via GraphHopper HTTP API
- ``GraphHopperClient``: HTTP client for the GraphHopper API
- ``FerrySegment``: Detected, contiguous ferry connection in a route
- ``detect_ferries``: Groups FERRY segments of a route into FerrySegment entries
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
