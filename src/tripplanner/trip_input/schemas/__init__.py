"""API-Schema-Modelle für trip_input (Supercharger-/Request-/Response-Schemata)."""

from tripplanner.trip_input.schemas.request import (
    FaehrAusschlussAPI,
    FaehrZeitfensterAPI,
    LadedauerVorgabeAPI,
    TripRequestAPI,
    WaypointAPI,
)
from tripplanner.trip_input.schemas.response import (
    ChargingCostByCurrencyAPI,
    ChargingStopAPI,
    ConstructionZoneAPI,
    ConstructionZoneEventAPI,
    FaehrSegmentAPI,
    FrameAPI,
    TripSimulationResultAPI,
    WaypointStopAPI,
)
from tripplanner.trip_input.schemas.superchargers import (
    SuperchargerStationAPI,
    SuperchargerStationDetailAPI,
)

__all__ = [
    "ChargingCostByCurrencyAPI",
    "ChargingStopAPI",
    "ConstructionZoneAPI",
    "ConstructionZoneEventAPI",
    "FaehrAusschlussAPI",
    "FaehrSegmentAPI",
    "FaehrZeitfensterAPI",
    "FrameAPI",
    "LadedauerVorgabeAPI",
    "SuperchargerStationAPI",
    "SuperchargerStationDetailAPI",
    "TripRequestAPI",
    "TripSimulationResultAPI",
    "WaypointAPI",
    "WaypointStopAPI",
]
