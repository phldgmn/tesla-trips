"""data models for the construction module: construction zones, closure types, provider protocol.

Alle Koordinaten im Projekt folgen der Konvention: (lat, lon) in Dezimalgrad (WGS84).
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
    """Ein construction_zones-Abschnitt mit speed_limit_kmh, closure_type und Umleitungshinweis.

    Args:
        betroffene_segmente: Liste von Routesegment-IDs (0-basiert), die von der
            construction_zone betroffen sind.
        speed_limit_kmh: Reduziertes speed_limit_kmh in km/h (None wenn keine
            Beschraenkung).
        closure_type: Art der Sperrung/construction_zone.
        umleitungshinweis: Freitext-Information zur Umleitung (optional).
        land: Land, in dem die construction_zone liegt.
        gueltig_von: Startzeitpunkt der construction_zone (ISO 8601).
        gueltig_bis: Endzeitpunkt der construction_zone (ISO 8601), None wenn
            unbestimmt.
        length_m: estimated length of the affected road section in meters
            (None wenn nicht berechenbar).
    """

    betroffene_segmente: list[int] = Field(
        description="List of RouteSegment IDs (0-based) affected by the construction_zone."
    )
    speed_limit_kmh: Annotated[int | None, Field(ge=0, le=200, default=None)] = Field(
        description="Reduziertes speed_limit_kmh in km/h (None wenn keine Beschraenkung)."
    )
    closure_type: ClosureType = Field(description="Type of closure/construction_zone.")
    umleitungshinweis: Annotated[str | None, Field(max_length=500, default=None)] = Field(
        description="Free-text information about the detour (optional)."
    )
    land: Land = Field(description="Country in which the construction_zone is located.")
    gueltig_von: datetime = Field(description="Start time of the construction_zone (ISO 8601).")
    gueltig_bis: Annotated[datetime | None, Field(default=None)] = Field(
        description="Endzeitpunkt der construction_zone (ISO 8601), None wenn unbestimmt."
    )
    length_m: Annotated[float | None, Field(ge=0, default=None)] = Field(
        description="Geschaetzte length der betroffenen Strassenstrecke in Metern."
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
            raise ValueError(
                f"speed_limit_kmh must be set for closure_type {self.closure_type}."
            )
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
