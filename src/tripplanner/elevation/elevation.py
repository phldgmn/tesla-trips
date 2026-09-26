"""Core logic for the elevation module.

Provides elevation providers for elevation profile extraction and
ascent calculation.
"""

from tripplanner.elevation.models import ElevationPoint, SegmentGradient
from tripplanner.elevation.providers import DEMDataSourceProtocol
from tripplanner.geo import Coordinate, haversine_distance_m


def calculate_horizontal_distance(coord1: Coordinate, coord2: Coordinate) -> float:
    """Calculatet horizontale distance zwischen zwei Koordinaten (WGS84).

    Uses ``haversine_distance_m`` from ``tripplanner.geo`` for the
    great-circle distance. For most use cases (routes with
    100m+ sampling) this is sufficiently accurate. For ultra-high
    precision (< 1cm) ``geographiclib`` could be used, but
    that would be overkill for this application.

    Args:
        coord1: (latitude, longitude) Punkt 1
        coord2: (latitude, longitude) Punkt 2

    Returns:
        Horizontale distance in Metern (nicht entlang der Route!)
    """
    return haversine_distance_m(coord1, coord2)


class ElevationProvider:
    """Main provider class for elevation data.

    Extracts elevation profiles along a route and calculates
    ascent profiles per segment.
    """

    def __init__(self, data_source: DEMDataSourceProtocol) -> None:
        """Initialisiere Elevationprovider.

        Args:
            data_source: DEMDataSourceProtocol implementation for height lookup
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
            List of ElevationPoint for each sample point (including start/end of each segment)

        Note:
            The route is first sampled densely at every segment end.
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
                        # First segment: add start point
                        coordinates.append(segment_coords[0])
                    # Add end point of each segment (except the last one, which would
                    # be counted twice)
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
            elevation_points: ElevationPoints in route order (start to destination)
            route: Original route (for segment geometry)

        Returns:
            List of segment gradients (one per segment)

        Raises:
            ValueError: If elevation_points does not have enough points for the segments
        """
        if not hasattr(route, "segments") or not route.segments:
            return []

        if len(elevation_points) < len(route.segments) + 1:
            raise ValueError(
                f"Not enough ElevationPoints for {len(route.segments)} segments. "
                f"Required: {len(route.segments) + 1}, got: {len(elevation_points)}"
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
