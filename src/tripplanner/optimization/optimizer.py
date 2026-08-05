"""Kern-Logik für die Optimierung: NetworkX- und OR-Tools-Implementierungen.

NetworkXOptimizer: A*/Dijkstra auf diskretisiertem Zustandsgraph.
ORToolsOptimizer: Platzhalter für zukünftige CP-SAT Implementierung.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import networkx as nx
from networkx import DiGraph

from tripplanner.battery.models import ChargingCurve, LadekurveReferenz
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization.discretizer import (
    SOC_STEP_PCT_DEFAULT,
    TIME_STEP_MIN_DEFAULT,
    bucket_to_soc,
    bucket_to_zeit,
    soc_to_bucket,
    zeit_to_bucket,
)
from tripplanner.optimization.models import (
    ChargingPlan,
    ChargingStop,
    OptimizationConstraints,
    OptimizerInterface,
)
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile, Waypoint

if TYPE_CHECKING:
    pass


# Konstanten für Kostenfunktion
COST_INF: float = 1e9  # Unendlich für unzulässige Kanten
"""Grobe Konstante für unzulässige Kanten (Constraint-Verletzung)."""

DEFAULT_SPEED_KMH: float = 110.0
"""Default-Reisegeschwindigkeit in km/h (Autobahn)."""

MAX_SOC_PCT: float = 100.0
"""Maximaler SoC in Prozent."""


class NetworkXOptimizer(OptimizerInterface):
    """A*/Dijkstra-Optimierung mit NetworkX (Prototyp)."""

    def __init__(
        self,
        soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
        time_step_min: int = TIME_STEP_MIN_DEFAULT,
    ) -> None:
        """Initialisiere den Optimierer.

        Args:
            soc_step_pct: Schrittweite für SoC-Diskretisierung in Prozent.
            time_step_min: Schrittweite für Zeit-Diskretisierung in Minuten.
        """
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self._base_time: datetime

    def optimize(  # noqa: PLR0913, PLR0917 -- vollständiger Zustand des Optimierungsproblems, siehe docs/plans/07-optimization.md Abschnitt 4
        self,
        route: Route,
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """Optimiert Ladeplan unter Verwendung eines diskretisierten Zustandsgraphen.

        A*-Suche mit Heuristik = verbleibende Distanz / geschätzte Reisegeschwindigkeit.

        Args:
            route: Die vollständige Route mit Metadaten.
            segments: Liste aller Route-Segmente.
            gradients: Liste der Segment-Gradienten.
            energy_results: Ergebnisse der Energieberechnung je Segment.
            charging_stations: Liste verfügbarer Ladestationen.
            waypoints: Liste von Zwischenstopps mit optionaler Aufenthaltsdauer.
            vehicle_profile: Physikalisches Fahrzeugprofil.
            constraints: Optimierungs-Constraints (Min-SoC, Ziel-SoC, etc.).
            start_soc_pct: Start-SoC des Fahrzeugs in Prozent.
            abfahrtszeit: Geplante Abfahrtszeit.
            iteration: Iterationsnummer für spätere Wetter-Iter.

        Returns:
            ChargingPlan mit Ladehalten und Gesamtreisezeit.
        """
        # Validiere Eingabeparameter
        if start_soc_pct < 0.0 or start_soc_pct > MAX_SOC_PCT:
            raise ValueError(f"Start-SoC muss im Bereich [0, 100] liegen, ist aber {start_soc_pct}")
        if start_soc_pct < constraints.min_soc_pct:
            raise ValueError(
                f"Start-SoC ({start_soc_pct}%) ist unter Min-SoC ({constraints.min_soc_pct}%)"
            )

        # Erstelle gerichteten Graphen
        G: DiGraph = nx.DiGraph()

        # Zielknoten: letztes Segment, Ziel-SoC (inkl. Sicherheitsreserve)
        ziel_soc_target = max(
            constraints.ziel_soc_pct - constraints.sicherheitsreserve_pct, constraints.min_soc_pct
        )
        ziel_soc_bucket = soc_to_bucket(ziel_soc_target, self.soc_step_pct)

        # Erstelle Startknoten (segment_index=0, soc=start_soc, zeit=abfahrtszeit)
        start_soc_bucket = soc_to_bucket(start_soc_pct, self.soc_step_pct)
        start_zeit_bucket = zeit_to_bucket(abfahrtszeit, abfahrtszeit, self.time_step_min)

        start_node = (0, start_soc_bucket, start_zeit_bucket)
        G.add_node(
            start_node,
            type="start",
            soc_pct=start_soc_pct,
            zeitpunkt=abfahrtszeit,
            segment_index=0,
        )

        # Mappe Zwischenstopps auf Segmente (Segment-Index → Waypoint)
        waypoint_map: dict[int, list[Waypoint]] = {}
        for wp in waypoints:
            seg_idx = self._waypoint_to_segment(wp, segments)
            if seg_idx not in waypoint_map:
                waypoint_map[seg_idx] = []
            waypoint_map[seg_idx].append(wp)

        # Mappe Ladestationen auf Segmente
        station_segments = self._map_stations_to_segments(charging_stations, segments)

        # Erstelle Ladekurve für das Fahrzeug (V3-Standard)
        ladekurve = LadekurveReferenz.model_3_lr_v3()

        # Setze Basiszeit für Zeit-Bucket Berechnungen
        self._base_time = abfahrtszeit

        # Generiere Knoten und Kanten
        self._generate_graph(
            G=G,
            segments=segments,
            energy_results=energy_results,
            charging_stations=charging_stations,
            waypoint_map=waypoint_map,
            station_segments=station_segments,
            vehicle_profile=vehicle_profile,
            ladekurve=ladekurve,
            constraints=constraints,
            start_node=start_node,
            ziel_soc_bucket=ziel_soc_bucket,
            ziel_soc_target=ziel_soc_target,
            max_time_buckets=self._estimate_max_time_buckets(segments),
        )

        # A*-Suche zum Zielknoten
        try:
            # Zielknoten: beliebiger SoC ≥ ziel_soc_target im letzten Segment
            # Wir wählen den Knoten mit niedrigster Kosten
            target_candidates = [
                (seg_idx, soc_b, zeit_b)
                for seg_idx, soc_b, zeit_b in G.nodes()
                if seg_idx == len(segments) - 1 and soc_b >= ziel_soc_bucket
            ]

            if not target_candidates:
                raise ValueError("Kein erreichbarer Zielknoten gefunden. Route nicht fahrbar.")

            # Finde günstigsten Zielknoten
            best_target = min(
                target_candidates,
                key=lambda n: G.nodes[n].get("total_cost", COST_INF),
            )

            path = nx.astar_path(
                G,
                source=start_node,
                target=best_target,
                heuristic=lambda u, v: self._heuristik(u, v, segments),
                weight="cost",
            )

        except nx.NetworkXNoPath:
            raise ValueError(
                "Kein fahrbarer Pfad gefunden. Eventuell zu wenig Reichweite."
            ) from None

        # Extrahiere ChargingStop-Objekte aus dem Pfad
        ladehalte = self._extract_charging_stops(
            path=path,
            segments=segments,
            charging_stations=charging_stations,
            vehicle_profile=vehicle_profile,
            ladekurve=ladekurve,
            constraints=constraints,
        )

        # Berechne Gesamtreisezeit
        last_node = path[-1]
        last_zeitpunkt = bucket_to_zeit(last_node[2], abfahrtszeit, self.time_step_min)

        # Addiere Fahrzeit des letzten Segments (falls Ziel nicht am Segmentende)
        if last_node[0] < len(segments) - 1:
            remaining_distance = sum(seg.laenge_m for seg in segments[last_node[0] + 1 :])
            remaining_time_s = remaining_distance / (DEFAULT_SPEED_KMH * 1000 / 3600)
            last_zeitpunkt += timedelta(seconds=remaining_time_s)

        gesamtreisezeit = int((last_zeitpunkt - abfahrtszeit).total_seconds())

        # Mindestankunftszeit für Zwischenstopps berechnen
        min_zwischenstopp_ankunftszeit = self._compute_waypoint_times(
            path=path,
            waypoints=waypoints,
            segments=segments,
            abfahrtszeit=abfahrtszeit,
        )

        return ChargingPlan(
            ladehalte=ladehalte,
            gesamtreisezeit_s=gesamtreisezeit,
            min_zwischenstopp_ankunftszeit=min_zwischenstopp_ankunftszeit,
        )

    def _waypoint_to_segment(self, waypoint: Waypoint, segments: list[RouteSegment]) -> int:
        """Ermittle das Segment, das einem Waypoint am nächsten liegt."""
        wp_coord = waypoint.koordinate

        min_dist = float("inf")
        closest_seg_idx = 0

        for idx, seg in enumerate(segments):
            # Benutze den Segment-Startpunkt als Referenz
            seg_start = seg.geometrie[0]
            dist = self._haversine_distance(wp_coord, seg_start)
            if dist < min_dist:
                min_dist = dist
                closest_seg_idx = idx

        return closest_seg_idx

    def _map_stations_to_segments(
        self, stations: list[ChargingStation], segments: list[RouteSegment]
    ) -> dict[int, list[ChargingStation]]:
        """Mappe Ladestationen auf nahegelegene Segmente."""
        station_map: dict[int, list[ChargingStation]] = {}

        for station in stations:
            best_seg_idx = self._station_to_segment(station, segments)
            if best_seg_idx not in station_map:
                station_map[best_seg_idx] = []
            station_map[best_seg_idx].append(station)

        return station_map

    def _station_to_segment(self, station: ChargingStation, segments: list[RouteSegment]) -> int:
        """Ermittle das Segment, das einer Ladestation am nächsten liegt."""
        station_coord = station.coordinate

        min_dist = float("inf")
        closest_seg_idx = 0

        for idx, seg in enumerate(segments):
            # Prüfe alle Punkte des Segments
            for coord in seg.geometrie:
                dist = self._haversine_distance(station_coord, coord)
                if dist < min_dist:
                    min_dist = dist
                    closest_seg_idx = idx

        return closest_seg_idx

    def _haversine_distance(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        """Berechne Haversine-Distanz zwischen zwei Koordinaten."""
        lat1 = math.radians(a[0])
        lat2 = math.radians(b[0])
        delta_lat = math.radians(b[0] - a[0])
        delta_lon = math.radians(b[1] - a[1])

        h = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))

        EARTH_RADIUS_M = 6_371_000.0
        return EARTH_RADIUS_M * c

    def _estimate_max_time_buckets(self, segments: list[RouteSegment]) -> int:
        """Schätze die maximale Anzahl an Zeit-Buckets für die gesamte Route."""
        total_distance_m = sum(seg.laenge_m for seg in segments)
        speed_mps = DEFAULT_SPEED_KMH * 1000 / 3600
        total_time_s = total_distance_m / speed_mps
        total_time_min = total_time_s / 60.0

        return int(total_time_min / self.time_step_min) + 5  # Sicherheitspuffer

    def _generate_graph(  # noqa: PLR0913, PLR0917 -- Graph-Konstruktion braucht den vollen Kontext (Route, Energie, Laden, Zwischenstopps, Constraints)
        self,
        G: DiGraph,
        segments: list[RouteSegment],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoint_map: dict[int, list[Waypoint]],
        station_segments: dict[int, list[ChargingStation]],
        vehicle_profile: VehicleProfile,
        ladekurve: ChargingCurve,
        constraints: OptimizationConstraints,
        start_node: tuple[int, int, int],
        ziel_soc_bucket: int,
        ziel_soc_target: float,
        max_time_buckets: int,
    ) -> None:
        """Generiere Knoten und Kanten für den Zustandsgraphen."""
        # Nutze BFS/DFS-artige Erweiterung: nur erreichbare Knoten erzeugen
        visited: set[tuple[int, int, int]] = set()
        queue: list[tuple[int, int, int]] = [start_node]
        G.nodes[start_node]["total_cost"] = 0.0
        G.nodes[start_node]["parent"] = None

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            seg_idx, soc_bucket, time_bucket = current

            # Prüfe, ob Ziel erreicht
            if seg_idx == len(segments) - 1 and soc_bucket >= ziel_soc_bucket:
                continue  # Ziel erreicht, nicht weiter erweitern

            # 1. Fahrtkante: Nächstes Segment fahren
            if seg_idx < len(segments) - 1:
                next_seg_idx = seg_idx + 1
                self._add_drive_edge(
                    G=G,
                    current=current,
                    next_seg_idx=next_seg_idx,
                    segments=segments,
                    energy_results=energy_results,
                    soc_bucket=soc_bucket,
                    time_bucket=time_bucket,
                    max_time_buckets=max_time_buckets,
                    constraints=constraints,
                    vehicle_profile=vehicle_profile,
                    ladekurve=ladekurve,
                    queue=queue,
                )

            # 2. Ladekante: An dieser Station laden (wenn verfügbar)
            if seg_idx in station_segments:
                self._add_charging_edges(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    stations=station_segments[seg_idx],
                    segments=segments,
                    vehicle_profile=vehicle_profile,
                    ladekurve=ladekurve,
                    soc_bucket=soc_bucket,
                    time_bucket=time_bucket,
                    max_time_buckets=max_time_buckets,
                    constraints=constraints,
                    queue=queue,
                )

            # 3. Zwischenstopp-Zwang: Aufenthaltsdauer einhalten
            if seg_idx in waypoint_map:
                for wp in waypoint_map[seg_idx]:
                    if wp.aufenthaltsdauer:
                        self._add_waypoint_wait_edge(
                            G=G,
                            current=current,
                            seg_idx=seg_idx,
                            waypoint=wp,
                            time_bucket=time_bucket,
                            max_time_buckets=max_time_buckets,
                            queue=queue,
                        )

    def _add_drive_edge(  # noqa: PLR0913, PLR0917 -- Fahrtkanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        next_seg_idx: int,
        segments: list[RouteSegment],
        energy_results: list[SegmentEnergyResult],
        soc_bucket: int,
        time_bucket: int,
        max_time_buckets: int,
        constraints: OptimizationConstraints,
        vehicle_profile: VehicleProfile,
        ladekurve: ChargingCurve,
        queue: list[tuple[int, int, int]],
    ) -> None:
        """Füge eine Fahrtkante zum nächsten Segment hinzu."""
        next_seg = segments[next_seg_idx]
        energy_result = energy_results[next_seg_idx]

        # SoC-Verbrauch für dieses Segment
        verbrauch_pct = self._calc_soc_verbrauch_pct(
            energy_result=energy_result,
            vehicle_profile=vehicle_profile,
        )

        new_soc_bucket = soc_bucket - round(verbrauch_pct / self.soc_step_pct)

        # Prüfe, ob SoC unter Min-SoC fällt
        new_soc_pct = bucket_to_soc(new_soc_bucket, self.soc_step_pct)
        if new_soc_pct < constraints.min_soc_pct:
            return  # Unzulässig

        # Fahrzeit berechnen
        fahrzeit_s = next_seg.laenge_m / (DEFAULT_SPEED_KMH * 1000 / 3600)
        new_time_bucket = time_bucket + int(fahrzeit_s / (self.time_step_min * 60))

        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        # Kosten berechnen
        kosten = fahrzeit_s  # Nur Fahrzeit, keine Ladezeit

        next_node = (next_seg_idx, new_soc_bucket, new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="drive",
                soc_pct=new_soc_pct,
                zeitpunkt=bucket_to_zeit(new_time_bucket, self._base_time, self.time_step_min),
                segment_index=next_seg_idx,
                total_cost=COST_INF,
                parent=None,
            )
            queue.append(next_node)

        # Kante hinzufügen mit Kosten
        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(current, next_node, cost=kosten)
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current

    def _add_charging_edges(  # noqa: PLR0913, PLR0917 -- Ladekanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        stations: list[ChargingStation],
        segments: list[RouteSegment],
        vehicle_profile: VehicleProfile,
        ladekurve: ChargingCurve,
        soc_bucket: int,
        time_bucket: int,
        max_time_buckets: int,
        constraints: OptimizationConstraints,
        queue: list[tuple[int, int, int]],
    ) -> None:
        """Füge Ladekanten zu allen Stationen in diesem Segment hinzu."""
        current_soc_pct = bucket_to_soc(soc_bucket, self.soc_step_pct)

        for station in stations:
            # Ladeziel wählen: Ziel-SoC oder 100% (je nach Distanz zum Ziel)
            remaining_segments = len(segments) - seg_idx - 1
            if remaining_segments == 0:
                Ziel_soc_pct = max(
                    constraints.ziel_soc_pct - constraints.sicherheitsreserve_pct,
                    constraints.min_soc_pct,
                )
            else:
                Ziel_soc_pct = MAX_SOC_PCT

            # Berechne Ladezeit für verschiedene Ziel-SoC-Werte
            Ziel_soc_values = [
                constraints.ziel_soc_pct,
                80.0,
                90.0,
                Ziel_soc_pct,
            ]
            for Ziel_soc in Ziel_soc_values:
                if Ziel_soc <= current_soc_pct:
                    continue  # Bereits höher als Ziel

                # Ladezeit berechnen
                delta_soc_pct = Ziel_soc - current_soc_pct
                ladezeit_s = self._calc_ladezeit_s(
                    delta_soc_pct=delta_soc_pct,
                    ladekurve=ladekurve,
                    batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                )

                if ladezeit_s > constraints.max_ladezeit_s:
                    continue  # Zu lange Ladezeit

                new_soc_bucket = soc_to_bucket(Ziel_soc, self.soc_step_pct)
                new_time_bucket = time_bucket + int(ladezeit_s / (self.time_step_min * 60))

                if new_time_bucket > max_time_buckets:
                    continue  # Zeitlimit überschritten

                # Kosten: Fahrzeit + Ladezeit
                kosten = ladezeit_s  # Nur Ladezeit zählt als Kosten (Fahrzeit war schon bezahlt)

                next_node = (seg_idx, new_soc_bucket, new_time_bucket)

                if next_node not in G.nodes:
                    G.add_node(
                        next_node,
                        type="charge",
                        station_id=station.station_id,
                        soc_pct=Ziel_soc,
                        zeitpunkt=bucket_to_zeit(
                            new_time_bucket, self._base_time, self.time_step_min
                        ),
                        segment_index=seg_idx,
                        total_cost=COST_INF,
                        parent=None,
                    )
                    queue.append(next_node)

                current_cost = G.nodes[current].get("total_cost", 0.0)
                new_total_cost = current_cost + kosten

                if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
                    G.add_edge(current, next_node, cost=kosten)
                    G.nodes[next_node]["total_cost"] = new_total_cost
                    G.nodes[next_node]["parent"] = current

    def _add_waypoint_wait_edge(  # noqa: PLR0913, PLR0917 -- Wartekanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        waypoint: Waypoint,
        time_bucket: int,
        max_time_buckets: int,
        queue: list[tuple[int, int, int]],
    ) -> None:
        """Füge Kante hinzu, um Zwischenstopp-Aufenthaltsdauer zu warten."""
        if not waypoint.aufenthaltsdauer:
            return

        wait_time_s = int(waypoint.aufenthaltsdauer.total_seconds())
        new_time_bucket = time_bucket + int(wait_time_s / (self.time_step_min * 60))

        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        # Kosten: Nur Wartezeit (kein SoC-Verlust)
        kosten = wait_time_s

        next_node = (seg_idx, current[1], new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="waypoint_wait",
                waypoint_koordinate=waypoint.koordinate,
                soc_pct=bucket_to_soc(current[1], self.soc_step_pct),
                zeitpunkt=bucket_to_zeit(new_time_bucket, self._base_time, self.time_step_min),
                segment_index=seg_idx,
                total_cost=COST_INF,
                parent=None,
            )
            queue.append(next_node)

        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(current, next_node, cost=kosten)
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current

    def _calc_soc_verbrauch_pct(
        self,
        energy_result: SegmentEnergyResult,
        vehicle_profile: VehicleProfile,
    ) -> float:
        """Berechne SoC-Verbrauch in Prozent für ein Segment."""
        batteriekapazitaet_kwh = vehicle_profile.batteriekapazitaet_kwh

        # Energiedifferenz (Verbrauch positiv, Rekuperation negativ)
        energie_kwh = energy_result.energiebedarf_kwh

        # In Prozent umrechnen
        return (energie_kwh / batteriekapazitaet_kwh) * MAX_SOC_PCT

    def _calc_ladezeit_s(
        self,
        delta_soc_pct: float,
        ladekurve: ChargingCurve,
        batteriekapazitaet_kwh: float,
    ) -> float:
        """Berechne Ladezeit in Sekunden für eine SoC-Differenz."""
        if delta_soc_pct <= 0:
            return 0.0

        # Mittlere Ladeleistung über den SoC-Bereich
        soc_start = bucket_to_soc(
            soc_to_bucket(
                bucket_to_soc(
                    soc_to_bucket(MAX_SOC_PCT - delta_soc_pct, self.soc_step_pct),
                    self.soc_step_pct,
                ),
                self.soc_step_pct,
            ),
            self.soc_step_pct,
        )
        soc_end = MAX_SOC_PCT

        # Vereinfachung: mittlere Ladeleistung aus Kurve
        mittlere_leistung_kw = self._mittlere_ladeleistung_kw(
            start_soc_pct=soc_start, end_soc_pct=soc_end, ladekurve=ladekurve
        )

        if mittlere_leistung_kw <= 0:
            return COST_INF  # Unendlich (nicht ladbar)

        # Energiebedarf in kWh
        energie_kwh = (delta_soc_pct / MAX_SOC_PCT) * batteriekapazitaet_kwh

        # Zeit in Sekunden
        return energie_kwh / mittlere_leistung_kw * 3600.0

    def _mittlere_ladeleistung_kw(
        self, start_soc_pct: float, end_soc_pct: float, ladekurve: ChargingCurve
    ) -> float:
        """Berechne mittlere Ladeleistung über einen SoC-Bereich."""
        if start_soc_pct >= end_soc_pct:
            return 0.0

        # Stichproben entlang der Kurve
        sample_points = 10
        total_power = 0.0

        for i in range(sample_points):
            soc = start_soc_pct + (end_soc_pct - start_soc_pct) * i / sample_points
            leistung = ladekurve.ladeleistung_bei_soc(soc)
            total_power += leistung

        return total_power / sample_points

    def _heuristik(
        self,
        u: tuple[int, int, int],
        v: tuple[int, int, int],
        segments: list[RouteSegment],
    ) -> float:
        """Admissible Heuristik: Zeit bis zum Ziel unter idealen Bedingungen.

        Args:
            u: Aktueller Knoten (segment_index, soc_bucket, time_bucket).
            v: Zielknoten (segment_index, soc_bucket, time_bucket).
            segments: Liste aller Route-Segmente.

        Returns:
            Geschätzte Zeit bis zum Ziel in Sekunden.
        """
        u_seg, _, _ = u

        # Distanz von u_seg bis zum Ende
        rest_distanz_m = sum(seg.laenge_m for seg in segments[u_seg:])

        # Idealgeschwindigkeit (Autobahn, 110 km/h)
        v_ideal_mps = DEFAULT_SPEED_KMH * 1000 / 3600

        # Zeitdauer
        rest_zeit_s = rest_distanz_m / v_ideal_mps

        return rest_zeit_s

    def _extract_charging_stops(  # noqa: PLR0913, PLR0917 -- Pfad-Extraktion braucht den vollen Ladeplan-Kontext
        self,
        path: list[tuple[int, int, int]],
        segments: list[RouteSegment],
        charging_stations: list[ChargingStation],
        vehicle_profile: VehicleProfile,
        ladekurve: ChargingCurve,
        constraints: OptimizationConstraints,
    ) -> list[ChargingStop]:
        """Extrahiere ChargingStop-Objekte aus dem Pfad."""
        ladehalte: list[ChargingStop] = []

        for i in range(1, len(path)):
            prev_node = path[i - 1]
            curr_node = path[i]

            # Prüfe, ob es sich um eine Ladekante handelt
            if curr_node[0] == prev_node[0]:  # Selbes Segment → Ladevorgang
                prev_soc_pct = bucket_to_soc(prev_node[1], self.soc_step_pct)
                curr_soc_pct = bucket_to_soc(curr_node[1], self.soc_step_pct)

                if curr_soc_pct > prev_soc_pct:
                    # Ladevorgang erkannt
                    segment = segments[curr_node[0]]

                    # Finde die Ladestation in diesem Segment
                    station = self._find_station_for_segment(segment, charging_stations)

                    if station is None:
                        continue  # Keine Station im Segment

                    delta_soc = curr_soc_pct - prev_soc_pct
                    ladezeit_s = self._calc_ladezeit_s(
                        delta_soc_pct=delta_soc,
                        ladekurve=ladekurve,
                        batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                    )

                    # Zeitpunkte
                    start_zeit = bucket_to_zeit(prev_node[2], self._base_time, self.time_step_min)
                    end_zeit = start_zeit + timedelta(seconds=ladezeit_s)

                    ladehalte.append(
                        ChargingStop(
                            station=station,
                            segment_index=curr_node[0],
                            ankunfts_soc_pct=prev_soc_pct,
                            ziel_soc_pct=curr_soc_pct,
                            geschaetzte_ladedauer_s=int(ladezeit_s),
                            ankunftszeit=start_zeit,
                            abfahrtszeit=end_zeit,
                        )
                    )

        return ladehalte

    def _find_station_for_segment(
        self, segment: RouteSegment, stations: list[ChargingStation]
    ) -> ChargingStation | None:
        """Finde eine Ladestation im Segment (nächste zum Segment-Mittelpunkt)."""
        if not stations:
            return None

        # Segment-Mittelpunkt
        mid_point = segment.geometrie[len(segment.geometrie) // 2]

        closest = min(
            stations,
            key=lambda s: self._haversine_distance(mid_point, s.coordinate),
            default=None,
        )

        return closest

    def _compute_waypoint_times(
        self,
        path: list[tuple[int, int, int]],
        waypoints: list[Waypoint],
        segments: list[RouteSegment],
        abfahrtszeit: datetime,
    ) -> dict[int, datetime]:
        """Berechne Mindestankunftszeit für Zwischenstopps."""
        min_ankunftszeit: dict[int, datetime] = {}

        for wp in waypoints:
            seg_idx = self._waypoint_to_segment(wp, segments)
            if seg_idx not in min_ankunftszeit:
                min_ankunftszeit[seg_idx] = abfahrtszeit

            if wp.aufenthaltsdauer:
                # Berechne Ankunftszeit + Aufenthaltsdauer
                current_time = min_ankunftszeit[seg_idx] + wp.aufenthaltsdauer
                min_ankunftszeit[seg_idx] = max(min_ankunftszeit[seg_idx], current_time)

        return min_ankunftszeit


def create_networkx_optimizer(
    soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
    time_step_min: int = TIME_STEP_MIN_DEFAULT,
) -> OptimizerInterface:
    """Factory-Funktion für den NetworkX-basierten Prototyp-Optimizer.

    Args:
        soc_step_pct: Schrittweite für SoC-Diskretisierung in Prozent.
        time_step_min: Schrittweite für Zeit-Diskretisierung in Minuten.

    Returns:
        NetworkXOptimizer-Instanz.
    """
    return NetworkXOptimizer(
        soc_step_pct=soc_step_pct,
        time_step_min=time_step_min,
    )


class ORToolsOptimizer(OptimizerInterface):
    """OR-Tools-basierter Optimizer (CP-SAT oder Routing Solver)."""

    def __init__(
        self,
        soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
        time_step_min: int = TIME_STEP_MIN_DEFAULT,
        use_cp_sat: bool = True,
    ) -> None:
        """Initialisiere den Optimierer.

        Args:
            soc_step_pct: Schrittweite für SoC-Diskretisierung in Prozent.
            time_step_min: Schrittweite für Zeit-Diskretisierung in Minuten.
            use_cp_sat: True = CP-SAT Solver, False = Routing Solver.
        """
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self.use_cp_sat = use_cp_sat

    def optimize(  # noqa: PLR0913, PLR0917 -- vollständiger Zustand des Optimierungsproblems, siehe docs/plans/07-optimization.md Abschnitt 4
        self,
        route: Route,
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """Optimiert Ladeplan mittels Constraint-Programmierung (CP-SAT) oder Routing-Solver.

        Hinweis: Dies ist ein Platzhalter für eine spätere Ausbaustufe.
        Die aktuelle Implementierung raise NotImplementedError.
        """
        raise NotImplementedError(
            "OR-Tools-Backend ist eine spätere Ausbaustufe, siehe docs/plans/07-optimization.md"
        )


def create_ortools_optimizer(
    soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
    time_step_min: int = TIME_STEP_MIN_DEFAULT,
    use_cp_sat: bool = True,
) -> OptimizerInterface:
    """Factory-Funktion für den OR-Tools-basierten Optimizer (spätere Version).

    Args:
        soc_step_pct: Schrittweite für SoC-Diskretisierung in Prozent.
        time_step_min: Schrittweite für Zeit-Diskretisierung in Minuten.
        use_cp_sat: True = CP-SAT Solver, False = Routing Solver.

    Returns:
        ORToolsOptimizer-Instanz.
    """
    return ORToolsOptimizer(
        soc_step_pct=soc_step_pct,
        time_step_min=time_step_min,
        use_cp_sat=use_cp_sat,
    )
