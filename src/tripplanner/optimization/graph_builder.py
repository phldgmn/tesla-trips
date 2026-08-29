"""State-Graph-Konstruktion für den NetworkX-Optimizer.

`StateGraphBuilder` ist die interne Implementierung der Zustandsgraph-
Erzeugung (Knoten + Kanten des diskretisierten Lade-/Fahr-Problems), die
aus `NetworkXOptimizer` nach `optimizer.py` extrahiert wurde. Er kapselt
sämtlichen Zustand, den die Graph-Konstruktion braucht (`soc_step_pct`,
`time_step_min`, `base_time`, `avg_verbrauch_kwh_pro_m`, `push_seq`-Zähler)
in seinem `__init__` und portiert die acht bisherigen privaten Methoden von
`NetworkXOptimizer` (`_generate_graph`, `_schedule`, `_required_departure`,
`_add_drive_edge`, `_add_ferry_edge`, `_add_charging_edges`,
`_fuege_ladekante_hinzu`, `_add_waypoint_wait_edge`) als eigene
Builder-Methoden.

`StateGraphBuilder` ist ein privates Implementierungsdetail von
`NetworkXOptimizer` und wird NICHT aus `optimization/__init__.py`
re-exportiert.
"""

from __future__ import annotations

import bisect
import heapq
import itertools
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from networkx import DiGraph

from tripplanner.optimization import charging_math, detour_costs
from tripplanner.optimization.discretizer import soc_to_bucket, time_to_bucket

if TYPE_CHECKING:
    from tripplanner.battery.models import ChargingCurve
    from tripplanner.charging_infrastructure.models import ChargingStation
    from tripplanner.optimization.models import DetourKosten, OptimizationConstraints
    from tripplanner.routing.models import RouteSegment
    from tripplanner.trip_input.models import VehicleProfile, Waypoint


# Konstanten für Kostenfunktion (identisch zu `optimizer.py`)
COST_INF: float = 1e9  # Unendlich für unzulässige Kanten
MAX_SOC_PCT: float = 100.0


class StateGraphBuilder:
    """Konstruiert den diskretisierten Zustandsgraphen für die Optimierung."""

    def __init__(
        self,
        soc_step_pct: float,
        time_step_min: int,
        base_time: datetime,
        avg_verbrauch_kwh_pro_m: float,
    ) -> None:
        """Initialisiert den Builder mit dem Graph-Konstruktions-Zustand.

        Args:
            soc_step_pct: Schrittweite für SoC-Diskretisierung in Prozent.
            time_step_min: Schrittweite für Zeit-Diskretisierung in Minuten.
            base_time: Basis-Zeitpunkt für die Zeit-Bucket-Berechnung.
            avg_verbrauch_kwh_pro_m: Durchschnittlicher Verbrauch der Route
                (kWh/m), Basis für die Detour-Kosten-Heuristik.
        """
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self.base_time = base_time
        self.avg_verbrauch_kwh_pro_m = avg_verbrauch_kwh_pro_m
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
        neu initialisiert) dient als Tie-Breaker, damit `heapq` bei gleichen
        Kosten NIEMALS die Knoten-Tupel selbst vergleicht.
        """
        heapq.heappush(heap, (total_cost, next(self.push_seq), node))

    def generate_graph(  # noqa: PLR0913, PLR0917 -- Graph-Konstruktion braucht den vollen Kontext (Route, Energie, Laden, Zwischenstopps, Constraints)
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
        detour_kosten: dict[str, DetourKosten] | None = None,
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
        # zwar noch aktualisiert (siehe Kommentare in `add_drive_edge` etc.),
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
        # Dominanz-Pruning: fuer dieselbe Position+SoC (`seg_idx, soc_bucket`)
        # ist ein SPAETERER Ankunftszeitpunkt bei GLEICHEN oder hoeheren
        # Gesamtkosten NIE von Vorteil - `total_cost` ist in diesem Modell
        # ueberall exakt die seit Abfahrt verstrichene Zeit (jede Kante ist
        # eine Zeitdauer: Fahrzeit/Ladezeit/Wartezeit/Faehr-Wartezeit), und
        # saemtliche Folgekosten (Energieverbrauch, Ladekurve, `max_time_
        # buckets`-Limit, sogar Faehr-Abfahrtsfenster - frueher ankommen
        # heisst dort hoechstens laenger warten, nie eine Faehre verpassen,
        # die ein spaeterer Zustand noch erreicht haette) haengen NUR vom
        # weiterhin identischen SoC und der (monoton) verstrichenen Zeit ab,
        # nie vom Kalenderzeitpunkt selbst. Der erste (Heap-Reihenfolge:
        # guenstigste) besuchte Knoten je `(seg_idx, soc_bucket)` erweitert
        # daher IMMER mindestens so guenstige Folgezustaende wie jeder
        # spaetere - dessen eigene ausgehende Kanten sind somit ueberfluessig
        # und werden uebersprungen. Ohne dieses Pruning haelt der Zustands-
        # graph pro Entscheidungspunkt bis zu O(SoC-Buckets x Zeit-Buckets)
        # tatsaechlich erweiterte Knoten statt O(SoC-Buckets) - bei Routen
        # mit vielen Ladestationen (z. B. lange Auslandsstrecken mit dichtem
        # Schnelllader-Netz) der dominante Faktor fuer eine quadratisch statt
        # linear mit der Stationsanzahl wachsende Laufzeit (siehe Nutzer-
        # Report: > 100s Optimierungszeit).
        dominanz_erweitert: set[tuple[int, int, bool]] = set()
        self.push_seq = itertools.count()
        heap: list[tuple[float, int, tuple[int, int, int]]] = []
        self.schedule(heap, start_node, 0.0)
        G.nodes[start_node]["total_cost"] = 0.0
        G.nodes[start_node]["parent"] = None

        # Sortierte Liste aller Entscheidungspunkte (Ladestation, Zwischenstopp
        # oder Fähr-Einstieg). Zwischen zwei Entscheidungspunkten gibt es im
        # Zustandsgraphen keine Verzweigung - eine Fahrtkante darf die
        # dazwischenliegenden Roh-Segmente daher in EINEM Sprung überspringen
        # (siehe `add_drive_edge`) statt pro Roh-Segment einen eigenen
        # Zustandsknoten zu erzeugen. Das reduziert die Knotenzahl von
        # O(Roh-Segmente x SoC-Buckets x Zeit-Buckets) auf
        # O(Entscheidungspunkte x SoC-Buckets x Zeit-Buckets) - bei
        # feingranularen Routen (tausende Roh-Segmente, wenige Dutzend
        # Ladestationen) der entscheidende Faktor (docs/plans/07-optimization.md).
        checkpoints: list[int] = sorted(set(waypoint_map) | set(station_segments) | set(ferry_pins))

        # Segmente von Zwischenstopps, an denen TATSAECHLICH geladen werden
        # kann (Ladeleistung gesetzt UND eine erzwungene Wartezeit vorliegt,
        # siehe `add_waypoint_wait_edge`/`required_departure` - ohne
        # Wartezeit findet dort kein Ladevorgang statt, siehe
        # `Waypoint.ladeleistung_kw`). Fuer diese Segmente gilt beim Anfahren
        # dieselbe abgesenkte Ankunfts-Untergrenze wie an einer Ladestation
        # (`mindest_ankunfts_soc_pct` statt des allgemeinen
        # `min_soc_pct`-Sicherheitsreserve fuer offene Strecke) - an einer
        # Ladestation UND an einem ladefaehigen Zwischenstopp ist ein
        # niedriger Ankunfts-SoC unbedenklich, weil garantiert nachgeladen
        # wird (siehe Nutzer-Report: Ankunft an einem ladenden Zwischenstopp
        # mit 21% statt der erwarteten ~5%, weil der Fahrt-Kante dorthin
        # faelschlich das Offene-Strecke-Minimum auferlegt wurde).
        waypoint_charge_segments: set[int] = {
            seg_idx
            for seg_idx, wps in waypoint_map.items()
            if any(
                wp.ladeleistung_kw is not None
                and wp.ladeleistung_kw > 0.0
                and (wp.aufenthaltsdauer is not None or wp.geplante_abfahrt is not None)
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
            # `Waypoint.aufenthaltsdauer`/`geplante_abfahrt` abgeleitete)
            # Mindestabfahrtszeit vor, die am aktuellen Knoten noch nicht
            # erreicht ist, MUSS zunaechst gewartet werden - Fahrt-/Faehrkante
            # (Block 2) werden dann NICHT erzeugt, sonst waere die Wartezeit
            # nur ein optionaler, vom A*-Kostenoptimierer als teurer verworfener
            # Zusatzpfad statt einer erzwungenen Mindestaufenthaltsdauer (siehe
            # Nutzer-Report: eine gesetzte Abfahrtszeit an einem Zwischenstopp
            # wurde bei der Ankunftszeit am Ziel ignoriert). Muss VOR dem
            # Dominanz-Check ausgewertet werden, denn `muss_warten` fliesst in
            # dessen Schluessel ein (siehe dort).
            required_departure, wait_koordinate, wait_ladeleistung_kw = self.required_departure(
                G=G, current=current, seg_idx=seg_idx, waypoint_map=waypoint_map
            )
            muss_warten = (
                required_departure is not None
                and G.nodes[current]["zeitpunkt"] < required_departure
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
            # einem Zwischenstopp gesetzte Abfahrtszeit fuehrte sonst zu
            # "Kein erreichbarer Zielknoten gefunden").
            dominanz_key = (seg_idx, soc_bucket, muss_warten)
            if dominanz_key in dominanz_erweitert:
                continue
            dominanz_erweitert.add(dominanz_key)

            # Prüfe, ob Ziel erreicht (alle Segmente abgefahren)
            if seg_idx == len(segments) and soc_bucket >= ziel_soc_bucket:
                continue  # Ziel erreicht, nicht weiter erweitern

            # 2. Fahrtkante: bis zum naechsten Entscheidungspunkt (oder bis
            # zum Ziel, falls keiner mehr folgt) in einem Sprung fahren -
            # ausser der Nutzer hat fuer diese Position einen festen
            # Fährfahrplan vorgegeben (`ferry_pins`), dann wird die gesamte
            # Fähr-Ueberfahrt separat modelliert (siehe `add_ferry_edge`).
            # seg_idx zaehlt bereits abgefahrene Segmente (0 = Start,
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
                        ziel_soc_target=ziel_soc_target,
                        cum_time_s=cum_time_s,
                        cum_energy_kwh=cum_energy_kwh,
                        max_time_buckets=max_time_buckets,
                        constraints=constraints,
                        vehicle_profile=vehicle_profile,
                        station_segments=station_segments,
                        waypoint_charge_segments=waypoint_charge_segments,
                        heap=heap,
                    )

            # 3. Ladekante: An dieser Station laden (wenn verfügbar) - bleibt
            # auch waehrend einer erzwungenen Zwischenstopp-Wartezeit erlaubt
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
                    ladedauer_vorgaben=ladedauer_vorgaben,
                    checkpoints=checkpoints,
                    station_segments=station_segments,
                    waypoint_charge_segments=waypoint_charge_segments,
                    cum_energy_kwh=cum_energy_kwh,
                    ziel_soc_target=ziel_soc_target,
                    detour_kosten=detour_kosten,
                )

            # 4. Zwischenstopp-Zwang: bis zur erforderlichen Abfahrtszeit
            # warten - optional mit Ladung ueber `wait_ladeleistung_kw`.
            if muss_warten and required_departure is not None and wait_koordinate is not None:
                self.add_waypoint_wait_edge(
                    G=G,
                    current=current,
                    seg_idx=seg_idx,
                    required_departure=required_departure,
                    koordinate=wait_koordinate,
                    ladeleistung_kw=wait_ladeleistung_kw,
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

        Kombiniert je Waypoint `aufenthaltsdauer` (relativ zur TATSAECHLICHEN
        Ankunft `stop_arrival`) und `geplante_abfahrt` (absolut) - `stop_arrival`
        ist der Zeitpunkt der TATSAECHLICHEN Ankunft an dieser Position (siehe
        `add_drive_edge`/`add_ferry_edge`), nicht der aktuelle Knoten-
        Zeitpunkt, der bereits eine laufende Ladung/Wartezeit am selben
        `seg_idx` widerspiegeln kann (sonst wuerde eine relative
        `aufenthaltsdauer` bei jeder erneuten Pruefung ab dem NEUEN Zeitpunkt
        nochmals aufgeschlagen und nie konvergieren). Liegen mehrere
        Zwischenstopps auf demselben Segment, gewinnt die spaeteste Abfahrts-
        zeit (deren Koordinate/Ladeleistung wird fuer die Wartekante genutzt).
        """
        if seg_idx not in waypoint_map:
            return None, None, None

        stop_arrival = G.nodes[current].get("stop_arrival", G.nodes[current]["zeitpunkt"])
        required_departure: datetime | None = None
        koordinate: tuple[float, float] | None = None
        ladeleistung_kw: float | None = None
        for wp in waypoint_map[seg_idx]:
            kandidaten: list[datetime] = []
            if wp.aufenthaltsdauer:
                kandidaten.append(stop_arrival + wp.aufenthaltsdauer)
            if wp.geplante_abfahrt:
                kandidaten.append(wp.geplante_abfahrt)
            if not kandidaten:
                continue
            kandidat_abfahrt = max(kandidaten)
            if required_departure is None or kandidat_abfahrt > required_departure:
                required_departure = kandidat_abfahrt
                koordinate = wp.koordinate
                ladeleistung_kw = wp.ladeleistung_kw

        return required_departure, koordinate, ladeleistung_kw

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
        ziel_soc_target: float,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Füge eine aggregierte Fahrtkante von `seg_idx` bis `target_seg_idx` hinzu.

        `target_seg_idx` ist der naechste Entscheidungspunkt (Ladestation,
        Zwischenstopp oder Fähr-Einstieg) nach `seg_idx`, oder `len(segments)`
        falls keiner mehr folgt (siehe `generate_graph`). Zwischen zwei
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
        verbrauch_pct = charging_math.calc_soc_verbrauch_pct(
            energie_kwh=energie_kwh,
            batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
        )

        # Verbrauch wird vom KONTINUIERLICHEN SoC des Vorgaengerknotens
        # abgezogen (nicht vom gerundeten Bucket) und erst danach fuer den
        # neuen Knoten wieder gebuckt - siehe docs/plans/07-optimization.md
        # (Regressionstest: SoC-Quantisierung bei feingranularen Segmenten).
        current_soc_pct = G.nodes[current]["soc_pct"]
        new_soc_pct = current_soc_pct - verbrauch_pct

        # Reichweite reicht nicht (SoC unter 0%) - eine unzulaessige Kante wie
        # jede andere Unterschreitung der geltenden Sicherheitsreserve.
        # Fuehrt die Fahrtkante zum eigentlichen FAHRTZIEL
        # (`target_seg_idx == total_segments`), gilt dort `ziel_soc_target`
        # (bereits um `sicherheitsreserve_pct` bereinigtes Ziel-SoC, siehe
        # `optimize()`) statt des allgemeinen `min_soc_pct` - die Fahrt endet
        # hier, ein zusaetzliches Offene-Strecke-Sicherheitsminimum ist nicht
        # einschlaegig (sonst kann ein vom Nutzer bewusst niedrig gewaehltes
        # Ziel-SoC, z. B. 5%, nie erreicht werden, siehe Nutzer-Report: Ziel-
        # SoC 5% gesetzt, Ankunft trotzdem bei 27%). Fuehrt sie stattdessen zu
        # einer Ladestation (`target_seg_idx in station_segments`) oder einem
        # ladefaehigen Zwischenstopp (`target_seg_idx in
        # waypoint_charge_segments`, siehe `generate_graph`), gilt dort
        # ebenfalls NICHT das allgemeine `min_soc_pct`, sondern das
        # niedrigere `mindest_ankunfts_soc_pct` - dort wird ja garantiert
        # nachgeladen, ein frueheres/hoeheres Pflicht-Minimum wuerde nur
        # unnoetig fruehes (und damit langsameres) Laden erzwingen (siehe
        # `OptimizationConstraints.mindest_ankunfts_soc_pct`).
        if target_seg_idx == total_segments:
            mindest_soc_pct = ziel_soc_target
        elif target_seg_idx in station_segments or target_seg_idx in waypoint_charge_segments:
            mindest_soc_pct = constraints.mindest_ankunfts_soc_pct
        else:
            mindest_soc_pct = constraints.min_soc_pct
        if new_soc_pct < 0.0 or new_soc_pct < mindest_soc_pct:
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
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self.base_time, self.time_step_min)

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
                stop_arrival=neuer_zeitpunkt,
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
            G.nodes[next_node]["stop_arrival"] = neuer_zeitpunkt
            # `soc_pct` MUSS bei jeder guenstigeren Kante aktualisiert werden
            # (nicht nur beim allerersten Anlegen des Knotens) - sonst kann
            # ein Knoten-Schluessel `(segment_index, soc_bucket, time_bucket)`,
            # der zuerst durch eine ANDERE (spaeter verworfene) Kante angelegt
            # wurde, einen veralteten SoC-Wert behalten, obwohl die tatsaechlich
            # gewaehlte Kante einen anderen kontinuierlichen SoC erreicht (siehe
            # `TestLadehaltUeberlebtKnotenKollision` in test_optimization.py).
            G.nodes[next_node]["soc_pct"] = new_soc_pct
            self.schedule(heap, next_node, new_total_cost)

    def add_ferry_edge(
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
        erzeugten `add_drive_edge`-Kanten für die dazwischenliegenden
        Fähr-Segmente (siehe `generate_graph`). Kein SoC-Verbrauch (Motor aus
        während der Überfahrt) - nur Wartezeit bis zur Abfahrt plus die
        Überfahrtsdauer als Kosten, analog zu `add_waypoint_wait_edge`s
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
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self.base_time, self.time_step_min)
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
            G.nodes[next_node]["zeitpunkt"] = neuer_zeitpunkt
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
        ladedauer_vorgaben: dict[str, int],
        checkpoints: list[int],
        station_segments: dict[int, list[tuple[ChargingStation, float]]],
        waypoint_charge_segments: set[int],
        cum_energy_kwh: list[float],
        ziel_soc_target: float,
        detour_kosten: dict[str, DetourKosten] | None = None,
    ) -> None:
        """Füge Ladekanten zu allen Stationen in diesem Segment hinzu.

        `stations` enthält je Station auch deren Luftlinien-Abstand (Meter)
        zum naechstgelegenen Routenpunkt (siehe `map_stations_to_segments`).
        Stationen, die nicht direkt AUF der Route liegen (der Regelfall - der
        Suchradius `search_radius_km` in `trip_input/api.py` erlaubt bewusst
        Kandidaten mehrere Kilometer abseits der Route), erfordern einen
        Hin- und Rückweg-Abstecher. Dessen Zeit-/Energiekosten werden über
        `detour_kosten` geschätzt und der Ladekante aufgeschlagen - ohne
        das würde die Optimierung eine weit abseits liegende, aber
        geografisch zufällig dem "billigsten" Segment zugeordnete Station als
        KOSTENLOS erreichbar behandeln und z. B. einen 90-minütigen Abstecher
        nur fürs Laden waehlen, obwohl eine naehere Station denselben SoC-
        Bedarf gedeckt haette (siehe Nutzer-Report: Jönköping -> Ödeshög und
        zurück statt direkt in Jönköping/Mariestad zu laden).

        Für Stationen mit einer vom Nutzer vorgegebenen festen Ladedauer
        (`ladedauer_vorgaben`, Schlüssel = `station_id`) wird GENAU EINE Kante
        mit dieser Dauer erzeugt (resultierender SoC per Bisektion über die
        Ladekurve ermittelt, siehe `soc_nach_fester_ladezeit`) statt der
        sonstigen SoC-Ziel-Iteration - die Vorgabe ist eine explizite
        Nutzer-Entscheidung und daher auch nicht durch
        `constraints.max_ladezeit_s` begrenzt (analog zur ungedeckelten
        Wartezeit in `add_waypoint_wait_edge`).
        """
        current_soc_pct = G.nodes[current]["soc_pct"]

        for station, offroute_distance_m in stations:
            detour_zeit_s, detour_soc_pct = detour_costs.detour_kosten(
                station_id=station.station_id,
                offroute_distance_m=offroute_distance_m,
                vehicle_profile=vehicle_profile,
                detour_kosten=detour_kosten,
                avg_verbrauch_kwh_pro_m=self.avg_verbrauch_kwh_pro_m,
            )
            ankunft_soc_pct = current_soc_pct - detour_soc_pct
            # Untergrenze `mindest_ankunfts_soc_pct` gilt fuer den
            # TATSAECHLICHEN SoC AN der Station, nicht nur fuer den
            # On-Route-SoC am Checkpoint vor dem Abstecher: eine abseits der
            # Route liegende Station (siehe `detour_kosten`) kostet
            # zusaetzliche Reichweite fuer den Hinweg dorthin - ohne diesen
            # Check wuerde `add_drive_edge`s Floor-Pruefung (die nur den
            # On-Route-SoC kennt) durch den anschliessenden Abstecher
            # unterlaufen und ein Ladehalt mit SoC UNTER der vom Nutzer
            # gesetzten Sicherheitsreserve entstehen (siehe Regressionstest
            # `test_create_trip_simulation_mindest_ankunfts_soc_pct_erlaubt_niedrigere_ladezeit`).
            if ankunft_soc_pct < constraints.mindest_ankunfts_soc_pct:
                continue  # Reichweite reicht nicht bis zur Station UEBER der Sicherheitsreserve

            vorgabe_s = ladedauer_vorgaben.get(station.station_id)
            if vorgabe_s is not None:
                ziel_soc = charging_math.soc_nach_fester_ladezeit(
                    start_soc_pct=ankunft_soc_pct,
                    ladezeit_s=float(vorgabe_s),
                    ladekurve=ladekurve,
                    batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                )
                self.fuege_ladekante_hinzu(
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

            Ziel_soc_values = charging_math.lade_ziel_kandidaten(
                ankunft_soc_pct=ankunft_soc_pct,
                seg_idx=seg_idx,
                checkpoints=checkpoints,
                station_segments=station_segments,
                waypoint_charge_segments=waypoint_charge_segments,
                cum_energy_kwh=cum_energy_kwh,
                total_segments=len(segments),
                vehicle_profile=vehicle_profile,
                constraints=constraints,
                ladekurve=ladekurve,
                ziel_soc_target=ziel_soc_target,
            )
            Ziel_soc_values = charging_math.kandidaten_mit_mindestladedauer(
                kandidaten=Ziel_soc_values,
                ankunft_soc_pct=ankunft_soc_pct,
                ladekurve=ladekurve,
                batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                mindest_ladezeit_s=float(constraints.mindest_ladezeit_s),
                max_lade_soc_pct=min(MAX_SOC_PCT, constraints.max_lade_soc_pct),
            )
            for Ziel_soc in Ziel_soc_values:
                if Ziel_soc <= ankunft_soc_pct:
                    continue  # Bereits höher als Ziel

                # Ladezeit berechnen (echtes Start-/End-SoC-Fenster, siehe
                # `calc_ladezeit_s`)
                ladezeit_s = charging_math.calc_ladezeit_s(
                    start_soc_pct=ankunft_soc_pct,
                    end_soc_pct=Ziel_soc,
                    ladekurve=ladekurve,
                    batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                )

                if ladezeit_s > constraints.max_ladezeit_s:
                    continue  # Zu lange Ladezeit

                self.fuege_ladekante_hinzu(
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

    def fuege_ladekante_hinzu(  # noqa: PLR0913, PLR0917 -- Ladekanten-Buchhaltung braucht den vollen Kantenkontext
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
        `detour_kosten`), die eigentliche Ladung (`ankunfts_soc_pct` ->
        `ziel_soc_pct` in `ladezeit_s`) und der Rückweg-Abstecher. Der neue
        Knoten-SoC ist daher `ziel_soc_pct` MINUS den Rückweg-Verbrauch, nicht
        `ziel_soc_pct` selbst - ein Ladehalt abseits der Route "kostet" auch
        auf dem Rückweg noch Reichweite. `ankunfts_soc_pct`/`ziel_soc_pct`
        (Zustand AN der Station) werden zusätzlich als Kanten-Attribute
        hinterlegt, damit `extract_charging_stops` den tatsächlichen
        Lade-Ablauf (nicht den um die Abstecher-Fahrt verfälschten
        Routen-SoC) berichten kann - gemeinsame Buchhaltung für sowohl die
        automatische SoC-Ziel-Iteration als auch eine vom Nutzer vorgegebene
        feste Ladedauer (siehe `add_charging_edges`).
        """
        route_soc_pct = ziel_soc_pct - detour_soc_pct_je_richtung
        if route_soc_pct < 0.0:
            return  # Reichweite reicht nicht für den Rückweg zur Route
        new_soc_bucket = soc_to_bucket(route_soc_pct, self.soc_step_pct)

        ankunftszeit = G.nodes[current]["zeitpunkt"] + timedelta(seconds=detour_zeit_s_je_richtung)
        abfahrtszeit = ankunftszeit + timedelta(seconds=ladezeit_s)
        neuer_zeitpunkt = abfahrtszeit + timedelta(seconds=detour_zeit_s_je_richtung)
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, self.base_time, self.time_step_min)

        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        # Kosten: Ladezeit PLUS Hin-/Rückweg-Fahrzeit des Abstechers (0 für
        # Stationen direkt auf der Route).
        kosten = ladezeit_s + 2.0 * detour_zeit_s_je_richtung
        next_node = (seg_idx, new_soc_bucket, new_time_bucket)

        stop_arrival = G.nodes[current].get("stop_arrival", G.nodes[current]["zeitpunkt"])
        if next_node not in G.nodes:
            G.add_node(
                next_node,
                type="charge",
                station_id=station.station_id,
                soc_pct=route_soc_pct,
                zeitpunkt=neuer_zeitpunkt,
                segment_index=seg_idx,
                stop_arrival=stop_arrival,
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
            # ist aber immer eindeutig - `extract_charging_stops` liest den
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
            G.nodes[next_node]["stop_arrival"] = stop_arrival
            self.schedule(heap, next_node, new_total_cost)

    def add_waypoint_wait_edge(  # noqa: PLR0913, PLR0917 -- Wartekanten-Konstruktion braucht den vollen Kantenkontext
        self,
        G: DiGraph,
        current: tuple[int, int, int],
        seg_idx: int,
        required_departure: datetime,
        koordinate: tuple[float, float],
        ladeleistung_kw: float | None,
        ladekurve: ChargingCurve,
        vehicle_profile: VehicleProfile,
        max_time_buckets: int,
        heap: list[tuple[float, int, tuple[int, int, int]]],
    ) -> None:
        """Füge Kante hinzu, um bis `required_departure` an einem Zwischenstopp zu warten.

        `required_departure` ist der bereits fertig aufgeloeste, absolute
        Mindestabfahrtszeitpunkt (siehe `generate_graph`, kombiniert aus
        `Waypoint.aufenthaltsdauer`/`geplante_abfahrt`) - diese Kante wird nur
        erzeugt, wenn er noch nicht erreicht ist. Optional wird waehrend der
        Wartezeit ueber eine vor Ort verfuegbare Ladeleistung
        (`ladeleistung_kw`) geladen: der resultierende SoC wird per Bisektion
        (`soc_nach_fester_ladezeit`, mit `ladeleistung_kw` als Leistungs-
        deckel gegenueber der Fahrzeug-Ladekurve) fuer die FESTE Wartedauer
        ermittelt - die Wartezeit selbst ist durch `required_departure`
        vorgegeben und wird durch das Laden weder verlaengert noch verkuerzt.
        """
        current_zeitpunkt = G.nodes[current]["zeitpunkt"]
        wait_time_s = (required_departure - current_zeitpunkt).total_seconds()
        if wait_time_s <= 0.0:
            return

        new_time_bucket = time_to_bucket(required_departure, self.base_time, self.time_step_min)
        if new_time_bucket > max_time_buckets:
            return  # Zeitlimit überschritten

        current_soc_pct = G.nodes[current]["soc_pct"]
        new_soc_pct = current_soc_pct
        if ladeleistung_kw is not None and ladeleistung_kw > 0.0:
            new_soc_pct = charging_math.soc_nach_fester_ladezeit(
                start_soc_pct=current_soc_pct,
                ladezeit_s=wait_time_s,
                ladekurve=ladekurve,
                batteriekapazitaet_kwh=vehicle_profile.batteriekapazitaet_kwh,
                leistungsdeckel_kw=ladeleistung_kw,
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
                zeitpunkt=required_departure,
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
                waypoint_koordinate=koordinate,
                waypoint_ankunftszeit=current_zeitpunkt,
                waypoint_abfahrtszeit=required_departure,
                waypoint_ankunfts_soc_pct=current_soc_pct,
                waypoint_ziel_soc_pct=new_soc_pct,
                waypoint_ladeleistung_kw=ladeleistung_kw,
            )
            G.nodes[next_node]["total_cost"] = new_total_cost
            G.nodes[next_node]["parent"] = current
            G.nodes[next_node]["zeitpunkt"] = required_departure
            G.nodes[next_node]["soc_pct"] = new_soc_pct
            G.nodes[next_node]["stop_arrival"] = stop_arrival
            self.schedule(heap, next_node, new_total_cost)
