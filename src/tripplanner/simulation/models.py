"""Datenmodelle für das simulation-Modul.

Pydantic-Modelle zur Darstellung von Simulationsframes und Ergebnissen.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

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
