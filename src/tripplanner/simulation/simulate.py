"""Simulation der Reise entlang der Route mit Ladeplan.

Das Modul rekonstruiert eine Zeitreihe aus Route, ChargingPlan,
SegmentEnergyResult und WeatherSample.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization.models import ChargingPlan
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.simulation.models import (
    ChargingStopSummary,
    LadehaltDetour,
    SimulationFrame,
    TripSimulationResult,
    TripState,
)

# Konstanten fuer maximale Werte
_MAX_SOC_PCT = 100.0
_MIN_SOC_PCT = 0.0


def _interpolate_position_along_segment(
    segment: RouteSegment,
    start_idx: int,
    end_idx: int,
    progress: float,
) -> tuple[float, float]:
    """Interpoliere Position entlang eines Route-Segments.

    Args:
        segment: Das Route-Segment mit Geometrie.
        start_idx: Index des Startpunktes in segment.geometrie.
        end_idx: Index des Endpunktes in segment.geometrie.
        progress: Fortschritt entlang des Segments (0.0 = start, 1.0 = end).

    Returns:
        Interpolierte Position als (lat, lon) Tuple.
    """
    if start_idx == end_idx or progress <= 0.0:
        return segment.geometrie[start_idx]
    if progress >= 1.0:
        return segment.geometrie[end_idx]

    start_coord = segment.geometrie[start_idx]
    end_coord = segment.geometrie[end_idx]

    lat = start_coord[0] + progress * (end_coord[0] - start_coord[0])
    lon = start_coord[1] + progress * (end_coord[1] - start_coord[1])
    return (lat, lon)


def _find_segment_for_time(
    cumulative_times: list[float],
    total_time_s: float,
    time_s: float,
    route: Route,
) -> tuple[int, float]:
    """Finde das Segment und den Fortschritt fuer eine gegebene, skalierte Fahrzeit.

    Sucht ueber die kumulierte Segment-FAHRZEIT (`cumulative_times`, aus
    `SegmentEnergyResult.fahrzeit_s`) statt ueber die kumulierte Distanz -
    damit Position, Segment-Index und
    SoC-Baseline der TATSAECHLICHEN, je Segment unterschiedlichen
    Geschwindigkeit folgen statt einer einzigen Durchschnittsgeschwindigkeit
    ueber die gesamte Reise. Siehe `simulate_trip`: ohne dies "hinkt" die
    Positions-/SoC-Schaetzung nach einem Ladehalt mehrere Frames lang der
    tatsaechlichen Segment-Grenze (`Ladehalt.segment_index`) hinterher, sobald
    die lokale Geschwindigkeit von der Reise-Durchschnittsgeschwindigkeit
    abweicht (z. B. langsamere Zufahrt zur Ladestation) - sichtbar als
    kurzzeitig falscher (zu niedriger) SoC direkt nach dem Ladehalt.

    Args:
        cumulative_times: Kumulierte Fahrzeit (s) bis zum Ende jedes Segments,
            bereits so skaliert, dass `cumulative_times[-1] == total_time_s`.
        total_time_s: Gesamte reine Fahrzeit der Reise in Sekunden.
        time_s: Gewuenschte, bereits um Ladezeit bereinigte Fahrzeit seit
            Reisebeginn.
        route: Die Route.

    Returns:
        Tuple von (segment_index, progress_in_segment).
    """
    if time_s <= 0:
        return (0, 0.0)
    if time_s >= total_time_s:
        return (len(route.segments) - 1, 1.0)

    segment_index = 0
    for i, cum_time in enumerate(cumulative_times):
        if time_s <= cum_time:
            segment_index = i
            break

    prev_cum_time = 0.0 if segment_index == 0 else cumulative_times[segment_index - 1]
    segment_start_s = prev_cum_time
    segment_end_s = cumulative_times[segment_index]
    segment_time_s = segment_end_s - segment_start_s

    time_in_segment = time_s - segment_start_s
    progress_in_segment = time_in_segment / segment_time_s if segment_time_s > 0 else 0.0

    return (segment_index, progress_in_segment)


def simulate_trip(  # noqa: PLR0913, PLR0917, PLR0912, PLR0915
    route: Route,
    charging_plan: ChargingPlan,
    segment_energy: list[SegmentEnergyResult],
    start_soc_pct: float,
    abfahrtszeit: datetime,
    output_resolution_seconds: int = 60,
    battery_capacity_kwh: float = 62.5,
    charging_stop_detours: dict[int, LadehaltDetour] | None = None,
) -> TripSimulationResult:
    """Simuliert die komplette Reise entlang der Route unter Beruecksichtigung des Ladeplans.

    Args:
        route: Route mit Segmente (feste Geometrie nach GraphHopper)
        charging_plan: Optimierter Ladeplan aus optimization.Modul
        segment_energy: Energiebedarf je Segment
        start_soc_pct: Start-SoC in %
        output_resolution_seconds: Zeitauflösung der Ausgabe (default: 60s)
        abfahrtszeit: Abfahrtszeitpunkt der Reise (timezone-aware datetime)
        battery_capacity_kwh: Nutzbare Batteriekapazitaet in kWh (default: 62.5 kWh)
        charging_stop_detours: Optionales, ueber GraphHopper geroutetes Detour-Ergebnis
            je Ladehalt (Schluessel: `id()` des `ChargingStop`-Objekts aus
            `charging_plan.ladehalte`), fuer eine strassengetreue Kartendarstellung
            des Abstechers zur Ladestation (siehe
            `tripplanner.trip_input.api._step_route_charging_detours`). Fehlt ein
            Eintrag, bleibt `ChargingStopSummary.detour_geometrie` leer.

    Returns:
        TripSimulationResult: Zeitreihe aus Frames (Zeit, Position, SoC, Zustand, Geschwindigkeit)

    Raises:
        ValueError: Wenn SoC-Werte ungueltig sind oder keine passenden Energy-Ergebnisse vorliegen.
    """
    if not (_MIN_SOC_PCT <= start_soc_pct <= _MAX_SOC_PCT):
        raise ValueError("Start-SoC muss zwischen 0% und 100% liegen")

    if not route.segments:
        raise ValueError("Route muss mindestens ein Segment enthalten")

    energy_map: dict[int, SegmentEnergyResult] = {}
    for energy in segment_energy:
        if energy.segment_index in energy_map:
            raise ValueError(f"Doppelter segment_index in segment_energy: {energy.segment_index}")
        energy_map[energy.segment_index] = energy

    for seg_idx in range(len(route.segments)):
        if seg_idx not in energy_map:
            raise ValueError(f"Kein Energy-Ergebnis fuer Segment {seg_idx}")

    cumulative_distances: list[float] = []
    total_distance_m = 0.0
    for segment in route.segments:
        total_distance_m += segment.laenge_m
        cumulative_distances.append(total_distance_m)

    # Praefix-Summe der Segment-Energie fuer O(1) "Energie seit Segment X"
    # Lookups statt O(n) Neuaufsummierung pro Frame (`energy_prefix[i]` =
    # kumulierter Energiebedarf der Segmente [0, i)).
    energy_prefix: list[float] = [0.0] * (len(route.segments) + 1)
    for i in range(len(route.segments)):
        energy_prefix[i + 1] = energy_prefix[i] + energy_map[i].energiebedarf_kwh

    # Ladehalte nach Segment-Index sortiert, um beim Durchlauf der FAHREN-
    # Frames den zuletzt ABGESCHLOSSENEN Ladehalt als SoC-Baseline zu finden
    # (siehe unten).
    ladehalte_sortiert = sorted(charging_plan.ladehalte, key=lambda lh: lh.segment_index)

    current_soc_pct = start_soc_pct
    frames: list[SimulationFrame] = []
    # Kumulierte Routendistanz, an der jeder Ladehalt beginnt (Schluessel:
    # `id()` des `ChargingStop`-Objekts) - erfasst beim ersten LADEN-Frame
    # dieses Halts, siehe unten. Fuer `ChargingStopSummary.distanz_m` und um
    # `ladehalt_detour_geometrie` an der richtigen Stelle in die Karten-
    # Geometrie einzufuegen (siehe `route-line.ts` im Frontend).
    distanz_bei_ladehalt: dict[int, float] = {}

    end_time_s = charging_plan.gesamtreisezeit_s if charging_plan.gesamtreisezeit_s > 0 else 1

    current_time_s = 0.0
    ms_to_kmh = 3.6

    # Verwende uebergebene Abfahrtszeit als Basis
    base_time = abfahrtszeit

    # Ladehalte mit relativen (Sekunden-seit-Abfahrt) Ankunfts-/Abfahrtszeiten
    # vorab aufbereiten. Wird sowohl zur Bestimmung des aktuellen Ladehalts
    # als auch zur Umrechnung von "Gesamtzeit" in "reine Fahrzeit" gebraucht
    # (siehe unten).
    ladehalte_mit_relzeit = [
        (
            (lh.ankunftszeit - base_time).total_seconds(),
            (lh.abfahrtszeit - base_time).total_seconds(),
            lh,
        )
        for lh in charging_plan.ladehalte
    ]
    total_charging_time_s = sum(
        rel_abfahrt - rel_ankunft for rel_ankunft, rel_abfahrt, _ in ladehalte_mit_relzeit
    )
    # Reine Fahrzeit = Gesamtreisezeit abzueglich aller Ladezeiten. Die
    # zurueckgelegte Distanz muss proportional zur bereits VERSTRICHENEN
    # FAHRZEIT wachsen, nicht zur verstrichenen Gesamtzeit (siehe Bugfix
    # unten) - sonst "faehrt" das Fahrzeug waehrend eines Ladehalts entlang
    # der Route weiter, statt an der Ladestation stehen zu bleiben.
    total_driving_time_s = max(end_time_s - total_charging_time_s, 1e-9)

    # Kumulierte, je Segment aus der tatsaechlichen Geschwindigkeit
    # berechnete Fahrzeit (`SegmentEnergyResult.fahrzeit_s`) statt einer
    # einzigen Durchschnittsgeschwindigkeit ueber die gesamte Reise (siehe
    # `_find_segment_for_time`) - auf `total_driving_time_s` skaliert, damit
    # Start (t=0 -> Distanz 0) und Ende (t=total_driving_time_s -> Distanz
    # total_distance_m) trotz eventueller kleiner Abweichungen zwischen der
    # Summe der Segment-Fahrzeiten und der vom Optimierer gelieferten
    # Gesamtreisezeit exakt erhalten bleiben.
    raw_cumulative_times: list[float] = []
    raw_elapsed_time_s = 0.0
    for i in range(len(route.segments)):
        raw_elapsed_time_s += energy_map[i].fahrzeit_s
        raw_cumulative_times.append(raw_elapsed_time_s)
    total_raw_time_s = raw_cumulative_times[-1] if raw_cumulative_times else 0.0
    time_scale = total_driving_time_s / total_raw_time_s if total_raw_time_s > 0 else 1.0
    cumulative_times = [t * time_scale for t in raw_cumulative_times]

    while current_time_s <= end_time_s + 1e-6:
        # Aktuellen Ladehalt bestimmen und zugleich die bereits waehrend
        # Ladehalten verstrichene Zeit bis zum aktuellen Zeitpunkt aufsummieren
        # (fuer bereits abgeschlossene Ladehalte vollstaendig, fuer den
        # laufenden Ladehalt anteilig).
        aktueller_ladehalt = None
        charging_elapsed_before_now_s = 0.0
        rel_ankunftszeit_s = 0.0
        for rel_ankunft, rel_abfahrt, ladehalt in ladehalte_mit_relzeit:
            if rel_abfahrt <= current_time_s:
                charging_elapsed_before_now_s += rel_abfahrt - rel_ankunft
            elif rel_ankunft <= current_time_s:
                charging_elapsed_before_now_s += current_time_s - rel_ankunft
                aktueller_ladehalt = ladehalt
                rel_ankunftszeit_s = rel_ankunft

        # Zurueckgelegte Distanz/Segment-Index anhand der bereits verstrichenen
        # reinen Fahrzeit, aufgeloest ueber die TATSAECHLICHE, je Segment
        # unterschiedliche Geschwindigkeit (siehe `_find_segment_for_time`) -
        # nicht ueber eine einzige Durchschnittsgeschwindigkeit der gesamten
        # Reise, sonst "hinkt" die Positions-/SoC-Schaetzung nach einem
        # Ladehalt der tatsaechlichen Segment-Grenze hinterher (Ladehalt-Zeit
        # "friert" die Distanz ein statt sie weiterzurechnen).
        effective_driving_time_s = current_time_s - charging_elapsed_before_now_s

        segment_idx, progress_in_segment = _find_segment_for_time(
            cumulative_times, total_driving_time_s, effective_driving_time_s, route
        )
        segment = route.segments[segment_idx]
        current_distance_m = (
            cumulative_distances[segment_idx - 1] if segment_idx > 0 else 0.0
        ) + progress_in_segment * segment.laenge_m

        if aktueller_ladehalt is not None:
            zustand = TripState.LADEN
            geschwindigkeit_kmh = 0.0
            distanz_bei_ladehalt.setdefault(id(aktueller_ladehalt), current_distance_m)

            total_charge_time_s = aktueller_ladehalt.geschaetzte_ladedauer_s
            if total_charge_time_s > 0:
                charge_progress = (current_time_s - rel_ankunftszeit_s) / total_charge_time_s
                charge_progress = min(1.0, max(0.0, charge_progress))
                current_soc_pct = aktueller_ladehalt.ankunfts_soc_pct + charge_progress * (
                    aktueller_ladehalt.ziel_soc_pct - aktueller_ladehalt.ankunfts_soc_pct
                )
        else:
            zustand = TripState.FAHREN

            if segment_idx in energy_map:
                energy = energy_map[segment_idx]
                geschwindigkeit_kmh = energy.geschwindigkeit_m_s * ms_to_kmh

                # SoC-Baseline: der zuletzt VOR/AN diesem Segment abgeschlossene
                # Ladehalt (dessen Ziel-SoC), sonst der Start-SoC. Ohne diese
                # Baseline wuerde jeder Ladegewinn beim naechsten FAHREN-Frame
                # verworfen, weil `start_soc_pct` immer auf den Reisebeginn
                # zurueckgreifen wuerde (siehe
                # docs/plans/08-simulation-visualization-api.md, Abschnitt 5.1.1).
                baseline_soc_pct = start_soc_pct
                baseline_segment_idx = 0
                for ladehalt in ladehalte_sortiert:
                    if ladehalt.segment_index > segment_idx:
                        break
                    baseline_soc_pct = ladehalt.ziel_soc_pct
                    baseline_segment_idx = ladehalt.segment_index

                # Energie seit der Baseline: Praefix-Summe der vollstaendig
                # durchfahrenen Segmente zwischen Baseline und aktuellem
                # Segment + anteilig aktuelles Segment.
                completed_segments_energy_kwh = (
                    energy_prefix[segment_idx] - energy_prefix[baseline_segment_idx]
                )
                current_segment_energy_kwh = energy.energiebedarf_kwh * progress_in_segment
                cumulative_energy_kwh = completed_segments_energy_kwh + current_segment_energy_kwh

                if battery_capacity_kwh > 0:
                    current_soc_pct = max(
                        _MIN_SOC_PCT,
                        baseline_soc_pct - (cumulative_energy_kwh / battery_capacity_kwh) * 100.0,
                    )
            else:
                geschwindigkeit_kmh = 0.0

        if aktueller_ladehalt is not None:
            # Waehrend eines Ladehalts steht das Fahrzeug an der Station -
            # exakte Stationskoordinate statt Streckeninterpolation, damit
            # alle LADEN-Frames eines Halts auf demselben Punkt liegen.
            position = aktueller_ladehalt.station.coordinate
        else:
            start_pt_idx = 0
            end_pt_idx = len(segment.geometrie) - 1
            position = _interpolate_position_along_segment(
                segment, start_pt_idx, end_pt_idx, progress_in_segment
            )
        frame = SimulationFrame(
            zeitpunkt=abfahrtszeit + timedelta(seconds=current_time_s),
            position=position,
            distanz_m=current_distance_m,
            soc_pct=current_soc_pct,
            zustand=zustand,
            geschwindigkeit_kmh=geschwindigkeit_kmh,
        )
        frames.append(frame)

        current_time_s += output_resolution_seconds

    detouren = charging_stop_detours or {}
    charging_stops: list[ChargingStopSummary] = []
    for ladehalt in ladehalte_sortiert:
        detour = detouren.get(id(ladehalt))
        charging_stops.append(
            ChargingStopSummary(
                name=ladehalt.station.name,
                station_id=ladehalt.station.station_id,
                position=ladehalt.station.coordinate,
                # Fallback (kein LADEN-Frame erfasst, z. B. sehr kurze
                # Ladedauer unterhalb der Frame-Aufloesung
                # `output_resolution_seconds`): Distanz am Beginn des
                # Ladehalt-Segments.
                distanz_m=distanz_bei_ladehalt.get(
                    id(ladehalt),
                    cumulative_distances[ladehalt.segment_index - 1]
                    if ladehalt.segment_index > 0
                    else 0.0,
                ),
                detour_geometrie=detour.geometrie if detour else [],
                route_index_vor=detour.route_index_vor if detour else None,
                route_index_nach=detour.route_index_nach if detour else None,
                detour_station_index=detour.station_index if detour else None,
                ankunfts_soc_pct=ladehalt.ankunfts_soc_pct,
                ziel_soc_pct=ladehalt.ziel_soc_pct,
                ladedauer_s=ladehalt.geschaetzte_ladedauer_s,
                energie_geladen_kwh=max(
                    0.0,
                    (ladehalt.ziel_soc_pct - ladehalt.ankunfts_soc_pct)
                    / 100.0
                    * battery_capacity_kwh,
                ),
                ankunftszeit=ladehalt.ankunftszeit,
                abfahrtszeit=ladehalt.abfahrtszeit,
            )
        )

    gesamt_ladezeit_min = 0.0
    for ladehalt in charging_plan.ladehalte:
        ladezeit_s = (ladehalt.abfahrtszeit - ladehalt.ankunftszeit).total_seconds()
        gesamt_ladezeit_min += ladezeit_s / 60.0

    gesamt_fahrzeit_min = max(0.0, (end_time_s / 60.0) - gesamt_ladezeit_min)

    end_soc_pct = frames[-1].soc_pct if frames else start_soc_pct

    return TripSimulationResult(
        frames=frames,
        gesamt_distanz_km=total_distance_m / 1000.0,
        gesamt_fahrzeit_min=gesamt_fahrzeit_min,
        gesamt_ladezeit_min=gesamt_ladezeit_min,
        start_soc_pct=start_soc_pct,
        ziel_soc_pct=end_soc_pct,
        charging_stops=charging_stops,
    )
