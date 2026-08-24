"""Datenmodelle für das optimization-Modul.

Pydantic-Modelle zur Darstellung von Ladeplänen, Constraints und dem
Optimizer-Interface für Austauschbarkeit zwischen NetworkX und OR-Tools.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field, field_validator, model_validator
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
    mindest_ankunfts_soc_pct: float = Field(
        default=5.0,
        ge=0.0,
        le=100.0,
        description=(
            "Minimal zulässiger SoC beim ANKOMMEN an einer Ladestation (nicht "
            "unterwegs auf offener Strecke - dort gilt weiterhin `min_soc_pct`). "
            "Da an einer Ladestation garantiert nachgeladen wird, darf der SoC "
            "dort bewusst tiefer sinken als das allgemeine Sicherheits-Minimum - "
            "das ermöglicht, die besonders schnelle Ladeleistung im unteren "
            "SoC-Bereich der Ladekurve auszunutzen, statt unnötig früh (und "
            "damit langsamer) nachzuladen."
        ),
    )
    max_ladezeit_s: int = Field(
        default=3600,
        ge=600,
        le=7200,
        description="Maximale Dauer eines einzelnen Ladevorgangs (optional)",
    )
    mindest_ladezeit_s: int = Field(
        default=600,
        ge=0,
        le=1800,
        description=(
            "Minimale Dauer eines einzelnen Ladevorgangs, WENN geladen wird. "
            "Ein Kandidat-Ladeziel, dessen Ladezeit darunter läge, wird auf "
            "genau diese Mindestdauer gestreckt statt verworfen - verhindert "
            "unnötig kurze Ladehalte (z. B. 1 Minute), ohne den Ladehalt an "
            "sich zu erzwingen (die parallele 'Station überspringen'-Option "
            "bleibt unverändert verfügbar, siehe `_add_drive_edge`)."
        ),
    )

    @model_validator(mode="after")
    def validate_mindest_ladezeit_unter_max(self) -> OptimizationConstraints:
        """Validiert, dass die Mindest- die Maximaldauer nicht überschreitet."""
        if self.mindest_ladezeit_s > self.max_ladezeit_s:
            raise PydanticCustomError(
                "mindest_ladezeit_zu_hoch",
                "mindest_ladezeit_s ({mindest}) darf max_ladezeit_s ({max}) nicht überschreiten",
                {"mindest": self.mindest_ladezeit_s, "max": self.max_ladezeit_s},
            )
        return self


class DetourKosten(BaseModel):
    """Real, road-network-routed one-way detour cost for one charging station.

    Computed once, before the state-graph search runs, by
    `optimization.detour_routing.precompute_detour_costs` - replaces the
    straight-line heuristic (`DETOUR_ROUTENFAKTOR`/
    `DETOUR_GESCHWINDIGKEIT_KMH` in `optimizer.py`) with an actual
    GraphHopper-routed, elevation-aware detour computed the same way the
    main route's distance/time/energy are computed.

    Values represent the AVERAGE of the two directions of the round trip
    (main route -> station, station -> main route) - `NetworkXOptimizer`
    doubles this for the full round trip, mirroring the existing
    heuristic's calling convention (`_fuege_ladekante_hinzu`).
    """

    distanz_m: float = Field(ge=0.0, description="Average one-way routed distance in meters")
    zeit_s: float = Field(ge=0.0, description="Average one-way drive time in seconds")
    energie_kwh: float = Field(
        description=(
            "Average one-way energy consumption in kWh (may be negative if the "
            "detour road is net downhill, matching SegmentEnergyResult.energiebedarf_kwh "
            "sign convention)"
        )
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
        detour_kosten: dict[str, DetourKosten] | None = None,
    ) -> ChargingPlan:
        """Optimierungsmethode, die von beiden Backend-Implementierungen bereitgestellt wird.

        `ladedauer_vorgaben` (optionale, vom Nutzer vorgegebene feste Ladedauern in
        Sekunden je Stations-ID) überschreibt die automatische SoC-basierte
        Ladedauer-Berechnung für die betroffene Station. `faehr_zeitfenster`
        (optionale, vom Nutzer vorgegebene Fährfahrpläne, als
        `segment_index_start -> (segment_index_end, abfahrt, ankunft)`, siehe
        `tripplanner.routing.models.FaehrSegment`) lässt Segmente in diesem
        Bereich als fixe Fährüberfahrt statt als normale Fahrtkanten modellieren.
        `detour_kosten` (optional, real routed detour costs per station, see
        `optimization.detour_routing.precompute_detour_costs`) is looked up
        before falling back to the straight-line heuristic when computing
        off-route detour cost.
        """
        ...
