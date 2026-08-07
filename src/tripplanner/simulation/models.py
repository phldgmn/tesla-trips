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


class TripSimulationResult(BaseModel):
    """Vollständige Zeitreihe einer Reise."""

    frames: list[SimulationFrame]
    gesamt_distanz_km: float = Field(..., ge=0, description="Gesamtdistanz in km")
    gesamt_fahrzeit_min: float = Field(..., ge=0, description="Gesamtfahrzeit in Minuten")
    gesamt_ladezeit_min: float = Field(..., ge=0, description="Gesamtladezeit in Minuten")
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start-SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC in %")
