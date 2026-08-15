"""Datenmodelle für das optimization-Modul.

Pydantic-Modelle zur Darstellung von Ladeplänen, Constraints und dem
Optimizer-Interface für Austauschbarkeit zwischen NetworkX und OR-Tools.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile, Waypoint


class ChargingStop(BaseModel):
    """Ein Ladehalt mit Station, Ankunfts- und Ziel-SoC sowie Zeitangaben.

    Wird als Ergebnis der Optimierung verwendet. Hinweis: Dieses Modell
    unterscheidet sich von `tripplanner.battery.models.ChargingStop`:
    Hier werden Datetime-Objekte verwendet (keine Sekunden-seit-Reisebeginn).
    """

    station: ChargingStation
    segment_index: int = Field(ge=0, description="Segment-Index der Ladestation")
    ankunfts_soc_pct: float = Field(ge=0.0, le=100.0, description="SoC bei Ankunft in %")
    ziel_soc_pct: float = Field(
        ge=0.0, le=100.0, description="Angestrebter SoC nach dem Laden in %"
    )
    geschaetzte_ladedauer_s: int = Field(ge=0, description="Geschätzte Ladedauer in Sekunden")
    ankunftszeit: datetime = Field(description="Zeitpunkt der Ankunft an der Station")
    abfahrtszeit: datetime = Field(description="Zeitpunkt der Abfahrt von der Station")

    @field_validator("ankunfts_soc_pct", "ziel_soc_pct")
    @classmethod
    def validate_soc_range(cls, v: float) -> float:
        """Validiere SoC-Werte im erlaubten Bereich [0, 100]."""
        max_soc_pct = 100.0
        if v < 0.0 or v > max_soc_pct:
            raise PydanticCustomError(
                "soc_range_error",
                "SoC muss zwischen 0.0 und 100.0 liegen, ist aber {value}",
                {"value": v},
            )
        return v


class OptimizationConstraints(BaseModel):
    """Harte Constraints und Sicherheitsparameter für die Optimierung."""

    min_soc_pct: float = Field(
        default=15.0,
        ge=0.0,
        le=100.0,
        description="Minimal zulässiger SoC (Sicherheitsreserve)",
    )
    ziel_soc_pct: float = Field(
        default=80.0,
        ge=0.0,
        le=100.0,
        description="Gewünschter SoC am Ziel",
    )
    max_etappenlaenge_km: float = Field(
        default=500.0,
        gt=0.0,
        description="Maximale Distanz zwischen Ladestopps (optional)",
    )
    sicherheitsreserve_pct: float = Field(
        default=5.0,
        ge=0.0,
        le=20.0,
        description=(
            "Reserve auf dem Ziel-SoC (z. B. Ziel-SoC = 80%, Reserve = 5% → "
            "faktischer Ziel-SoC = 75%)"
        ),
    )
    max_ladezeit_s: int = Field(
        default=3600,
        ge=600,
        le=7200,
        description="Maximale Dauer eines einzelnen Ladevorgangs (optional)",
    )


class ChargingPlan(BaseModel):
    """Ergebnis der Optimierung: geordnete Liste von Ladehalten + Gesamtreisezeit."""

    ladehalte: list[ChargingStop]
    gesamtreisezeit_s: int = Field(ge=0, description="Gesamtreisezeit in Sekunden")
    min_zwischenstopp_ankunftszeit: dict[int, datetime] = Field(
        default_factory=dict,
        description="Mindestankunftszeit für Zwischenstopps (wenn nicht geladen wird)",
    )


class StateNode(BaseModel):
    """Interner Knoten im Zustandsgraphen: (segment_index, soc_bucket, time_bucket).

    Wird nicht als Pydantic-Exportmodell verwendet, dient nur interner Darstellung.
    """

    segment_index: int
    soc_pct: float  # diskretisiert
    zeitpunkt: datetime


class OptimizerInterface(Protocol):
    """Protocol für Austauschbarkeit zwischen NetworkX (Prototyp) und OR-Tools."""

    def optimize(  # noqa: PLR0913, PLR0917 -- vollständiger Zustand des Optimierungsproblems, siehe docs/plans/07-optimization.md Abschnitt 4
        self,
        route: Route,
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
        ladedauer_vorgaben: dict[str, int] | None = None,
        faehr_zeitfenster: dict[int, tuple[int, datetime, datetime]] | None = None,
    ) -> ChargingPlan:
        """Optimierungsmethode, die von beiden Backend-Implementierungen bereitgestellt wird.

        `ladedauer_vorgaben` (optionale, vom Nutzer vorgegebene feste Ladedauern in
        Sekunden je Stations-ID) überschreibt die automatische SoC-basierte
        Ladedauer-Berechnung für die betroffene Station. `faehr_zeitfenster`
        (optionale, vom Nutzer vorgegebene Fährfahrpläne, als
        `segment_index_start -> (segment_index_end, abfahrt, ankunft)`, siehe
        `tripplanner.routing.models.FaehrSegment`) lässt Segmente in diesem
        Bereich als fixe Fährüberfahrt statt als normale Fahrtkanten modellieren.
        """
        ...
