"""Energy-Modul (Phase 3): Physikalisches Energieverbrauchsmodell.

Das Modul berechnet den energy_consumption für Elektrofahrzeuge physikalisch fundiert
unter Berücksichtigung von:
- rolling_resistance (inkl. Straßenbelag-Faktor)
- air_drag (inkl. Windkomponenten)
- gradient/Höhenenergie
- recuperation (regeneratives Bremsen)
- HVAC-consumption (temperaturabhängig)
"""

from tripplanner.energy.energy import (
    berechne_luftdichte,
    calculate_segment_consumption,
    calculate_total_consumption,
    f_oberflaeche,
    f_strassenzustand,
)
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters

__all__ = [
    "SegmentEnergyResult",
    "VehicleEnergyParameters",
    "berechne_luftdichte",
    "calculate_segment_consumption",
    "calculate_total_consumption",
    "f_oberflaeche",
    "f_strassenzustand",
]
