"""simulation-Modul für Tesla-Reisen.

Das Modul erzeugt aus Route, ChargingPlan, segmentEnergyResult und WeatherSample
eine diskrete Zeitreihe (TripsimulationResult) mit Zustaenden (FAHREN/LADEN/PAUSE),
Positionen, SoC und speed.
"""

from __future__ import annotations

from tripplanner.simulation.models import (
    ChargingCostByCurrency,
    ChargingStopSummary,
    SimulationFrame,
    TripSimulationResult,
    TripState,
)
from tripplanner.simulation.simulate import simulate_trip

__all__ = [
    "ChargingCostByCurrency",
    "ChargingStopSummary",
    "SimulationFrame",
    "TripSimulationResult",
    "TripState",
    "simulate_trip",
]
