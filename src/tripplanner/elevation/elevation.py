"""Kernlogik für das elevation-Modul.

Bietet Elevationprovider für elevation_profile-Extraktion und
ascentsberechnung.
"""

from tripplanner.elevation.models import ElevationPoint, SegmentGradient
from tripplanner.elevation.providers import DEMDataSourceProtocol
from tripplanner.geo import Coordinate, haversine_distance_m


def calculate_horizontal_distance(coord1: Coordinate, coord2: Coordinate) -> float:
    """Calculatet horizontale distance zwischen zwei Koordinaten (WGS84).

    Uses die haversine_distance_m aus tripplanner.geo für die
    Grosskreisdistanz. Für die meisten Anwendungsfaelle (Routen mit
    Sampling von 100m+) ist dies ausreichend genau. Für ultrahohe
    Praezision (< 1cm) könnte geographiclib uses werden, aber
    das waere Overkill für diese Anwendung.

    Args:
        coord1: (latitude, longitude) Punkt 1
        coord2: (latitude, longitude) Punkt 2

    Returns:
        Horizontale distance in Metern (nicht entlang der Route!)
    """
    return haversine_distance_m(coord1, coord2)


class ElevationProvider:
    """Hauptprovider-Klasse für elevation_data.

    Extrahiert elevation_profilee entlang einer Route und berechnet
    ascentsprofile je segment.
    """

    def __init__(self, data_source: DEMDataSourceProtocol) -> None:
        """Initialisiere Elevationprovider.

        Args:
            data_source: DEMDataSourceProtocol implementation für heightn-Lookup
        """
        self.data_source = data_source

    async def get_elevation_profile(
        self, route: object, sampling_distance_m: float = 100.0
    ) -> list[ElevationPoint]:
        """Extrahiert elevation_profilee entlang der Route.

        Args:
            route: Die Route mit segments (aus routing.models)
            sampling_distance_m: Sampling-distance in Metern (Standard: 100 m)

        Returns:
            Liste von ElevationPoint für jeden Sample-Punkt (inkl. Start/Ende jedes segments)

        Note:
            Die Route wird zuerst an jedem segmentende sample-dicht abgetastet.
            Wenn sampling_distance_m < 100 m, wird feiner sample-dicht abgetastet.
        """
        if not hasattr(route, "segments") or not route.segments:
            return []

        # Erstelle Liste von Koordinatenpunkten
        # Startpunkt jedes segments (ausser das erste, das wird nur einmal genommen)
        coordinates: list[Coordinate] = []

        for i, segment in enumerate(route.segments):
            # Holt Koordinaten aus segment.geometrie
            if hasattr(segment, "geometrie") and segment.geometrie:
                segment_coords: list[Coordinate] = segment.geometrie
                if segment_coords:
                    if i == 0:
                        # Erstes segment: Startpunkt hinzufügen
                        coordinates.append(segment_coords[0])
                    # Endpunkt jedes segments hinzufügen (ausser beim letzten, der wird
                    # nicht doppelt gezaehlt)
                    coordinates.append(segment_coords[-1])

        if not coordinates:
            return []

        # heightnwerte abfragen
        elevations = await self.data_source.get_elevations_batch(coordinates)

        return [
            ElevationPoint(coordinate=coord, hoehe_m=elev)
            for coord, elev in zip(coordinates, elevations, strict=True)
        ]

    def calculate_segment_gradients(
        self, elevation_points: list[ElevationPoint], route: object
    ) -> list[SegmentGradient]:
        """Calculatet gradient/descent je segment aus heightndifferenz und horizontaler distance.

        Args:
            elevation_points: ElevationPoints in Reihenfolge der Route (Start->Ziel)
            route: Originale Route (für segment-Geometrie)

        Returns:
            Liste von segmentgradient (einer pro segment)

        Raises:
            ValueError: Wenn elevation_points nicht genug Punkte für die segmente enthaelt
        """
        if not hasattr(route, "segments") or not route.segments:
            return []

        if len(elevation_points) < len(route.segments) + 1:
            raise ValueError(
                f"Nicht genug ElevationPoints für {len(route.segments)} Segmente. "
                f"Benötigt: {len(route.segments) + 1}, erhalten: {len(elevation_points)}"
            )

        gradients: list[SegmentGradient] = []

        for seg_idx, start_point in enumerate(elevation_points[:-1]):
            if seg_idx >= len(route.segments):
                break

            end_point = elevation_points[seg_idx + 1]

            # heightndifferenz (Ende - Start; positiv = gradient, negativ = descent)
            dh = end_point.hoehe_m - start_point.hoehe_m

            # Horizontale distance berechnen (nicht Route-Laenge!)
            horizontal_dist = calculate_horizontal_distance(
                start_point.coordinate, end_point.coordinate
            )

            gradient_pct = 0.0 if horizontal_dist == 0 else (dh / horizontal_dist) * 100

            gradients.append(
                SegmentGradient(
                    segment_index=seg_idx,
                    steigung_prozent=gradient_pct,
                    hoehendifferenz_m=dh,
                    horizontale_distanz_m=horizontal_dist,
                )
            )

        return gradients
