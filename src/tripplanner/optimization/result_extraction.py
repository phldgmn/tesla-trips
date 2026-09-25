"""Extraktion von Ergebnisobjekten (ChargingStops, Zwischenstopps) aus einem Pfad."""

from __future__ import annotations

from datetime import datetime

from networkx import DiGraph

from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.optimization.models import (
    ChargingStop,
    OptimizationConstraints,
    WaypointDwell,
)
from tripplanner.routing.models import RouteSegment
from tripplanner.trip_input.models import Waypoint


def extract_charging_stops(
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

        # charging_edge ueber das EDGE-Attribut `station_id` erkennen (siehe
        # `_fuege_ladekante_hinzu`) statt ueber segment-Index-Gleichheit +
        # Node-Attribut: der Knoten-Schluessel `(segment_index, soc_bucket,
        # time_bucket)` kann durch Diskretisierung mit einer ANDEREN,
        # bereits frueher angelegten Fahrt-/FerryEdge kollidieren, die
        # keine `station_id` traegt - der Knoten selbst wird dann NICHT
        # erneut mit den charging_edgen-Attributen initialisiert. Die
        # tatsaechlich im Pfad gewaehlte edge ist aber immer eindeutig,
        # daher hier von der KANTE statt vom Knoten lesen (sonst wird der
        # Ladehalt bei einer solchen Kollision aus dem Ergebnis
        # verschluckt, obwohl seine Kosten/time sehr wohl im Pfad stecken).
        edge_data = G.get_edge_data(prev_node, curr_node)
        station_id = edge_data.get("station_id") if edge_data else None
        if station_id is None:
            continue  # Fahrt-/ferry-/Wartekante, keine Ladekante

        # Station ueber die an der edge hinterlegte `station_id`
        # auflösen - NICHT ueber eine erneute geografische Naechste-
        # Station-Suche (`segment.geometrie`-Mittelpunkt): mehrere
        # charging_edgen koennen am selben segment fuer VERSCHIEDENE
        # Stationen existieren (z. B. wenn zwei Stationen auf denselben
        # naechstgelegenen segment-Index abgebildet werden, siehe
        # `_map_stations_to_segments`) - eine geografische new-Suche
        # wuerde dann unabhaengig von der TATSAECHLICH gewaehlten edge
        # immer dieselbe (naechstgelegene) Station zurueckgeben und so
        # z. B. eine gezielt an einer ANDEREN Station vorgegebene feste
        # charge_duration (`charging_duration_specifications`) der falschen Station zuschreiben.
        station = stations_by_id.get(station_id)
        if station is None:
            continue  # Sollte nicht vorkommen (station_id stets gueltig)

        # Ketten-Zusammenfuehrung: Der Zustandsgraph allowed MEHRERE
        # aufeinanderfolgende charging_edgen an derselben Station (Knoten
        # eines Ladeziel-Kandidaten teilt denselben `seg_idx` wie der
        # Ausgangsknoten und ist daher selbst wieder Ausgangspunkt fuer
        # `_add_charging_edges`) - das ist GEWOLLT: es erschliesst
        # SoC-Ziele, die aus dem urspruenglichen Ankunfts-SoC keine
        # eigene Kandidaten waeren, aus einem bereits erreichten
        # Zwischen-Ladeziel heraus (siehe `_charging_target_candidates`).
        # Physisch ist das aber EIN einziger Ladehalt, kein zweiter -
        # aufeinanderfolgende edges mit derselben `station_id` werden
        # daher zu EINEM `ChargingStop` zusammengefuehrt: Ankunfts-SoC/
        # -time von der ERSTEN edge der Kette, Ziel-SoC/-time von der
        # LETZTEN, charge_duration als Summe aller Kettenglieder (sonst wuerde
        # ein Teil der tatsaechlich verbrachten charge_time im Ergebnis
        # verschwinden, siehe Regressionstest
        # `test_optimierer_findet_das_globale_zeitoptimum_ueber_ladehalte_hinweg`).
        if ladehalte and ladehalte[-1].station.station_id == station.station_id:
            vorheriger = ladehalte[-1]
            ladehalte[-1] = ChargingStop(
                station=station,
                segment_index=curr_node[0],
                arrival_soc_pct=vorheriger.arrival_soc_pct,
                target_soc_pct=edge_data["target_soc_pct"],
                geschaetzte_ladedauer_s=vorheriger.geschaetzte_ladedauer_s
                + int(edge_data["ladezeit_s"]),
                arrival_time=vorheriger.arrival_time,
                departure_time=edge_data["departure_time"],
            )
            continue

        # `arrival_soc_pct`/`target_soc_pct`/`ladezeit_s`/`arrival_time`/
        # `departure_time` direkt aus den edges-Attributen lesen (siehe
        # `_fuege_ladekante_hinzu`) statt aus den Knoten-`soc_pct`/
        # `timestamp`-Werten: bei einer Station abseits der Route
        # enthaelt der Knoten-SoC/-timestamp bereits den return-
        # Abstecher (siehe `_fuege_ladekante_hinzu`) - der tatsaechliche
        # charging_process (Ankunft/Abfahrt AN der Station) waere daraus nicht
        # more rekonstruierbar. Fuer eine vom Nutzer per
        # `charging_duration_specifications` fest vorgegebene charge_duration (siehe
        # `_add_charging_edges`) ist das zugleich die exakte, dort
        # hinterlegte duration statt einer angenaeherten Neuberechnung.
        ladehalte.append(
            ChargingStop(
                station=station,
                segment_index=curr_node[0],
                arrival_soc_pct=edge_data["arrival_soc_pct"],
                target_soc_pct=edge_data["target_soc_pct"],
                geschaetzte_ladedauer_s=int(edge_data["ladezeit_s"]),
                arrival_time=edge_data["arrival_time"],
                departure_time=edge_data["departure_time"],
            )
        )

    return ladehalte


def extract_waypoint_aufenthalte(
    G: DiGraph,
    path: list[tuple[int, int, int]],
) -> list[WaypointDwell]:
    """Extrahiere `WaypointDwell`-Objekte aus dem Pfad.

    Analog zu `extract_charging_stops`, aber ueber das EDGE-Attribut
    `waypoint_ankunftszeit` (siehe `_add_waypoint_wait_edge`) statt
    `station_id` - Zwischenstopp-Aufenthalte sind unabhaengig von der
    Supercharger-Stationsinfrastruktur (keine `ChargingStation`, kein
    Detour-/Preis-Handling).
    """
    aufenthalte: list[WaypointDwell] = []

    for i in range(1, len(path)):
        prev_node = path[i - 1]
        curr_node = path[i]
        edge_data = G.get_edge_data(prev_node, curr_node)
        if edge_data is None or "waypoint_ankunftszeit" not in edge_data:
            continue  # Fahrt-/ferry-/Ladekante, kein Zwischenstopp-Aufenthalt

        aufenthalte.append(
            WaypointDwell(
                coordinate=edge_data["waypoint_koordinate"],
                segment_index=curr_node[0],
                arrival_time=edge_data["waypoint_ankunftszeit"],
                departure_time=edge_data["waypoint_abfahrtszeit"],
                charging_power_kw=edge_data["waypoint_ladeleistung_kw"],
                arrival_soc_pct=edge_data["waypoint_ankunfts_soc_pct"],
                target_soc_pct=edge_data["waypoint_ziel_soc_pct"],
            )
        )

    return aufenthalte


def compute_waypoint_times(
    path: list[tuple[int, int, int]],
    waypoints: list[Waypoint],
    waypoint_segment_indices: list[int],
    departure_time: datetime,
) -> dict[int, datetime]:
    """Calculate minimum arrival time for waypoints."""
    min_ankunftszeit: dict[int, datetime] = {}

    for wp, seg_idx in zip(waypoints, waypoint_segment_indices, strict=True):
        if seg_idx not in min_ankunftszeit:
            min_ankunftszeit[seg_idx] = departure_time

        if wp.stay_duration:
            # calculate arrival_time + Aufenthaltsdauer
            current_time = min_ankunftszeit[seg_idx] + wp.stay_duration
            min_ankunftszeit[seg_idx] = max(min_ankunftszeit[seg_idx], current_time)

    return min_ankunftszeit
