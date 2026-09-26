"""Data models for the wind module.

Pydantic-modele zur Darstellung von Wind components entlang einer Route.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WindComponents(BaseModel):
    """Wind components entlang einer Route.

    Attributes:
        segment_index: Nullbasierter Index des segments in der Route.
            gegenwind_ms: headwind/tailwind component in m/s.
                Positive = headwind (braking), negative = tailwind (assisting).
        seitenwind_ms: crosswind-Komponente in m/s.
            Positiv = von rechts, negativ = von links.
    """

    segment_index: int = Field(..., description="Nullbasierter Index dieses Segments in der Route")
    gegenwind_ms: float = Field(
        ...,
                description="headwind/tailwind component in m/s (positive=headwind, negative=tailwind)",
    )
    seitenwind_ms: float = Field(
        ..., description="crosswind-Komponente in m/s (positiv=von rechts, negativ=von links)"
    )
