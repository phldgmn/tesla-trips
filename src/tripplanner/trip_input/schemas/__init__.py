"""API schema models for trip_input (supercharger/request/response schemas)."""

from tripplanner.trip_input.schemas.request import (
    ChargingDurationSpecificationAPI,
    FerryExclusionAPI,
    FerryTimeWindowAPI,
    TripRequestAPI,
    WaypointAPI,
)
from tripplanner.trip_input.schemas.response import (
    ChargingCostByCurrencyAPI,
    ChargingStopAPI,
    ConstructionZoneAPI,
    ConstructionZoneEventAPI,
    FerrySegmentAPI,
    FrameAPI,
    TripSimulationResultAPI,
    WaypointStopAPI,
)
from tripplanner.trip_input.schemas.superchargers import (
    SuperchargerPricingAPI,
    SuperchargerStationAPI,
    SuperchargerStationDetailAPI,
)

__all__ = [
    "ChargingCostByCurrencyAPI",
    "ChargingDurationSpecificationAPI",
    "ChargingStopAPI",
    "ConstructionZoneAPI",
    "ConstructionZoneEventAPI",
    "FerryExclusionAPI",
    "FerrySegmentAPI",
    "FerryTimeWindowAPI",
    "FrameAPI",
    "SuperchargerPricingAPI",
    "SuperchargerStationAPI",
    "SuperchargerStationDetailAPI",
    "TripRequestAPI",
    "TripSimulationResultAPI",
    "WaypointAPI",
    "WaypointStopAPI",
]
