"""Geteilte, abhaengigkeitsfreie geografische Primitive.

No business module in the sense of the module-boundary rule — analog zu einer externen
Bibliothek von jedem anderen `tripplanner`-Modul importierbar (siehe
`docs/07-implementierungsplan.md`, Abschnitt 6.2). Alle Koordinaten sind
`(lat, lon)` in Dezimalgrad (WGS84).
"""

from tripplanner.geo.geo import Coordinate, bearing_deg, geodesic_length_m, haversine_distance_m

__all__ = ["Coordinate", "bearing_deg", "geodesic_length_m", "haversine_distance_m"]
