"""data models für das wind-Modul.

Pydantic-modele zur Darstellung von Wind components entlang einer Route.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WindComponents(BaseModel):
    """Wind components entlang einer Route.

    Attributes:
        segment_index: Nullbasierter Index des segments in der Route.
        gegenwind_ms: headwind-/Rückenwind-Komponente in m/s.
            Positiv = headwind (bremsend), negativ = Rückenwind (unterstützend).
        seitenwind_ms: crosswind-Komponente in m/s.
            Positiv = von rechts, negativ = von links.
    """

    segment_index: int = Field(..., description="Nullbasierter Index dieses Segments in der Route")
    gegenwind_ms: float = Field(
        ...,
        description="headwind-/Rückenwind-Komponente in m/s (positiv=headwind, negativ=Rückenwind)",
    )
    seitenwind_ms: float = Field(
        ..., description="crosswind-Komponente in m/s (positiv=von rechts, negativ=von links)"
    )
