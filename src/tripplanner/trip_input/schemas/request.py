"""API request schemas for the /trips endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from tripplanner.trip_input.models import VehicleProfile
from tripplanner.trip_input.schemas.base import CamelCaseAPI

Lat = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Lon = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
LatLon = tuple[Lat, Lon]

MAX_WAYPOINTS = 25
MAX_LIST_ITEMS = 50


class WaypointAPI(CamelCaseAPI):
    """A mandatory waypoint between start and destination."""

    coordinate: LatLon = Field(..., description="(lat, lon) coordinate in decimal degrees")
    stay_duration_s: int | None = Field(None, ge=0, description="Minimum stay duration in seconds")
    planned_departure: datetime | None = Field(
        None,
        description="Earliest desired departure time (ISO-8601)",
    )
    charging_power_kw: float | None = Field(
        None,
        ge=0.0,
        le=350.0,
        description="Charging power available at this waypoint in kW, optional",
    )


class VehicleProfileAPI(CamelCaseAPI):
    """Physical vehicle profile, mirrors ``trip_input.models.VehicleProfile``."""

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

    @classmethod
    def from_domain(cls, profile: VehicleProfile) -> VehicleProfileAPI:
        """Build the API model from a domain ``VehicleProfile``."""
        return cls.model_validate(profile.model_dump(), by_name=True)

    def to_domain(self) -> VehicleProfile:
        """Convert to the domain ``VehicleProfile``."""
        return VehicleProfile(**self.model_dump(by_alias=False))


class FerryExclusionAPI(CamelCaseAPI):
    """A previously detected ferry connection to avoid."""

    name: str = Field(..., description="Display name of the ferry connection")
    bbox_sw: LatLon = Field(..., description="South-west corner of the bounding box")
    bbox_ne: LatLon = Field(..., description="North-east corner of the bounding box")


class FerryTimeWindowAPI(CamelCaseAPI):
    """A fixed ferry schedule (departure/arrival)."""

    name: str = Field(..., description="Display name of the ferry connection")
    bbox_sw: LatLon = Field(..., description="South-west corner of the bounding box")
    bbox_ne: LatLon = Field(..., description="North-east corner of the bounding box")
    departure: datetime = Field(..., description="Fixed departure time (ISO-8601)")
    arrival: datetime = Field(..., description="Fixed arrival time (ISO-8601)")


class ChargingDurationSpecificationAPI(CamelCaseAPI):
    """A user-specified fixed charging duration at a station."""

    station_id: str = Field(..., min_length=1, description="Unique charging station ID")
    charging_duration_s: int = Field(..., ge=0, description="Fixed charging duration in seconds")


class PreferencesAPI(CamelCaseAPI):
    """User preferences. Currently empty; unknown keys are rejected."""


class TripRequestAPI(CamelCaseAPI):
    """Request body for the /trips endpoint."""

    start: LatLon = Field(..., description="(lat, lon) start coordinate")
    destination: LatLon = Field(..., description="(lat, lon) destination coordinate")
    waypoints: list[WaypointAPI] = Field(
        default_factory=list, max_length=MAX_WAYPOINTS, description="Ordered list of waypoints"
    )
    departure_time: datetime = Field(
        ..., description="ISO-8601 departure time (e.g. '2026-08-15T08:30:00')"
    )
    vehicle_profile: VehicleProfileAPI = Field(..., description="Physical vehicle profile")
    start_soc_pct: float = Field(80.0, ge=0.0, le=100.0, description="Start SoC in percent")
    target_soc_pct: float = Field(20.0, ge=0.0, le=100.0, description="Target SoC in percent")
    min_arrival_soc_pct: float = Field(
        5.0,
        ge=0.0,
        le=100.0,
        description=(
            "Minimum allowed SoC when arriving at a charging station (may be lower "
            "than the general safety reserve on open road, since charging is guaranteed there)"
        ),
    )
    min_charging_time_s: int = Field(
        600,
        ge=0,
        le=1800,
        description=(
            "Minimum duration of a single charging session in seconds when charging "
            "(prevents needlessly short stops without forcing a stop)"
        ),
    )
    max_charge_soc_pct: float = Field(
        100.0,
        ge=0.0,
        le=100.0,
        description=(
            "Upper limit for the target SoC at regular charging stops "
            "(Supercharger stations) in percent. 100.0 = disabled."
        ),
    )
    preferences: PreferencesAPI = Field(
        default_factory=PreferencesAPI, description="User preferences (currently none)"
    )
    avoid_all_ferries: bool = Field(
        default=False, description="If True, all ferry connections are avoided"
    )
    highway_preference: Literal["off", "low", "medium", "high"] = Field(
        default="off",
        description=(
            "Highway preference level: 'off' (none), 'low' (priority boost *1.1), "
            "'medium' (*1.2), 'high' (*1.3) for road_class == MOTORWAY, without "
            "excluding non-highway routes."
        ),
    )
    avoided_ferries: list[FerryExclusionAPI] = Field(
        default_factory=list,
        max_length=MAX_LIST_ITEMS,
        description=("Previously detected ferry connections to avoid"),
    )
    ferry_time_windows: list[FerryTimeWindowAPI] = Field(
        default_factory=list,
        max_length=MAX_LIST_ITEMS,
        description=(
            "User-specified departure/arrival times for previously detected ferry connections"
        ),
    )
    charging_duration_specifications: list[ChargingDurationSpecificationAPI] = Field(
        default_factory=list,
        max_length=MAX_LIST_ITEMS,
        description="User-specified fixed charging durations for individual charging stops",
    )
    weather_detail_level: Literal["off", "low", "medium", "high"] = Field(
        default="high",
        description=(
            "Weather detail level: 'off', 'low', 'medium', or 'high'. "
            "'low'/'medium' use coarser weather resolution and complete faster; "
            "'off' skips weather entirely (placeholder values); 'high' uses "
            "per-segment weather (default)."
        ),
    )

    consider_construction_sites: bool = Field(
        default=True,
        description=(
            "If False, the construction-site provider is skipped for this request "
            "(no roadwork speed reductions), which makes planning faster."
        ),
    )
