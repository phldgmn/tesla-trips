"""Energy-Modul (Phase 3): Physikalisches Energieconsumptionsmodell.

This module calculates energy consumption for electric vehicles based on physics
considering:
- rolling_resistance (inkl. road_surface_factor)
- air_drag (inkl. Wind components)
- gradient/elevation_energy
- recuperation (regeneratives Bremsen)
- HVAC consumption (temperature-dependent)
"""

from tripplanner.energy.energy import (
    berechne_luftdichte,
    calculate_segment_consumption,
    calculate_total_consumption,
    f_road_surface,
    f_strassenzustand,
)
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters

__all__ = [
    "SegmentEnergyResult",
    "VehicleEnergyParameters",
    "berechne_luftdichte",
    "calculate_segment_consumption",
    "calculate_total_consumption",
    "f_road_surface",
    "f_strassenzustand",
]
