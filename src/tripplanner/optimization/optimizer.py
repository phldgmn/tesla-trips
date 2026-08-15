"""Kern-Logik für die Optimierung: NetworkX- und OR-Tools-Implementierungen.

NetworkXOptimizer: A*/Dijkstra auf diskretisiertem Zustandsgraph.
ORToolsOptimizer: Platzhalter für zukünftige CP-SAT Implementierung.
"""

from __future__ import annotations

import math
from collections import deque
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
        ladedauer_vorgaben: dict[str, int] | None = None,
        faehr_zeitfenster: dict[int, tuple[int, datetime, datetime]] | None = None,
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
            ladedauer_vorgaben: Optionale feste Ladedauern (Sekunden) je Stations-ID.
            faehr_zeitfenster: Optionale feste Fährfahrpläne je
                `segment_index_start -> (segment_index_end, abfahrt, ankunft)`.

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
            max_time_buckets=self._estimate_max_time_buckets(
                segments,
                total_energy_kwh=sum(e.energiebedarf_kwh for e in energy_results),
                vehicle_profile=vehicle_profile,
                constraints=constraints,
            ),
            ladedauer_vorgaben=ladedauer_vorgaben or {},
            ferry_pins=faehr_zeitfenster or {},
        )

        # A*-Suche zum Zielknoten
        try:
            # Zielknoten: beliebiger SoC ≥ ziel_soc_target im letzten Segment
            # Wir wählen den Knoten mit niedrigster Kosten
            target_candidates = [
                (seg_idx, soc_b, zeit_b)
                for seg_idx, soc_b, zeit_b in G.nodes()
                if seg_idx == len(segments) and soc_b >= ziel_soc_bucket
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
            G=G,
            path=path,
            segments=segments,
            charging_stations=charging_stations,
            constraints=constraints,
        )

        # Berechne Gesamtreisezeit: der Zielknoten hat immer segment_index ==
        # len(segments) (alle Segmente vollstaendig abgefahren). `zeitpunkt`
        # ist die tatsaechliche kumulierte Ankunftszeit (siehe _add_drive_edge
        # etc.) statt einer aus dem gerundeten Zeit-Bucket rekonstruierten
        # Naeherung, die bei feingranularen Segmenten Praezision verlieren
        # wuerde.
        last_node = path[-1]
        last_zeitpunkt = G.nodes[last_node]["zeitpunkt"]

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

    def _estimate_max_time_buckets(
        self,
        segments: list[RouteSegment],
        total_energy_kwh: float,
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
    ) -> int:
        """Schätze die maximale Anzahl an Zeit-Buckets für die gesamte Route.

        Das Zeitbudget MUSS die für notwendige Ladestopps benötigte Zeit mit
        einschließen - ein reiner Fahrzeit-Puffer (ohne Ladezeit) würde jede
        Route, die mehr als eine Handvoll Minuten Laden braucht, fälschlich
        als "nicht fahrbar" verwerfen, sobald der kumulierte Zeit-Bucket-Pfad
        durchs Laden über die reine Fahrzeit-Schätzung hinauswächst (siehe
        docs/plans/07-optimization.md).
        """
        total_distance_m = sum(seg.laenge_m for seg in segments)
        speed_mps = DEFAULT_SPEED_KMH * 1000 / 3600
        total_time_s = total_distance_m / speed_mps
        total_time_min = total_time_s / 60.0

        # Worst-Case-Anzahl Ladestopps: Gesamtenergiebedarf geteilt durch die
        # nutzbare Kapazität je Ladezyklus (konservativ: halbe Batteriekapazität
        # je Stopp, da praktisch selten von 0% auf 100% geladen wird).
        nutzbare_kapazitaet_je_stopp_kwh = max(vehicle_profile.batteriekapazitaet_kwh * 0.5, 1.0)
        geschaetzte_ladestopps = max(
            math.ceil(total_energy_kwh / nutzbare_kapazitaet_je_stopp_kwh) - 1, 0
        )
        ladezeit_puffer_min = geschaetzte_ladestopps * (constraints.max_ladezeit_s / 60.0)

        return int((total_time_min + ladezeit_puffer_min) / self.time_step_min) + 5

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
        ladedauer_vorgaben: dict[str, int],
        ferry_pins: dict[int, tuple[int, datetime, datetime]],
    ) -> None:
        """Generiere Knoten und Kanten für den Zustandsgraphen."""
        # Nutze BFS/DFS-artige Erweiterung: nur erreichbare Knoten erzeugen
        visited: set[tuple[int, int, int]] = set()
        # deque statt list: `pop(0)` auf einer Python-Liste ist O(n) (Shift
        # aller Folgeelemente), macht die BFS bei feingranularen Routen mit
        # zehntausenden Zustandsknoten quadratisch. `popleft()` auf `deque`
        # ist O(1).
        queue: deque[tuple[int, int, int]] = deque([start_node])
        G.nodes[start_node]["total_cost"] = 0.0
        G.nodes[start_node]["parent"] = None

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            seg_idx, soc_bucket, time_bucket = current

            # Prüfe, ob Ziel erreicht (alle Segmente abgefahren)
            if seg_idx == len(segments) and soc_bucket >= ziel_soc_bucket:
                continue  # Ziel erreicht, nicht weiter erweitern

            # 1. Fahrtkante: naechstes noch zu befahrendes Segment fahren -
            # ausser der Nutzer hat fuer diese Position einen festen
            # Fährfahrplan vorgegeben (`ferry_pins`), dann wird die gesamte
            # Fähr-Ueberfahrt in einem Sprung modelliert (siehe
            # `_add_ferry_edge`) statt sie Segment fuer Segment als normale
            # Fahrt zu behandeln. seg_idx zaehlt bereits abgefahrene
            # Segmente (0 = Start, len(segments) = Ziel erreicht);
            # segments[seg_idx] ist also das naechste Segment, das noch
            # gefahren werden muss.
            if seg_idx < len(segments):
                if seg_idx in ferry_pins:
                    self._add_ferry_edge(
                        G=G,
                        current=current,
                        pin=ferry_pins[seg_idx],
                        max_time_buckets=max_time_buckets,
                        queue=queue,
                    )
                else:
                    self._add_drive_edge(
                        G=G,
                        current=current,
                        drive_seg_idx=seg_idx,
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
                    ladedauer_vorgaben=ladedauer_vorgaben,
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
        drive_seg_idx: int,
        segments: list[RouteSegment],
        energy_results: list[SegmentEnergyResult],
        soc_bucket: int,
        time_bucket: int,
        max_time_buckets: int,
        constraints: OptimizationConstraints,
        vehicle_profile: VehicleProfile,
        ladekurve: ChargingCurve,
        queue: deque[tuple[int, int, int]],
    ) -> None:
        """Füge eine Fahrtkante für segments[drive_seg_idx] hinzu (naechstes Segment)."""
        next_seg = segments[drive_seg_idx]
        energy_result = energy_results[drive_seg_idx]

        # SoC-Verbrauch für dieses Segment
        verbrauch_pct = self._calc_soc_verbrauch_pct(
            energy_result=energy_result,
            vehicle_profile=vehicle_profile,
        )

        # Verbrauch wird vom KONTINUIERLICHEN SoC des Vorgaengerknotens
        # abgezogen (nicht vom gerundeten Bucket) und erst danach fuer den
        # neuen Knoten wieder gebuckt. Wuerde man stattdessen bei jeder
        # Kante `round(verbrauch_pct / soc_step_pct)` vom Bucket abziehen,
        # ginge bei feingranularen Routen (z. B. ein Segment pro GraphHopper-
        # Polyline-Punktpaar, oft <200 m) der Grossteil des Verbrauchs pro
        # Kante unter der halben Bucket-Schrittweite (Default 1%) verloren -
        # bei tausenden Segmenten summiert sich das zu praktisch null
        # Gesamtverbrauch und der Optimierer haelt faelschlich gar kein
        # Laden fuer noetig (siehe docs/plans/07-optimization.md).
        current_soc_pct = G.nodes[current]["soc_pct"]
        new_soc_pct = current_soc_pct - verbrauch_pct

        # Reichweite reicht nicht (SoC unter 0% oder unter Min-SoC) - eine
        # unzulaessige Kante wie jede andere Unterschreitung von min_soc_pct.
        if new_soc_pct < 0.0 or new_soc_pct < constraints.min_soc_pct:
            return  # Unzulässig
        new_soc_bucket = soc_to_bucket(new_soc_pct, self.soc_step_pct)

        # Fahrzeit berechnen. Der neue Zeit-Bucket wird aus der TATSAECHLICHEN
        # kumulierten Zeit des Vorgaengerknotens abgeleitet (nicht inkrementell
        # aus dem bereits gerundeten Bucket) -- sonst wuerde bei feingranularen
        # Segmenten (z. B. Fake-Provider-Segmente von wenigen km, jeweils
        # deutlich kuerzer als ein Zeit-Bucket) jede einzelne Fahrtkante auf 0
        # Minuten abgerundet und die gesamte Fahrzeit ginge verloren.
        fahrzeit_s = next_seg.laenge_m / (DEFAULT_SPEED_KMH * 1000 / 3600)
        neuer_zeitpunkt = G.nodes[current]["zeitpunkt"] + timedelta(seconds=fahrzeit_s)
        new_time_bucket = zeit_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)

        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        # Kosten berechnen
        kosten = fahrzeit_s  # Nur Fahrzeit, keine Ladezeit

        # Nach dem Durchfahren von segments[drive_seg_idx] ist ein weiteres
        # Segment abgefahren -> Landeknoten zaehlt eins mehr.
        next_seg_idx = drive_seg_idx + 1
        next_node = (next_seg_idx, new_soc_bucket, new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="drive",
                soc_pct=new_soc_pct,
                zeitpunkt=neuer_zeitpunkt,
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
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt

    def _add_ferry_edge(
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        pin: tuple[int, datetime, datetime],
        max_time_buckets: int,
        queue: deque[tuple[int, int, int]],
    ) -> None:
        """Fügt eine Kante für eine terminierte Fährüberfahrt hinzu.

        Modelliert eine vom Nutzer terminierte Fährüberfahrt (fixe Abfahrts-/
        Ankunftszeit, `optimize()`-Parameter `faehr_zeitfenster`) in EINEM Sprung
        von `current` zum Segment nach der Fähre - anstelle der sonst pro Segment
        erzeugten `_add_drive_edge`-Kanten für die dazwischenliegenden
        Fähr-Segmente (siehe `_generate_graph`). Kein SoC-Verbrauch (Motor aus
        während der Überfahrt) - nur Wartezeit bis zur Abfahrt plus die
        Überfahrtsdauer als Kosten, analog zu `_add_waypoint_wait_edge`s
        "kein SoC-Verlust"-Ansatz. `pin` ist
        `(segment_index_end, abfahrt, ankunft)`, wobei `segment_index_end` das
        erste Segment NACH der Fähre ist (siehe
        `tripplanner.routing.models.FaehrSegment.segment_index_end`).
        """
        segment_index_end, abfahrt, ankunft = pin
        current_zeitpunkt = G.nodes[current]["zeitpunkt"]

        if current_zeitpunkt > abfahrt:
            return  # Fähre zu diesem Zeitpunkt bereits abgefahren - Pfad unzulässig

        neuer_zeitpunkt = ankunft
        new_time_bucket = zeit_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)
        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        wartezeit_s = (abfahrt - current_zeitpunkt).total_seconds()
        ueberfahrt_s = (ankunft - abfahrt).total_seconds()
        kosten = wartezeit_s + ueberfahrt_s

        # SoC-Bucket unveraendert (kein Verbrauch waehrend der Ueberfahrt)
        next_node = (segment_index_end, current[1], new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="ferry",
                soc_pct=G.nodes[current]["soc_pct"],
                zeitpunkt=neuer_zeitpunkt,
                segment_index=segment_index_end,
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
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt

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
        queue: deque[tuple[int, int, int]],
        ladedauer_vorgaben: dict[str, int],
    ) -> None:
        """Füge Ladekanten zu allen Stationen in diesem Segment hinzu.

        Für Stationen mit einer vom Nutzer vorgegebenen festen Ladedauer
        (`ladedauer_vorgaben`, Schlüssel = `station_id`) wird GENAU EINE Kante
        mit dieser Dauer erzeugt (resultierender SoC per Bisektion über die
        Ladekurve ermittelt, siehe `_soc_nach_fester_ladezeit`) statt der
        sonstigen SoC-Ziel-Iteration - die Vorgabe ist eine explizite
        Nutzer-Entscheidung und daher auch nicht durch
        `constraints.max_ladezeit_s` begrenzt (analog zur ungedeckelten
        Wartezeit in `_add_waypoint_wait_edge`).
        """
        current_soc_pct = G.nodes[current]["soc_pct"]

        for station in stations:
            vorgabe_s = ladedauer_vorgaben.get(station.station_id)
            if vorgabe_s is not None:
                ziel_soc = self._soc_nach_fester_ladezeit(
                    start_soc_pct=current_soc_pct,
                    ladezeit_s=float(vorgabe_s),
                    ladekurve=ladekurve,
                    batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                )
                self._fuege_ladekante_hinzu(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    station=station,
                    ziel_soc_pct=ziel_soc,
                    ladezeit_s=float(vorgabe_s),
                    max_time_buckets=max_time_buckets,
                    queue=queue,
                )
                continue

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

                self._fuege_ladekante_hinzu(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    station=station,
                    ziel_soc_pct=Ziel_soc,
                    ladezeit_s=ladezeit_s,
                    max_time_buckets=max_time_buckets,
                    queue=queue,
                )

    def _fuege_ladekante_hinzu(  # noqa: PLR0913, PLR0917 -- Ladekanten-Buchhaltung braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        station: ChargingStation,
        ziel_soc_pct: float,
        ladezeit_s: float,
        max_time_buckets: int,
        queue: deque[tuple[int, int, int]],
    ) -> None:
        """Fügt eine Ladekante hinzu (Knoten-/Kanten-/Kosten-Buchhaltung).

        Erzeugt (falls günstiger als ein bestehender Pfad) eine Ladekante von
        `current` zu einem Knoten mit `ziel_soc_pct` nach `ladezeit_s` Sekunden
        Ladezeit an `station` - gemeinsame Buchhaltung für sowohl die
        automatische SoC-Ziel-Iteration als auch eine vom Nutzer vorgegebene
        feste Ladedauer (siehe `_add_charging_edges`).
        """
        new_soc_bucket = soc_to_bucket(ziel_soc_pct, self.soc_step_pct)
        neuer_zeitpunkt = G.nodes[current]["zeitpunkt"] + timedelta(seconds=ladezeit_s)
        new_time_bucket = zeit_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)

        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        # Kosten: Nur Ladezeit zählt (Fahrzeit war schon bezahlt)
        kosten = ladezeit_s
        next_node = (seg_idx, new_soc_bucket, new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="charge",
                station_id=station.station_id,
                soc_pct=ziel_soc_pct,
                zeitpunkt=neuer_zeitpunkt,
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
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt

    def _soc_nach_fester_ladezeit(
        self,
        start_soc_pct: float,
        ladezeit_s: float,
        ladekurve: ChargingCurve,
        batteriekapazitaet_kwh: float,
    ) -> float:
        """Ermittelt den SoC nach einer FESTEN Ladedauer.

        Inverse zu `_calc_ladezeit_s` per Bisektion: `_calc_ladezeit_s` ist
        monoton steigend in `delta_soc_pct`, aber nicht analytisch invertierbar
        (basiert auf `ladekurve.ladeleistung_bei_soc`-Stichproben) - daher
        numerische Nullstellensuche statt einer geschlossenen Formel.
        """
        max_delta = MAX_SOC_PCT - start_soc_pct
        if ladezeit_s <= 0.0 or max_delta <= 0.0:
            return start_soc_pct

        ladezeit_bei_max = self._calc_ladezeit_s(
            delta_soc_pct=max_delta,
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=batteriekapazitaet_kwh,
        )
        if ladezeit_bei_max <= ladezeit_s:
            return MAX_SOC_PCT  # Batterie ist vor Ablauf der Ladedauer voll

        lo, hi = 0.0, max_delta
        for _ in range(40):  # 40 Iterationen: Präzision weit unter 1e-9 %-Punkte
            mid = (lo + hi) / 2.0
            dauer = self._calc_ladezeit_s(
                delta_soc_pct=mid,
                ladekurve=ladekurve,
                batteriekapazitaet_kwh=batteriekapazitaet_kwh,
            )
            if dauer < ladezeit_s:
                lo = mid
            else:
                hi = mid
        return start_soc_pct + (lo + hi) / 2.0

    def _add_waypoint_wait_edge(  # noqa: PLR0913, PLR0917 -- Wartekanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        waypoint: Waypoint,
        time_bucket: int,
        max_time_buckets: int,
        queue: deque[tuple[int, int, int]],
    ) -> None:
        """Füge Kante hinzu, um Zwischenstopp-Aufenthaltsdauer zu warten."""
        if not waypoint.aufenthaltsdauer:
            return

        wait_time_s = int(waypoint.aufenthaltsdauer.total_seconds())
        neuer_zeitpunkt = G.nodes[current]["zeitpunkt"] + timedelta(seconds=wait_time_s)
        new_time_bucket = zeit_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)

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
                soc_pct=G.nodes[current]["soc_pct"],
                zeitpunkt=neuer_zeitpunkt,
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
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt

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

    def _extract_charging_stops(
        self,
        G: DiGraph,
        path: list[tuple[int, int, int]],
        segments: list[RouteSegment],
        charging_stations: list[ChargingStation],
        constraints: OptimizationConstraints,
    ) -> list[ChargingStop]:
        """Extrahiere ChargingStop-Objekte aus dem Pfad."""
        ladehalte: list[ChargingStop] = []
        stations_by_id = {s.station_id: s for s in charging_stations}

        for i in range(1, len(path)):
            prev_node = path[i - 1]
            curr_node = path[i]

            # Prüfe, ob es sich um eine Ladekante handelt
            if curr_node[0] == prev_node[0]:  # Selbes Segment → Ladevorgang
                prev_soc_pct = G.nodes[prev_node]["soc_pct"]
                curr_soc_pct = G.nodes[curr_node]["soc_pct"]

                if curr_soc_pct > prev_soc_pct:
                    # Ladevorgang erkannt. Station ueber die beim Erzeugen der
                    # Kante (`_fuege_ladekante_hinzu`) am Knoten hinterlegte
                    # `station_id` auflösen - NICHT ueber eine erneute
                    # geografische Naechste-Station-Suche
                    # (`segment.geometrie`-Mittelpunkt): mehrere Ladekanten
                    # koennen am selben Segment fuer VERSCHIEDENE Stationen
                    # existieren (z. B. wenn zwei Stationen auf denselben
                    # naechstgelegenen Segment-Index abgebildet werden, siehe
                    # `_map_stations_to_segments`) - eine geografische
                    # Neu-Suche wuerde dann unabhaengig von der TATSAECHLICH
                    # gewaehlten Kante immer dieselbe (naechstgelegene)
                    # Station zurueckgeben und so z. B. eine gezielt an einer
                    # ANDEREN Station vorgegebene feste Ladedauer
                    # (`ladedauer_vorgaben`) der falschen Station zuschreiben.
                    station_id = G.nodes[curr_node].get("station_id")
                    station = stations_by_id.get(station_id) if station_id else None

                    if station is None:
                        continue  # Keine Station im Segment

                    # Zeitpunkte direkt aus den Knoten lesen statt die Ladezeit
                    # erneut ueber die Ladekurve zu berechnen: `zeitpunkt` ist
                    # exakt der Wert, der beim Erzeugen dieser Kante in
                    # `_fuege_ladekante_hinzu` gesetzt wurde - fuer eine vom
                    # Nutzer per `ladedauer_vorgaben` fest vorgegebene Ladedauer
                    # (siehe `_add_charging_edges`) waere eine Neuberechnung ueber
                    # `_calc_ladezeit_s(delta_soc, ...)` NICHT die vorgegebene
                    # Dauer, sondern die (durch Bisektion nur angenaeherte)
                    # automatische Herleitung - und selbst im Normalfall vermeidet
                    # dies eine unnoetige zweite, rundungsbehaftete Berechnung.
                    start_zeit = G.nodes[prev_node]["zeitpunkt"]
                    end_zeit = G.nodes[curr_node]["zeitpunkt"]
                    ladezeit_s = (end_zeit - start_zeit).total_seconds()

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
        ladedauer_vorgaben: dict[str, int] | None = None,
        faehr_zeitfenster: dict[int, tuple[int, datetime, datetime]] | None = None,
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
