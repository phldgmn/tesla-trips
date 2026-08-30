"""Kern-Logik für die Optimierung: NetworkX- und OR-Tools-Implementierungen.

NetworkXOptimizer: A*/Dijkstra auf diskretisiertem Zustandsgraph.
ORToolsOptimizer: Platzhalter für zukünftige CP-SAT Implementierung.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING

import networkx as nx
from networkx import DiGraph

from tripplanner.battery.models import ChargingCurve, LadekurveReferenz
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization import charging_math, station_mapping
from tripplanner.optimization.discretizer import (
    SOC_STEP_PCT_DEFAULT,
    TIME_STEP_MIN_DEFAULT,
    soc_to_bucket,
    time_to_bucket,
)
from tripplanner.optimization.graph_builder import StateGraphBuilder
from tripplanner.optimization.models import (
    ChargingPlan,
    ChargingStop,
    DetourKosten,
    OptimizationConstraints,
    OptimizerInterface,
    ZwischenstoppAufenthalt,
)
from tripplanner.optimization.result_extraction import (
    compute_waypoint_times,
    extract_charging_stops,
    extract_waypoint_aufenthalte,
)
from tripplanner.optimization.station_mapping import map_stations_to_segments
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile, Waypoint

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


# Konstanten für Kostenfunktion
COST_INF: float = 1e9  # Unendlich für unzulässige Kanten
"""Grobe Konstante für unzulässige Kanten (Constraint-Verletzung)."""

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
        self._cum_time_s: list[float]
        self._avg_verbrauch_kwh_pro_m: float = 0.0

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
        detour_kosten: dict[str, DetourKosten] | None = None,
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
            detour_kosten: Optionale real routed detour costs per station, see
                `optimization.detour_routing.precompute_detour_costs`.

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

        # Zielknoten: letztes Segment, Ziel-SoC (inkl. Sicherheitsreserve).
        # Am Ziel endet die Fahrt - das allgemeine `min_soc_pct` (Reserve fuer
        # WEITERFAHRT auf offener Strecke, siehe `OptimizationConstraints`)
        # ist hier nicht einschlaegig (analog zu `mindest_ankunfts_soc_pct`
        # an einer Ladestation: dort droht ebenfalls kein Liegenbleiben MEHR,
        # weil ohnehin nicht weitergefahren wird, bevor geladen wurde). Ein
        # vom Nutzer bewusst niedrig gewaehltes `ziel_soc_pct` (z. B. 5%) darf
        # daher nicht durch den default-15%-Sicherheitsreserve-Floor
        # ueberschrieben werden (siehe Nutzer-Report: Ziel-SoC 5% gesetzt,
        # Optimierung plante dennoch auf 15% - inkl. Folgefehler bei
        # nachgelagerten Ladehalt-Kandidaten, die sich an `ziel_soc_target`
        # orientieren).
        ziel_soc_target = max(constraints.ziel_soc_pct - constraints.sicherheitsreserve_pct, 0.0)
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

        # Mappe Zwischenstopps auf Segmente (Segment-Index → Waypoint).
        waypoint_segment_indices = self._map_waypoints_to_segments(
            waypoints=waypoints, segments=segments, route=route
        )
        waypoint_map: dict[int, list[Waypoint]] = {}
        for wp, seg_idx in zip(waypoints, waypoint_segment_indices, strict=True):
            if seg_idx not in waypoint_map:
                waypoint_map[seg_idx] = []
            waypoint_map[seg_idx].append(wp)

        # Mappe Ladestationen auf Segmente
        station_segments = self._map_stations_to_segments(charging_stations, segments)

        # Erstelle Ladekurve für das Fahrzeug (V3-Standard)
        ladekurve = LadekurveReferenz.model_3_sr()

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

        # Generiere Knoten und Kanten (State-Graph-Konstruktion ausgelagert
        # nach `graph_builder.StateGraphBuilder`, siehe dort für die acht
        # bisherigen privaten Methoden).
        builder = StateGraphBuilder(
            soc_step_pct=self.soc_step_pct,
            time_step_min=self.time_step_min,
            base_time=self._base_time,
            avg_verbrauch_kwh_pro_m=self._avg_verbrauch_kwh_pro_m,
        )
        builder.generate_graph(
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
                waypoints=waypoints,
                abfahrtszeit=abfahrtszeit,
            ),
            ladedauer_vorgaben=ladedauer_vorgaben or {},
            ferry_pins=faehr_zeitfenster or {},
            detour_kosten=detour_kosten,
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
            logger.info(
                "best_target=%s total_cost=%.1fs (astar will reconstruct the path from here)",
                best_target,
                G.nodes[best_target].get("total_cost", COST_INF),
            )
            self._log_parent_chain(G, best_target)
            self._log_best_cost_per_final_station(G, target_candidates)

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
        zwischenstopp_aufenthalte = self._extract_waypoint_aufenthalte(G=G, path=path)
        logger.info(
            "Optimizer chose %d charging stop(s): %s",
            len(ladehalte),
            [
                f"{s.station.station_id} ({s.station.name}) seg={s.segment_index} "
                f"{s.ankunfts_soc_pct:.1f}%->{s.ziel_soc_pct:.1f}% "
                f"{s.geschaetzte_ladedauer_s:.0f}s"
                for s in ladehalte
            ],
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
            waypoint_segment_indices=waypoint_segment_indices,
            abfahrtszeit=abfahrtszeit,
        )

        return ChargingPlan(
            ladehalte=ladehalte,
            gesamtreisezeit_s=gesamtreisezeit,
            min_zwischenstopp_ankunftszeit=min_zwischenstopp_ankunftszeit,
            zwischenstopp_aufenthalte=zwischenstopp_aufenthalte,
        )

    def _log_parent_chain(self, G: DiGraph, best_target: tuple[int, int, int]) -> None:
        """Logs the charging stations on the `parent`-reconstructed path.

        Uses `parent` pointers set DIRECTLY by `generate_graph`'s own
        Dijkstra bookkeeping - NOT via `nx.astar_path`'s independent
        re-derivation in `optimize()`. If these two disagree on which
        stations/costs are used, the discrepancy is isolated to the
        `nx.astar_path` step; if they agree, the discrepancy (if any) is in
        `generate_graph`'s cost accounting itself.
        """
        parent_chain_stations: list[str] = []
        node: tuple[int, int, int] | None = best_target
        visited_chain: set[tuple[int, int, int]] = set()
        while node is not None and node not in visited_chain:
            visited_chain.add(node)
            parent = G.nodes[node].get("parent")
            if parent is not None:
                edge_data = G.get_edge_data(parent, node) or {}
                station_id = edge_data.get("station_id")
                if station_id is not None:
                    parent_chain_stations.append(
                        f"{station_id}@{node} edge_cost={edge_data.get('cost', -1):.1f}s "
                        f"total_cost={G.nodes[node].get('total_cost', COST_INF):.1f}s"
                    )
            node = parent
        logger.info(
            "parent-chain reconstruction: %d charging stop(s): %s",
            len(parent_chain_stations),
            list(reversed(parent_chain_stations)),
        )

    def _log_best_cost_per_final_station(
        self, G: DiGraph, target_candidates: list[tuple[int, int, int]]
    ) -> None:
        """Logs the cheapest destination total_cost per last-used charging station.

        Scans ALL `target_candidates` (not just `best_target`). Directly
        answers whether a cheaper destination-reaching state involving a
        specific station (e.g. one the user expected to be used) exists
        ANYWHERE in the fully-built graph, without relying on error-prone
        manual reconstruction from DEBUG logs: if that station's best entry
        here is cheaper than the chosen `best_target`, `min(target_candidates,
        ...)`/graph construction has a genuine bug; if it is NOT cheaper, the
        chosen station is the true Dijkstra optimum given this route's real
        costs, and the report is a legitimate result rather than a defect.
        """
        best_per_station: dict[str, tuple[float, tuple[int, int, int]]] = {}
        for target in target_candidates:
            total_cost = G.nodes[target].get("total_cost", COST_INF)
            node: tuple[int, int, int] | None = target
            visited_chain: set[tuple[int, int, int]] = set()
            last_station: str | None = None
            while node is not None and node not in visited_chain:
                visited_chain.add(node)
                parent = G.nodes[node].get("parent")
                if parent is not None:
                    station_id = (G.get_edge_data(parent, node) or {}).get("station_id")
                    if station_id is not None:
                        last_station = station_id
                        break
                node = parent
            key = last_station or "<no charging stop>"
            existing = best_per_station.get(key)
            if existing is None or total_cost < existing[0]:
                best_per_station[key] = (total_cost, target)
        logger.info(
            "best destination-reaching total_cost per LAST charging station: %s",
            sorted(
                ((station, cost) for station, (cost, _) in best_per_station.items()),
                key=lambda item: item[1],
            ),
        )

    def _map_waypoints_to_segments(
        self,
        waypoints: list[Waypoint],
        segments: list[RouteSegment],
        route: Route,
    ) -> list[int]:
        """Ermittelt fuer jeden Waypoint den Segment-Index, an dem er liegt.

        Bevorzugt `route.via_point_indices` - vom Routing-Provider EXAKT
        gelieferte Segment-Indizes (bei GraphHopper aus der "reached via
        point"-Instruktion, sign=5; siehe `GraphHopperRoutingProvider.
        _map_path_to_route`) statt einer reinen Naechster-Punkt-Suche, die
        auf sich selbst kreuzenden/schleifenden Routen mehrdeutig waere
        (siehe `_waypoint_to_segment`-Docstring). Faellt auf die monoton
        fortschreitende Naechster-Punkt-Suche zurueck, falls ein Provider
        keine (oder eine unpassende Anzahl) `via_point_indices` liefert
        (z. B. ein zukuenftiger/alternativer Provider ohne diese Information).
        """
        return station_mapping.map_waypoints_to_segments(waypoints, segments, route)

    def _waypoint_to_segment(
        self,
        waypoint: Waypoint,
        segments: list[RouteSegment],
        min_seg_idx: int = 0,
    ) -> int:
        """Ermittle das Segment, das einem Waypoint am nächsten liegt.

        Sucht nur ab `min_seg_idx` (Segmente vor dem vorherigen, in Fahrt-
        richtung bereits zugeordneten Waypoint werden ausgeschlossen).
        `RouteSegment`s sind einer pro GraphHopper-Polyline-Punktpaar (siehe
        `GraphHopperRoutingProvider._map_path_to_route`), also sehr
        feingranular - eine reine Distanzsuche ueber ALLE Segmente kann bei
        sich kreuzenden/parallel verlaufenden Strassen (z. B. eine Route, die
        nahe an einer bereits befahrenen Kreuzung vorbeikommt) faelschlich
        einen geometrisch nahen, aber entlang der Route weit entfernten
        Punkt treffen - sichtbar u. a. als falscher SoC-Gradient-Sprung weit
        vor/hinter dem tatsaechlichen Zwischenstopp auf der Karte. Da
        Zwischenstopps als GraphHopper-Via-Punkte in Anfragereihenfolge in
        die Route geroutet werden (siehe `GraphHopperRoutingProvider.
        berechne_route`), muessen sie auch entlang der Route in dieser
        Reihenfolge auftreten - ein monoton steigender Suchstart pro
        Waypoint erzwingt das.
        """
        return station_mapping.waypoint_to_segment(waypoint, segments, min_seg_idx)

    def _map_stations_to_segments(
        self, stations: list[ChargingStation], segments: list[RouteSegment]
    ) -> dict[int, list[tuple[ChargingStation, float]]]:
        """Maps charging stations onto their nearest route segment.

        Delegates to `station_mapping.map_stations_to_segments` - see there
        for the shared implementation (also used by
        `optimization.detour_routing.precompute_detour_costs`, which needs
        the same mapping BEFORE `optimize()` runs).
        """
        return map_stations_to_segments(stations, segments)

    def _estimate_max_time_buckets(  # noqa: PLR0913, PLR0917 -- Zeitbudget braucht Fahrzeit-, Lade- UND Wartezeit-Kontext
        self,
        total_time_s: float,
        total_energy_kwh: float,
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        waypoints: list[Waypoint],
        abfahrtszeit: datetime,
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
            waypoints: Zwischenstopps, deren `aufenthaltsdauer`/`geplante_abfahrt`
                zusaetzliche, erzwungene Wartezeit ins Budget einbringen kann -
                ohne das koennte ein ueber Nacht geplanter Zwischenstopp das
                Zeitbudget sprengen und die Route faelschlich als "nicht
                fahrbar" verwerfen, obwohl nur gewartet werden muss.
            abfahrtszeit: Abfahrtszeitpunkt der gesamten Reise, Referenz fuer
                eine absolute `geplante_abfahrt` an einem Zwischenstopp.
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

        # Worst-Case-Wartezeit je Zwischenstopp: die groessere von Mindest-
        # aufenthaltsdauer und (absoluter) geplanter Abfahrt relativ zur
        # Gesamt-Abfahrtszeit - eine grobe, bewusst grosszuegige obere
        # Schranke (keine Simulation der tatsaechlichen Ankunftszeit noetig,
        # da ein zu grosses Budget nur die Zustandsgraph-Groesse, nie die
        # Korrektheit beeinflusst).
        wartezeit_puffer_min = 0.0
        for wp in waypoints:
            kandidaten_min = 0.0
            if wp.aufenthaltsdauer:
                kandidaten_min = wp.aufenthaltsdauer.total_seconds() / 60.0
            if wp.geplante_abfahrt:
                kandidaten_min = max(
                    kandidaten_min,
                    (wp.geplante_abfahrt - abfahrtszeit).total_seconds() / 60.0,
                )
            wartezeit_puffer_min += max(kandidaten_min, 0.0)

        return (
            int((total_time_min + ladezeit_puffer_min + wartezeit_puffer_min) / self.time_step_min)
            + 5
        )

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
        return charging_math.calc_soc_verbrauch_pct(
            energie_kwh, vehicle_profile.batteriekapazitaet_kwh
        )

    def _calc_ladezeit_s(
        self,
        start_soc_pct: float,
        end_soc_pct: float,
        ladekurve: ChargingCurve,
        batteriekapazitaet_kwh: float,
        leistungsdeckel_kw: float | None = None,
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
        return charging_math.calc_ladezeit_s(
            start_soc_pct,
            end_soc_pct,
            ladekurve,
            batteriekapazitaet_kwh,
            leistungsdeckel_kw,
        )

    def _mittlere_ladeleistung_kw(
        self,
        start_soc_pct: float,
        end_soc_pct: float,
        ladekurve: ChargingCurve,
        leistungsdeckel_kw: float | None = None,
    ) -> float:
        """Berechne mittlere Ladeleistung über einen SoC-Bereich."""
        return charging_math.mittlere_ladeleistung_kw(
            start_soc_pct,
            end_soc_pct,
            ladekurve,
            leistungsdeckel_kw,
        )

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
        return extract_charging_stops(G, path, segments, charging_stations, constraints)

    def _extract_waypoint_aufenthalte(
        self,
        G: DiGraph,
        path: list[tuple[int, int, int]],
    ) -> list[ZwischenstoppAufenthalt]:
        """Extrahiere `ZwischenstoppAufenthalt`-Objekte aus dem Pfad.

        Analog zu `_extract_charging_stops`, aber ueber das EDGE-Attribut
        `waypoint_ankunftszeit` (siehe `_add_waypoint_wait_edge`) statt
        `station_id` - Zwischenstopp-Aufenthalte sind unabhaengig von der
        Supercharger-Stationsinfrastruktur (keine `ChargingStation`, kein
        Detour-/Preis-Handling).
        """
        return extract_waypoint_aufenthalte(G, path)

    def _compute_waypoint_times(
        self,
        path: list[tuple[int, int, int]],
        waypoints: list[Waypoint],
        waypoint_segment_indices: list[int],
        abfahrtszeit: datetime,
    ) -> dict[int, datetime]:
        """Berechne Mindestankunftszeit für Zwischenstopps."""
        return compute_waypoint_times(path, waypoints, waypoint_segment_indices, abfahrtszeit)


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
        detour_kosten: dict[str, DetourKosten] | None = None,
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
