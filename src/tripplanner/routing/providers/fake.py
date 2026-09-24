"""Fake-Routing-Anbieter für Tests und lokalen Dev-Betrieb."""

from __future__ import annotations

from datetime import timedelta

from tripplanner.geo import bearing_deg, haversine_distance_m
from tripplanner.routing.models import Coordinate, Route, RouteSegment
from tripplanner.trip_input.models import TripRequest


class FakeRoutingProvider:
    """Fake-Implementierung ohne laufenden GraphHopper-Server (Tests & lokaler Dev-Betrieb).

    Diskretisiert jede Teilstrecke (Start -> Zwischenstopp -> ... -> Ziel) in
    mehrere kleinere Segmente (~SEGMENT_LAENGE_ZIEL_M je Segment), damit Anzahl
    und Granularität der Segmente mit `GraphHopperRoutingProvider` (ein Segment
    pro Polyline-Punktpaar) vergleichbar sind. `NetworkXOptimizer` modelliert
    Ladehalte und die "letztes Segment vor dem Ziel"-Heuristik pro Segment; mit
    nur einem einzigen, riesigen Segment pro Teilstrecke waeren diese Modelle
    unbrauchbar (drive_time_s/Energiebedarf des Segments wuerden effektiv nicht
    granular genug abgebildet).
    """

    SEGMENT_LAENGE_ZIEL_M: float = 5_000.0
    """Zielgroesse pro Fake-Segment in Metern."""

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Compute a fake route for a TripRequest, including its waypoints."""
        waypoints = [(wp.coordinate, wp.stay_duration) for wp in anfrage.waypoints]
        return await self.berechne_route_mit_waypoints(
            anfrage.start, anfrage.destination, waypoints
        )

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        destination: Coordinate,
        waypoints: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Compute a discretized fake route through the given waypoints."""
        points = [start] + [wp[0] for wp in waypoints] + [destination]

        segments: list[RouteSegment] = []
        total_distance = 0.0
        full_geometrie = [start]
        # Exact segment index of each waypoint (see the `Route.via_point_indices`
        # docstring): trivially available here because each leg is concatenated
        # separately - waypoint `points[i]` sits exactly at the segment count
        # after the previous leg is finished.
        via_point_indices: list[int] = []

        for i in range(len(points) - 1):
            for seg_start, seg_ende, length_m in self._diskretisiere_teilstrecke(
                points[i], points[i + 1]
            ):
                segments.append(
                    RouteSegment(
                        segment_index=len(segments),
                        geometrie=[seg_start, seg_ende],
                        length_m=length_m,
                        strassenklasse="PRIMARY",
                        surface="asphalt",
                        speed_limit_kmh=100,
                        steigung_rohdaten=1.5,
                        bearing_deg=bearing_deg(seg_start, seg_ende),
                    )
                )
                total_distance += length_m
                full_geometrie.append(seg_ende)
            # points[i + 1] is a waypoint unless it is the destination (last item).
            if i + 1 < len(points) - 1:
                via_point_indices.append(len(segments))
        if not segments:
            # Start and destination are identical: return a minimal one-segment route
            segments.append(
                RouteSegment(
                    segment_index=0,
                    geometrie=[start, start],
                    length_m=0.0,
                    strassenklasse="OTHER",
                    surface="asphalt",
                    speed_limit_kmh=0,
                    steigung_rohdaten=0.0,
                    bearing_deg=0.0,
                )
            )
            total_distance = 0.0
            full_geometrie = [start, start]
        return Route(
            segments=segments,
            gesamtlaenge_m=total_distance,
            geometrie=full_geometrie,
            bbox=(
                min(pt[0] for pt in points),
                min(pt[1] for pt in points),
                max(pt[0] for pt in points),
                max(pt[1] for pt in points),
            ),
            via_point_indices=via_point_indices,
        )

    def _diskretisiere_teilstrecke(
        self, start: Coordinate, end: Coordinate
    ) -> list[tuple[Coordinate, Coordinate, float]]:
        """Zerlegt eine Teilstrecke in mehrere kuerzere Segmente (~SEGMENT_LAENGE_ZIEL_M).

        Identische Start-/Endkoordinaten (length_m 0) liefern eine leere Liste,
        sodass der Aufrufer diese Teilstrecke automatisch überspringt.
        """
        gesamtlaenge_m = haversine_distance_m(start, end)
        if gesamtlaenge_m <= 0:
            return []

        anzahl_segmente = max(1, round(gesamtlaenge_m / self.SEGMENT_LAENGE_ZIEL_M))
        punkte: list[Coordinate] = [start]
        for i in range(1, anzahl_segmente):
            anteil = i / anzahl_segmente
            punkte.append(
                (
                    start[0] + (end[0] - start[0]) * anteil,
                    start[1] + (end[1] - start[1]) * anteil,
                )
            )
        punkte.append(end)

        ergebnis: list[tuple[Coordinate, Coordinate, float]] = []
        for i in range(len(punkte) - 1):
            seg_start, seg_ende = punkte[i], punkte[i + 1]
            length_m = haversine_distance_m(seg_start, seg_ende)
            if length_m > 0:
                ergebnis.append((seg_start, seg_ende, length_m))
        return ergebnis
