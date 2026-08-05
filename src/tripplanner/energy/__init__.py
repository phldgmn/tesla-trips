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
    berechne_gesamtverbrauch,
    berechne_segment_verbrauch,
)
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters

__all__ = [
    "SegmentEnergyResult",
    "VehicleEnergyParameters",
    "berechne_gesamtverbrauch",
    "berechne_segment_verbrauch",
]
