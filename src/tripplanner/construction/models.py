"""Datenmodelle für das construction-Modul: Baustellen, Sperrungstypen, Provider-Protocol.

Alle Koordinaten im Projekt folgen der Konvention: (lat, lon) in Dezimalgrad (WGS84).
"""

import contextlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, Field, model_validator


class Sperrungstyp(StrEnum):
    """Sperrungstyp gemäß DATEX II RoadOrCarriagewayOrLaneManagementType."""

    FULLY_CLOSED = "fullyClosed"
    PARTIALLY_CLOSED = "partiallyClosed"
    LANE_CLOSED = "laneClosed"
    TEMPORARY_SPEED_LIMIT = "temporarySpeedLimit"
    REDUCED_LANES = "reducedLanes"
    DETOUR_REQUIRED = "detrourRequired"


class Land(StrEnum):
    """Ländercodes für Baustellen (DE=Deutschland, DK=Dänemark, SE=Schweden)."""

    DE = "DE"
    DK = "DK"
    SE = "SE"


class ConstructionZone(BaseModel):
    """Ein Baustellen-Abschnitt mit Tempolimit, Sperrungstyp und Umleitungshinweis.

    Args:
        betroffene_segmente: Liste von RouteSegment-IDs (0-basiert), die von der
            Baustelle betroffen sind.
        tempolimit_kmh: Reduziertes Tempolimit in km/h (None wenn keine
            Beschränkung).
        sperrungstyp: Art der Sperrung/Baustelle.
        umleitungshinweis: Freitext-Information zur Umleitung (optional).
        land: Land, in dem die Baustelle liegt.
        gueltig_von: Startzeitpunkt der Baustelle (ISO 8601).
        gueltig_bis: Endzeitpunkt der Baustelle (ISO 8601), None wenn
            unbestimmt.
    """

    betroffene_segmente: list[int] = Field(
        description="Liste von RouteSegment-IDs (0-basiert), die von der Baustelle betroffen sind."
    )
    tempolimit_kmh: Annotated[int | None, Field(ge=0, le=200, default=None)] = Field(
        description="Reduziertes Tempolimit in km/h (None wenn keine Beschränkung)."
    )
    sperrungstyp: Sperrungstyp = Field(description="Art der Sperrung/Baustelle.")
    umleitungshinweis: Annotated[str | None, Field(max_length=500, default=None)] = Field(
        description="Freitext-Information zur Umleitung (optional)."
    )
    land: Land = Field(description="Land, in dem die Baustelle liegt.")
    gueltig_von: datetime = Field(description="Startzeitpunkt der Baustelle (ISO 8601).")
    gueltig_bis: Annotated[datetime | None, Field(default=None)] = Field(
        description="Endzeitpunkt der Baustelle (ISO 8601), None wenn unbestimmt."
    )

    @model_validator(mode="after")
    def validate_tempolimit_for_sperrungstyp(self) -> Self:
        """Validiert, dass tempolimit_kmh bei bestimmten Sperrungstypen gesetzt ist.

        Raises:
            ValueError: Wenn tempolimit_kmh bei TEMPORARY_SPEED_LIMIT,
                PARTIALLY_CLOSED, LANE_CLOSED oder REDUCED_LANES fehlt.
        """
        if (
            self.sperrungstyp
            in (
                Sperrungstyp.TEMPORARY_SPEED_LIMIT,
                Sperrungstyp.PARTIALLY_CLOSED,
                Sperrungstyp.LANE_CLOSED,
                Sperrungstyp.REDUCED_LANES,
            )
            and self.tempolimit_kmh is None
        ):
            raise ValueError(
                f"tempolimit_kmh muss gesetzt sein für Sperrungstyp {self.sperrungstyp}."
            )
        return self


class ConstructionProvider(Protocol):
    """Protocol für Datenprovider von Baustelleninformationen.

    Alle implementierenden Provider müssen die Methode `fetch_construction_zones` implementieren,
    die eine Liste von ConstructionZone für eine gegebene Route zurückgibt.
    """

    async def fetch_construction_zones(
        self,
        route: "tripplanner.routing.models.Route",
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Abfrage von Baustellen entlang der Route für die angegebenen Länder."""
        raise NotImplementedError


# Re-export from tripplanner.routing for type hints
with contextlib.suppress(ImportError):
    import tripplanner.routing.models
