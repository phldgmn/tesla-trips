"""Provider-Schicht für Routing-Anbieter.

Protokolle und Implementierungen für Routing-Anbieter (GraphHopper, Fake für Tests).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

import polyline

from tripplanner.geo import bearing_deg, haversine_distance_m
from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.models import (
    Coordinate,
    GraphHopperPath,
    Route,
    RouteSegment,
)
from tripplanner.trip_input.models import TripRequest


class RoutingProvider(Protocol):
    """Interface für Routing-Anbieter. Ermöglicht Fake-Implementierungen für Tests."""

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Berechnet eine Route für die gegebene TripRequest."""
        ...

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        ziel: Coordinate,
        zwischenstopps: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Berechnet eine Route mit expliziten Zwischenstopps."""
        ...


class FakeRoutingProvider:
    """Fake-Implementierung ohne laufenden GraphHopper-Server (Tests & lokaler Dev-Betrieb).

    Diskretisiert jede Teilstrecke (Start -> Zwischenstopp -> ... -> Ziel) in
    mehrere kleinere Segmente (~SEGMENT_LAENGE_ZIEL_M je Segment), damit Anzahl
    und Granularität der Segmente mit `GraphHopperRoutingProvider` (ein Segment
    pro Polyline-Punktpaar) vergleichbar sind. `NetworkXOptimizer` modelliert
    Ladehalte und die "letztes Segment vor dem Ziel"-Heuristik pro Segment; mit
    nur einem einzigen, riesigen Segment pro Teilstrecke waeren diese Modelle
    unbrauchbar (Fahrzeit/Energiebedarf des Segments wuerden effektiv nicht
    granular genug abgebildet).
    """

    SEGMENT_LAENGE_ZIEL_M: float = 5_000.0
    """Zielgroesse pro Fake-Segment in Metern."""

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Berechnet eine Route für eine TripRequest (inkl. Zwischenstopps) mit Fake-Daten."""
        zwischenstopps = [(wp.koordinate, wp.aufenthaltsdauer) for wp in anfrage.zwischenstopps]
        return await self.berechne_route_mit_waypoints(anfrage.start, anfrage.ziel, zwischenstopps)

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        ziel: Coordinate,
        zwischenstopps: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Berechnet eine diskretisierte Route mit Zwischenstopps mit Fake-Daten."""
        waypoints = [start] + [wp[0] for wp in zwischenstopps] + [ziel]

        segments: list[RouteSegment] = []
        total_distance = 0.0
        full_geometrie = [start]

        for i in range(len(waypoints) - 1):
            for seg_start, seg_ende, laenge_m in self._diskretisiere_teilstrecke(
                waypoints[i], waypoints[i + 1]
            ):
                segments.append(
                    RouteSegment(
                        segment_index=len(segments),
                        geometrie=[seg_start, seg_ende],
                        laenge_m=laenge_m,
                        strassenklasse="PRIMARY",
                        oberflaeche="asphalt",
                        tempolimit_kmh=100,
                        steigung_rohdaten=1.5,
                        bearing_deg=bearing_deg(seg_start, seg_ende),
                    )
                )
                total_distance += laenge_m
                full_geometrie.append(seg_ende)

        if not segments:
            raise ValueError(
                "Start und Ziel (inkl. Zwischenstopps) sind identisch - keine Route "
                "mit positiver Laenge berechenbar."
            )

        return Route(
            segments=segments,
            gesamtlaenge_m=total_distance,
            geometrie=full_geometrie,
            bbox=(
                min(wp[0] for wp in waypoints),
                min(wp[1] for wp in waypoints),
                max(wp[0] for wp in waypoints),
                max(wp[1] for wp in waypoints),
            ),
        )

    def _diskretisiere_teilstrecke(
        self, start: Coordinate, ende: Coordinate
    ) -> list[tuple[Coordinate, Coordinate, float]]:
        """Zerlegt eine Teilstrecke in mehrere kuerzere Segmente (~SEGMENT_LAENGE_ZIEL_M).

        Identische Start-/Endkoordinaten (Laenge 0) liefern eine leere Liste,
        sodass der Aufrufer diese Teilstrecke automatisch überspringt.
        """
        gesamtlaenge_m = haversine_distance_m(start, ende)
        if gesamtlaenge_m <= 0:
            return []

        anzahl_segmente = max(1, round(gesamtlaenge_m / self.SEGMENT_LAENGE_ZIEL_M))
        punkte: list[Coordinate] = [start]
        for i in range(1, anzahl_segmente):
            anteil = i / anzahl_segmente
            punkte.append(
                (
                    start[0] + (ende[0] - start[0]) * anteil,
                    start[1] + (ende[1] - start[1]) * anteil,
                )
            )
        punkte.append(ende)

        ergebnis: list[tuple[Coordinate, Coordinate, float]] = []
        for i in range(len(punkte) - 1):
            seg_start, seg_ende = punkte[i], punkte[i + 1]
            laenge_m = haversine_distance_m(seg_start, seg_ende)
            if laenge_m > 0:
                ergebnis.append((seg_start, seg_ende, laenge_m))
        return ergebnis


class GraphHopperRoutingProvider:
    """Konkrete Implementierung über GraphHopper HTTP API."""

    def __init__(self, client: GraphHopperClient, use_custom_model: bool = False):
        """Initialisiert den GraphHopper Routing Provider.

        Args:
            client: GraphHopperClient für HTTP Kommunikation
            use_custom_model: Falls True, benutzerdefiniertes Fahrzeugprofil verwenden
        """
        self.client = client
        self.use_custom_model = use_custom_model

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Berechnet eine Route für eine TripRequest (inkl. Zwischenstopps)."""
        # Umwandlung TripRequest → GraphHopper Parameter
        points = [anfrage.start] + [wp.koordinate for wp in anfrage.zwischenstopps] + [anfrage.ziel]

        # details_list = ["road_class", "max_speed", "average_slope", "surface"]
        details_list = ["road_class", "max_speed", "average_slope", "surface"]

        # custom_model nur verwenden, wenn gewünscht
        custom_model = None
        if self.use_custom_model:
            custom_model = {
                "speed": [
                    {"if": "road_class == MOTORWAY", "limit_to": 130},
                    {"if": "true", "limit_to": 100},
                ],
                "priority": [{"if": "road_class == MOTORWAY", "multiply_by": 1.0}],
                "distance_influence": 0.0,
            }

        response = await self.client.route(
            points=points,
            profile="car",
            elevation=True,
            details=details_list,
            custom_model=custom_model,
        )

        # Mapping GraphHopperResponse → Route
        return self._map_path_to_route(response.paths[0])

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        ziel: Coordinate,
        zwischenstopps: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Berechnet eine Route mit Zwischenstopps über GraphHopper."""
        # Umwandlung waypoints → points list
        points = [start] + [wp[0] for wp in zwischenstopps] + [ziel]

        details_list = ["road_class", "max_speed", "average_slope", "surface"]

        response = await self.client.route(
            points=points,
            profile="car",
            elevation=True,
            details=details_list,
        )

        return self._map_path_to_route(response.paths[0])

    def _map_path_to_route(self, path: GraphHopperPath) -> Route:
        """Mapped GraphHopperPath zu Route mit RouteSegments."""
        # Dekodiere Polyline
        coordinates: list[Coordinate] = polyline.decode(path.points)

        segments: list[RouteSegment] = []
        total_distance = 0.0
        full_geometrie = coordinates

        # Extrahiere Details für jedes Segment
        # Details: road_class, max_speed, average_slope, surface sind als Listen im dict enthalten
        road_classes = path.details.get("road_class", [])
        max_speeds = path.details.get("max_speed", [])
        average_slopes = path.details.get("average_slope", [])
        surfaces = path.details.get("surface", [])

        # Erstelle ein Segment pro Edge (zwischen zwei aufeinanderfolgenden Points)
        for i in range(len(coordinates) - 1):
            start_coord = coordinates[i]
            end_coord = coordinates[i + 1]

            # Berechne Länge des Segments (Haversine Distanz)
            laenge_m = haversine_distance_m(start_coord, end_coord)
            total_distance += laenge_m

            # Extrahiere Segment-Attribute aus Details
            # Wenn details kürzer als Edges sind, nutze den letzten Wert oder None
            idx = min(i, len(road_classes) - 1) if road_classes else None
            idx_slope = min(i, len(average_slopes) - 1) if average_slopes else None

            strassenklasse = road_classes[idx] if road_classes and idx is not None else "OTHER"
            tempolimit_kmh = self._normalize_max_speed(
                max_speeds[idx] if max_speeds and idx is not None else None
            )
            steigung_rohdaten = (
                average_slopes[idx_slope] if average_slopes and idx_slope is not None else None
            )
            oberflaeche = surfaces[idx] if surfaces and idx is not None else None

            # Berechne Bearing für das Segment
            bearing = bearing_deg(start_coord, end_coord)

            segment = RouteSegment(
                segment_index=i,
                geometrie=[start_coord, end_coord],
                laenge_m=laenge_m,
                strassenklasse=strassenklasse,
                oberflaeche=oberflaeche,
                tempolimit_kmh=tempolimit_kmh,
                steigung_rohdaten=steigung_rohdaten,
                bearing_deg=bearing,
            )
            segments.append(segment)

        # Bounding box berechnen
        if coordinates:
            lats = [c[0] for c in coordinates]
            lons = [c[1] for c in coordinates]
            bbox = (min(lats), min(lons), max(lats), max(lons))
        else:
            bbox = None

        return Route(
            segments=segments,
            gesamtlaenge_m=total_distance,
            geometrie=full_geometrie,
            bbox=bbox,
        )

    def _normalize_max_speed(self, value: str | float | None) -> int | None:
        """Normalisiert GraphHopper max_speed Werte.

        - None → None (nicht verfügbar)
        - 0 → None (kein Schild, z. B. Spielstraßen)
        - -1 → None (nicht bekannt)
        - positive Werte → int (km/h)
        """
        if value is None:
            return None
        speed = float(value)
        if speed <= 0:
            return None
        return int(speed)
