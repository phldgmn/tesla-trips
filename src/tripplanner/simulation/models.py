"""Datenmodelle für das simulation-Modul.

Pydantic-Modelle zur Darstellung von Simulationsframes und Ergebnissen.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from tripplanner.routing.models import Coordinate

# Konstanten für Geschwindigkeitsschwellen
_MAX_LADE_GESCHWINDIGKIT_KMH = 0.5
_MAX_PAUSE_GESCHWINDIGKIT_KMH = 5.0


class TripState(StrEnum):
    """Zustand des Fahrzeugs zu einem Zeitpunkt in der Simulation."""

    FAHREN = "FAHREN"
    LADEN = "LADEN"
    PAUSE = "PAUSE"


class SimulationFrame(BaseModel):
    """Ein einzelner Zeitpunkt in der Reisesimulation."""

    zeitpunkt: datetime
    position: tuple[float, float] = Field(..., description="Position als (lat, lon) Tuple in WGS84")
    distanz_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz vom Reisebeginn entlang der Route in Metern"
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ladestand in Prozent")
    zustand: TripState
    geschwindigkeit_kmh: float = Field(..., ge=0.0, description="Geschwindigkeit in km/h")

    @model_validator(mode="after")
    def validate_speed_state_consistency(self) -> SimulationFrame:
        """Validiere Konsistenz zwischen Zustand und Geschwindigkeit."""
        if (
            self.zustand == TripState.LADEN
            and self.geschwindigkeit_kmh > _MAX_LADE_GESCHWINDIGKIT_KMH
        ):
            raise ValueError("Beim Laden muss Geschwindigkeit ≈ 0 km/h sein")
        if (
            self.zustand == TripState.PAUSE
            and self.geschwindigkeit_kmh > _MAX_PAUSE_GESCHWINDIGKIT_KMH
        ):
            raise ValueError("Bei Pause sollte Geschwindigkeit sehr gering sein")
        return self


class ChargingStopSummary(BaseModel):
    """Zusammenfassung eines Ladehalts fuer die Visualisierung.

    Ein Eintrag pro tatsaechlichem Ladehalt (nicht pro Simulationsframe) -
    im Gegensatz zu den `SimulationFrame`-Eintraegen mit `zustand == LADEN`,
    von denen es waehrend eines einzelnen Ladehalts mehrere geben kann.
    """

    name: str = Field(..., min_length=1, description="Name der Ladestation")
    station_id: str = Field(
        ...,
        min_length=1,
        description="Eindeutige ID der Ladestation, zur Identifikation "
        "bei einer vom Nutzer vorgegebenen Ladedauer (siehe "
        "`tripplanner.trip_input.models.LadedauerVorgabe`)",
    )
    position: tuple[float, float] = Field(
        ..., description="Position der Ladestation als (lat, lon)"
    )
    distanz_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz entlang der Route, an der abgebogen wird"
    )
    detour_geometrie: list[tuple[float, float]] = Field(
        default_factory=list,
        description=(
            "Echte, ueber GraphHopper geroutete Geometrie von der Route zur Ladestation und "
            "zurueck (leer, falls die Detour-Route nicht ermittelt werden konnte)"
        ),
    )
    route_index_vor: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `Route.geometrie`/`route_geometrie`, ab dem `detour_geometrie` die "
            "Hauptroute ersetzt (None, falls `detour_geometrie` leer ist)"
        ),
    )
    route_index_nach: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `Route.geometrie`/`route_geometrie`, bis zu dem (inklusive) "
            "`detour_geometrie` die Hauptroute ersetzt (None, falls `detour_geometrie` leer ist)"
        ),
    )
    detour_station_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `detour_geometrie`, an dem die Ladestation tatsaechlich erreicht wird - "
            "dort erfolgt der SoC-Sprung Ankunfts- zu Ziel-Wert in der Kartendarstellung, statt "
            "ueber die gesamte Rueckfahrt der Detour-Schleife verschmiert zu werden (None, "
            "falls `detour_geometrie` leer ist)."
        ),
    )
    ankunfts_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Ankunft in %")
    ziel_soc_pct: float = Field(
        ..., ge=0.0, le=100.0, description="Angestrebter SoC nach dem Laden in %"
    )
    ladedauer_s: int = Field(..., ge=0, description="Ladedauer in Sekunden")
    energie_geladen_kwh: float = Field(
        ..., ge=0.0, description="Waehrend des Ladehalts geladene Energiemenge in kWh"
    )
    ankunftszeit: datetime = Field(..., description="Zeitpunkt der Ankunft an der Station")
    abfahrtszeit: datetime = Field(..., description="Zeitpunkt der Abfahrt von der Station")


class TripSimulationResult(BaseModel):
    """Vollständige Zeitreihe einer Reise."""

    frames: list[SimulationFrame]
    gesamt_distanz_km: float = Field(..., ge=0, description="Gesamtdistanz in km")
    gesamt_fahrzeit_min: float = Field(..., ge=0, description="Gesamtfahrzeit in Minuten")
    gesamt_ladezeit_min: float = Field(..., ge=0, description="Gesamtladezeit in Minuten")
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start-SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC in %")
    charging_stops: list[ChargingStopSummary] = Field(
        default_factory=list,
        description="Ein Eintrag pro Ladehalt (chronologisch), fuer die Kartendarstellung",
    )


class LadehaltDetour(BaseModel):
    """Ergebnis des Detour-Routings zu einem Ladehalt.

    Input fuer `simulate_trip()`, produziert von
    `tripplanner.trip_input.api._step_lade_detours_routen`.

    Die beiden Klammerpunkte (`route_index_vor`/`route_index_nach`, indices in
    `Route.geometrie`) liegen bewusst deutlich VOR/NACH dem eigentlichen
    Abzweigpunkt auf der Route - ein Detour-Request mit `start == ziel`
    (derselbe Punkt) ist fuer GraphHopper richtungsmehrdeutig und fuehrt zu
    unnoetigen Umwegen (an der falschen Ausfahrt vorbei, an der naechsten
    wenden). Mit zwei UNTERSCHIEDLICHEN, bereits auf der Hauptroute in
    korrekter Fahrtrichtung liegenden Punkten ist die Fahrtrichtung dagegen
    von vornherein eindeutig.
    """

    geometrie: list[Coordinate] = Field(
        ...,
        min_length=2,
        description="Geroutete Geometrie von `route_index_vor` ueber die Station zum Ziel",
    )
    station_index: int = Field(
        ...,
        ge=0,
        description=(
            "Index in `geometrie`, an dem die Ladestation tatsaechlich erreicht wird (Ende des "
            "Hinwegs / Anfang des Rueckwegs, siehe `_step_lade_detours_routen` - zwei separat "
            "geroutete Beine statt eines Via-Punkt-Requests, damit dieser Index exakt statt per "
            "Naechster-Punkt-Heuristik ermittelt wird)"
        ),
    )
    route_index_vor: int = Field(
        ...,
        ge=0,
        description="Index in `Route.geometrie`, ab dem diese Geometrie die Hauptroute ersetzt",
    )
    route_index_nach: int = Field(
        ...,
        ge=0,
        description=(
            "Index in `Route.geometrie`, bis zu dem (inklusive) diese Geometrie die Hauptroute "
            "ersetzt"
        ),
    )
