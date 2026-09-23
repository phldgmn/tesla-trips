"""Input models for trip requests.

Data models for `trip_input`: `TripRequest`, `Waypoint`, `VehicleProfile`.
Consumed by `routing` (start/destination/waypoints) and `energy`/`optimization`
(vehicle parameters). All coordinates are `(lat, lon)` in decimal degrees (WGS84),
see `tripplanner.geo`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from tripplanner.geo import Coordinate


class TripInfeasibleError(ValueError):
    """Domain error whose message is safe to show to API clients.

    Raised when a trip cannot be planned (e.g. no reachable destination with
    the given range). Only this `ValueError` subtype is passed through to HTTP
    responses; all other exceptions are reported as generic 500 errors.
    """


class Waypoint(BaseModel):
    """A mandatory waypoint with a coordinate and an optional minimum stay.

    A waypoint is conceptually independent of a charging stop (see
    `docs/06-offene-punkte-widersprueche.md`, item 4): it may coincide with a
    charging stop, but is not automatically a charging point.
    """

    coordinate: Coordinate = Field(..., description="(lat, lon) coordinate in decimal degrees")
    stay_duration: timedelta | None = Field(
        default=None, description="Optional minimum stay duration at this waypoint"
    )
    planned_departure: datetime | None = Field(
        default=None,
        description="Earliest desired departure time at this waypoint",
    )
    charging_power_kw: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Charging power available at this waypoint in kW (e.g. a wall box at "
            "the overnight stop), optional. Only used during a wait forced by "
            "`stay_duration`/`planned_departure` - without a wait there is no "
            "time window and therefore no charging."
        ),
    )


class FerryExclusion(BaseModel):
    """A ferry connection the user wants to avoid.

    Originates from a `FerrySegment` previously detected in a computed route via
    `tripplanner.routing.detect_ferries()` (same field names for
    `name`/`bbox_sw`/`bbox_ne`, but defined separately): `routing` already imports
    `trip_input.models` (`TripRequest`), so importing the other way would create a
    module cycle. The API layer (`trip_input.api`, which imports both) converts
    between the two representations.
    """

    name: str = Field(
        ...,
        description="Display name of the ferry connection (from a previous route computation)",
    )
    bbox_sw: Coordinate = Field(
        ..., description="South-west corner of the (buffered) bounding box around the ferry"
    )
    bbox_ne: Coordinate = Field(
        ..., description="North-east corner of the (buffered) bounding box around the ferry"
    )


class FerryTimeWindow(BaseModel):
    """User-specified departure/arrival time for a ferry connection.

    Aligns the plan with the actual ferry timetable. Identified via
    `name`/`bbox_sw`/`bbox_ne` like `FerryExclusion` (from a previous route
    computation via `tripplanner.routing.detect_ferries()`). The pipeline matches
    it against the freshly computed route and, on a match, passes the fixed
    departure/arrival time to `optimization.optimizer` as a schedule constraint
    (see `_match_ferry_time_windows`).
    """

    name: str = Field(
        ...,
        description="Display name of the ferry connection (from a previous route computation)",
    )
    bbox_sw: Coordinate = Field(
        ..., description="South-west corner of the (buffered) bounding box around the ferry"
    )
    bbox_ne: Coordinate = Field(
        ..., description="North-east corner of the (buffered) bounding box around the ferry"
    )
    departure: datetime = Field(..., description="Fixed ferry departure time")
    arrival: datetime = Field(..., description="Fixed ferry arrival time")

    @field_validator("arrival")
    @classmethod
    def _arrival_after_departure(cls, v: datetime, info: ValidationInfo) -> datetime:
        """Ensure the arrival is after the departure."""
        departure = info.data.get("departure")
        if departure is not None and v <= departure:
            raise ValueError("arrival must be after departure")
        return v


class ChargingDurationSpecification(BaseModel):
    """User-specified fixed charging duration for a stop at a given station.

    Lets the user adjust the computed charging plan (e.g. for real queueing
    times or a desired break length). Identified by the stable `station_id` (see
    `tripplanner.charging_infrastructure.models.ChargingStation.station_id`)
    rather than a coordinate/bounding box, because a charging station - unlike a
    ferry line - keeps the same unique ID across route computations.
    """

    station_id: str = Field(..., min_length=1, description="Unique charging station ID")
    charging_duration_s: int = Field(..., ge=0, description="Fixed charging duration in seconds")


class VehicleProfile(BaseModel):
    """Physical vehicle profile, consumed by `energy`/`optimization`.

    Field names match `tripplanner.energy.models.VehicleEnergyParameters`
    (see `docs/plans/06-energy.md`) so `trip_input` can be converted directly
    into a `VehicleEnergyParameters` object.
    """

    mass_kg: float = Field(..., gt=0, description="Vehicle mass including load in kg")
    drag_coefficient: float = Field(..., ge=0.0, description="Aerodynamic drag coefficient (cW)")
    frontal_area_m2: float = Field(..., gt=0, description="Frontal area in m²")
    rolling_resistance_coefficient: float = Field(
        ..., ge=0.0, description="Rolling resistance coefficient c_r"
    )
    battery_capacity_kwh: float = Field(..., gt=0, description="Usable battery capacity in kWh")
    auxiliary_baseline_kw: float = Field(
        default=0.34, ge=0.0, description="Baseline auxiliary load in kW"
    )
    tire_type: Literal["standard", "winter", "low_rolling_resistance", "performance"] = Field(
        default="standard", description="Tire type, modulates rolling resistance"
    )
    roof_box: bool = Field(default=False, description="Whether a roof box is mounted")


class TripRequest(BaseModel):
    """Complete trip request: start, destination, waypoints, departure, vehicle."""

    start: Coordinate = Field(..., description="(lat, lon) start coordinate in decimal degrees")
    destination: Coordinate = Field(
        ..., description="(lat, lon) destination coordinate in decimal degrees"
    )
    waypoints: list[Waypoint] = Field(
        default_factory=list,
        description="Ordered list of mandatory waypoints between start and destination",
    )
    departure_time: datetime = Field(..., description="Planned departure time")
    vehicle_profile: VehicleProfile = Field(..., description="Physical vehicle profile")
    avoid_all_ferries: bool = Field(
        default=False,
        description=(
            "If True, all ferry connections are avoided during routing "
            "(GraphHopper custom_model: road_environment == FERRY excluded)."
        ),
    )
    highway_preference: Literal["off", "low", "medium", "high"] = Field(
        default="off",
        description=(
            "Highway preference during routing: 'off' (none), 'low' (priority *1.1), "
            "'medium' (*1.2), 'high' (*1.3) for road_class == MOTORWAY in the "
            "GraphHopper custom_model, without excluding non-highway routes (e.g. "
            "when a charging stop lies off the highway)."
        ),
    )
    avoided_ferries: list[FerryExclusion] = Field(
        default_factory=list,
        description=(
            "Previously detected ferry connections to avoid during routing (see FerryExclusion)."
        ),
    )
    ferry_time_windows: list[FerryTimeWindow] = Field(
        default_factory=list,
        description=(
            "User-specified departure/arrival times for previously detected ferry "
            "connections, to match the actual timetable (see FerryTimeWindow)."
        ),
    )
    charging_duration_specifications: list[ChargingDurationSpecification] = Field(
        default_factory=list,
        description=(
            "User-specified fixed charging durations for individual stops, "
            "identified by station ID (see ChargingDurationSpecification)."
        ),
    )
    preferences: dict[str, object] = Field(
        default_factory=dict,
        description="Extensible user preferences (currently unspecified)",
    )
