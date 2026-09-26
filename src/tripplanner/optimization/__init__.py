"""Optimization module for Tesla trips.

Calculates the optimal charging plan along an already established by GraphHopper
established route. Solves a discretized state-space search problem
mit NetworkX (Prototyp) oder OR-Tools (spaetere Ausbaustufe).
"""

from tripplanner.optimization.models import (
    ChargingPlan,
    ChargingStop,
    DetourKosten,
    OptimizationConstraints,
    OptimizerInterface,
)
from tripplanner.optimization.optimizer import (
    create_networkx_optimizer,
    create_ortools_optimizer,
)

__all__ = [
    "ChargingPlan",
    "ChargingStop",
    "DetourKosten",
    "OptimizationConstraints",
    "OptimizerInterface",
    "create_networkx_optimizer",
    "create_ortools_optimizer",
]
