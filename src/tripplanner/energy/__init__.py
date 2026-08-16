"""Energy-Modul (Phase 3): Physikalisches Energieverbrauchsmodell.

Das Modul berechnet den Energieverbrauch für Elektrofahrzeuge physikalisch fundiert
unter Berücksichtigung von:
- Rollwiderstand (inkl. Straßenbelag-Faktor)
- Luftwiderstand (inkl. Windkomponenten)
- Steigung/Höhenenergie
- Rekuperation (regeneratives Bremsen)
- HVAC-Verbrauch (temperaturabhängig)
"""

from tripplanner.energy.energy import (
    calculate_segment_consumption,
    calculate_total_consumption,
)
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters

__all__ = [
    "SegmentEnergyResult",
    "VehicleEnergyParameters",
    "calculate_segment_consumption",
    "calculate_total_consumption",
]
