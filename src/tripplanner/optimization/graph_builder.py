"""State graph construction for the NetworkX optimizer.

`StateGraphBuilder` is the internal implementation of the state graph-
creation (nodes + edges of the discretized charging/driving problem) that
extracted from `NetworkXOptimizer` into `optimizer.py`. It encapsulates
all the state that the graph construction needs (`soc_step_pct`,
`time_step_min`, `base_time`, `avg_consumption_kwh_per_m`, `push_seq` counter)
in its `__init__` and ports the eight previous private methods of
`NetworkXOptimizer` (`_generate_graph`, `_schedule`, `_required_departure`,
`_add_drive_edge`, `_add_ferry_edge`, `_add_charging_edges`,
`_fuege_ladekante_hinzu`, `_add_waypoint_wait_edge`) as its own
builder methods.

`StateGraphBuilder` is a private implementation detail of
`NetworkXOptimizer` and is NOT exported from `optimization/__init__.py`
re-exported.
"""

from __future__ import annotations

import bisect
import heapq
import itertools
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from networkx import DiGraph

from tripplanner.optimization import charging_math, detour_costs
from tripplanner.optimization.discretizer import soc_to_bucket, time_to_bucket

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tripplanner.battery.models import ChargingCurve
    from tripplanner.charging_infrastructure.models import ChargingStation
    from tripplanner.optimization.models import DetourKosten, OptimizationConstraints
    from tripplanner.routing.models import RouteSegment
    from tripplanner.trip_input.models import VehicleProfile, Waypoint


# constants for cost function (same as `optimizer.py`)
COST_INF: float = 1e9  # Infinity for invalid edges
MAX_SOC_PCT: float = 100.0
# Vanishingly small cost tie-breaker (seconds per percentage point charged)
# for charging edges at REGULAR charging stations (NOT at waypoint stops, see
# `add_waypoint_wait_edge`): faellt ein Ladehalt vor einer erzwungenen,
# spaeten departure_time (`Waypoint.planned_departure`/`stay_duration`), ist
# die zusaetzliche charge_time dort rechnerisch EXAKT kostenneutral - jede
# Sekunde laenger geladen wird 1:1 durch eine Sekunde kuerzeres Warten am
# waypoint (`add_waypoint_wait_edge`, `costs = wait_time_s`
# ist affin in der arrival_time). Ohne einen Tie-Breaker waehlt Dijkstra bei
# dieser exakten Kostengleichheit ein beliebiges (oft unnoetig hohes)
# Ladeziel statt des tatsaechlich benoetigten Minimums (siehe Nutzer-Report:
# charging_station short vor einem ueber Nacht charging Zwischenstopp laedt bis auf
# the `max_charge_soc_pct` cap, even though the waypoint itself anyway
# unlimited to 100% anyway). The value is many orders of magnitude smaller
# than any time difference that actually matters (seconds to minutes per
# time bucket) and can therefore NEVER distort a real time optimization -
# er decides nur echte Gleichstaende zugunsten des sparsameren Ladeziels.
LADE_TIEBREAK_S_PRO_PROZENTPUNKT: float = 1e-4


class StateGraphBuilder:
    """Constructs the discretized state graph for the optimization."""

    def __init__(
        self,
        soc_step_pct: float,
        time_step_min: int,
        base_time: datetime,
        avg_consumption_kwh_per_m: float,
    ) -> None:
        """Initialisiert den Builder mit dem Graph-Konstruktions-Zustand.

        Args:
            soc_step_pct: step size for SoC discretization in percentage.
            time_step_min: step size for time discretization in minutes.
            base_time: Base timestamp for the time bucket calculation.
            avg_consumption_kwh_pro_m: Durchschnittlicher consumption der Route
                (kWh/m), base for the detour cost heuristic.
        """
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self.base_time = base_time
        self.avg_consumption_kwh_per_m = avg_consumption_kwh_per_m
        self.push_seq: itertools.count[int] = itertools.count()

    def schedule(
        self,
        heap: list[tuple[float, int, tuple[int, int, int]]],
        node: tuple[int, int, int],
        total_cost: float,
    ) -> None:
        """Plant `node` mit `total_cost` auf dem Dijkstra-Min-Heap ein.

        Ein Knoten kann mehrfach eingeplant werden (einmal je Verbesserung
        seiner Gesamtkosten) - veraltete, teurere Eintraege werden beim Pop in
        `generate_graph` per `visited`-Check ignoriert ("lazy deletion").
        `self.push_seq` (ein `itertools.count()`, pro `generate_graph`-Lauf
        new initialisiert) dient als Tie-Breaker, damit `heapq` bei gleichen
        Kosten NIEMALS die Knoten-Tupel selbst vergleicht.
        """
        heapq.heappush(heap, (total_cost, next(self.push_seq), node))

    def generate_graph(  # noqa: PLR0913, PLR0917 -- Graph-Konstruktion braucht den vollen Kontext (Route, energy, Laden, Zwischenstopps, Constraints)
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
        target_soc_target: float,
        max_time_buckets: int,
        charging_duration_specifications: dict[str, int],
        ferry_pins: dict[int, tuple[int, datetime, datetime]],
        detour_kosten: dict[str, DetourKosten] | None = None,
    ) -> None:
        """Generate nodes and edges for the state graph."""
        # Dijkstra-artige Erweiterung mit Min-Heap statt FIFO-BFS: nur
        # erreichbare Knoten erzeugen. Eine reine FIFO-Reihenfolge (frueher:
        # `deque`/`popleft`) verletzt die Dijkstra-Invariante, dass ein Knoten
        # erst dann als "final" markiert (und seine ausgehenden edges erzeugt)
        # werden darf, wenn er mit MINIMALEN Gesamtkosten aus der Warteschlange
        # entnommen wird. Bei FIFO kann ein Knoten mit einem zuerst entdeckten,
        # aber teureren/pessimistischeren SoC verarbeitet werden, WAEHREND ein
        # spaeterer, guenstigerer Pfad zu demselben Knoten `total_cost`/`soc_pct`
        # zwar noch aktualisiert (siehe Kommentare in `add_drive_edge` etc.),
        # dessen ausgehende edges aber NIE (er ist ja schon "visited") new
        # erzeugt werden. Ergebnis: nachgelagerte edges (z. B. "charging_station
        # ueberspringen, weiterfahren") werden mit einem zu niedrigen SoC
        # geplant und faelschlich als unzulaessig verworfen - das erzwingt
        # unnoetige Zwischenladestopps, obwohl der tatsaechlich guenstigste
        # (spaeter gefundene) Zustand ausgereicht haette (siehe Nutzer-Report:
        # unnoetiger 70%->80%-charging_stop in Kamen vor Holdorf-Ankunft mit 24%).
        # Ein Min-Heap mit "lazy deletion" (veraltete Eintraege werden beim Pop
        # anhand von `visited` uebersprungen) behebt das bei nichtnegativen
        # edgesgewichten (drive_time_s/charge_time/Wartezeit sind stets >= 0)
        # korrekt: der erste Pop eines Knotens liefert garantiert dessen
        # minimale Gesamtkosten.
        visited: set[tuple[int, int, int]] = set()
        # Dominanz-Pruning: fuer dieselbe Position+SoC (`seg_idx, soc_bucket`)
        # ist ein SPAETERER arrival_time bei GLEICHEN oder hoeheren
        # Gesamtkosten NIE von Vorteil - `total_cost` ist in diesem model
        # ueberall exakt die seit Abfahrt verstrichene time (jede edge ist
        # eine Zeitdauer: drive_time_s/charge_time/Wartezeit/ferry-Wartezeit), und
        # saemtliche Folgekosten (energy_consumption, charging_curve, `max_time_
        # buckets`-Limit, sogar ferry-Abfahrtsfenster - frueher ankommen
        # heisst dort hoechstens laenger warten, nie eine Ferry verpassen,
        # die ein spaeterer Zustand noch erreicht haette) haengen NUR vom
        # weiterhin identischen SoC und der (monoton) verstrichenen time ab,
        # nie vom Kalenderzeitpunkt selbst. Der erste (Heap-Reihenfolge:
        # guenstigste) besuchte Knoten je `(seg_idx, soc_bucket)` erweitert
        # daher IMMER mindestens so guenstige Folgezustaende wie jeder
        # spaetere - dessen eigene ausgehende edges sind somit ueberfluessig
        # und werden uebersprungen. Ohne dieses Pruning haelt der Zustands-
        # graph pro decisionspunkt bis zu O(SoC-Buckets x time-Buckets)
        # tatsaechlich erweiterte Knoten statt O(SoC-Buckets) - bei Routen
        # mit vielen charging_stationen (z. B. lange Auslandsstrecken mit dichtem
        # Schnelllader-Netz) der dominante Faktor fuer eine quadratisch statt
        # linear mit der Stationsanzahl wachsende Laufzeit (siehe Nutzer-
        # Report: > 100s Optimierungszeit).
        dominanz_erweitert: set[tuple[int, int, bool]] = set()
        self.push_seq = itertools.count()
        heap: list[tuple[float, int, tuple[int, int, int]]] = []
        self.schedule(heap, start_node, 0.0)
        G.nodes[start_node]["total_cost"] = 0.0
        G.nodes[start_node]["parent"] = None

        # Sortierte Liste aller decisionspunkte (charging_station, Zwischenstopp
        # or ferry boarding). Between two decision points, there are in
        # Zustandsgraphen keine Verzweigung - eine Fahrtkante darf die
        # raw segments in between can therefore be skipped in ONE jump
        # (siehe `add_drive_edge`) statt pro Roh-segment einen eigenen
        # Zustandsknoten zu erzeugen. Das reduziert die Knotenzahl von
        # O(Roh-segmente x SoC-Buckets x time-Buckets) auf
        # O(decisionspunkte x SoC-Buckets x time-Buckets) - bei
        # feingranularen Routen (tausende Roh-segmente, wenige Dutzend
        # charging_stationen) der criticale Faktor (docs/plans/07-optimization.md).
        checkpoints: list[int] = sorted(set(waypoint_map) | set(station_segments) | set(ferry_pins))

        # segmente von Zwischenstopps, an denen TATSAECHLICH geladen werden
        # kann (charging_power gesetzt UND eine erzwungene Wartezeit vorliegt,
        # siehe `add_waypoint_wait_edge`/`required_departure` - ohne
        # Wartezeit findet dort kein charging_process statt, siehe
        # `Waypoint.charging_power_kw`). Fuer diese segmente gilt beim Anfahren
        # dieselbe abgesenkte Ankunfts-Untergrenze wie an einer charging_station
        # (`min_arrival_soc_pct` statt des allgemeinen
        # `min_soc_pct`-Sicherheitsreserve fuer offene segment) - an einer
        # charging_station UND an einem ladefaehigen Zwischenstopp ist ein
        # niedriger Ankunfts-SoC unbedenklich, weil garantiert nachgeladen
        # wird (siehe Nutzer-Report: Ankunft an einem charging Zwischenstopp
        # mit 21% statt der erwarteten ~5%, weil der Fahrt-edge dorthin
        # faelschlich das Offene-segment-Minimum auferlegt wurde).
        waypoint_charge_segments: set[int] = {
            seg_idx
            for seg_idx, wps in waypoint_map.items()
            if any(
                wp.charging_power_kw is not None
                and wp.charging_power_kw > 0.0
                and (wp.stay_duration is not None or wp.planned_departure is not None)
                for wp in wps
            )
        }

        while heap:
            _, _, current = heapq.heappop(heap)
            if current in visited:
                continue  # Veralteter Heap-Eintrag (Kosten wurden inzwischen unterboten)
            visited.add(current)

            seg_idx, soc_bucket, time_bucket = current

            # Zwischenstopp-Zwang pruefen: liegt fuer diese Position eine (aus
            # `Waypoint.stay_duration`/`planned_departure` abgeleitete)
            # Mindestabfahrtszeit vor, die am aktuellen Knoten noch nicht
            # erreicht ist, MUSS zunaechst gewartet werden - Fahrt-/FerryEdge
            # (Block 2) werden dann NICHT erzeugt, sonst waere die Wartezeit
            # nur ein optionaler, vom A*-Kostenoptimierer als teurer verworfener
            # Zusatzpfad statt einer erzwungenen Mindestaufenthaltsdauer (siehe
            # Nutzer-Report: eine gesetzte departure_time an einem Zwischenstopp
            # wurde bei der arrival_time am Ziel ignoriert). Muss VOR dem
            # Dominanz-Check ausgewertet werden, denn `muss_warten` fliesst in
            # dessen Schluessel ein (siehe dort).
            required_departure, wait_koordinate, wait_ladeleistung_kw = self.required_departure(
                G=G, current=current, seg_idx=seg_idx, waypoint_map=waypoint_map
            )
            muss_warten = (
                required_departure is not None
                and G.nodes[current]["timestamp"] < required_departure
            )

            # Dominanz-Check (siehe Kommentar oben): pro `(seg_idx, soc_bucket,
            # muss_warten)` wird NUR der zuerst (= guenstigste, Heap-Reihen-
            # folge) besuchte Knoten tatsaechlich erweitert. `muss_warten`
            # MUSS Teil des Schluessels sein: ein noch wartepflichtiger Knoten
            # erzeugt NUR eine Wartekante (Block 2/Fahrtkante bleibt aus),
            # waehrend ein bereits abfahrbereiter Knoten am GLEICHEN
            # `(seg_idx, soc_bucket)` die Fahrtkante erzeugt - ohne die Phase
            # im Schluessel wuerde der zuerst besuchte (noch wartepflichtige)
            # Knoten den Schluessel belegen und den spaeter erreichten,
            # bereits abfahrbereiten Knoten von JEDER Erweiterung ausschliessen
            # (die Fahrt kommt dann nie zustande - Regressionstest: eine an
            # einem Zwischenstopp gesetzte departure_time fuehrte sonst zu
            # "Kein erreichbarer Zielknoten gefunden").
            dominanz_key = (seg_idx, soc_bucket, muss_warten)
            if dominanz_key in dominanz_erweitert:
                continue
            dominanz_erweitert.add(dominanz_key)

            # Check if target reached (all segments traversed)
            if seg_idx == len(segments) and soc_bucket >= ziel_soc_bucket:
                continue  # Ziel erreicht, nicht weiter erweitern

            # 2. Fahrtkante: bis zum naechsten decisionspunkt (oder bis
            # zum Ziel, falls keiner more folgt) in einem Sprung fahren -
            # ausser der Nutzer hat fuer diese Position einen festen
            # ferry schedule given (`ferry_pins`), then the entire
            # ferry crossing modeled separately (see `add_ferry_edge`).
            # seg_idx zaehlt bereits abgefahrene segmente (0 = Start,
            # len(segments) = Ziel erreicht).
            if seg_idx < len(segments) and not muss_warten:
                if seg_idx in ferry_pins:
                    self.add_ferry_edge(
                        G=G,
                        current=current,
                        pin=ferry_pins[seg_idx],
                        max_time_buckets=max_time_buckets,
                        heap=heap,
                    )
                else:
                    idx = bisect.bisect_right(checkpoints, seg_idx)
                    target_seg_idx = checkpoints[idx] if idx < len(checkpoints) else len(segments)
                    self.add_drive_edge(
                        G=G,
                        current=current,
                        seg_idx=seg_idx,
                        target_seg_idx=target_seg_idx,
                        total_segments=len(segments),
                        target_soc_target=target_soc_target,
                        cum_time_s=cum_time_s,
                        cum_energy_kwh=cum_energy_kwh,
                        max_time_buckets=max_time_buckets,
                        constraints=constraints,
                        vehicle_profile=vehicle_profile,
                        station_segments=station_segments,
                        waypoint_charge_segments=waypoint_charge_segments,
                        heap=heap,
                    )

            # 3. charging_edge: Charge at this station (if available) - remains
            # auch waehrend einer erzwungenen Zwischenstopp-Wartezeit allowed
            # (Laden UND Warten schliessen sich nicht aus).
            if seg_idx in station_segments:
                self.add_charging_edges(
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
                    charging_duration_specifications=charging_duration_specifications,
                    checkpoints=checkpoints,
                    station_segments=station_segments,
                    waypoint_charge_segments=waypoint_charge_segments,
                    cum_energy_kwh=cum_energy_kwh,
                    target_soc_target=target_soc_target,
                    detour_kosten=detour_kosten,
                )

            # 4. Zwischenstopp-Zwang: bis zur requireden departure_time
            # warten - optional mit Ladung ueber `wait_ladeleistung_kw`.
            if muss_warten and required_departure is not None and wait_koordinate is not None:
                self.add_waypoint_wait_edge(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    required_departure=required_departure,
                    coordinate=wait_koordinate,
                    charging_power_kw=wait_ladeleistung_kw,
                    ladekurve=ladekurve,
                    vehicle_profile=vehicle_profile,
                    max_time_buckets=max_time_buckets,
                    heap=heap,
                )

    def required_departure(
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        waypoint_map: dict[int, list[Waypoint]],
    ) -> tuple[datetime | None, tuple[float, float] | None, float | None]:
        """Ermittelt die (spaeteste) erzwungene Mindestabfahrtszeit an `seg_idx`.

        Kombiniert je Waypoint `stay_duration` (relativ zur ACTUALN
        Ankunft `stop_arrival`) und `planned_departure` (absolut) - `stop_arrival`
        ist der timestamp der ACTUALN Ankunft an dieser Position (siehe
        `add_drive_edge`/`add_ferry_edge`), nicht der aktuelle Knoten-
        timestamp, der bereits eine laufende Ladung/Wartezeit am selben
        `seg_idx` widerspiegeln kann (sonst wuerde eine relative
        `stay_duration` bei jeder erneuten Pruefung ab dem NEUEN timestamp
        nochmals aufgeschlagen und nie konvergieren). Liegen mehrere
        Zwischenstopps auf demselben segment, gewinnt die spaeteste Abfahrts-
        time (deren Koordinate/charging_power wird fuer die Wartekante genutzt).
        """
        if seg_idx not in waypoint_map:
            return None, None, None

        stop_arrival = G.nodes[current].get("stop_arrival", G.nodes[current]["timestamp"])
        required_departure: datetime | None = None
        coordinate: tuple[float, float] | None = None
        charging_power_kw: float | None = None
        for wp in waypoint_map[seg_idx]:
            kandidaten: list[datetime] = []
            if wp.stay_duration:
                kandidaten.append(stop_arrival + wp.stay_duration)
            if wp.planned_departure:
                kandidaten.append(wp.planned_departure)
            if not kandidaten:
                continue
            kandidat_abfahrt = max(kandidaten)
            if required_departure is None or kandidat_abfahrt > required_departure:
                required_departure = kandidat_abfahrt
                coordinate = wp.coordinate
                charging_power_kw = wp.charging_power_kw

        return required_departure, coordinate, charging_power_kw

    def add_drive_edge(  # noqa: PLR0913, PLR0917 -- Fahrtkanten-Konstruktion braucht den vollen Kantenkontext
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
        station_segments: dict[int, list[tuple[ChargingStation, float]]],
        waypoint_charge_segments: set[int],
        total_segments: int,
        target_soc_target: float,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Add an aggregated driving edge from `seg_idx` to `target_seg_idx`.

        `target_seg_idx` ist der naechste decisionspunkt (charging_station,
        waypoint or ferry boarding) after `seg_idx`, or `len(segments)`
        falls keiner more folgt (siehe `generate_graph`). Zwischen zwei
        decisionspunkten verzweigt der Zustandsgraph nicht - eine einzelne
        driving edge across ALL raw segments in between provides exactly
        dasselbe Ergebnis wie eine edge pro Roh-segment (energy/time sind
        linear additiv, siehe `cum_time_s`/`cum_energy_kwh` in `optimize()`),
        vermeidet aber die sonst bei feingranularen Routen (ein segment pro
        GraphHopper-Polyline-Punktpaar) explodierende Anzahl an
        Zustandsknoten (docs/plans/07-optimization.md, Risiko "Skalierbarkeit
        the NetworkX solution").
        """
        # SoC consumption for the entire subsection (aggregated energy over
        # cum_energy_kwh, siehe optimize()).
        energy_kwh = cum_energy_kwh[target_seg_idx] - cum_energy_kwh[seg_idx]
        verbrauch_pct = charging_math.calc_soc_verbrauch_pct(
            energy_kwh=energy_kwh,
            battery_capacity_kwh=vehicle_profile.battery_capacity_kwh,
        )

        # consumption wird vom KONTINUIERLICHEN SoC des Vorgaengerknotens
        # abgezogen (nicht vom gerundeten Bucket) und erst danach fuer den
        # neuen Knoten wieder gebuckt - siehe docs/plans/07-optimization.md
        # (Regressionstest: SoC-Quantisierung bei feingranularen segmenten).
        current_soc_pct = G.nodes[current]["soc_pct"]
        new_soc_pct = current_soc_pct - verbrauch_pct

        # Reichweite reicht nicht (SoC unter 0%) - eine unzulaessige edge wie
        # jede andere Unterschreitung der geltenden Sicherheitsreserve.
        # Fuehrt die Fahrtkante zum eigentlichen FAHRTZIEL
        # (`target_seg_idx == total_segments`), gilt dort `target_soc_target`
        # (bereits um `sicherheitsreserve_pct` bereinigtes Ziel-SoC, siehe
        # `optimize()`) statt des allgemeinen `min_soc_pct` - die Fahrt endet
        # hier, ein zusaetzliches Offene-segment-Sicherheitsminimum ist nicht
        # einschlaegig (sonst kann ein vom Nutzer bewusst low gewaehltes
        # Ziel-SoC, z. B. 5%, nie erreicht werden, siehe Nutzer-Report: Ziel-
        # SoC 5% gesetzt, Ankunft trotzdem bei 27%). Fuehrt sie stattdessen zu
        # einer charging_station (`target_seg_idx in station_segments`) oder einem
        # ladefaehigen Zwischenstopp (`target_seg_idx in
        # waypoint_charge_segments`, siehe `generate_graph`), gilt dort
        # ebenfalls NICHT das allgemeine `min_soc_pct`, sondern das
        # niedrigere `min_arrival_soc_pct` - dort wird ja garantiert
        # nachgeladen, ein frueheres/hoeheres Pflicht-Minimum wuerde nur
        # unnoetig fruehes (und damit langsameres) Laden erzwingen (siehe
        # `OptimizationConstraints.min_arrival_soc_pct`).
        if target_seg_idx == total_segments:
            mindest_soc_pct = target_soc_target
        elif target_seg_idx in station_segments or target_seg_idx in waypoint_charge_segments:
            mindest_soc_pct = constraints.min_arrival_soc_pct
        else:
            mindest_soc_pct = constraints.min_soc_pct
        if new_soc_pct < 0.0 or new_soc_pct < mindest_soc_pct:
            return  # Unzulaessig
        new_soc_bucket = soc_to_bucket(new_soc_pct, self.soc_step_pct)

        # drive_time_s fuer die gesamte Teilstrecke: aggregierte, aus der
        # ACTUALN je-segment-speed ermittelte drive_time_s
        # (`cum_time_s`, siehe `optimize()`/`segmentEnergyResult.drive_time_s`) -
        # NICHT aus einer pauschalen Durchschnittsgeschwindigkeit. Der neue
        # time-Bucket wird aus der ACTUALN kumulierten time des
        # Vorgaengerknotens abgeleitet (nicht inkrementell aus dem bereits
        # gerundeten Bucket), sonst ginge bei kurzen Teilstrecken drive_time_s
        # durch Rundung verloren.
        drive_time_s = cum_time_s[target_seg_idx] - cum_time_s[seg_idx]
        neuer_zeitpunkt = G.nodes[current]["timestamp"] + timedelta(seconds=drive_time_s)
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self.base_time, self.time_step_min)

        if new_time_bucket > max_time_buckets:
            return  # Time limit exceeded

        # Kosten berechnen
        kosten = drive_time_s  # Nur drive_time_s, keine Ladezeit

        next_node = (target_seg_idx, new_soc_bucket, new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="drive",
                soc_pct=new_soc_pct,
                timestamp=neuer_zeitpunkt,
                segment_index=target_seg_idx,
                stop_arrival=neuer_zeitpunkt,
                total_cost=COST_INF,
                parent=None,
            )

        # add edge with cost
        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(current, next_node, cost=kosten)
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["timestamp"] = neuer_zeitpunkt
            G.nodes[next_node]["stop_arrival"] = neuer_zeitpunkt
            # `soc_pct` MUSS bei jeder guenstigeren edge aktualisiert werden
            # (nicht nur beim allerersten Anlegen des Knotens) - sonst kann
            # ein Knoten-Schluessel `(segment_index, soc_bucket, time_bucket)`,
            # der zuerst durch eine ANDERE (spaeter verworfene) edge angelegt
            # wurde, einen veralteten SoC-Wert behalten, obwohl die tatsaechlich
            # gewaehlte edge einen anderen kontinuierlichen SoC erreicht (siehe
            # `TestLadehaltUeberlebtKnotenKollision` in test_optimization.py).
            G.nodes[next_node]["soc_pct"] = new_soc_pct
            self.schedule(heap, next_node, new_total_cost)
        else:
            logger.debug(
                "Drive edge %d->%d SHADOWED by existing node %s: "
                "new_total_cost=%.1fs >= existing total_cost=%.1fs (new_soc=%.2f%%)",
                seg_idx,
                target_seg_idx,
                next_node,
                new_total_cost,
                G.nodes[next_node].get("total_cost", COST_INF),
                new_soc_pct,
            )

    def add_ferry_edge(
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        pin: tuple[int, datetime, datetime],
        max_time_buckets: int,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Adds an edge for a user-initiated ferry crossing.

        models a user-initiated ferry crossing (fixed departure/
        arrival_time, `optimize()`-Parameter `ferry_time_windows`) in EINEM Sprung
        from `current` to the segment after the ferry - instead of the otherwise per segment
        generated `add_drive_edge` edges for the in-between
        ferry segments (see `generate_graph`). No SoC consumption (engine off
        during crossing) - only wait time until departure plus the
        crossing duration as cost, analogous to `add_waypoint_wait_edge`s
        "kein SoC-Verlust"-Ansatz. `pin` ist
        `(segment_index_end, departure, arrival)`, wobei `segment_index_end` das
        first segment AFTER the Faehre ist (siehe
        `tripplanner.routing.models.Ferrysegment.segment_index_end`).
        """
        segment_index_end, departure, arrival = pin
        current_zeitpunkt = G.nodes[current]["timestamp"]

        if current_zeitpunkt > departure:
            return  # Ferry already departed at this timestamp - path invalid

        neuer_zeitpunkt = arrival
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self.base_time, self.time_step_min)
        if new_time_bucket > max_time_buckets:
            return  # Time limit exceeded

        wartezeit_s = (departure - current_zeitpunkt).total_seconds()
        ueberfahrt_s = (arrival - departure).total_seconds()
        kosten = wartezeit_s + ueberfahrt_s

        # SoC-Bucket unveraendert (kein consumption waehrend der Ueberfahrt)
        next_node = (segment_index_end, current[1], new_time_bucket)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="ferry",
                soc_pct=G.nodes[current]["soc_pct"],
                timestamp=neuer_zeitpunkt,
                segment_index=segment_index_end,
                stop_arrival=neuer_zeitpunkt,
                total_cost=COST_INF,
                parent=None,
            )

        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(current, next_node, cost=kosten)
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["timestamp"] = neuer_zeitpunkt
            G.nodes[next_node]["soc_pct"] = G.nodes[current]["soc_pct"]
            G.nodes[next_node]["stop_arrival"] = neuer_zeitpunkt
            self.schedule(heap, next_node, new_total_cost)

    def add_charging_edges(  # noqa: PLR0913, PLR0917 -- Ladekanten-Konstruktion braucht den vollen Kantenkontext
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
        charging_duration_specifications: dict[str, int],
        checkpoints: list[int],
        station_segments: dict[int, list[tuple[ChargingStation, float]]],
        waypoint_charge_segments: set[int],
        cum_energy_kwh: list[float],
        target_soc_target: float,
        detour_kosten: dict[str, DetourKosten] | None = None,
    ) -> None:
        """Füge charging_edgen zu allen Stationen in diesem segment hinzu.

        `stations` enthaelt je Station auch deren Luftlinien-Abstand (Meter)
        zum naechstgelegenen Routenpunkt (siehe `map_stations_to_segments`).
        Stationen, die nicht direkt AUF der Route liegen (der Regelfall - der
        Suchradius `search_radius_km` in `trip_input/api.py` allowed bewusst
        Kandidaten mehrere Kilometer abseits der Route), erfordern einen
        Hin- und return-Abstecher. Dessen time-/Energiekosten werden über
        `detour_kosten` geestimates und der charging_edge aufgeschlagen - ohne
        das würde die Optimierung eine weit abseits liegende, aber
        geografisch zufaellig dem "billigsten" segment zugeordnete Station als
        KOSTENLOS erreichbar behandeln und z. B. einen 90-minütigen Abstecher
        nur fürs Laden waehlen, obwohl eine naehere Station denselben SoC-
        Bedarf gedeckt haette (siehe Nutzer-Report: Jönköping -> Ödeshög und
        zurück statt direkt in Jönköping/Mariestad zu laden).

        Für Stationen mit einer vom Nutzer vorgegebenen festen charge_duration
        (`charging_duration_specifications`, Schlüssel = `station_id`) wird EXACTLY ONE edge
        mit dieser duration erzeugt (resultierender SoC per Bisektion über die
        charging_curve ermittelt, siehe `soc_nach_fester_ladezeit`) statt der
        sonstigen SoC target iteration - die Vorgabe ist eine explizite
        Nutzer-decision und daher auch nicht durch
        `constraints.max_ladezeit_s` begrenzt (analog zur ungedeckelten
        Wartezeit in `add_waypoint_wait_edge`).
        """
        current_soc_pct = G.nodes[current]["soc_pct"]

        for station, offroute_distance_m in stations:
            detour_ergebnis = detour_costs.detour_kosten(
                station_id=station.station_id,
                offroute_distance_m=offroute_distance_m,
                vehicle_profile=vehicle_profile,
                detour_kosten=detour_kosten,
                avg_consumption_kwh_per_m=self.avg_consumption_kwh_per_m,
            )
            hinweg_zeit_s, hinweg_soc_pct, rueckweg_zeit_s, rueckweg_soc_pct = detour_ergebnis
            arrival_soc_pct = current_soc_pct - hinweg_soc_pct
            # Untergrenze `min_arrival_soc_pct` gilt fuer den
            # ACTUALN SoC AN der Station, nicht nur fuer den
            # On-Route-SoC am Checkpoint vor dem Abstecher: eine abseits der
            # Route liegende Station (siehe `detour_kosten`) kostet
            # zusaetzliche Reichweite fuer den outbound dorthin - ohne diesen
            # Check wuerde `add_drive_edge`s Floor-Pruefung (die nur den
            # On-Route-SoC kennt) durch den anschliessenden Abstecher
            # unterlaufen und ein Ladehalt mit SoC UNTER der vom Nutzer
            # gesetzten Sicherheitsreserve entstehen (siehe Regressionstest
            # `test_create_trip_simulation_mindest_ankunfts_soc_pct_allowed_niedrigere_ladezeit`).
            # Uses bewusst NUR den outbound-Anteil (nicht einen gemittelten
            # Hin-/return-Wert, siehe `DetourKosten`-Docstring): der
            # return ist fuer die Ankunft AN der Station irrelevant und ein
            # gemitteltes "je Richtung"-SoC wuerde eine Station mit kurzem
            # outbound aber langem return faelschlich unter die
            # Sicherheitsreserve druecken.
            if arrival_soc_pct < constraints.min_arrival_soc_pct:
                logger.debug(
                    "Charging candidate %s (%s) rejected at segment %d: arrival SoC %.2f%% "
                    "(on-route %.2f%% - hinweg %.2f%%) < min_arrival_soc_pct %.2f%% "
                    "(hinweg=%.1fs/%.2f%%, rueckweg=%.1fs/%.2f%%, offroute=%.0fm)",
                    station.station_id,
                    station.name,
                    seg_idx,
                    arrival_soc_pct,
                    current_soc_pct,
                    hinweg_soc_pct,
                    constraints.min_arrival_soc_pct,
                    hinweg_zeit_s,
                    hinweg_soc_pct,
                    rueckweg_zeit_s,
                    rueckweg_soc_pct,
                    offroute_distance_m,
                )
                continue  # Reichweite reicht nicht bis zur Station UEBER der Sicherheitsreserve

            logger.debug(
                "Charging candidate %s (%s) considered at segment %d: arrival SoC %.2f%% "
                "(hinweg=%.1fs/%.2f%%, rueckweg=%.1fs/%.2f%%, offroute=%.0fm)",
                station.station_id,
                station.name,
                seg_idx,
                arrival_soc_pct,
                hinweg_zeit_s,
                hinweg_soc_pct,
                rueckweg_zeit_s,
                rueckweg_soc_pct,
                offroute_distance_m,
            )

            vorgabe_s = charging_duration_specifications.get(station.station_id)
            if vorgabe_s is not None:
                ziel_soc = charging_math.soc_nach_fester_ladezeit(
                    start_soc_pct=arrival_soc_pct,
                    ladezeit_s=float(vorgabe_s),
                    ladekurve=ladekurve,
                    battery_capacity_kwh=vehicle_profile.battery_capacity_kwh,
                )
                self.fuege_ladekante_hinzu(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    station=station,
                    arrival_soc_pct=arrival_soc_pct,
                    target_soc_pct=ziel_soc,
                    ladezeit_s=float(vorgabe_s),
                    hinweg_zeit_s=hinweg_zeit_s,
                    rueckweg_zeit_s=rueckweg_zeit_s,
                    rueckweg_soc_pct=rueckweg_soc_pct,
                    max_time_buckets=max_time_buckets,
                    heap=heap,
                )
                continue

            target_soc_candidates = charging_math.charging_target_candidates(
                arrival_soc_pct=arrival_soc_pct,
                seg_idx=seg_idx,
                checkpoints=checkpoints,
                station_segments=station_segments,
                waypoint_charge_segments=waypoint_charge_segments,
                cum_energy_kwh=cum_energy_kwh,
                total_segments=len(segments),
                vehicle_profile=vehicle_profile,
                constraints=constraints,
                ladekurve=ladekurve,
                target_soc_target=target_soc_target,
            )
            target_soc_candidates = charging_math.candidates_with_min_charge_duration(
                kandidaten=target_soc_candidates,
                arrival_soc_pct=arrival_soc_pct,
                ladekurve=ladekurve,
                battery_capacity_kwh=vehicle_profile.battery_capacity_kwh,
                min_charging_time_s=float(constraints.min_charging_time_s),
                max_charge_soc_pct=min(MAX_SOC_PCT, constraints.max_charge_soc_pct),
            )
            for Ziel_soc in target_soc_candidates:
                if Ziel_soc <= arrival_soc_pct:
                    continue  # Already higher than target

                # charge_time berechnen (echtes Start-/End-SoC window, siehe
                # `calc_ladezeit_s`)
                ladezeit_s = charging_math.calc_ladezeit_s(
                    start_soc_pct=arrival_soc_pct,
                    end_soc_pct=Ziel_soc,
                    ladekurve=ladekurve,
                    battery_capacity_kwh=vehicle_profile.battery_capacity_kwh,
                )

                if ladezeit_s > constraints.max_ladezeit_s:
                    continue  # Zu lange Ladezeit

                self.fuege_ladekante_hinzu(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    station=station,
                    arrival_soc_pct=arrival_soc_pct,
                    target_soc_pct=Ziel_soc,
                    ladezeit_s=ladezeit_s,
                    hinweg_zeit_s=hinweg_zeit_s,
                    rueckweg_zeit_s=rueckweg_zeit_s,
                    rueckweg_soc_pct=rueckweg_soc_pct,
                    max_time_buckets=max_time_buckets,
                    heap=heap,
                )

    def fuege_ladekante_hinzu(  # noqa: PLR0913, PLR0917 -- Ladekanten-Buchhaltung braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        station: ChargingStation,
        arrival_soc_pct: float,
        target_soc_pct: float,
        ladezeit_s: float,
        hinweg_zeit_s: float,
        rueckweg_zeit_s: float,
        rueckweg_soc_pct: float,
        max_time_buckets: int,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Fügt eine charging_edge hinzu (Knoten-/edges-/Kosten-Buchhaltung).

        Erzeugt (falls günstiger als ein bestehender Pfad) eine charging_edge von
        `current` zu einem Knoten, der wieder AUF der Route liegt (derselbe
        `seg_idx`) - dazwischen liegen outbound-Abstecher (`hinweg_zeit_s`, der
        SoC consumption dafuer steckt bereits in `arrival_soc_pct`, siehe
        `add_charging_edges`), die eigentliche Ladung (`arrival_soc_pct` ->
        `target_soc_pct` in `ladezeit_s`) und der return-Abstecher
        (`rueckweg_zeit_s`/`rueckweg_soc_pct`, siehe `detour_kosten`). Der
        neue Knoten-SoC ist daher `target_soc_pct` MINUS den return-consumption,
        nicht `target_soc_pct` selbst - ein Ladehalt abseits der Route "kostet"
        auch auf dem return noch Reichweite. Hin- und return werden bewusst
        NICHT gemittelt (siehe `DetourKosten`-Docstring): beide Legs koennen
        real unterschiedlich long sein, und jede Seite braucht ihren
        EIGENEN, nicht symmetrisierten Wert, sonst kann eine Station mit
        kurzem outbound aber langem return (oder umgekehrt) faelschlich als
        nicht erreichbar/nicht rueckfuehrbar verworfen werden, obwohl sie es
        real ist. `arrival_soc_pct`/`target_soc_pct` (Zustand AN der Station)
        werden zusaetzlich als edges-Attribute hinterlegt, damit
        `extract_charging_stops` den tatsaechlichen Lade-Ablauf (nicht den um
        die Abstecher-Fahrt verfaelschten route SoC) berichten kann -
        gemeinsame Buchhaltung für sowohl die automatische SoC target iteration
        als auch eine vom Nutzer vorgegebene feste charge_duration (siehe
        `add_charging_edges`).
        """
        route_soc_pct = target_soc_pct - rueckweg_soc_pct
        if route_soc_pct < 0.0:
            return  # Range not sufficient for the return to the route
        new_soc_bucket = soc_to_bucket(route_soc_pct, self.soc_step_pct)

        arrival_time = G.nodes[current]["timestamp"] + timedelta(seconds=hinweg_zeit_s)
        departure_time = arrival_time + timedelta(seconds=ladezeit_s)
        neuer_zeitpunkt = departure_time + timedelta(seconds=rueckweg_zeit_s)
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self.base_time, self.time_step_min)

        if new_time_bucket > max_time_buckets:
            return  # Time limit exceeded

        # Kosten: charge_time PLUS Hin-/return-drive_time_s des Abstechers (0 für
        # Stationen direkt auf der Route) PLUS verschwindend kleiner
        # Tie-Breaker zugunsten des sparsameren Ladeziels (siehe
        # `LADE_TIEBREAK_S_PRO_PROZENTPUNKT`).
        kosten = (
            ladezeit_s
            + hinweg_zeit_s
            + rueckweg_zeit_s
            + LADE_TIEBREAK_S_PRO_PROZENTPUNKT * (target_soc_pct - arrival_soc_pct)
        )
        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten
        logger.debug(
            "Charging edge %s (%s) at segment %d: %.2f%% -> %.2f%% in %.1fs "
            "(hinweg=%.1fs, rueckweg=%.1fs, edge kosten=%.1fs, predecessor total_cost=%.1fs, "
            "resulting total_cost=%.1fs)",
            station.station_id,
            station.name,
            seg_idx,
            arrival_soc_pct,
            target_soc_pct,
            ladezeit_s,
            hinweg_zeit_s,
            rueckweg_zeit_s,
            kosten,
            current_cost,
            new_total_cost,
        )
        next_node = (seg_idx, new_soc_bucket, new_time_bucket)

        stop_arrival = G.nodes[current].get("stop_arrival", G.nodes[current]["timestamp"])
        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="charge",
                station_id=station.station_id,
                soc_pct=route_soc_pct,
                timestamp=neuer_zeitpunkt,
                segment_index=seg_idx,
                stop_arrival=stop_arrival,
                total_cost=COST_INF,
                parent=None,
            )

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            # `station_id` als EDGE-Attribut (nicht nur Node-Attribut) setzen:
            # ein Knoten-Schluessel `(segment_index, soc_bucket, time_bucket)`
            # kann durch Diskretisierung mit einer ANDEREN Fahrt-/FerryEdge
            # kollidieren, die denselben Knoten frueher bereits (mit
            # `type="drive"`, ohne `station_id`) angelegt hat - der Knoten
            # selbst wird dann NICHT erneut mit `type="charge"`/`station_id`
            # initialisiert (siehe `if next_node not in G.nodes` oben). Die
            # tatsaechlich im Pfad gewaehlte edge (`prev_node -> curr_node`)
            # ist aber immer eindeutig - `extract_charging_stops` liest den
            # Ladehalt daher von der KANTE, nicht vom Knoten (sonst wird der
            # Ladehalt bei einer solchen Kollision aus dem Ergebnis verschluckt,
            # obwohl seine Kosten/time sehr wohl im Pfad stecken - sichtbar als
            # Diskrepanz zwischen `gesamtreisezeit_s` und der Summe der
            # tatsaechlich zurueckgegebenen `ChargingStop`-Ladedauern).
            G.add_edge(
                current,
                next_node,
                cost=kosten,
                station_id=station.station_id,
                arrival_soc_pct=arrival_soc_pct,
                target_soc_pct=target_soc_pct,
                ladezeit_s=ladezeit_s,
                arrival_time=arrival_time,
                departure_time=departure_time,
            )
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["timestamp"] = neuer_zeitpunkt
            G.nodes[next_node]["soc_pct"] = route_soc_pct
            G.nodes[next_node]["stop_arrival"] = stop_arrival
            self.schedule(heap, next_node, new_total_cost)
        else:
            # Diagnostik: diese edge wurde BERECHNET, verliert aber gegen
            # einen bereits existierenden guenstigeren Knoten am selben
            # (segment_index, soc_bucket, time_bucket)-Schluessel - sie wird
            # NIE Teil von G und kann daher auch nie auf dem gewaehlten Pfad
            # landen, selbst wenn sie fuer sich genommen die bessere Option
            # waere (siehe Nutzer-Report: Ladehalt-Auswahl bevorzugt eine
            # weiter entfernte Station).
            logger.debug(
                "Charging edge %s (%s) at segment %d SHADOWED by existing node %s: "
                "new_total_cost=%.1fs >= existing total_cost=%.1fs",
                station.station_id,
                station.name,
                seg_idx,
                next_node,
                new_total_cost,
                G.nodes[next_node].get("total_cost", COST_INF),
            )

    def add_waypoint_wait_edge(  # noqa: PLR0913, PLR0917 -- Wartekanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        required_departure: datetime,
        coordinate: tuple[float, float],
        charging_power_kw: float | None,
        ladekurve: ChargingCurve,
        vehicle_profile: VehicleProfile,
        max_time_buckets: int,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Add edge to wait at a waypoint until `required_departure`.

        `required_departure` ist der bereits fertig aufgeloeste, absolute
        Mindestabfahrtszeitpunkt (siehe `generate_graph`, kombiniert aus
        `Waypoint.stay_duration`/`planned_departure`) - diese edge wird nur
        erzeugt, wenn er noch nicht erreicht ist. Optional wird waehrend der
        Wartezeit ueber eine vor Ort verfuegbare charging_power
        (`charging_power_kw`) geladen: der resultierende SoC wird per Bisektion
        (`soc_nach_fester_ladezeit`, mit `charging_power_kw` als Leistungs-
        deckel gegenueber der vehicle-charging_curve) fuer die FESTE Wartedauer
        ermittelt - die Wartezeit selbst ist durch `required_departure`
        vorgegeben und wird durch das Laden weder verlaengert noch verkuerzt.
        """
        current_zeitpunkt = G.nodes[current]["timestamp"]
        wait_time_s = (required_departure - current_zeitpunkt).total_seconds()
        if wait_time_s <= 0.0:
            return

        new_time_bucket = time_to_bucket(required_departure, self.base_time, self.time_step_min)
        if new_time_bucket > max_time_buckets:
            return  # Time limit exceeded

        current_soc_pct = G.nodes[current]["soc_pct"]
        new_soc_pct = current_soc_pct
        if charging_power_kw is not None and charging_power_kw > 0.0:
            new_soc_pct = charging_math.soc_nach_fester_ladezeit(
                start_soc_pct=current_soc_pct,
                ladezeit_s=wait_time_s,
                ladekurve=ladekurve,
                battery_capacity_kwh=vehicle_profile.battery_capacity_kwh,
                leistungsdeckel_kw=charging_power_kw,
            )
        new_soc_bucket = soc_to_bucket(new_soc_pct, self.soc_step_pct)

        # Kosten: Nur Wartezeit (kein zusaetzlicher Zeitverlust durchs Laden -
        # das Laden laeuft waehrend der ohnehin erzwungenen Wartezeit ab).
        kosten = wait_time_s

        next_node = (seg_idx, new_soc_bucket, new_time_bucket)
        stop_arrival = G.nodes[current].get("stop_arrival", current_zeitpunkt)

        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="waypoint_wait",
                soc_pct=new_soc_pct,
                timestamp=required_departure,
                segment_index=seg_idx,
                stop_arrival=stop_arrival,
                total_cost=COST_INF,
                parent=None,
            )

        current_cost = G.nodes[current].get("total_cost", 0.0)
        new_total_cost = current_cost + kosten

        if new_total_cost < G.nodes[next_node].get("total_cost", COST_INF):
            G.add_edge(
                current,
                next_node,
                cost=kosten,
                waypoint_koordinate=coordinate,
                waypoint_ankunftszeit=current_zeitpunkt,
                waypoint_abfahrtszeit=required_departure,
                waypoint_ankunfts_soc_pct=current_soc_pct,
                waypoint_ziel_soc_pct=new_soc_pct,
                waypoint_ladeleistung_kw=charging_power_kw,
            )
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["timestamp"] = required_departure
            G.nodes[next_node]["soc_pct"] = new_soc_pct
            G.nodes[next_node]["stop_arrival"] = stop_arrival
            self.schedule(heap, next_node, new_total_cost)
