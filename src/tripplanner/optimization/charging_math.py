"""Reine Lade-Mathematik-Funktionen (rechenbare Kernlogik, ohne Optimizer-Zustand).

Extrahiert aus `optimizer.py` (Task 2.1) als freie, testbare Funktionen.
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING

from tripplanner.optimization.models import OptimizationConstraints

if TYPE_CHECKING:
    from tripplanner.battery.models import ChargingCurve
    from tripplanner.charging_infrastructure.models import ChargingStation
    from tripplanner.trip_input.models import VehicleProfile


# constants for cost function (same as `optimizer.py`)
COST_INF: float = 1e9  # Infinity for invalid edges
MAX_SOC_PCT: float = 100.0


def calc_soc_verbrauch_pct(energy_kwh: float, battery_capacity_kwh: float) -> float:
    """Calculate SoC consumption in percentage for a given energy requirement.

    Args:
        energy_kwh: Energy requirement in kWh (consumption positive, recuperation
            negative) - typically aggregated over a subsection
            (siehe `_add_drive_edge`).
        battery_capacity_kwh: Batteriekapazitaet des Fahrzeugs.

    Returns:
        SoC consumption in Prozent.
    """
    return (energy_kwh / battery_capacity_kwh) * MAX_SOC_PCT


def calc_ladezeit_s(
    start_soc_pct: float,
    end_soc_pct: float,
    ladekurve: ChargingCurve,
    battery_capacity_kwh: float,
    leistungsdeckel_kw: float | None = None,
) -> float:
    """Calculate charge_time in seconds for the charging_process `start_soc_pct` -> `end_soc_pct`.

    Die average charging power MUST over the ACTUAL start/end-
    SoC window averaged (`mittlere_ladeleistung_kw(start_soc_pct,
    end_soc_pct, ...)`) - an earlier version derived the window
    stattdessen ausschliesslich aus der SoC-Differenz ab (angenommenes
    Fenster ``[100-delta, 100]``, so als wuerde JEDER charging_process bei 100%
    end). This resulted for partial charges from low SoC (e.g. 20% -> 80%,
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
    if end_soc_pct <= start_soc_pct:
        return 0.0

    mittlere_leistung_kw = mittlere_ladeleistung_kw(
        start_soc_pct=start_soc_pct,
        end_soc_pct=end_soc_pct,
        ladekurve=ladekurve,
        leistungsdeckel_kw=leistungsdeckel_kw,
    )

    if mittlere_leistung_kw <= 0:
        return COST_INF  # Unendlich (nicht ladbar)

    # Energiebedarf in kWh
    delta_soc_pct = end_soc_pct - start_soc_pct
    energy_kwh = (delta_soc_pct / MAX_SOC_PCT) * battery_capacity_kwh

    # time in Sekunden
    return energy_kwh / mittlere_leistung_kw * 3600.0


def mittlere_ladeleistung_kw(
    start_soc_pct: float,
    end_soc_pct: float,
    ladekurve: ChargingCurve,
    leistungsdeckel_kw: float | None = None,
) -> float:
    """Calculate average charging_power over an SoC range."""
    if start_soc_pct >= end_soc_pct:
        return 0.0

    # Stichproben entlang der Kurve - EIN Batch-Aufruf statt `sample_points`
    # einzelner `ladeleistung_bei_soc`-Aufrufe (siehe `ChargingCurve.
    # ladeleistung_bei_soc_batch`-Docstring: amortisiert den Pydantic-
    # `PrivateAttr` access over all samples statt pro Punkt - bei
    # Millionen Aufrufen pro Optimierung der dominante Restanteil).
    sample_points = 10
    delta = end_soc_pct - start_soc_pct
    socs = [start_soc_pct + delta * i / sample_points for i in range(sample_points)]
    leistungen = ladekurve.ladeleistung_bei_soc_batch(socs)
    if leistungsdeckel_kw is not None:
        leistungen = [min(p, leistungsdeckel_kw) for p in leistungen]

    return sum(leistungen) / sample_points


def soc_nach_fester_ladezeit(
    start_soc_pct: float,
    ladezeit_s: float,
    ladekurve: ChargingCurve,
    battery_capacity_kwh: float,
    leistungsdeckel_kw: float | None = None,
) -> float:
    """Ermittelt den SoC nach einer FESTEN charge_duration.

    Inverse zu `_calc_ladezeit_s` per Bisektion: `_calc_ladezeit_s` ist
    monoton steigend in `delta_soc_pct`, aber nicht analytisch invertierbar
    (basiert auf `ladekurve.ladeleistung_bei_soc`-Stichproben) - daher
    numerische Nullstellensuche statt einer geschlossenen Formel.
    """
    max_delta = MAX_SOC_PCT - start_soc_pct
    if ladezeit_s <= 0.0 or max_delta <= 0.0:
        return start_soc_pct

    ladezeit_bei_max = calc_ladezeit_s(
        start_soc_pct=start_soc_pct,
        end_soc_pct=MAX_SOC_PCT,
        ladekurve=ladekurve,
        battery_capacity_kwh=battery_capacity_kwh,
        leistungsdeckel_kw=leistungsdeckel_kw,
    )
    if ladezeit_bei_max <= ladezeit_s:
        return MAX_SOC_PCT  # Batterie ist vor Ablauf der charge_duration voll

    lo, hi = 0.0, max_delta
    # 20 Iterationen: Praezision `max_delta / 2^20` <= 100 / ~1.05e6 ~= 1e-4
    # %-Punkte - weit unter der SoC-Bucket-Granularitaet (`soc_step_pct`,
    # Standard 1.0%, siehe `discretizer.soc_to_bucket`), auf die das
    # Ergebnis ohnehin gerundet wird. Frueher 40 Iterationen (Praezision
    # ~9e-11 %-Punkte) - bei Routen mit vielen charging_stationen UND vielen
    # zu kurzen Kandidaten (siehe `_candidates_with_min_charge_duration`)
    # dominierte diese ungenutzte Ueberpraezision (je Iteration ein
    # `_calc_ladezeit_s`-Aufruf mit 10 Stichproben, siehe
    # `_mittlere_ladeleistung_kw`) einen Grossteil der Optimierungszeit
    # (siehe Nutzer-Report: ~58s fuer `optimize_charging_plan`).
    for _ in range(20):
        mid = (lo + hi) / 2.0
        duration = calc_ladezeit_s(
            start_soc_pct=start_soc_pct,
            end_soc_pct=start_soc_pct + mid,
            ladekurve=ladekurve,
            battery_capacity_kwh=battery_capacity_kwh,
            leistungsdeckel_kw=leistungsdeckel_kw,
        )
        if duration < ladezeit_s:
            lo = mid
        else:
            hi = mid
    return start_soc_pct + (lo + hi) / 2.0


def charging_target_candidates(  # noqa: PLR0913, PLR0917 -- Kandidatenermittlung braucht den vollen Reichweiten-/Kurvenkontext
    arrival_soc_pct: float,
    seg_idx: int,
    checkpoints: list[int],
    station_segments: dict[int, list[tuple[ChargingStation, float]]],
    waypoint_charge_segments: set[int],
    cum_energy_kwh: list[float],
    total_segments: int,
    vehicle_profile: VehicleProfile,
    constraints: OptimizationConstraints,
    ladekurve: ChargingCurve,
    target_soc_target: float,
) -> list[float]:
    """Determines informed charging target SoC candidates (%) for a stop.

    Statt eines starren Satzes runder Prozentzahlen (fruehere Version:
    80/90/100) kombiniert dies zwei Kandidatenarten, die den A*-Suchraum
    gezielt um die tatsaechlich relevanten Ladeziele anreichern:

    1. REICHWEITEN-Kandidaten (Lookahead ueber 2 decisionspunkte):
       das MINIMALE Ladeziel, um den naechsten bzw. UEBERNAECHSTEN
       decisionspunkt (charging_station, Zwischenstopp, Ferry oder Ziel)
       mit der jeweils dort geltenden Sicherheitsreserve zu erreichen
       (`min_arrival_soc_pct` fuer eine weitere charging_station,
       `target_soc_target` fuers Fahrtziel, sonst `min_soc_pct`). Der
       Uebernaechste-Kandidat modelliert explizit die Alternative "hier
       etwas more laden, um die naechste Station ganz zu ueberspringen" -
       ohne ihn wuerde die Suche diese Option nur zufaellig ueber einen
       der anderen Kandidaten treffen (siehe Nutzer-Report: Ladehalt in
       Kamen auf 80%, obwohl Holdorf ohnehin mit 24% erreicht wurde -
       der minimale Reichweiten-Kandidat fuer Holdorf haette exakt den
       tatsaechlich noetigen, viel kleineren Ladebetrag geliefert).
    2. KURVEN-Kandidaten: die eigenen Stuetzstellen der charging_curve
       (`ladekurve.points`) oberhalb der Ankunfts-SoC - genau dort
       aendert sich die charging_power spuerbar (schnell im unteren
       Bereich, tapering danach, siehe `charging_curveReferenz`), sie
       markieren die natuerlichen "bis hier lohnt sich schnelles Laden
       noch"-Grenzen JEDER charging_curve (nicht nur der Tesla-Referenzkurven
       mit ihren 20/50/80/90/100%-Stuetzstellen).

    Der A*-Kostenoptimierer (drive_time_s + echte kurvenbasierte charge_time
    via `_calc_ladezeit_s`, see `_generate_graph`) selects from these
    Kandidaten anschliessend selbst die zeitoptimale Kombination UEBER
    ALLE Ladehalte hinweg - eine nachtraegliche "Backpropagation" auf
    einen bereits gewaehlten frueheren Ladehalt ist dafuer nicht noetig:
    Dijkstra wertet jede Kandidaten-Kombination End-zu-Ende aus und
    waehlt global, nicht gierig pro Halt (ein spaeterer, guenstigerer
    Folgezustand "strahlt" so automatisch auf die Wahl am fruehreren
    Halt zurueck, weil dessen Gesamtkosten die Folgekosten einschliessen).
    """
    cap_soc_pct = min(MAX_SOC_PCT, constraints.max_charge_soc_pct)
    kandidaten: set[float] = {cap_soc_pct}
    if constraints.target_soc_pct > arrival_soc_pct:
        kandidaten.add(constraints.target_soc_pct)

    idx = bisect.bisect_right(checkpoints, seg_idx)
    nachfolger_seg_idx = [
        checkpoints[i] if i < len(checkpoints) else total_segments for i in (idx, idx + 1)
    ]
    for ziel_seg_idx in nachfolger_seg_idx:
        if ziel_seg_idx <= seg_idx:
            continue
        verbrauch_pct = calc_soc_verbrauch_pct(
            energy_kwh=cum_energy_kwh[ziel_seg_idx] - cum_energy_kwh[seg_idx],
            battery_capacity_kwh=vehicle_profile.battery_capacity_kwh,
        )
        if ziel_seg_idx == total_segments:
            puffer_pct = target_soc_target
        elif ziel_seg_idx in station_segments or ziel_seg_idx in waypoint_charge_segments:
            puffer_pct = constraints.min_arrival_soc_pct
        else:
            puffer_pct = constraints.min_soc_pct
        kandidaten.add(verbrauch_pct + puffer_pct)

    for punkt in ladekurve.points:
        if punkt.soc_pct > arrival_soc_pct:
            kandidaten.add(punkt.soc_pct)

    return sorted(v for v in kandidaten if arrival_soc_pct < v <= cap_soc_pct)


def candidates_with_min_charge_duration(  # noqa: PLR0913, PLR0917 -- Mindestdauer-Streckung braucht Ladekurve, Kapazitaet, Mindestdauer und Cap
    kandidaten: list[float],
    arrival_soc_pct: float,
    ladekurve: ChargingCurve,
    battery_capacity_kwh: float,
    min_charging_time_s: float,
    max_charge_soc_pct: float = 100.0,
) -> list[float]:
    """Raises candidates whose charge_time would be below min_charging_time_s to that SoC.

    Statt sie zu verwerfen, wird GENAU auf die Mindestdauer gestreckt.

    Eine echte Teilladung dauert danach entweder GAR NICHT (die parallele
    "Station skip"-driving edge in `_add_drive_edge` remains
    untouched) or at least `min_charging_time_s`. Prevents unnecessarily
    kurze Ladehalte (siehe Nutzer-Report: ein 1-Minuten-Stopp, gefolgt
    von einem weiteren Halt nach nur gut 10 Minuten Fahrt - beide Halte
    zusammen kosten durch Ein-/Ausparken, Stecker anclose etc. more
    time as a single, slightly longer charge), without the charging stop to
    sich zu erzwingen.

    Multiple too-short raw candidates can stretch to the SAME
    Ziel-SoC abgebildet werden - per `set` dedupliziert, damit nicht
    mehrfach identische charging_edgen erzeugt werden.
    """
    if min_charging_time_s <= 0.0:
        return kandidaten

    angepasst: set[float] = set()
    for destination in kandidaten:
        ladezeit_s = calc_ladezeit_s(
            start_soc_pct=arrival_soc_pct,
            end_soc_pct=destination,
            ladekurve=ladekurve,
            battery_capacity_kwh=battery_capacity_kwh,
        )
        ziel_gestreckt = destination
        if 0.0 < ladezeit_s < min_charging_time_s:
            ziel_gestreckt = soc_nach_fester_ladezeit(
                start_soc_pct=arrival_soc_pct,
                ladezeit_s=min_charging_time_s,
                ladekurve=ladekurve,
                battery_capacity_kwh=battery_capacity_kwh,
            )
        if ziel_gestreckt > arrival_soc_pct:
            angepasst.add(min(ziel_gestreckt, max_charge_soc_pct))

    return sorted(angepasst)
