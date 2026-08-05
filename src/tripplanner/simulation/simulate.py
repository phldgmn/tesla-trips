"""Simulation der Reise entlang der Route mit Ladeplan.

Das Modul rekonstruiert eine Zeitreihe aus Route, ChargingPlan,
SegmentEnergyResult und WeatherSample.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization.models import ChargingPlan
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.simulation.models import SimulationFrame, TripSimulationResult, TripState
from tripplanner.weather.models import WeatherSample

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


def _find_segment_for_distance(
    cumulative_distances: list[float],
    total_distance_m: float,
    distance_m: float,
    route: Route,
) -> tuple[int, float]:
    """Finde das Segment und den Fortschritt fuer eine gegebene Distanz vom Start.

    Args:
        cumulative_distances: Kumulative Distanzen bis zum Ende jedes Segments.
        total_distance_m: Gesamtdistanz der Route in Metern.
        distance_m: Gewuenschte Distanz vom Route-Start.
        route: Die Route.

    Returns:
        Tuple von (segment_index, progress_in_segment).
    """
    if distance_m <= 0:
        return (0, 0.0)
    if distance_m >= total_distance_m:
        return (len(route.segments) - 1, 1.0)

    segment_index = 0
    for i, cum_dist in enumerate(cumulative_distances):
        if distance_m <= cum_dist:
            segment_index = i
            break

    prev_cum_dist = 0.0 if segment_index == 0 else cumulative_distances[segment_index - 1]
    segment_start_m = prev_cum_dist
    segment_end_m = cumulative_distances[segment_index]
    segment_distance_m = segment_end_m - segment_start_m

    distance_in_segment = distance_m - segment_start_m
    progress_in_segment = (
        distance_in_segment / segment_distance_m if segment_distance_m > 0 else 0.0
    )

    return (segment_index, progress_in_segment)


def simulate_trip(  # noqa: PLR0913, PLR0917, PLR0912, PLR0915
    route: Route,
    charging_plan: ChargingPlan,
    segment_energy: list[SegmentEnergyResult],
    weather_samples: list[WeatherSample],
    start_soc_pct: float,
    abfahrtszeit: datetime,
    max_iterations: int = 3,
    convergence_threshold_minutes: float = 30.0,
    output_resolution_seconds: int = 60,
    battery_capacity_kwh: float = 62.5,
) -> TripSimulationResult:
    """Simuliert die komplette Reise entlang der Route unter Beruecksichtigung des Ladeplans.

    Args:
        route: Route mit Segmente (feste Geometrie nach GraphHopper)
        charging_plan: Optimierter Ladeplan aus optimization.Modul
        segment_energy: Energiebedarf je Segment
        weather_samples: Wetter pro Abfragepunkt (nicht verwendet)
        start_soc_pct: Start-SoC in %
        max_iterations: Max. Anzahl Iterationen fuer ETA-Wetter-Konvergenz (nicht verwendet)
        convergence_threshold_minutes: Schwelle in Minuten fuer Iterationserneuerung
        output_resolution_seconds: Zeitauflösung der Ausgabe (default: 60s)
        abfahrtszeit: Abfahrtszeitpunkt der Reise (timezone-aware datetime)
        battery_capacity_kwh: Nutzbare Batteriekapazitaet in kWh (default: 62.5 kWh)

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

    current_soc_pct = start_soc_pct
    frames: list[SimulationFrame] = []

    end_time_s = charging_plan.gesamtreisezeit_s if charging_plan.gesamtreisezeit_s > 0 else 1

    current_time_s = 0.0
    ms_to_kmh = 3.6

    # Verwende uebergebene Abfahrtszeit als Basis
    base_time = abfahrtszeit

    while current_time_s <= end_time_s + 1e-6:
        # Berechne zurückgelegte Distanz proportional zur Zeit
        time_fraction = min(current_time_s / end_time_s, 1.0)
        current_distance_m = time_fraction * total_distance_m

        segment_idx, progress_in_segment = _find_segment_for_distance(
            cumulative_distances, total_distance_m, current_distance_m, route
        )
        segment = route.segments[segment_idx]

        # Finde den aktuellen Ladehalt (falls vorhanden)
        aktueller_ladehalt = None
        for ladehalt in charging_plan.ladehalte:
            rel_ankunftszeit_s = (ladehalt.ankunftszeit - base_time).total_seconds()
            rel_abfahrtszeit_s = (ladehalt.abfahrtszeit - base_time).total_seconds()
            if rel_ankunftszeit_s <= current_time_s <= rel_abfahrtszeit_s:
                aktueller_ladehalt = ladehalt
                break

        if aktueller_ladehalt is not None:
            zustand = TripState.LADEN
            geschwindigkeit_kmh = 0.0

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

                # Kumulative Energie seit Fahrtbeginn berechnen:
                # Summe aller vollstaendig durchfahrenen Segmente + anteilig aktuelles Segment
                completed_segments_energy_kwh = sum(
                    energy_map[i].energiebedarf_kwh for i in range(segment_idx) if i in energy_map
                )
                current_segment_energy_kwh = energy.energiebedarf_kwh * progress_in_segment
                cumulative_energy_kwh = completed_segments_energy_kwh + current_segment_energy_kwh

                if battery_capacity_kwh > 0:
                    current_soc_pct = max(
                        _MIN_SOC_PCT,
                        start_soc_pct - (cumulative_energy_kwh / battery_capacity_kwh) * 100.0,
                    )
            else:
                geschwindigkeit_kmh = 0.0

        start_pt_idx = 0
        end_pt_idx = len(segment.geometrie) - 1
        position = _interpolate_position_along_segment(
            segment, start_pt_idx, end_pt_idx, progress_in_segment
        )
        frame = SimulationFrame(
            zeitpunkt=abfahrtszeit + timedelta(seconds=current_time_s),
            position=position,
            soc_pct=current_soc_pct,
            zustand=zustand,
            geschwindigkeit_kmh=geschwindigkeit_kmh,
        )
        frames.append(frame)

        current_time_s += output_resolution_seconds

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
    )
