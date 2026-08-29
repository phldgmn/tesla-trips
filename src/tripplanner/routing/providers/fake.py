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
        # Exakter Segment-Index jedes Zwischenstopps (siehe
        # `Route.via_point_indices`-Docstring): hier trivial verfuegbar, da
        # jede Teilstrecke separat konkateniert wird - der Zwischenstopp
        # `waypoints[i]` liegt exakt an der Segment-Anzahl nach Abschluss der
        # vorherigen Teilstrecke.
        via_point_indices: list[int] = []

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
            # waypoints[i + 1] ist ein Zwischenstopp, falls es nicht das Ziel
            # (letztes Element) ist.
            if i + 1 < len(waypoints) - 1:
                via_point_indices.append(len(segments))
        if not segments:
            # Start und Ziel identisch: liefere minimale Route mit einem Segment
            segments.append(
                RouteSegment(
                    segment_index=0,
                    geometrie=[start, start],
                    laenge_m=0.0,
                    strassenklasse="OTHER",
                    oberflaeche="asphalt",
                    tempolimit_kmh=0,
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
                min(wp[0] for wp in waypoints),
                min(wp[1] for wp in waypoints),
                max(wp[0] for wp in waypoints),
                max(wp[1] for wp in waypoints),
            ),
            via_point_indices=via_point_indices,
        )

    def _diskretisiere_teilstrecke(
        self, start: Coordinate, end: Coordinate
    ) -> list[tuple[Coordinate, Coordinate, float]]:
        """Zerlegt eine Teilstrecke in mehrere kuerzere Segmente (~SEGMENT_LAENGE_ZIEL_M).

        Identische Start-/Endkoordinaten (Laenge 0) liefern eine leere Liste,
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
            laenge_m = haversine_distance_m(seg_start, seg_ende)
            if laenge_m > 0:
                ergebnis.append((seg_start, seg_ende, laenge_m))
        return ergebnis
