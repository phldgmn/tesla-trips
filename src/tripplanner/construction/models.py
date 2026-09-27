"""data models for the construction module: construction zones, closure types, provider protocol.

All coordinates in the project follow the convention: (lat, lon) in decimal degrees (WGS84).
"""

import contextlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, Field, model_validator

DEFAULT_ROADWORKS_SPEED_LIMIT_KMH = 80


class ClosureType(StrEnum):
    """closure type per DATEX II RoadOrCarriagewayManagementType."""

    FULLY_CLOSED = "fullyClosed"
    PARTIALLY_CLOSED = "partiallyClosed"
    LANE_CLOSED = "laneClosed"
    TEMPORARY_SPEED_LIMIT = "temporarySpeedLimit"
    REDUCED_LANES = "reducedLanes"
    DETOUR_REQUIRED = "detrourRequired"


class Land(StrEnum):
    """Country codes for construction zones (DE=Germany, DK=Denmark, SE=Sweden)."""

    DE = "DE"
    DK = "DK"
    SE = "SE"


class ConstructionZone(BaseModel):
    """A construction zone section with speed_limit_kmh, closure_type and detour information.

    Args:
        affected_segments: List of route segment IDs (0-based) affected by the
            construction zone.
        speed_limit_kmh: Reduced speed limit in km/h (None if no
            restriction).
        closure_type: Type of closure/construction zone.
        detour_info: Free-text information about the detour (optional).
        land: Country in which the construction zone is located.
        valid_from: Start time of the construction zone (ISO 8601).
        valid_until: Endzeitpunkt der construction_zone (ISO 8601), None wenn
            unbestimmt.
        length_m: estimated length of the affected road section in meters
            (None wenn nicht berechenbar).
    """

    affected_segments: list[int] = Field(
        description="List of RouteSegment IDs (0-based) affected by the construction_zone."
    )
    speed_limit_kmh: Annotated[int | None, Field(ge=0, le=200, default=None)] = Field(
        description="Reduced speed limit in km/h (None if no restriction)."
    )
    closure_type: ClosureType = Field(description="Type of closure/construction_zone.")
    detour_info: Annotated[str | None, Field(max_length=500, default=None)] = Field(
        description="Free-text information about the detour (optional)."
    )
    land: Land = Field(description="Country in which the construction_zone is located.")
    valid_from: datetime = Field(description="Start time of the construction_zone (ISO 8601).")
    valid_until: Annotated[datetime | None, Field(default=None)] = Field(
        description="End time of the construction zone (ISO 8601), None if indefinite."
    )
    length_m: Annotated[float | None, Field(ge=0, default=None)] = Field(
        description="Estimated length of the affected road section in meters."
    )

    @model_validator(mode="after")
    def validate_tempolimit_for_sperrungstyp(self) -> Self:
        """Validiert, dass speed_limit_kmh bei bestimmten closure_types gesetzt ist.

        Raises:
            ValueError: Wenn speed_limit_kmh bei TEMPORARY_SPEED_LIMIT,
                PARTIALLY_CLOSED, LANE_CLOSED oder REDUCED_LANES fehlt.
        """
        if (
            self.closure_type
            in (
                ClosureType.TEMPORARY_SPEED_LIMIT,
                ClosureType.PARTIALLY_CLOSED,
                ClosureType.LANE_CLOSED,
                ClosureType.REDUCED_LANES,
            )
            and self.speed_limit_kmh is None
        ):
            raise ValueError(f"speed_limit_kmh must be set for closure_type {self.closure_type}.")
        return self


class ConstructionProvider(Protocol):
    """Protocol for data providers of construction site information.

    All implementing providers must die Methode `fetch_construction_zones` implementieren,
    which returns a list of ConstructionZone for a given route.
    """

    async def fetch_construction_zones(
        self,
        route: "tripplanner.routing.models.Route",
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Query for construction zones along the route for the specified countries."""
        raise NotImplementedError


# Re-export from tripplanner.routing for type hints
with contextlib.suppress(ImportError):
    import tripplanner.routing.models
