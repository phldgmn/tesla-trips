"""Wind-Modul: Berechnung von Gegenwind- und Seitenwind-Komponenten.

Dieses Modul berechnet aus den Wetterdaten (Windgeschwindigkeit und -richtung)
und der Fahrtrichtung (Bearing) eines Route-Segments die effektiven Windkomponenten:
- Gegenwind-/Rückenwind-Komponente (m/s, positiv = Gegenwind, negativ = Rückenwind)
- Seitenwind-Komponente (m/s, positiv = von rechts, negativ = von links)

Das Modul ist reine Berechnungslogik ohne externe Datenquellen.
"""

from tripplanner.wind.models import WindComponents
from tripplanner.wind.wind import compute_wind_components, compute_wind_components_for_route

__all__ = [
    "WindComponents",
    "compute_wind_components",
    "compute_wind_components_for_route",
]
