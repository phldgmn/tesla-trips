"""Wind-Modul: Berechnung von headwind- und crosswind-Komponenten.

Dieses Modul berechnet aus den Wetterdaten (wind_speed_ms und -richtung)
und der heading (Bearing) eines Route-Segments die effektiven Windkomponenten:
- headwind-/Rückenwind-Komponente (m/s, positiv = headwind, negativ = Rückenwind)
- crosswind-Komponente (m/s, positiv = von rechts, negativ = von links)

Das Modul ist reine Berechnungslogik ohne externe Datenquellen.
"""

from tripplanner.wind.models import WindComponents
from tripplanner.wind.wind import compute_wind_components, compute_wind_components_for_route

__all__ = [
    "WindComponents",
    "compute_wind_components",
    "compute_wind_components_for_route",
]
