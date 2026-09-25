"""Optimierungs-Modul für Tesla-Reisen.

calculatet den optimalen Ladeplan entlang einer bereits von GraphHopper
festgelegten Route. Löst ein diskretisiertes Zustandsraum-Suchproblem
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
