"""Core optimization logic: NetworkX and OR-Tools implementations.

NetworkXOptimizer: A*/Dijkstra auf diskretisiertem Zustandsgraph.
OR-Tools-based optimizer (future version).
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
    WaypointDwell,
)
from tripplanner.optimization.result_extraction import (
    compute_waypoint_times,
    extract_charging_stops,
    extract_waypoint_aufenthalte,
)
from tripplanner.optimization.station_mapping import map_stations_to_segments
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import TripInfeasibleError, VehicleProfile, Waypoint

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


# constants for cost function
COST_INF: float = 1e9  # Infinity for invalid edges
"""Roughly large constant for invalid edges (constraint violation)."""

MAX_SOC_PCT: float = 100.0
"""Maximaler SoC in Prozent."""


class NetworkXOptimizer(OptimizerInterface):
    """A*/Dijkstra-Optimierung mit NetworkX (Prototyp)."""

    def __init__(
        self,
        soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
        time_step_min: int = TIME_STEP_MIN_DEFAULT,
    ) -> None:
        """Initialize the optimizer.

        Args:
            soc_step_pct: step size for SoC discretization in percentage.
            time_step_min: step size for time discretization in minutes.
        """
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self._base_time: datetime
        self._cum_time_s: list[float]
        self._avg_consumption_kwh_per_m: float = 0.0

    def optimize(  # noqa: PLR0913, PLR0917 -- complete state of the optimization problem, see docs/plans/07-optimization.md section 4
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
        departure_time: datetime,
        iteration: int = 1,
        charging_duration_specifications: dict[str, int] | None = None,
        ferry_time_windows: dict[int, tuple[int, datetime, datetime]] | None = None,
        detour_kosten: dict[str, DetourKosten] | None = None,
    ) -> ChargingPlan:
        """Optimizes charging plan using a discretized state graph.

        A* search with heuristic = remaining drive_time_s based on actual, per
        segment ermittelten speeden (siehe `_heuristik`,
        `segmentEnergyResult.drive_time_s`).

        Args:
            route: The complete route with metadata.
            segments: List of all route segments.
            gradients: List of segment gradients.
            energy_results: Energy calculation results per segment.
            charging_stations: List of available charging stations.
            waypoints: List of waypoints with optional dwell time.
            vehicle_profile: Physical vehicle profile.
            constraints: Optimization constraints (min SoC, target SoC, etc.).
            start_soc_pct: Vehicle start SoC in percentage.
            departure_time: Geplante departure_time.
            iteration: Iteration number for later weather iteration.
            charging_duration_specifications: Optional fixed charge durations (s)
                per station ID.
            ferry_time_windows: Optional fixed ferry schedules per
                `segment_index_start -> (segment_index_end, departure, arrival)`.
            detour_kosten: Optionale real routed detour costs per station, see
                `optimization.detour_routing.precompute_detour_costs`.

        Returns:
            ChargingPlan mit Ladehalten und Gesamtreisezeit.
        """
        # Validiere Eingabeparameter
        if start_soc_pct < 0.0 or start_soc_pct > MAX_SOC_PCT:
            raise TripInfeasibleError(
                f"Start-SoC muss im Bereich [0, 100] liegen, ist aber {start_soc_pct}"
            )
        if start_soc_pct < constraints.min_soc_pct:
            raise TripInfeasibleError(
                f"Start-SoC ({start_soc_pct}%) ist unter Min-SoC ({constraints.min_soc_pct}%)"
            )

        # Erstelle gerichteten Graphen
        G: DiGraph = nx.DiGraph()

        # Zielknoten: letztes segment, Ziel-SoC (inkl. Sicherheitsreserve).
        # Am Ziel endet die Fahrt - das allgemeine `min_soc_pct` (Reserve fuer
        # WEITERFAHRT auf offener segment, siehe `OptimizationConstraints`)
        # ist hier nicht einschlaegig (analog zu `min_arrival_soc_pct`
        # an einer charging_station: dort droht ebenfalls kein Liegenbleiben more,
        # weil ohnehin nicht weitergefahren wird, bevor geladen wurde). Ein
        # vom Nutzer bewusst low gewaehltes `target_soc_pct` (z. B. 5%) darf
        # daher nicht durch den default-15%-Sicherheitsreserve-Floor
        # ueberschrieben werden (siehe Nutzer-Report: Ziel-SoC 5% gesetzt,
        # Optimierung plante dennoch auf 15% - inkl. Folgefehler bei
        # nachgelagerten Ladehalt-Kandidaten, die sich an `target_soc_target`
        # orientieren).
        target_soc_target = max(
            constraints.target_soc_pct - constraints.sicherheitsreserve_pct, 0.0
        )
        ziel_soc_bucket = soc_to_bucket(target_soc_target, self.soc_step_pct)

        # Erstelle Startknoten (segment_index=0, soc=start_soc, time=departure_time)
        start_soc_bucket = soc_to_bucket(start_soc_pct, self.soc_step_pct)
        start_zeit_bucket = time_to_bucket(departure_time, departure_time, self.time_step_min)

        start_node = (0, start_soc_bucket, start_zeit_bucket)
        G.add_node(
            start_node,
            type="start",
            soc_pct=start_soc_pct,
            timestamp=departure_time,
            segment_index=0,
        )

        # Mappe Zwischenstopps auf segmente (segment-Index → Waypoint).
        waypoint_segment_indices = self._map_waypoints_to_segments(
            waypoints=waypoints, segments=segments, route=route
        )
        waypoint_map: dict[int, list[Waypoint]] = {}
        for wp, seg_idx in zip(waypoints, waypoint_segment_indices, strict=True):
            if seg_idx not in waypoint_map:
                waypoint_map[seg_idx] = []
            waypoint_map[seg_idx].append(wp)

        # Mappe charging_stationen auf segmente
        station_segments = self._map_stations_to_segments(charging_stations, segments)

        # Create charging curve for the vehicle (V3 standard)
        ladekurve = LadekurveReferenz.model_3_sr()

        # Set base time for time bucket calculations
        self._base_time = departure_time

        # Cumulative energy-/drive_time_s prefix sums over raw segments,
        # aus den ACTUALN, je segment via `segmentEnergyResult.drive_time_s`
        # determined speeds (speed_limit_kmh/construction zones override, see
        # `energy.calculate_segment_consumption`) - NICHT aus einer einzigen
        # average speed over the entire journey. Enable
        # O(1)-Aggregation einer ganzen Teilstrecke zwischen zwei
        # decisionspunkten (`_add_drive_edge`) sowie eine O(1)-Restfahrzeit
        # fuer die A*-Heuristik (`_heuristik`) statt O(n) Neuberechnung pro
        # edge/Heuristik-Aufruf - kritisch bei feingranularen Routen mit
        # tausenden Roh-segmenten (ein segment pro GraphHopper-Polyline-
        # Punktpaar), siehe docs/plans/07-optimization.md, Risiko
        # "scalability of the NetworkX solution". Only with this real
        # Zeitbasis stimmen Ankunfts-/Abfahrtszeiten an Ladehalten
        # (`ChargingStop.arrival_time`/`departure_time`) sowie `gesamtreisezeit_s`
        # mit dem tatsaechlichen, je segment unterschiedlichen Tempo ueberein -
        # eine pauschale Durchschnittsgeschwindigkeit fuehrt sonst dazu, dass
        # die Ankunft an einem Ladehalt (bzw. dessen `segment_index`) und die
        # drive_time_s bis dorthin auseinanderlaufen (sichtbar u. a. als falscher
        # SoC/Position fuer mehrere Frames direkt nach einem Ladehalt in
        # `simulate_trip`).
        cum_energy_kwh = [0.0] * (len(segments) + 1)
        cum_time_s = [0.0] * (len(segments) + 1)
        for i, (_, er) in enumerate(zip(segments, energy_results, strict=True)):
            cum_energy_kwh[i + 1] = cum_energy_kwh[i] + er.energiebedarf_kwh
            cum_time_s[i + 1] = cum_time_s[i] + er.drive_time_s
        self._cum_time_s = cum_time_s

        # Durchschnittlicher consumption (kWh/m) ueber die GESAMTE Route - dient
        # als Naeherung fuer den Energiebedarf eines Abstechers abseits der
        # Route zu einer charging_station (siehe `_detour_kosten`). Exakte
        # segment-fuer-segment-energy fuer eine segment, die GraphHopper nie
        # berechnet hat, existiert nicht - der Routendurchschnitt ist die
        # naheliegende Naeherung (Topografie/speed_limit_kmh der Route selbst sind
        # ohnehin die beste verfuegbare Schaetzung fuer eine nahegelegene
        # Nebenstrecke).
        gesamtlaenge_m = route.gesamtlaenge_m or sum(seg.length_m for seg in segments)
        self._avg_consumption_kwh_per_m = (
            cum_energy_kwh[-1] / gesamtlaenge_m if gesamtlaenge_m > 0.0 else 0.0
        )

        # Generiere Knoten und edges (State-Graph-Konstruktion ausgelagert
        # via `graph_builder.StateGraphBuilder`, see there for the eight
        # bisherigen privaten Methoden).
        builder = StateGraphBuilder(
            soc_step_pct=self.soc_step_pct,
            time_step_min=self.time_step_min,
            base_time=self._base_time,
            avg_consumption_kwh_per_m=self._avg_consumption_kwh_per_m,
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
            target_soc_target=target_soc_target,
            max_time_buckets=self._estimate_max_time_buckets(
                total_time_s=cum_time_s[-1],
                total_energy_kwh=cum_energy_kwh[-1],
                vehicle_profile=vehicle_profile,
                constraints=constraints,
                waypoints=waypoints,
                departure_time=departure_time,
            ),
            charging_duration_specifications=charging_duration_specifications or {},
            ferry_pins=ferry_time_windows or {},
            detour_kosten=detour_kosten,
        )

        # A*-Suche zum Zielknoten
        try:
            # Target node: any SoC ≥ target_soc_target in the last segment
            # We choose the node with lowest cost
            target_candidates = [
                (seg_idx, soc_b, zeit_b)
                for seg_idx, soc_b, zeit_b in G.nodes()
                if seg_idx == len(segments) and soc_b >= ziel_soc_bucket
            ]

            if not target_candidates:
                raise TripInfeasibleError("No reachable target node found. Route not feasible.")

            # Find cheapest target node
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
            raise TripInfeasibleError(
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
                f"{s.arrival_soc_pct:.1f}%->{s.target_soc_pct:.1f}% "
                f"{s.geschaetzte_ladedauer_s:.0f}s"
                for s in ladehalte
            ],
        )

        # calculate Gesamtreisezeit: der Zielknoten hat immer segment_index ==
        # len(segments) (alle segmente vollstaendig abgefahren). `timestamp`
        # ist die tatsaechliche kumulierte arrival_time (siehe _add_drive_edge
        # etc.) statt einer aus dem gerundeten time-Bucket rekonstruierten
        # Naeherung, die bei feingranularen segmenten Praezision verlieren
        # wuerde.
        last_node = path[-1]
        last_zeitpunkt = G.nodes[last_node]["timestamp"]

        gesamtreisezeit = int((last_zeitpunkt - departure_time).total_seconds())

        # Calculate minimum arrival time for waypoints
        min_zwischenstopp_ankunftszeit = self._compute_waypoint_times(
            path=path,
            waypoints=waypoints,
            waypoint_segment_indices=waypoint_segment_indices,
            departure_time=departure_time,
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
        """Ermittelt fuer jeden Waypoint den segment-Index, an dem er liegt.

        Bevorzugt `route.via_point_indices` - vom Routing-provider EXAKT
        gelieferte segment-Indizes (bei GraphHopper aus der "reached via
        point"-Instruktion, sign=5; siehe `GraphHopperRoutingprovider.
        _map_path_to_route`) statt einer reinen Naechster-Punkt-Suche, die
        auf sich selbst kreuzenden/schleifenden Routen mehrdeutig waere
        (siehe `_waypoint_to_segment`-Docstring). Faellt auf die monoton
        fortschreitende Naechster-Punkt-Suche zurueck, falls ein provider
        keine (oder eine unpassende Anzahl) `via_point_indices` liefert
        (z. B. ein zukuenftiger/alternativer provider ohne diese Information).
        """
        return station_mapping.map_waypoints_to_segments(waypoints, segments, route)

    def _waypoint_to_segment(
        self,
        waypoint: Waypoint,
        segments: list[RouteSegment],
        min_seg_idx: int = 0,
    ) -> int:
        """Find the segment closest to a waypoint.

        Sucht nur ab `min_seg_idx` (segmente vor dem vorherigen, in Fahrt-
        richtung bereits zugeordneten Waypoint werden ausgeschlossen).
        `Routesegment`s sind einer pro GraphHopper-Polyline-Punktpaar (siehe
        `GraphHopperRoutingprovider._map_path_to_route`), also sehr
        feingranular - eine reine Distanzsuche ueber ALLE segmente kann bei
        sich kreuzenden/parallel verlaufenden Strassen (z. B. eine Route, die
        nahe an einer bereits befahrenen Kreuzung vorbeikommt) faelschlich
        einen geometrisch nahen, aber entlang der Route weit entfernten
        Punkt treffen - sichtbar u. a. als falscher SoC-gradient-Sprung weit
        vor/hinter dem tatsaechlichen Zwischenstopp auf der Karte. Da
        Zwischenstopps als GraphHopper-Via-Punkte in requestreihenfolge in
        die Route geroutet werden (siehe `GraphHopperRoutingprovider.
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

    def _estimate_max_time_buckets(  # noqa: PLR0913, PLR0917 -- Zeitbudget braucht drive_time_s-, Lade- UND Wartezeit-Kontext
        self,
        total_time_s: float,
        total_energy_kwh: float,
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        waypoints: list[Waypoint],
        departure_time: datetime,
    ) -> int:
        """Estimate the maximum number of time buckets for the entire route.

        The time budget MUST include the time needed for necessary charging stops
        include - a pure drive_time_s buffer (without charge_time) would every
        route that needs more than a handful of minutes of charging, falsely
        as "not feasible" discard once the cumulative time bucket path
        by charging beyond the pure drive_time_s estimate (see
        docs/plans/07-optimization.md).

        Args:
            total_time_s: Real, accumulated from `segmentEnergyResult.drive_time_s`
                total driving time of the route (`cum_time_s[-1]` in `optimize()`) - no
                distance/average speed estimate, otherwise it could
                budget at actually slower route sections
                be underestimated and feasible but slower routes falsely
                as "not feasible" discard.
            total_energy_kwh: total energy requirement of the route in kWh.
            vehicle_profile: Physical vehicle profile.
            constraints: optimization constraints (incl. `max_ladezeit_s`).
            waypoints: waypoints whose `stay_duration`/`planned_departure`
                additional forced wait time into the budget can -
                without this an overnight planned waypoint could the
                time budget break and the route falsely as "not
                fahrbar" verwerfen, obwohl nur gewartet werden muss.
            departure_time: departure_time of the entire journey, reference for
                an absolute `planned_departure` at a waypoint.
        """
        total_time_min = total_time_s / 60.0

        # Worst-case number of charging_stops: total energy requirement divided by the
        # usable capacity per charge cycle (conservative: half battery capacity
        # per stop, since in practice rarely charged from 0% to 100%.
        usable_capacity_per_stop_kwh = max(vehicle_profile.battery_capacity_kwh * 0.5, 1.0)
        estimated_charging_stops = max(
            math.ceil(total_energy_kwh / usable_capacity_per_stop_kwh) - 1, 0
        )
        charge_time_buffer_min = estimated_charging_stops * (constraints.max_ladezeit_s / 60.0)

        # Worst-Case-Wartezeit je Zwischenstopp: die groessere von Mindest-
        # stay_duration und (absoluter) geplanter Abfahrt relativ zur
        # Gesamt-departure_time - eine grobe, bewusst grosszuegige obere
        # Schranke (keine simulation der tatsaechlichen arrival_time noetig,
        # da ein zu grosses Budget nur die Zustandsgraph-size, nie die
        # Korrektheit beeinflusst).
        wait_time_buffer_min = 0.0
        for wp in waypoints:
            kandidaten_min = 0.0
            if wp.stay_duration:
                kandidaten_min = wp.stay_duration.total_seconds() / 60.0
            if wp.planned_departure:
                kandidaten_min = max(
                    kandidaten_min,
                    (wp.planned_departure - departure_time).total_seconds() / 60.0,
                )
            wait_time_buffer_min += max(kandidaten_min, 0.0)

        return (
            int(
                (total_time_min + charge_time_buffer_min + wait_time_buffer_min)
                / self.time_step_min
            )
            + 5
        )

    def _calc_soc_verbrauch_pct(
        self,
        energy_kwh: float,
        vehicle_profile: VehicleProfile,
    ) -> float:
        """Calculate SoC consumption in percentage for a given energy requirement.

        Args:
            energy_kwh: Energy requirement in kWh (consumption positive, recuperation
                negative) - typically aggregated over a subsection
                (siehe `_add_drive_edge`).
            vehicle_profile: vehicle profile (provides the battery capacity).
        """
        return charging_math.calc_soc_verbrauch_pct(
            energy_kwh, vehicle_profile.battery_capacity_kwh
        )

    def _calc_ladezeit_s(
        self,
        start_soc_pct: float,
        end_soc_pct: float,
        ladekurve: ChargingCurve,
        battery_capacity_kwh: float,
        leistungsdeckel_kw: float | None = None,
    ) -> float:
        """Calculate charge time in seconds for SoC `start_soc_pct` to `end_soc_pct`.

        Die average charging power MUST over the ACTUAL start/end-
        SoC window averaged (`_mittlere_ladeleistung_kw(start_soc_pct,
        end_soc_pct, ...)`) - an earlier version derived the window
        instead derived solely from the SoC difference (angenommenes
        Fenster `[100-delta, 100]`, as if EVERY charging_process bei 100%
        end). This resulted for partial charges from low SoC (z. B. 20% → 80%,
        actually mostly in the fast lower curve region) falsely the
        SLOW taper region near 100% as reference, causing partial charges
        compared to a full charge to 100% (dort stimmte das angenommene
        window happened to be 100% is) systematically to
        teuer geestimates wurden. Der A*-Kostenoptimierer bevorzugte dadurch
        Volladungen auf 100% und vermied es, den SoC vor einem Ladehalt weit
        absinken zu lassen (siehe Nutzer-Report: Ladehalte mit ~20% Rest-SoC
        statt der eingestellten Sicherheitsreserve, sowie Volladungen auf
        100% instead of the desired 60-80%).
        """
        return charging_math.calc_ladezeit_s(
            start_soc_pct,
            end_soc_pct,
            ladekurve,
            battery_capacity_kwh,
            leistungsdeckel_kw,
        )

    def _mittlere_ladeleistung_kw(
        self,
        start_soc_pct: float,
        end_soc_pct: float,
        ladekurve: ChargingCurve,
        leistungsdeckel_kw: float | None = None,
    ) -> float:
        """Calculate average charging_power over an SoC range."""
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
        """Admissible Heuristik: verbleibende reale drive_time_s unter idealen Bedingungen.

        Ideale Bedingungen = ohne charging_stops.

        Args:
            u: Aktueller Knoten (segment_index, soc_bucket, time_bucket).
            v: Zielknoten (segment_index, soc_bucket, time_bucket).
            segments: List of all route segments.

        Returns:
            estimated time bis zum Ziel in Sekunden.
        """
        u_seg, _, _ = u

        # Exakte verbleibende drive_time_s per O(1)-Lookup aus der in `optimize()`
        # vorberechneten Praefixsumme der ACTUALN, je segment
        # ermittelten Fahrzeiten (`self._cum_time_s`, siehe
        # `segmentEnergyResult.drive_time_s`) statt einer distance/Pauschal-
        # speed-Schaetzung. A* ruft die Heuristik pro expandiertem
        # Knoten auf; bei feingranularen Routen mit tausenden segmenten waere
        # eine O(n)-Neuberechnung sonst selbst nach der Aggregation der
        # Fahrtkanten (`_add_drive_edge`) noch ein spuerbarer Kostenfaktor.
        # Admissible, da die reine Restfahrzeit (ohne charging_stops) niemals
        # groesser als die tatsaechlichen Restkosten (drive_time_s + evtl.
        # charge_time) sein kann - und straffer/informierter als eine pauschale
        # 110-km/h-Annahme, die auf routenabschnitten mit hoeherem
        # speed_limit_kmh sogar INADMISSIBLE waere (Heuristik > wahre Kosten).
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
    ) -> list[WaypointDwell]:
        """Extrahiere `WaypointDwell`-Objekte aus dem Pfad.

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
        departure_time: datetime,
    ) -> dict[int, datetime]:
        """Calculate minimum arrival time for waypoints."""
        return compute_waypoint_times(path, waypoints, waypoint_segment_indices, departure_time)


def create_networkx_optimizer(
    soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
    time_step_min: int = TIME_STEP_MIN_DEFAULT,
) -> OptimizerInterface:
    """Factory function for the NetworkX-based prototype optimizer.

    Args:
        soc_step_pct: step size for SoC discretization in percentage.
        time_step_min: step size for time discretization in minutes.

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
        """Initialize the optimizer.

        Args:
            soc_step_pct: step size for SoC discretization in percentage.
            time_step_min: step size for time discretization in minutes.
            use_cp_sat: True = CP-SAT Solver, False = Routing Solver.
        """
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self.use_cp_sat = use_cp_sat

    def optimize(  # noqa: PLR0913, PLR0917 -- complete state of the optimization problem, see docs/plans/07-optimization.md section 4
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
        departure_time: datetime,
        iteration: int = 1,
        charging_duration_specifications: dict[str, int] | None = None,
        ferry_time_windows: dict[int, tuple[int, datetime, datetime]] | None = None,
        detour_kosten: dict[str, DetourKosten] | None = None,
    ) -> ChargingPlan:
        """Optimizes charging plan via constraint programming (CP-SAT) oder Routing-Solver.

        Note: This is a placeholder for a future development stage.
        Die aktuelle implementation raise NotImplementedError.
        """
        raise NotImplementedError(
            "OR-Tools backend is a future development stage, siehe docs/plans/07-optimization.md"
        )


def create_ortools_optimizer(
    soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
    time_step_min: int = TIME_STEP_MIN_DEFAULT,
    use_cp_sat: bool = True,
) -> OptimizerInterface:
    """Factory function for the OR-Tools-based optimizer (future version).

    Args:
        soc_step_pct: step size for SoC discretization in percentage.
        time_step_min: step size for time discretization in minutes.
        use_cp_sat: True = CP-SAT Solver, False = Routing Solver.

    Returns:
        ORToolsOptimizer-Instanz.
    """
    return ORToolsOptimizer(
        soc_step_pct=soc_step_pct,
        time_step_min=time_step_min,
        use_cp_sat=use_cp_sat,
    )
