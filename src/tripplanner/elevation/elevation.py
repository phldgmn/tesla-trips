"""Kernlogik für das elevation-Modul.

Bietet ElevationProvider für Höhenprofil-Extraktion und
Steigungsberechnung.
"""

from tripplanner.elevation.models import ElevationPoint, SegmentGradient
from tripplanner.elevation.providers import DEMDataSourceProtocol
from tripplanner.geo import Coordinate, haversine_distance_m


def calculate_horizontal_distance(coord1: Coordinate, coord2: Coordinate) -> float:
    """Berechnet horizontale Distanz zwischen zwei Koordinaten (WGS84).

    Nutzt die haversine_distance_m aus tripplanner.geo für die
    Großkreisdistanz. Für die meisten Anwendungsfälle (Routen mit
    Sampling von 100m+) ist dies ausreichend genau. Für ultrahohe
    Präzision (< 1cm) könnte geographiclib verwendet werden, aber
    das wäre Overkill für diese Anwendung.

    Args:
        coord1: (breitengrad, laengengrad) Punkt 1
        coord2: (breitengrad, laengengrad) Punkt 2

    Returns:
        Horizontale Distanz in Metern (nicht entlang der Route!)
    """
    return haversine_distance_m(coord1, coord2)


class ElevationProvider:
    """Hauptprovider-Klasse für Höhendaten.

    Extrahiert Höhenprofile entlang einer Route und berechnet
    Steigungsprofile je Segment.
    """

    def __init__(self, data_source: DEMDataSourceProtocol) -> None:
        """Initialisiere ElevationProvider.

        Args:
            data_source: DEMDataSourceProtocol Implementierung für Höhen-Lookup
        """
        self.data_source = data_source

    def get_elevation_profile(
        self, route: object, sampling_distance_m: float = 100.0
    ) -> list[ElevationPoint]:
        """Extrahiert Höhenprofile entlang der Route.

        Args:
            route: Die Route mit segments (aus routing.models)
            sampling_distance_m: Sampling-Distanz in Metern (Standard: 100 m)

        Returns:
            Liste von ElevationPoint für jeden Sample-Punkt (inkl. Start/Ende jedes Segments)

        Note:
            Die Route wird zuerst an jedem Segmentende sample-dicht abgetastet.
            Wenn sampling_distance_m < 100 m, wird feiner sample-dicht abgetastet.
        """
        if not hasattr(route, "segments") or not route.segments:
            return []

        # Erstelle Liste von Koordinatenpunkten
        # Startpunkt jedes Segments (außer das erste, das wird nur einmal genommen)
        coordinates: list[Coordinate] = []

        for i, segment in enumerate(route.segments):
            # Holt Koordinaten aus segment.geometrie
            if hasattr(segment, "geometrie") and segment.geometrie:
                segment_coords: list[Coordinate] = segment.geometrie
                if segment_coords:
                    if i == 0:
                        # Erstes Segment: Startpunkt hinzufügen
                        coordinates.append(segment_coords[0])
                    # Endpunkt jedes Segments hinzufügen (außer beim letzten, der wird
                    # nicht doppelt gezählt)
                    coordinates.append(segment_coords[-1])

        if not coordinates:
            return []

        # Höhenwerte abfragen
        elevations = self.data_source.get_elevations_batch(coordinates)

        return [
            ElevationPoint(koordinate=coord, hoehe_m=elev)
            for coord, elev in zip(coordinates, elevations, strict=True)
        ]

    def calculate_segment_gradients(
        self, elevation_points: list[ElevationPoint], route: object
    ) -> list[SegmentGradient]:
        """Berechnet Steigung/Gefälle je Segment aus Höhendifferenz und horizontaler Distanz.

        Args:
            elevation_points: ElevationPoints in Reihenfolge der Route (Start->Ziel)
            route: Originale Route (für Segment-Geometrie)

        Returns:
            Liste von SegmentGradient (einer pro Segment)

        Raises:
            ValueError: Wenn elevation_points nicht genug Punkte für die Segmente enthält
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

            # Höhendifferenz (Ende - Start; positiv = Steigung, negativ = Gefälle)
            dh = end_point.hoehe_m - start_point.hoehe_m

            # Horizontale Distanz berechnen (nicht Route-Länge!)
            horizontal_dist = calculate_horizontal_distance(
                start_point.koordinate, end_point.koordinate
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
