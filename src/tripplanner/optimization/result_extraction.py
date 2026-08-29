"""Extraktion von Ergebnisobjekten (ChargingStops, Zwischenstopps) aus einem Pfad."""

from __future__ import annotations

from datetime import datetime

from networkx import DiGraph

from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.optimization.models import (
    ChargingStop,
    OptimizationConstraints,
    ZwischenstoppAufenthalt,
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

        # Ketten-Zusammenfuehrung: Der Zustandsgraph erlaubt MEHRERE
        # aufeinanderfolgende Ladekanten an derselben Station (Knoten
        # eines Ladeziel-Kandidaten teilt denselben `seg_idx` wie der
        # Ausgangsknoten und ist daher selbst wieder Ausgangspunkt fuer
        # `_add_charging_edges`) - das ist GEWOLLT: es erschliesst
        # SoC-Ziele, die aus dem urspruenglichen Ankunfts-SoC keine
        # eigene Kandidaten waeren, aus einem bereits erreichten
        # Zwischen-Ladeziel heraus (siehe `_lade_ziel_kandidaten`).
        # Physisch ist das aber EIN einziger Ladehalt, kein zweiter -
        # aufeinanderfolgende Kanten mit derselben `station_id` werden
        # daher zu EINEM `ChargingStop` zusammengefuehrt: Ankunfts-SoC/
        # -zeit von der ERSTEN Kante der Kette, Ziel-SoC/-zeit von der
        # LETZTEN, Ladedauer als Summe aller Kettenglieder (sonst wuerde
        # ein Teil der tatsaechlich verbrachten Ladezeit im Ergebnis
        # verschwinden, siehe Regressionstest
        # `test_optimierer_findet_das_globale_zeitoptimum_ueber_ladehalte_hinweg`).
        if ladehalte and ladehalte[-1].station.station_id == station.station_id:
            vorheriger = ladehalte[-1]
            ladehalte[-1] = ChargingStop(
                station=station,
                segment_index=curr_node[0],
                ankunfts_soc_pct=vorheriger.ankunfts_soc_pct,
                ziel_soc_pct=edge_data["ziel_soc_pct"],
                geschaetzte_ladedauer_s=vorheriger.geschaetzte_ladedauer_s
                + int(edge_data["ladezeit_s"]),
                ankunftszeit=vorheriger.ankunftszeit,
                abfahrtszeit=edge_data["abfahrtszeit"],
            )
            continue

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


def extract_waypoint_aufenthalte(
    G: DiGraph,
    path: list[tuple[int, int, int]],
) -> list[ZwischenstoppAufenthalt]:
    """Extrahiere `ZwischenstoppAufenthalt`-Objekte aus dem Pfad.

    Analog zu `extract_charging_stops`, aber ueber das EDGE-Attribut
    `waypoint_ankunftszeit` (siehe `_add_waypoint_wait_edge`) statt
    `station_id` - Zwischenstopp-Aufenthalte sind unabhaengig von der
    Supercharger-Stationsinfrastruktur (keine `ChargingStation`, kein
    Detour-/Preis-Handling).
    """
    aufenthalte: list[ZwischenstoppAufenthalt] = []

    for i in range(1, len(path)):
        prev_node = path[i - 1]
        curr_node = path[i]
        edge_data = G.get_edge_data(prev_node, curr_node)
        if edge_data is None or "waypoint_ankunftszeit" not in edge_data:
            continue  # Fahrt-/Faehr-/Ladekante, kein Zwischenstopp-Aufenthalt

        aufenthalte.append(
            ZwischenstoppAufenthalt(
                koordinate=edge_data["waypoint_koordinate"],
                segment_index=curr_node[0],
                ankunftszeit=edge_data["waypoint_ankunftszeit"],
                abfahrtszeit=edge_data["waypoint_abfahrtszeit"],
                ladeleistung_kw=edge_data["waypoint_ladeleistung_kw"],
                ankunfts_soc_pct=edge_data["waypoint_ankunfts_soc_pct"],
                ziel_soc_pct=edge_data["waypoint_ziel_soc_pct"],
            )
        )

    return aufenthalte


def compute_waypoint_times(
    path: list[tuple[int, int, int]],
    waypoints: list[Waypoint],
    waypoint_segment_indices: list[int],
    abfahrtszeit: datetime,
) -> dict[int, datetime]:
    """Berechne Mindestankunftszeit für Zwischenstopps."""
    min_ankunftszeit: dict[int, datetime] = {}

    for wp, seg_idx in zip(waypoints, waypoint_segment_indices, strict=True):
        if seg_idx not in min_ankunftszeit:
            min_ankunftszeit[seg_idx] = abfahrtszeit

        if wp.aufenthaltsdauer:
            # Berechne Ankunftszeit + Aufenthaltsdauer
            current_time = min_ankunftszeit[seg_idx] + wp.aufenthaltsdauer
            min_ankunftszeit[seg_idx] = max(min_ankunftszeit[seg_idx], current_time)

    return min_ankunftszeit
