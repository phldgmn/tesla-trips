"""Kern-Logik für die Optimierung: NetworkX- und OR-Tools-Implementierungen.

NetworkXOptimizer: A*/Dijkstra auf diskretisiertem Zustandsgraph.
ORToolsOptimizer: Platzhalter für zukünftige CP-SAT Implementierung.
"""

from __future__ import annotations

import bisect
import heapq
import itertools
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
    soc_to_bucket,
    time_to_bucket,
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

MAX_SOC_PCT: float = 100.0
"""Maximaler SoC in Prozent."""

DETOUR_ROUTENFAKTOR: float = 1.6
"""Multiplikator, um aus der Luftlinien-Entfernung Station<->Route eine
realistische Straßendistanz zu schätzen (echte Straßen sind selten
geradlinig - kalibriert an den 1.2x-2x, die `_step_route_charging_detours`
live gegen GraphHopper für Abstecher zu Ladestationen beobachtet, siehe
`_find_bracket_points`-Docstring in `trip_input/api.py`)."""

DETOUR_GESCHWINDIGKEIT_KMH: float = 70.0
"""Angenommene Durchschnittsgeschwindigkeit auf dem Abstecher zur Ladestation
(oft Landstraße/Zubringer, nicht die Haupttrasse - konservativ niedriger als
ein Autobahn-Tempolimit)."""


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
        self._cum_time_s: list[float]
        self._avg_verbrauch_kwh_pro_m: float = 0.0
        self._push_seq: itertools.count[int]

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

        A*-Suche mit Heuristik = verbleibende Fahrzeit unter den tatsaechlichen, je
        Segment ermittelten Geschwindigkeiten (siehe `_heuristik`,
        `SegmentEnergyResult.fahrzeit_s`).

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
        start_zeit_bucket = time_to_bucket(abfahrtszeit, abfahrtszeit, self.time_step_min)

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

        # Kumulative Energie-/Fahrzeit-Praefixsummen ueber die Roh-Segmente,
        # aus den TATSAECHLICHEN, je Segment via `SegmentEnergyResult.fahrzeit_s`
        # ermittelten Geschwindigkeiten (Tempolimit/Baustellen-Override, siehe
        # `energy.calculate_segment_consumption`) - NICHT aus einer einzigen
        # Durchschnittsgeschwindigkeit ueber die gesamte Reise. Ermoeglichen
        # O(1)-Aggregation einer ganzen Teilstrecke zwischen zwei
        # Entscheidungspunkten (`_add_drive_edge`) sowie eine O(1)-Restfahrzeit
        # fuer die A*-Heuristik (`_heuristik`) statt O(n) Neuberechnung pro
        # Kante/Heuristik-Aufruf - kritisch bei feingranularen Routen mit
        # tausenden Roh-Segmenten (ein Segment pro GraphHopper-Polyline-
        # Punktpaar), siehe docs/plans/07-optimization.md, Risiko
        # "Skalierbarkeit der NetworkX-Lösung". Nur mit dieser echten
        # Zeitbasis stimmen Ankunfts-/Abfahrtszeiten an Ladehalten
        # (`ChargingStop.ankunftszeit`/`abfahrtszeit`) sowie `gesamtreisezeit_s`
        # mit dem tatsaechlichen, je Segment unterschiedlichen Tempo ueberein -
        # eine pauschale Durchschnittsgeschwindigkeit fuehrt sonst dazu, dass
        # die Ankunft an einem Ladehalt (bzw. dessen `segment_index`) und die
        # Fahrzeit bis dorthin auseinanderlaufen (sichtbar u. a. als falscher
        # SoC/Position fuer mehrere Frames direkt nach einem Ladehalt in
        # `simulate_trip`).
        cum_energy_kwh = [0.0] * (len(segments) + 1)
        cum_time_s = [0.0] * (len(segments) + 1)
        for i, (_, er) in enumerate(zip(segments, energy_results, strict=True)):
            cum_energy_kwh[i + 1] = cum_energy_kwh[i] + er.energiebedarf_kwh
            cum_time_s[i + 1] = cum_time_s[i] + er.fahrzeit_s
        self._cum_time_s = cum_time_s

        # Durchschnittlicher Verbrauch (kWh/m) ueber die GESAMTE Route - dient
        # als Naeherung fuer den Energiebedarf eines Abstechers abseits der
        # Route zu einer Ladestation (siehe `_detour_kosten`). Exakte
        # Segment-fuer-Segment-Energie fuer eine Strecke, die GraphHopper nie
        # berechnet hat, existiert nicht - der Routendurchschnitt ist die
        # naheliegende Naeherung (Topografie/Tempolimit der Route selbst sind
        # ohnehin die beste verfuegbare Schaetzung fuer eine nahegelegene
        # Nebenstrecke).
        gesamtlaenge_m = route.gesamtlaenge_m or sum(seg.laenge_m for seg in segments)
        self._avg_verbrauch_kwh_pro_m = (
            cum_energy_kwh[-1] / gesamtlaenge_m if gesamtlaenge_m > 0.0 else 0.0
        )

        # Generiere Knoten und Kanten
        self._generate_graph(
            G=G,
            segments=segments,
            cum_time_s=cum_time_s,
            cum_energy_kwh=cum_energy_kwh,
            waypoint_map=waypoint_map,
            station_segments=station_segments,
            vehicle_profile=vehicle_profile,
            ladekurve=ladekurve,
            constraints=constraints,
            start_node=start_node,
            ziel_soc_bucket=ziel_soc_bucket,
            ziel_soc_target=ziel_soc_target,
            max_time_buckets=self._estimate_max_time_buckets(
                total_time_s=cum_time_s[-1],
                total_energy_kwh=cum_energy_kwh[-1],
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
    ) -> dict[int, list[tuple[ChargingStation, float]]]:
        """Mappe Ladestationen auf nahegelegene Segmente.

        Jeder Eintrag traegt zusaetzlich die Luftlinien-Entfernung (Meter)
        zwischen Station und dem naechstgelegenen Routenpunkt - Basis fuer die
        Abstecher-Kosten in `_detour_kosten` (siehe dort). Ohne diese Distanz
        wuerde die Optimierung eine Station, die zwar dem naechsten
        Segment-Index zugeordnet ist aber viele Kilometer abseits der Route
        liegt, faelschlich als kostenlos erreichbar behandeln.
        """
        station_map: dict[int, list[tuple[ChargingStation, float]]] = {}

        for station in stations:
            best_seg_idx, offroute_distance_m = self._station_to_segment(station, segments)
            station_map.setdefault(best_seg_idx, []).append((station, offroute_distance_m))

        return station_map

    def _station_to_segment(
        self, station: ChargingStation, segments: list[RouteSegment]
    ) -> tuple[int, float]:
        """Ermittle das naechstgelegene Segment und den Abstand dorthin (Meter)."""
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

        return closest_seg_idx, min_dist

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
        total_time_s: float,
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

        Args:
            total_time_s: Reale, aus `SegmentEnergyResult.fahrzeit_s` aufsummierte
                Gesamtfahrzeit der Route (`cum_time_s[-1]` in `optimize()`) - kein
                Distanz/Durchschnittsgeschwindigkeit-Schaetzwert, sonst koennte das
                Budget bei tatsaechlich langsameren Streckenabschnitten
                unterschaetzt werden und fahrbare, nur langsamere Routen faelschlich
                als "nicht fahrbar" verwerfen.
            total_energy_kwh: Gesamtenergiebedarf der Route in kWh.
            vehicle_profile: Physikalisches Fahrzeugprofil.
            constraints: Optimierungs-Constraints (u. a. `max_ladezeit_s`).
        """
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

    def _schedule(
        self,
        heap: list[tuple[float, int, tuple[int, int, int]]],
        node: tuple[int, int, int],
        total_cost: float,
    ) -> None:
        """Plant `node` mit `total_cost` auf dem Dijkstra-Min-Heap ein.

        Ein Knoten kann mehrfach eingeplant werden (einmal je Verbesserung
        seiner Gesamtkosten) - veraltete, teurere Eintraege werden beim Pop in
        `_generate_graph` per `visited`-Check ignoriert ("lazy deletion").
        `self._push_seq` (ein `itertools.count()`, pro `_generate_graph`-Lauf
        neu initialisiert) dient als Tie-Breaker, damit `heapq` bei gleichen
        Kosten NIEMALS die Knoten-Tupel selbst vergleicht.
        """
        heapq.heappush(heap, (total_cost, next(self._push_seq), node))

    def _generate_graph(  # noqa: PLR0913, PLR0917 -- Graph-Konstruktion braucht den vollen Kontext (Route, Energie, Laden, Zwischenstopps, Constraints)
        self,
        G: DiGraph,
        segments: list[RouteSegment],
        cum_time_s: list[float],
        cum_energy_kwh: list[float],
        waypoint_map: dict[int, list[Waypoint]],
        station_segments: dict[int, list[tuple[ChargingStation, float]]],
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
        # Dijkstra-artige Erweiterung mit Min-Heap statt FIFO-BFS: nur
        # erreichbare Knoten erzeugen. Eine reine FIFO-Reihenfolge (frueher:
        # `deque`/`popleft`) verletzt die Dijkstra-Invariante, dass ein Knoten
        # erst dann als "final" markiert (und seine ausgehenden Kanten erzeugt)
        # werden darf, wenn er mit MINIMALEN Gesamtkosten aus der Warteschlange
        # entnommen wird. Bei FIFO kann ein Knoten mit einem zuerst entdeckten,
        # aber teureren/pessimistischeren SoC verarbeitet werden, WAEHREND ein
        # spaeterer, guenstigerer Pfad zu demselben Knoten `total_cost`/`soc_pct`
        # zwar noch aktualisiert (siehe Kommentare in `_add_drive_edge` etc.),
        # dessen ausgehende Kanten aber NIE (er ist ja schon "visited") neu
        # erzeugt werden. Ergebnis: nachgelagerte Kanten (z. B. "Ladestation
        # ueberspringen, weiterfahren") werden mit einem zu niedrigen SoC
        # geplant und faelschlich als unzulaessig verworfen - das erzwingt
        # unnoetige Zwischenladestopps, obwohl der tatsaechlich guenstigste
        # (spaeter gefundene) Zustand ausgereicht haette (siehe Nutzer-Report:
        # unnoetiger 70%->80%-Ladestopp in Kamen vor Holdorf-Ankunft mit 24%).
        # Ein Min-Heap mit "lazy deletion" (veraltete Eintraege werden beim Pop
        # anhand von `visited` uebersprungen) behebt das bei nichtnegativen
        # Kantengewichten (Fahrzeit/Ladezeit/Wartezeit sind stets >= 0)
        # korrekt: der erste Pop eines Knotens liefert garantiert dessen
        # minimale Gesamtkosten.
        visited: set[tuple[int, int, int]] = set()
        self._push_seq = itertools.count()
        heap: list[tuple[float, int, tuple[int, int, int]]] = []
        self._schedule(heap, start_node, 0.0)
        G.nodes[start_node]["total_cost"] = 0.0
        G.nodes[start_node]["parent"] = None

        # Sortierte Liste aller Entscheidungspunkte (Ladestation, Zwischenstopp
        # oder Fähr-Einstieg). Zwischen zwei Entscheidungspunkten gibt es im
        # Zustandsgraphen keine Verzweigung - eine Fahrtkante darf die
        # dazwischenliegenden Roh-Segmente daher in EINEM Sprung überspringen
        # (siehe `_add_drive_edge`) statt pro Roh-Segment einen eigenen
        # Zustandsknoten zu erzeugen. Das reduziert die Knotenzahl von
        # O(Roh-Segmente x SoC-Buckets x Zeit-Buckets) auf
        # O(Entscheidungspunkte x SoC-Buckets x Zeit-Buckets) - bei
        # feingranularen Routen (tausende Roh-Segmente, wenige Dutzend
        # Ladestationen) der entscheidende Faktor (docs/plans/07-optimization.md).
        checkpoints: list[int] = sorted(set(waypoint_map) | set(station_segments) | set(ferry_pins))

        while heap:
            _, _, current = heapq.heappop(heap)
            if current in visited:
                continue  # Veralteter Heap-Eintrag (Kosten wurden inzwischen unterboten)
            visited.add(current)

            seg_idx, soc_bucket, time_bucket = current

            # Prüfe, ob Ziel erreicht (alle Segmente abgefahren)
            if seg_idx == len(segments) and soc_bucket >= ziel_soc_bucket:
                continue  # Ziel erreicht, nicht weiter erweitern

            # 1. Fahrtkante: bis zum naechsten Entscheidungspunkt (oder bis
            # zum Ziel, falls keiner mehr folgt) in einem Sprung fahren -
            # ausser der Nutzer hat fuer diese Position einen festen
            # Fährfahrplan vorgegeben (`ferry_pins`), dann wird die gesamte
            # Fähr-Ueberfahrt separat modelliert (siehe `_add_ferry_edge`).
            # seg_idx zaehlt bereits abgefahrene Segmente (0 = Start,
            # len(segments) = Ziel erreicht).
            if seg_idx < len(segments):
                if seg_idx in ferry_pins:
                    self._add_ferry_edge(
                        G=G,
                        current=current,
                        pin=ferry_pins[seg_idx],
                        max_time_buckets=max_time_buckets,
                        heap=heap,
                    )
                else:
                    idx = bisect.bisect_right(checkpoints, seg_idx)
                    target_seg_idx = checkpoints[idx] if idx < len(checkpoints) else len(segments)
                    self._add_drive_edge(
                        G=G,
                        current=current,
                        seg_idx=seg_idx,
                        target_seg_idx=target_seg_idx,
                        cum_time_s=cum_time_s,
                        cum_energy_kwh=cum_energy_kwh,
                        max_time_buckets=max_time_buckets,
                        constraints=constraints,
                        vehicle_profile=vehicle_profile,
                        heap=heap,
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
                    heap=heap,
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
                            heap=heap,
                        )

    def _add_drive_edge(  # noqa: PLR0913, PLR0917 -- Fahrtkanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        target_seg_idx: int,
        cum_time_s: list[float],
        cum_energy_kwh: list[float],
        max_time_buckets: int,
        constraints: OptimizationConstraints,
        vehicle_profile: VehicleProfile,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Füge eine aggregierte Fahrtkante von `seg_idx` bis `target_seg_idx` hinzu.

        `target_seg_idx` ist der naechste Entscheidungspunkt (Ladestation,
        Zwischenstopp oder Fähr-Einstieg) nach `seg_idx`, oder `len(segments)`
        falls keiner mehr folgt (siehe `_generate_graph`). Zwischen zwei
        Entscheidungspunkten verzweigt der Zustandsgraph nicht - eine einzelne
        Fahrtkante über ALLE dazwischenliegenden Roh-Segmente liefert exakt
        dasselbe Ergebnis wie eine Kante pro Roh-Segment (Energie/Zeit sind
        linear additiv, siehe `cum_time_s`/`cum_energy_kwh` in `optimize()`),
        vermeidet aber die sonst bei feingranularen Routen (ein Segment pro
        GraphHopper-Polyline-Punktpaar) explodierende Anzahl an
        Zustandsknoten (docs/plans/07-optimization.md, Risiko "Skalierbarkeit
        der NetworkX-Lösung").
        """
        # SoC-Verbrauch für die gesamte Teilstrecke (aggregierte Energie über
        # cum_energy_kwh, siehe optimize()).
        energie_kwh = cum_energy_kwh[target_seg_idx] - cum_energy_kwh[seg_idx]
        verbrauch_pct = self._calc_soc_verbrauch_pct(
            energie_kwh=energie_kwh,
            vehicle_profile=vehicle_profile,
        )

        # Verbrauch wird vom KONTINUIERLICHEN SoC des Vorgaengerknotens
        # abgezogen (nicht vom gerundeten Bucket) und erst danach fuer den
        # neuen Knoten wieder gebuckt - siehe docs/plans/07-optimization.md
        # (Regressionstest: SoC-Quantisierung bei feingranularen Segmenten).
        current_soc_pct = G.nodes[current]["soc_pct"]
        new_soc_pct = current_soc_pct - verbrauch_pct

        # Reichweite reicht nicht (SoC unter 0% oder unter Min-SoC) - eine
        # unzulaessige Kante wie jede andere Unterschreitung von min_soc_pct.
        if new_soc_pct < 0.0 or new_soc_pct < constraints.min_soc_pct:
            return  # Unzulässig
        new_soc_bucket = soc_to_bucket(new_soc_pct, self.soc_step_pct)

        # Fahrzeit fuer die gesamte Teilstrecke: aggregierte, aus der
        # TATSAECHLICHEN je-Segment-Geschwindigkeit ermittelte Fahrzeit
        # (`cum_time_s`, siehe `optimize()`/`SegmentEnergyResult.fahrzeit_s`) -
        # NICHT aus einer pauschalen Durchschnittsgeschwindigkeit. Der neue
        # Zeit-Bucket wird aus der TATSAECHLICHEN kumulierten Zeit des
        # Vorgaengerknotens abgeleitet (nicht inkrementell aus dem bereits
        # gerundeten Bucket), sonst ginge bei kurzen Teilstrecken Fahrzeit
        # durch Rundung verloren.
        fahrzeit_s = cum_time_s[target_seg_idx] - cum_time_s[seg_idx]
        neuer_zeitpunkt = G.nodes[current]["zeitpunkt"] + timedelta(seconds=fahrzeit_s)
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)

        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        # Kosten berechnen
        kosten = fahrzeit_s  # Nur Fahrzeit, keine Ladezeit

        next_node = (target_seg_idx, new_soc_bucket, new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="drive",
                soc_pct=new_soc_pct,
                zeitpunkt=neuer_zeitpunkt,
                segment_index=target_seg_idx,
                total_cost=COST_INF,
                parent=None,
            )

        # Kante hinzufügen mit Kosten
        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(current, next_node, cost=kosten)
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt
            # `soc_pct` MUSS bei jeder guenstigeren Kante aktualisiert werden
            # (nicht nur beim allerersten Anlegen des Knotens) - sonst kann
            # ein Knoten-Schluessel `(segment_index, soc_bucket, time_bucket)`,
            # der zuerst durch eine ANDERE (spaeter verworfene) Kante angelegt
            # wurde, einen veralteten SoC-Wert behalten, obwohl die tatsaechlich
            # gewaehlte Kante einen anderen kontinuierlichen SoC erreicht (siehe
            # `TestLadehaltUeberlebtKnotenKollision` in test_optimization.py).
            G.nodes[next_node]["soc_pct"] = new_soc_pct
            self._schedule(heap, next_node, new_total_cost)

    def _add_ferry_edge(
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        pin: tuple[int, datetime, datetime],
        max_time_buckets: int,
        heap: list[tuple[float, int, tuple[int, int, int]]],
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
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)
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

        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(current, next_node, cost=kosten)
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt
            G.nodes[next_node]["soc_pct"] = G.nodes[current]["soc_pct"]
            self._schedule(heap, next_node, new_total_cost)

    def _add_charging_edges(  # noqa: PLR0913, PLR0917 -- Ladekanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        stations: list[tuple[ChargingStation, float]],
        segments: list[RouteSegment],
        vehicle_profile: VehicleProfile,
        ladekurve: ChargingCurve,
        soc_bucket: int,
        time_bucket: int,
        max_time_buckets: int,
        constraints: OptimizationConstraints,
        heap: list[tuple[float, int, tuple[int, int, int]]],
        ladedauer_vorgaben: dict[str, int],
    ) -> None:
        """Füge Ladekanten zu allen Stationen in diesem Segment hinzu.

        `stations` enthält je Station auch deren Luftlinien-Abstand (Meter)
        zum naechstgelegenen Routenpunkt (siehe `_map_stations_to_segments`).
        Stationen, die nicht direkt AUF der Route liegen (der Regelfall - der
        Suchradius `search_radius_km` in `trip_input/api.py` erlaubt bewusst
        Kandidaten mehrere Kilometer abseits der Route), erfordern einen
        Hin- und Rückweg-Abstecher. Dessen Zeit-/Energiekosten werden über
        `_detour_kosten` geschätzt und der Ladekante aufgeschlagen - ohne
        das würde die Optimierung eine weit abseits liegende, aber
        geografisch zufällig dem "billigsten" Segment zugeordnete Station als
        KOSTENLOS erreichbar behandeln und z. B. einen 90-minütigen Abstecher
        nur fürs Laden waehlen, obwohl eine naehere Station denselben SoC-
        Bedarf gedeckt haette (siehe Nutzer-Report: Jönköping -> Ödeshög und
        zurück statt direkt in Jönköping/Mariestad zu laden).

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

        for station, offroute_distance_m in stations:
            detour_zeit_s, detour_soc_pct = self._detour_kosten(
                offroute_distance_m=offroute_distance_m,
                vehicle_profile=vehicle_profile,
            )
            ankunft_soc_pct = current_soc_pct - detour_soc_pct
            if ankunft_soc_pct < 0.0:
                continue  # Reichweite reicht nicht einmal bis zur Station

            vorgabe_s = ladedauer_vorgaben.get(station.station_id)
            if vorgabe_s is not None:
                ziel_soc = self._soc_nach_fester_ladezeit(
                    start_soc_pct=ankunft_soc_pct,
                    ladezeit_s=float(vorgabe_s),
                    ladekurve=ladekurve,
                    batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                )
                self._fuege_ladekante_hinzu(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    station=station,
                    ankunfts_soc_pct=ankunft_soc_pct,
                    ziel_soc_pct=ziel_soc,
                    ladezeit_s=float(vorgabe_s),
                    detour_zeit_s_je_richtung=detour_zeit_s,
                    detour_soc_pct_je_richtung=detour_soc_pct,
                    max_time_buckets=max_time_buckets,
                    heap=heap,
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
                if Ziel_soc <= ankunft_soc_pct:
                    continue  # Bereits höher als Ziel

                # Ladezeit berechnen (echtes Start-/End-SoC-Fenster, siehe
                # `_calc_ladezeit_s`)
                ladezeit_s = self._calc_ladezeit_s(
                    start_soc_pct=ankunft_soc_pct,
                    end_soc_pct=Ziel_soc,
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
                    ankunfts_soc_pct=ankunft_soc_pct,
                    ziel_soc_pct=Ziel_soc,
                    ladezeit_s=ladezeit_s,
                    detour_zeit_s_je_richtung=detour_zeit_s,
                    detour_soc_pct_je_richtung=detour_soc_pct,
                    max_time_buckets=max_time_buckets,
                    heap=heap,
                )

    def _detour_kosten(
        self,
        offroute_distance_m: float,
        vehicle_profile: VehicleProfile,
    ) -> tuple[float, float]:
        """Schätzt Zeit (s) und SoC-Verbrauch (%) für die einfache Strecke.

        Betrifft die einfache Fahrtstrecke zwischen Route und Ladestation.

        `offroute_distance_m` ist die Luftlinie (siehe `_station_to_segment`);
        `DETOUR_ROUTENFAKTOR` approximiert die tatsächliche, nicht-geradlinige
        Straßendistanz daraus, `DETOUR_GESCHWINDIGKEIT_KMH` die Fahrzeit. Der
        Energiebedarf nutzt den über die Gesamtroute gemittelten Verbrauch je
        Meter (`self._avg_verbrauch_kwh_pro_m`, siehe `optimize()`) - eine
        Station direkt AUF der Route (`offroute_distance_m == 0`) hat
        dementsprechend keine Zusatzkosten.
        """
        if offroute_distance_m <= 0.0:
            return 0.0, 0.0

        strecke_m = offroute_distance_m * DETOUR_ROUTENFAKTOR
        detour_geschwindigkeit_m_s = DETOUR_GESCHWINDIGKEIT_KMH * 1000.0 / 3600.0
        zeit_s = strecke_m / detour_geschwindigkeit_m_s
        energie_kwh = strecke_m * self._avg_verbrauch_kwh_pro_m
        soc_pct = self._calc_soc_verbrauch_pct(energie_kwh, vehicle_profile)
        return zeit_s, soc_pct

    def _fuege_ladekante_hinzu(  # noqa: PLR0913, PLR0917 -- Ladekanten-Buchhaltung braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        station: ChargingStation,
        ankunfts_soc_pct: float,
        ziel_soc_pct: float,
        ladezeit_s: float,
        detour_zeit_s_je_richtung: float,
        detour_soc_pct_je_richtung: float,
        max_time_buckets: int,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Fügt eine Ladekante hinzu (Knoten-/Kanten-/Kosten-Buchhaltung).

        Erzeugt (falls günstiger als ein bestehender Pfad) eine Ladekante von
        `current` zu einem Knoten, der wieder AUF der Route liegt (derselbe
        `seg_idx`) - dazwischen liegen Hinweg-Abstecher
        (`detour_zeit_s_je_richtung`/`detour_soc_pct_je_richtung`, siehe
        `_detour_kosten`), die eigentliche Ladung (`ankunfts_soc_pct` ->
        `ziel_soc_pct` in `ladezeit_s`) und der Rückweg-Abstecher. Der neue
        Knoten-SoC ist daher `ziel_soc_pct` MINUS den Rückweg-Verbrauch, nicht
        `ziel_soc_pct` selbst - ein Ladehalt abseits der Route "kostet" auch
        auf dem Rückweg noch Reichweite. `ankunfts_soc_pct`/`ziel_soc_pct`
        (Zustand AN der Station) werden zusätzlich als Kanten-Attribute
        hinterlegt, damit `_extract_charging_stops` den tatsächlichen
        Lade-Ablauf (nicht den um die Abstecher-Fahrt verfälschten
        Routen-SoC) berichten kann - gemeinsame Buchhaltung für sowohl die
        automatische SoC-Ziel-Iteration als auch eine vom Nutzer vorgegebene
        feste Ladedauer (siehe `_add_charging_edges`).
        """
        route_soc_pct = ziel_soc_pct - detour_soc_pct_je_richtung
        if route_soc_pct < 0.0:
            return  # Reichweite reicht nicht für den Rückweg zur Route
        new_soc_bucket = soc_to_bucket(route_soc_pct, self.soc_step_pct)

        ankunftszeit = G.nodes[current]["zeitpunkt"] + timedelta(seconds=detour_zeit_s_je_richtung)
        abfahrtszeit = ankunftszeit + timedelta(seconds=ladezeit_s)
        neuer_zeitpunkt = abfahrtszeit + timedelta(seconds=detour_zeit_s_je_richtung)
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)

        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        # Kosten: Ladezeit PLUS Hin-/Rückweg-Fahrzeit des Abstechers (0 für
        # Stationen direkt auf der Route).
        kosten = ladezeit_s + 2.0 * detour_zeit_s_je_richtung
        next_node = (seg_idx, new_soc_bucket, new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="charge",
                station_id=station.station_id,
                soc_pct=route_soc_pct,
                zeitpunkt=neuer_zeitpunkt,
                segment_index=seg_idx,
                total_cost=COST_INF,
                parent=None,
            )

        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            # `station_id` als EDGE-Attribut (nicht nur Node-Attribut) setzen:
            # ein Knoten-Schluessel `(segment_index, soc_bucket, time_bucket)`
            # kann durch Diskretisierung mit einer ANDEREN Fahrt-/Faehrkante
            # kollidieren, die denselben Knoten frueher bereits (mit
            # `type="drive"`, ohne `station_id`) angelegt hat - der Knoten
            # selbst wird dann NICHT erneut mit `type="charge"`/`station_id`
            # initialisiert (siehe `if next_node not in G.nodes` oben). Die
            # tatsaechlich im Pfad gewaehlte Kante (`prev_node -> curr_node`)
            # ist aber immer eindeutig - `_extract_charging_stops` liest den
            # Ladehalt daher von der KANTE, nicht vom Knoten (sonst wird der
            # Ladehalt bei einer solchen Kollision aus dem Ergebnis verschluckt,
            # obwohl seine Kosten/Zeit sehr wohl im Pfad stecken - sichtbar als
            # Diskrepanz zwischen `gesamtreisezeit_s` und der Summe der
            # tatsaechlich zurueckgegebenen `ChargingStop`-Ladedauern).
            G.add_edge(
                current,
                next_node,
                cost=kosten,
                station_id=station.station_id,
                ankunfts_soc_pct=ankunfts_soc_pct,
                ziel_soc_pct=ziel_soc_pct,
                ladezeit_s=ladezeit_s,
                ankunftszeit=ankunftszeit,
                abfahrtszeit=abfahrtszeit,
            )
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt
            G.nodes[next_node]["soc_pct"] = route_soc_pct
            self._schedule(heap, next_node, new_total_cost)

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
            start_soc_pct=start_soc_pct,
            end_soc_pct=MAX_SOC_PCT,
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=batteriekapazitaet_kwh,
        )
        if ladezeit_bei_max <= ladezeit_s:
            return MAX_SOC_PCT  # Batterie ist vor Ablauf der Ladedauer voll

        lo, hi = 0.0, max_delta
        for _ in range(40):  # 40 Iterationen: Präzision weit unter 1e-9 %-Punkte
            mid = (lo + hi) / 2.0
            dauer = self._calc_ladezeit_s(
                start_soc_pct=start_soc_pct,
                end_soc_pct=start_soc_pct + mid,
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
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Füge Kante hinzu, um Zwischenstopp-Aufenthaltsdauer zu warten."""
        if not waypoint.aufenthaltsdauer:
            return

        wait_time_s = int(waypoint.aufenthaltsdauer.total_seconds())
        neuer_zeitpunkt = G.nodes[current]["zeitpunkt"] + timedelta(seconds=wait_time_s)
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self._base_time, self.time_step_min)

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

        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(current, next_node, cost=kosten)
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt
            G.nodes[next_node]["soc_pct"] = G.nodes[current]["soc_pct"]
            self._schedule(heap, next_node, new_total_cost)

    def _calc_soc_verbrauch_pct(
        self,
        energie_kwh: float,
        vehicle_profile: VehicleProfile,
    ) -> float:
        """Berechne SoC-Verbrauch in Prozent für einen gegebenen Energiebedarf.

        Args:
            energie_kwh: Energiebedarf in kWh (Verbrauch positiv, Rekuperation
                negativ) - typischerweise über eine Teilstrecke aggregiert
                (siehe `_add_drive_edge`).
            vehicle_profile: Fahrzeugprofil (liefert die Batteriekapazität).
        """
        return (energie_kwh / vehicle_profile.batteriekapazitaet_kwh) * MAX_SOC_PCT

    def _calc_ladezeit_s(
        self,
        start_soc_pct: float,
        end_soc_pct: float,
        ladekurve: ChargingCurve,
        batteriekapazitaet_kwh: float,
    ) -> float:
        """Berechne Ladezeit in Sekunden für den Ladevorgang `start_soc_pct` → `end_soc_pct`.

        Die mittlere Ladeleistung MUSS über das TATSAECHLICHE Start-/End-
        SoC-Fenster gemittelt werden (`_mittlere_ladeleistung_kw(start_soc_pct,
        end_soc_pct, ...)`) - eine frühere Fassung leitete das Fenster
        stattdessen ausschließlich aus der SoC-Differenz ab (angenommenes
        Fenster `[100-delta, 100]`, so als würde JEDER Ladevorgang bei 100%
        enden). Das ergab für Teilladungen von niedrigem SoC (z. B. 20% → 80%,
        real größtenteils im schnellen unteren Kurvenbereich) fälschlich die
        LANGSAME Taper-Region nahe 100% als Referenz, wodurch Teilladungen
        gegenüber einer Volladung auf 100% (dort stimmte das angenommene
        Fenster zufällig, da `end_soc_pct` ohnehin 100% ist) systematisch zu
        teuer geschätzt wurden. Der A*-Kostenoptimierer bevorzugte dadurch
        Volladungen auf 100% und vermied es, den SoC vor einem Ladehalt weit
        absinken zu lassen (siehe Nutzer-Report: Ladehalte mit ~20% Rest-SoC
        statt der eingestellten Sicherheitsreserve, sowie Volladungen auf
        100% statt der gewünschten 60-80%).
        """
        if end_soc_pct <= start_soc_pct:
            return 0.0

        mittlere_leistung_kw = self._mittlere_ladeleistung_kw(
            start_soc_pct=start_soc_pct, end_soc_pct=end_soc_pct, ladekurve=ladekurve
        )

        if mittlere_leistung_kw <= 0:
            return COST_INF  # Unendlich (nicht ladbar)

        # Energiebedarf in kWh
        delta_soc_pct = end_soc_pct - start_soc_pct
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
        """Admissible Heuristik: verbleibende reale Fahrzeit unter idealen Bedingungen.

        Ideale Bedingungen = ohne Ladestopps.

        Args:
            u: Aktueller Knoten (segment_index, soc_bucket, time_bucket).
            v: Zielknoten (segment_index, soc_bucket, time_bucket).
            segments: Liste aller Route-Segmente.

        Returns:
            Geschätzte Zeit bis zum Ziel in Sekunden.
        """
        u_seg, _, _ = u

        # Exakte verbleibende Fahrzeit per O(1)-Lookup aus der in `optimize()`
        # vorberechneten Praefixsumme der TATSAECHLICHEN, je Segment
        # ermittelten Fahrzeiten (`self._cum_time_s`, siehe
        # `SegmentEnergyResult.fahrzeit_s`) statt einer Distanz/Pauschal-
        # geschwindigkeit-Schaetzung. A* ruft die Heuristik pro expandiertem
        # Knoten auf; bei feingranularen Routen mit tausenden Segmenten waere
        # eine O(n)-Neuberechnung sonst selbst nach der Aggregation der
        # Fahrtkanten (`_add_drive_edge`) noch ein spuerbarer Kostenfaktor.
        # Admissible, da die reine Restfahrzeit (ohne Ladestopps) niemals
        # groesser als die tatsaechlichen Restkosten (Fahrzeit + evtl.
        # Ladezeit) sein kann - und straffer/informierter als eine pauschale
        # 110-km/h-Annahme, die auf Streckenabschnitten mit hoeherem
        # Tempolimit sogar INADMISSIBLE waere (Heuristik > wahre Kosten).
        return self._cum_time_s[len(segments)] - self._cum_time_s[u_seg]

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

            # Ladekante ueber das EDGE-Attribut `station_id` erkennen (siehe
            # `_fuege_ladekante_hinzu`) statt ueber Segment-Index-Gleichheit +
            # Node-Attribut: der Knoten-Schluessel `(segment_index, soc_bucket,
            # time_bucket)` kann durch Diskretisierung mit einer ANDEREN,
            # bereits frueher angelegten Fahrt-/Faehrkante kollidieren, die
            # keine `station_id` traegt - der Knoten selbst wird dann NICHT
            # erneut mit den Ladekanten-Attributen initialisiert. Die
            # tatsaechlich im Pfad gewaehlte Kante ist aber immer eindeutig,
            # daher hier von der KANTE statt vom Knoten lesen (sonst wird der
            # Ladehalt bei einer solchen Kollision aus dem Ergebnis
            # verschluckt, obwohl seine Kosten/Zeit sehr wohl im Pfad stecken).
            edge_data = G.get_edge_data(prev_node, curr_node)
            station_id = edge_data.get("station_id") if edge_data else None
            if station_id is None:
                continue  # Fahrt-/Faehr-/Wartekante, keine Ladekante

            # Station ueber die an der Kante hinterlegte `station_id`
            # auflösen - NICHT ueber eine erneute geografische Naechste-
            # Station-Suche (`segment.geometrie`-Mittelpunkt): mehrere
            # Ladekanten koennen am selben Segment fuer VERSCHIEDENE
            # Stationen existieren (z. B. wenn zwei Stationen auf denselben
            # naechstgelegenen Segment-Index abgebildet werden, siehe
            # `_map_stations_to_segments`) - eine geografische Neu-Suche
            # wuerde dann unabhaengig von der TATSAECHLICH gewaehlten Kante
            # immer dieselbe (naechstgelegene) Station zurueckgeben und so
            # z. B. eine gezielt an einer ANDEREN Station vorgegebene feste
            # Ladedauer (`ladedauer_vorgaben`) der falschen Station zuschreiben.
            station = stations_by_id.get(station_id)
            if station is None:
                continue  # Sollte nicht vorkommen (station_id stets gueltig)

            # `ankunfts_soc_pct`/`ziel_soc_pct`/`ladezeit_s`/`ankunftszeit`/
            # `abfahrtszeit` direkt aus den Kanten-Attributen lesen (siehe
            # `_fuege_ladekante_hinzu`) statt aus den Knoten-`soc_pct`/
            # `zeitpunkt`-Werten: bei einer Station abseits der Route
            # enthaelt der Knoten-SoC/-Zeitpunkt bereits den Rueckweg-
            # Abstecher (siehe `_fuege_ladekante_hinzu`) - der tatsaechliche
            # Ladevorgang (Ankunft/Abfahrt AN der Station) waere daraus nicht
            # mehr rekonstruierbar. Fuer eine vom Nutzer per
            # `ladedauer_vorgaben` fest vorgegebene Ladedauer (siehe
            # `_add_charging_edges`) ist das zugleich die exakte, dort
            # hinterlegte Dauer statt einer angenaeherten Neuberechnung.
            ladehalte.append(
                ChargingStop(
                    station=station,
                    segment_index=curr_node[0],
                    ankunfts_soc_pct=edge_data["ankunfts_soc_pct"],
                    ziel_soc_pct=edge_data["ziel_soc_pct"],
                    geschaetzte_ladedauer_s=int(edge_data["ladezeit_s"]),
                    ankunftszeit=edge_data["ankunftszeit"],
                    abfahrtszeit=edge_data["abfahrtszeit"],
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
