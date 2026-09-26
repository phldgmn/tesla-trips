"""simulation der Reise entlang der Route mit Ladeplan.

Das Modul rekonstruiert eine Zeitreihe aus Route, ChargingPlan,
segmentEnergyResult und WeatherSample.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from tripplanner.construction.models import ConstructionZone
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization.models import ChargingPlan, ChargingStop
from tripplanner.optimization.models import WaypointDwell as WaypointStop
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.simulation.models import (
    ChargingStopSummary,
    LadehaltDetour,
    SimulationFrame,
    TripSimulationResult,
    TripState,
    WaypointStopSummary,
)
from tripplanner.trip_input.models import TripInfeasibleError
from tripplanner.weather.models import WeatherSample

# Constants for maximum values
_MAX_SOC_PCT = 100.0
_MIN_SOC_PCT = 0.0


def _interpolate_position_along_segment(
    segment: RouteSegment,
    start_idx: int,
    end_idx: int,
    progress: float,
) -> tuple[float, float]:
    """Interpoliere Position entlang eines Route-segments.

    Args:
        segment: Das Route-segment mit Geometrie.
        start_idx: Index des Startpunktes in segment.geometrie.
        end_idx: Index des Endpunktes in segment.geometrie.
        progress: Fortschritt entlang des segments (0.0 = start, 1.0 = end).

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
    """Find the segment and progress for a given, scaled drive_time_s.

    Sucht ueber die kumulierte segment-drive_time_s (`cumulative_times`, aus
    ``segmentEnergyResult.drive_time_s``) instead of via the cumulative distance -
    damit Position, segment-Index und
    SoC-Baseline der ACTUALN, je segment unterschiedlichen
    speed folgen statt einer einzigen Durchschnittsgeschwindigkeit
    ueber die gesamte Reise. Siehe `simulate_trip`: ohne dies "hinkt" die
    Positions-/SoC-Schaetzung nach einem Ladehalt mehrere Frames long der
    tatsaechlichen segment-Grenze (`Ladehalt.segment_index`) hinterher, sobald
    die lokale speed von der Reise-Durchschnittsgeschwindigkeit
    abweicht (z. B. langsamere Zufahrt zur charging_station) - sichtbar als
    kurzzeitig falscher (zu niedriger) SoC direkt nach dem Ladehalt.

    Args:
        cumulative_times: Kumulierte drive_time_s (s) bis zum Ende jedes segments,
            bereits so skaliert, dass `cumulative_times[-1] == total_time_s`.
        total_time_s: Gesamte reine drive_time_s der Reise in Sekunden.
        time_s: Gewuenschte, bereits um charge_time bereinigte drive_time_s seit
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


def _build_charging_stop_summaries(
    ladehalte_sortiert: list[ChargingStop],
    detouren: dict[int, LadehaltDetour],
    distanz_bei_ladehalt: dict[int, float],
    cumulative_distances: list[float],
    battery_capacity_kwh: float,
) -> list[ChargingStopSummary]:
    """Baue ChargingStopSummary-Liste aus den sortierten Ladehalten.

    Args:
        ladehalte_sortiert: Sortierte Liste der Ladehalte.
        detouren: Detour-Geometrien pro Ladehalt.
        distanz_bei_ladehalt: Erfasste distance beim Erreichen jedes Ladehalts.
        cumulative_distances: Kumulierte Routendistanz je segment.
        battery_capacity_kwh: Nutzbare Batteriekapazitaet in kWh.

    Returns:
        Liste von ChargingStopSummary fuer jeden Ladehalt.
    """
    charging_stops: list[ChargingStopSummary] = []
    for ladehalt in ladehalte_sortiert:
        detour = detouren.get(id(ladehalt))
        charging_stops.append(
            ChargingStopSummary(
                name=ladehalt.station.name,
                station_id=ladehalt.station.station_id,
                position=ladehalt.station.coordinate,
                # Fallback (kein LADEN-Frame erfasst, z. B. sehr kurze
                # charge_duration unterhalb der Frame-Aufloesung
                # `output_resolution_seconds`): distance am Beginn des
                # Ladehalt-segments.
                distance_m=distanz_bei_ladehalt.get(
                    id(ladehalt),
                    cumulative_distances[ladehalt.segment_index - 1]
                    if ladehalt.segment_index > 0
                    else 0.0,
                ),
                detour_geometrie=detour.geometrie if detour else [],
                route_index_vor=detour.route_index_vor if detour else None,
                route_index_nach=detour.route_index_nach if detour else None,
                detour_station_index=detour.station_index if detour else None,
                arrival_soc_pct=ladehalt.arrival_soc_pct,
                target_soc_pct=ladehalt.target_soc_pct,
                charging_duration_s=ladehalt.geschaetzte_ladedauer_s,
                energie_geladen_kwh=max(
                    0.0,
                    (ladehalt.target_soc_pct - ladehalt.arrival_soc_pct)
                    / 100.0
                    * battery_capacity_kwh,
                ),
                arrival_time=ladehalt.arrival_time,
                departure_time=ladehalt.departure_time,
            )
        )
    return charging_stops


def _build_waypoint_stop_summaries(
    aufenthalte_sortiert: list[WaypointStop],
    cumulative_distances: list[float],
    battery_capacity_kwh: float,
) -> list[WaypointStopSummary]:
    """Baue WaypointStopSummary-Liste aus den sortierten Zwischenstopp-Aufenthalten.

    Args:
        aufenthalte_sortiert: Sortierte Liste der Zwischenstopp-Aufenthalte.
        cumulative_distances: Kumulierte Routendistanz je segment.
        battery_capacity_kwh: Nutzbare Batteriekapazitaet in kWh.

    Returns:
        Liste von WaypointStopSummary fuer jeden Zwischenstopp-Aufenthalt.
    """
    waypoint_stops: list[WaypointStopSummary] = []
    for aufenthalt in aufenthalte_sortiert:
        waypoint_stops.append(
            WaypointStopSummary(
                position=aufenthalt.coordinate,
                # Rein geometrisch aus dem (per GraphHopper-Via-Punkt exakt
                # aufgeloesten, siehe `NetworkXOptimizer.
                # _map_waypoints_to_segments`) `segment_index` abgeleitet -
                # NICHT aus der zeitbasierten Positionsrekonstruktion (siehe
                # Kommentar bei `distanz_bei_ladehalt` oben).
                distance_m=cumulative_distances[aufenthalt.segment_index - 1]
                if aufenthalt.segment_index > 0
                else 0.0,
                arrival_time=aufenthalt.arrival_time,
                departure_time=aufenthalt.departure_time,
                charging_power_kw=aufenthalt.charging_power_kw,
                arrival_soc_pct=aufenthalt.arrival_soc_pct,
                target_soc_pct=aufenthalt.target_soc_pct,
                energie_geladen_kwh=max(
                    0.0,
                    (aufenthalt.target_soc_pct - aufenthalt.arrival_soc_pct)
                    / 100.0
                    * battery_capacity_kwh,
                ),
            )
        )
    return waypoint_stops


def _compute_total_times(
    charging_plan: ChargingPlan,
    end_time_s: float,
) -> tuple[float, float, float]:
    """Calculate Gesamt-drive_time_s, -charge_time und -Wartezeit in Minuten.

    Args:
        charging_plan: Der optimierte Ladeplan.
        end_time_s: Gesamtreisezeit in Sekunden.

    Returns:
        Tuple von (gesamt_ladezeit_min, gesamt_wartezeit_min, gesamt_fahrzeit_min).
    """
    gesamt_ladezeit_min = 0.0
    for ladehalt in charging_plan.ladehalte:
        ladezeit_s = (ladehalt.departure_time - ladehalt.arrival_time).total_seconds()
        gesamt_ladezeit_min += ladezeit_s / 60.0

    # Zwischenstopp-Aufenthalte zaehlen ausschliesslich als Wartezeit
    # (`gesamt_wartezeit_min`) - auch wenn an ihnen geladen wird. Nur die
    # tatsaechlichen charging_stops (Supercharger, `ladehalte`; siehe
    # `gesamt_ladezeit_min` oben) gehen in die total_charge_time ein. Beide
    # zusammen mit `gesamt_fahrzeit_min` ergeben die volle Gesamtreisezeit.
    gesamt_wartezeit_min = 0.0
    for aufenthalt in charging_plan.zwischenstopp_aufenthalte:
        wartezeit_s = (aufenthalt.departure_time - aufenthalt.arrival_time).total_seconds()
        gesamt_wartezeit_min += wartezeit_s / 60.0

    gesamt_fahrzeit_min = max(0.0, (end_time_s / 60.0) - gesamt_ladezeit_min - gesamt_wartezeit_min)

    return gesamt_ladezeit_min, gesamt_wartezeit_min, gesamt_fahrzeit_min


def simulate_trip(  # noqa: PLR0913, PLR0917, PLR0912, PLR0915
    route: Route,
    charging_plan: ChargingPlan,
    segment_energy: list[SegmentEnergyResult],
    start_soc_pct: float,
    departure_time: datetime,
    output_resolution_seconds: int = 60,
    battery_capacity_kwh: float = 62.5,
    charging_stop_detours: dict[int, LadehaltDetour] | None = None,
    construction_zones: list[ConstructionZone] | None = None,
    weather_samples: Sequence[WeatherSample] | None = None,
) -> TripSimulationResult:
    """Simuliert die komplette Reise entlang der Route unter Beruecksichtigung des Ladeplans.

    Args:
        route: Route mit segmente (feste Geometrie nach GraphHopper)
        charging_plan: Optimierter Ladeplan aus optimization.Modul
        segment_energy: Energiebedarf je segment
        start_soc_pct: Start-SoC in %
        output_resolution_seconds: Output resolution (default: 60s)
        departure_time: Trip departure time (timezone-aware datetime)
        battery_capacity_kwh: Usable battery capacity in kWh (default: 62.5 kWh)
        charging_stop_detours: Optional, GraphHopper-routed detour result
            per charging stop (key: ``id()`` of the ``ChargingStop`` object from
            ``charging_plan.charging_stops``), for a street-accurate map display
            of the detour to the charging station (see
            ``tripplanner.trip_input.api._step_route_charging_detours``. If missing
            entry, ``ChargingStopSummary.detour_geometrie`` remains empty.
        construction_zones: construction zones along the route (optional), for the
            map display passed unchanged to ``TripSimulationResult.construction_zones``
            passed through.
        weather_samples: One ``WeatherSample`` per route segment (same
            order as ``route.segments``, see
            ``tripplanner.trip_input.pipeline._step_5_fetch_weather``), for the
            route hover display in the frontend (``simulationFrame.temperature_c``
            etc.). ``None`` (default, or when weather was not used in the calculation
            beruecksichtigt wurde, siehe `WeatherDetailLevel` 'off') laesst
            diese Felder auf jedem Frame leer statt irrefuehrende Platzhalter-
            werte anzuzeigen.

    Returns:
        TripsimulationResult: Zeitreihe aus Frames (time, Position, SoC, Zustand, speed)

    Raises:
        ValueError: Wenn SoC-Werte ungueltig sind oder keine passenden Energy-Ergebnisse vorliegen.
    """
    if not (_MIN_SOC_PCT <= start_soc_pct <= _MAX_SOC_PCT):
        raise TripInfeasibleError("Start SoC must be between 0% and 100%")

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

# Weather data per segment for the route hover display in the frontend
    # (``simulationFrame.temperature_c`` etc., see ``buildRouteHoverText`` in
    # ``popups.ts``) - independent of the vehicle state (DRIVE/CHARGE/PAUSE)
    # zugeordnet, da sie nur die Position beschreiben, nicht die Fahrt.
    weather_by_segment: dict[int, WeatherSample] = (
        dict(enumerate(weather_samples)) if weather_samples else {}
    )

    cumulative_distances: list[float] = []
    total_distance_m = 0.0
    for segment in route.segments:
        total_distance_m += segment.length_m
        cumulative_distances.append(total_distance_m)

    # Prefix sum of the segment energy for O(1) "energy since segment X"
    # lookups instead of O(n) recalculation per frame (``energy_prefix[i]`` =
    # cumulative energy demand of the segments [0, i)).
    energy_prefix: list[float] = [0.0] * (len(route.segments) + 1)
    for i in range(len(route.segments)):
        energy_prefix[i + 1] = energy_prefix[i] + energy_map[i].energiebedarf_kwh

    # Ladehalte nach segment-Index sortiert, um beim Durchlauf der FAHREN-
    # Frames den zuletzt ABGESCHLOSSENEN Ladehalt als SoC-Baseline zu finden
    # (siehe unten). Zwischenstopp-Aufenthalte analog - beide koennen den SoC
    # veraendern (Ladehalt immer, Zwischenstopp nur mit `charging_power_kw`).
    ladehalte_sortiert = sorted(charging_plan.ladehalte, key=lambda lh: lh.segment_index)
    aufenthalte_sortiert = sorted(
        charging_plan.zwischenstopp_aufenthalte, key=lambda a: a.segment_index
    )
    # Gemeinsame SoC-Baseline-Checkpoints (Ladehalt + Zwischenstopp), nach
    # segment-Index sortiert - der SPAETESTE Checkpoint bei/vor dem aktuellen
    # segment liefert den korrekten Baseline-SoC unabhaengig davon, ob der
    # SoC-Sprung von einem Ladehalt oder einer Zwischenstopp-Ladung stammt.
    soc_checkpoints_sortiert: list[tuple[int, float]] = sorted(
        [(lh.segment_index, lh.target_soc_pct) for lh in charging_plan.ladehalte]
        + [(a.segment_index, a.target_soc_pct) for a in charging_plan.zwischenstopp_aufenthalte],
        key=lambda t: t[0],
    )

    current_soc_pct = start_soc_pct
    frames: list[SimulationFrame] = []
    # Kumulierte Routendistanz, an der jeder Ladehalt beginnt (Schluessel:
    # `id()` des Ladehalts) - erfasst beim ersten LADEN-Frame, siehe unten.
    # Fuer `ChargingStopSummary.distance_m` und um `ladehalt_detour_geometrie`
    # an der richtigen Stelle in die Karten-Geometrie einzufuegen (siehe
    # `route-line.ts` im Frontend). Zwischenstopp-Aufenthalte brauchen dieses
    # zeitbasierte Tracking NICHT: ihr `segment_index` (siehe
    # `NetworkXOptimizer._map_waypoints_to_segments`) zeigt bereits exakt auf
    # den per GraphHopper-Via-Punkt aufgeloesten Geometrie-Punkt, sodass
    # `WaypointStopSummary.distance_m` direkt aus `cumulative_distances`
    # abgeleitet wird (siehe unten) - rein geometrisch, unabhaengig von der
    # zeitbasierten Positionsrekonstruktion (die bei Routen mit ferries vor
    # dem Zwischenstopp durch GraphHoppers unrealistische ferry-segmentzeiten
    # um mehrere Kilometer abweichen kann, siehe Regressionstest).
    distanz_bei_ladehalt: dict[int, float] = {}

    end_time_s = charging_plan.gesamtreisezeit_s if charging_plan.gesamtreisezeit_s > 0 else 1

    current_time_s = 0.0
    ms_to_kmh = 3.6

    # Verwende uebergebene departure_time als Basis
    base_time = departure_time

    # Ladehalte/Zwischenstopp-Aufenthalte mit relativen (Sekunden-seit-
    # Abfahrt) Ankunfts-/Abfahrtszeiten vorab aufbereiten. Wird sowohl zur
    # Bestimmung des aktuellen Halts/Aufenthalts als auch zur Umrechnung von
    # "total_time" in "reine drive_time_s" gebraucht (siehe unten).
    ladehalte_mit_relzeit = [
        (
            (lh.arrival_time - base_time).total_seconds(),
            (lh.departure_time - base_time).total_seconds(),
            lh,
        )
        for lh in charging_plan.ladehalte
    ]
    aufenthalte_mit_relzeit = [
        (
            (a.arrival_time - base_time).total_seconds(),
            (a.departure_time - base_time).total_seconds(),
            a,
        )
        for a in charging_plan.zwischenstopp_aufenthalte
    ]
    total_charging_time_s = sum(
        rel_abfahrt - rel_ankunft for rel_ankunft, rel_abfahrt, _ in ladehalte_mit_relzeit
    )
    total_waypoint_wait_time_s = sum(
        rel_abfahrt - rel_ankunft for rel_ankunft, rel_abfahrt, _ in aufenthalte_mit_relzeit
    )
    # Reine drive_time_s = Gesamtreisezeit abzueglich aller Lade-/Wartezeiten. Die
    # zurueckgelegte distance muss proportional zur bereits VERSTRICHENEN
    # drive_time_s wachsen, nicht zur verstrichenen total_time (siehe Bugfix
    # unten) - sonst "faehrt" das vehicle waehrend eines Ladehalts/
    # Zwischenstopp-Aufenthalts entlang der Route weiter, statt stehen zu
    # bleiben.
    total_driving_time_s = max(
        end_time_s - total_charging_time_s - total_waypoint_wait_time_s, 1e-9
    )

    # Kumulierte, je segment aus der tatsaechlichen speed
    # computed drive_time_s (``segmentEnergyResult.drive_time_s``) instead of a
    # single average speed over the entire trip (see
    # ``_find_segment_for_time``) - scaled to ``total_driving_time_s`` so that
    # start (t=0 → distance 0) and end (t=total_driving_time_s → distance
    # total_distance_m) despite potentially small deviations between the
    # Summe der segment-Fahrzeiten und der vom Optimierer gelieferten
    # Gesamtreisezeit exakt erhalten bleiben.
    raw_cumulative_times: list[float] = []
    raw_elapsed_time_s = 0.0
    for i in range(len(route.segments)):
        raw_elapsed_time_s += energy_map[i].drive_time_s
        raw_cumulative_times.append(raw_elapsed_time_s)
    total_raw_time_s = raw_cumulative_times[-1] if raw_cumulative_times else 0.0
    time_scale = total_driving_time_s / total_raw_time_s if total_raw_time_s > 0 else 1.0
    cumulative_times = [t * time_scale for t in raw_cumulative_times]

    while current_time_s <= end_time_s + 1e-6:
        # Aktuellen Ladehalt/Zwischenstopp-Aufenthalt bestimmen und zugleich
    # the elapsed time during stops/stays up to the
    # current timestamp (for already completed
    # completely, for the ongoing proportionally). A charging stop and a
    # waypoint stay never overlap in time (the same
    # trip cannot be in two places at the same time.
        aktueller_ladehalt = None
        aktueller_aufenthalt = None
        stationaer_elapsed_before_now_s = 0.0
        rel_ankunftszeit_s = 0.0
        for rel_ankunft, rel_abfahrt, ladehalt in ladehalte_mit_relzeit:
            if rel_abfahrt <= current_time_s:
                stationaer_elapsed_before_now_s += rel_abfahrt - rel_ankunft
            elif rel_ankunft <= current_time_s:
                stationaer_elapsed_before_now_s += current_time_s - rel_ankunft
                aktueller_ladehalt = ladehalt
                rel_ankunftszeit_s = rel_ankunft
        for rel_ankunft, rel_abfahrt, aufenthalt in aufenthalte_mit_relzeit:
            if rel_abfahrt <= current_time_s:
                stationaer_elapsed_before_now_s += rel_abfahrt - rel_ankunft
            elif rel_ankunft <= current_time_s:
                stationaer_elapsed_before_now_s += current_time_s - rel_ankunft
                aktueller_aufenthalt = aufenthalt
                rel_ankunftszeit_s = rel_ankunft

    # Distance traveled/segment index based on the already elapsed
    # pure drive_time_s, resolved via the ACTUAL per-segment
    # different speed (see ``_find_segment_for_time``) -
        # nicht ueber eine einzige Durchschnittsgeschwindigkeit der gesamten
        # Reise, sonst "hinkt" die Positions-/SoC-Schaetzung nach einem
        # Ladehalt der tatsaechlichen segment-Grenze hinterher (Ladehalt-time
        # "friert" die distance ein statt sie weiterzurechnen).
        effective_driving_time_s = current_time_s - stationaer_elapsed_before_now_s

        segment_idx, progress_in_segment = _find_segment_for_time(
            cumulative_times, total_driving_time_s, effective_driving_time_s, route
        )
        segment = route.segments[segment_idx]
        current_distance_m = (
            cumulative_distances[segment_idx - 1] if segment_idx > 0 else 0.0
        ) + progress_in_segment * segment.length_m
        wetter = weather_by_segment.get(segment_idx)

        if aktueller_ladehalt is not None:
            zustand = TripState.LADEN
            speed_kmh = 0.0
            distanz_bei_ladehalt.setdefault(id(aktueller_ladehalt), current_distance_m)

            total_charge_time_s = aktueller_ladehalt.geschaetzte_ladedauer_s
            if total_charge_time_s > 0:
                charge_progress = (current_time_s - rel_ankunftszeit_s) / total_charge_time_s
                charge_progress = min(1.0, max(0.0, charge_progress))
                current_soc_pct = aktueller_ladehalt.arrival_soc_pct + charge_progress * (
                    aktueller_ladehalt.target_soc_pct - aktueller_ladehalt.arrival_soc_pct
                )
        elif aktueller_aufenthalt is not None:
            # Zwischenstopp-Aufenthalt: PAUSE ohne charging_power, LADEN mit -
            # das vehicle steht in beiden Faellen an der Zwischenstopp-
            # Koordinate (siehe Positions-Block unten).
            zustand = (
                TripState.LADEN
                if aktueller_aufenthalt.charging_power_kw is not None
                else TripState.PAUSE
            )
            speed_kmh = 0.0

            total_wait_time_s = (
                aktueller_aufenthalt.departure_time - aktueller_aufenthalt.arrival_time
            ).total_seconds()
            if total_wait_time_s > 0:
                wait_progress = (current_time_s - rel_ankunftszeit_s) / total_wait_time_s
                wait_progress = min(1.0, max(0.0, wait_progress))
                current_soc_pct = aktueller_aufenthalt.arrival_soc_pct + wait_progress * (
                    aktueller_aufenthalt.target_soc_pct - aktueller_aufenthalt.arrival_soc_pct
                )
        else:
            zustand = TripState.FAHREN

            if segment_idx in energy_map:
                energy = energy_map[segment_idx]
                speed_kmh = energy.speed_ms * ms_to_kmh

                # SoC-Baseline: der zuletzt VOR/AN diesem segment abgeschlossene
                # Ladehalt ODER Zwischenstopp-Aufenthalt (dessen Ziel-SoC),
                # sonst der Start-SoC. Ohne diese Baseline wuerde jeder
                # Ladegewinn beim naechsten FAHREN-Frame verworfen, weil
                # `start_soc_pct` immer auf den Reisebeginn zurueckgreifen
                # wuerde (siehe
                # docs/plans/08-simulation-visualization-api.md, Abschnitt 5.1.1).
                baseline_soc_pct = start_soc_pct
                baseline_segment_idx = 0
                for checkpoint_segment_idx, checkpoint_ziel_soc_pct in soc_checkpoints_sortiert:
                    if checkpoint_segment_idx > segment_idx:
                        break
                    baseline_soc_pct = checkpoint_ziel_soc_pct
                    baseline_segment_idx = checkpoint_segment_idx

                # energy seit der Baseline: Praefix-Summe der vollstaendig
                # durchfahrenen segmente zwischen Baseline und aktuellem
                # segment + anteilig aktuelles segment.
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
                speed_kmh = 0.0

        if aktueller_ladehalt is not None:
            # Waehrend eines Ladehalts steht das vehicle an der Station -
            # exakte Stationskoordinate statt routeninterpolation, damit
            # alle LADEN-Frames eines Halts auf demselben Punkt liegen.
            position = aktueller_ladehalt.station.coordinate
        elif aktueller_aufenthalt is not None:
            # Waehrend eines Zwischenstopp-Aufenthalts steht das vehicle an
            # dessen Koordinate - exakte Koordinate statt routeninterpolation,
            # analog zum Ladehalt oben.
            position = aktueller_aufenthalt.coordinate
        else:
            start_pt_idx = 0
            end_pt_idx = len(segment.geometrie) - 1
            position = _interpolate_position_along_segment(
                segment, start_pt_idx, end_pt_idx, progress_in_segment
            )
        frame = SimulationFrame(
            timestamp=departure_time + timedelta(seconds=current_time_s),
            position=position,
            distance_m=current_distance_m,
            soc_pct=current_soc_pct,
            zustand=zustand,
            speed_kmh=speed_kmh,
            temperature_c=wetter.temperature_c if wetter else None,
            wind_speed_ms=wetter.wind_speed_ms if wetter else None,
            wind_direction_deg=wetter.wind_direction_deg if wetter else None,
            precipitation_mm=wetter.precipitation_mm if wetter else None,
        )
        frames.append(frame)

        current_time_s += output_resolution_seconds

    detouren = charging_stop_detours or {}
    charging_stops = _build_charging_stop_summaries(
        ladehalte_sortiert,
        detouren,
        distanz_bei_ladehalt,
        cumulative_distances,
        battery_capacity_kwh,
    )
    waypoint_stops = _build_waypoint_stop_summaries(
        aufenthalte_sortiert,
        cumulative_distances,
        battery_capacity_kwh,
    )
    gesamt_ladezeit_min, gesamt_wartezeit_min, gesamt_fahrzeit_min = _compute_total_times(
        charging_plan, end_time_s
    )

    end_soc_pct = frames[-1].soc_pct if frames else start_soc_pct

    return TripSimulationResult(
        frames=frames,
        gesamt_distanz_km=total_distance_m / 1000.0,
        gesamt_fahrzeit_min=gesamt_fahrzeit_min,
        gesamt_ladezeit_min=gesamt_ladezeit_min,
        gesamt_wartezeit_min=gesamt_wartezeit_min,
        start_soc_pct=start_soc_pct,
        target_soc_pct=end_soc_pct,
        charging_stops=charging_stops,
        waypoint_stops=waypoint_stops,
        construction_zones=construction_zones or [],
    )
