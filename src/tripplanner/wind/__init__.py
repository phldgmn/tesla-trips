"""Wind-Modul: calculation von headwind- und crosswind-Komponenten.

Dieses Modul berechnet aus den weatherdaten (wind_speed_ms und -richtung)
und der heading (Bearing) eines Route-segments die effektiven Wind components:
- headwind-/Rückenwind-Komponente (m/s, positiv = headwind, negativ = Rückenwind)
- crosswind-Komponente (m/s, positiv = von rechts, negativ = von links)

Das Modul ist reine calculationslogik ohne externe dataquellen.
"""

from tripplanner.wind.models import WindComponents
from tripplanner.wind.wind import compute_wind_components, compute_wind_components_for_route

__all__ = [
    "WindComponents",
    "compute_wind_components",
    "compute_wind_components_for_route",
]
