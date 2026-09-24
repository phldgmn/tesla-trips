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


# Konstanten für Kostenfunktion (identisch zu `optimizer.py`)
COST_INF: float = 1e9  # Unendlich für unzulässige Kanten
MAX_SOC_PCT: float = 100.0


def calc_soc_verbrauch_pct(energy_kwh: float, battery_capacity_kwh: float) -> float:
    """Berechne SoC-consumption in Prozent für einen gegebenen Energiebedarf.

    Args:
        energy_kwh: Energiebedarf in kWh (consumption positiv, recuperation
            negativ) - typischerweise über eine Teilstrecke aggregiert
            (siehe `_add_drive_edge`).
        battery_capacity_kwh: Batteriekapazität des Fahrzeugs.

    Returns:
        SoC-consumption in Prozent.
    """
    return (energy_kwh / battery_capacity_kwh) * MAX_SOC_PCT


def calc_ladezeit_s(
    start_soc_pct: float,
    end_soc_pct: float,
    ladekurve: ChargingCurve,
    battery_capacity_kwh: float,
    leistungsdeckel_kw: float | None = None,
) -> float:
    """Berechne Ladezeit in Sekunden für den Ladevorgang `start_soc_pct` → `end_soc_pct`.

    Die mittlere Ladeleistung MUSS über das TATSAECHLICHE Start-/End-
    SoC-Fenster gemittelt werden (`mittlere_ladeleistung_kw(start_soc_pct,
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
    """Berechne mittlere Ladeleistung über einen SoC-Bereich."""
    if start_soc_pct >= end_soc_pct:
        return 0.0

    # Stichproben entlang der Kurve - EIN Batch-Aufruf statt `sample_points`
    # einzelner `ladeleistung_bei_soc`-Aufrufe (siehe `ChargingCurve.
    # ladeleistung_bei_soc_batch`-Docstring: amortisiert den Pydantic-
    # `PrivateAttr`-Zugriff über alle Stichproben statt pro Punkt - bei
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
    # ~9e-11 %-Punkte) - bei Routen mit vielen Ladestationen UND vielen
    # zu kurzen Kandidaten (siehe `_kandidaten_mit_mindestladedauer`)
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


def lade_ziel_kandidaten(  # noqa: PLR0913, PLR0917 -- Kandidatenermittlung braucht den vollen Reichweiten-/Kurvenkontext
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
    """Ermittelt informierte Ladeziel-SoC-Kandidaten (%) für einen Halt.

    Statt eines starren Satzes runder Prozentzahlen (fruehere Version:
    80/90/100) kombiniert dies zwei Kandidatenarten, die den A*-Suchraum
    gezielt um die tatsaechlich relevanten Ladeziele anreichern:

    1. REICHWEITEN-Kandidaten (Lookahead ueber 2 Entscheidungspunkte):
       das MINIMALE Ladeziel, um den naechsten bzw. UEBERNAECHSTEN
       Entscheidungspunkt (Ladestation, Zwischenstopp, Ferry oder Ziel)
       mit der jeweils dort geltenden Sicherheitsreserve zu erreichen
       (`min_arrival_soc_pct` fuer eine weitere Ladestation,
       `target_soc_target` fuers Fahrtziel, sonst `min_soc_pct`). Der
       Uebernaechste-Kandidat modelliert explizit die Alternative "hier
       etwas more laden, um die naechste Station ganz zu ueberspringen" -
       ohne ihn wuerde die Suche diese Option nur zufaellig ueber einen
       der anderen Kandidaten treffen (siehe Nutzer-Report: Ladehalt in
       Kamen auf 80%, obwohl Holdorf ohnehin mit 24% erreicht wurde -
       der minimale Reichweiten-Kandidat fuer Holdorf haette exakt den
       tatsaechlich noetigen, viel kleineren Ladebetrag geliefert).
    2. KURVEN-Kandidaten: die eigenen Stuetzstellen der Ladekurve
       (`ladekurve.points`) oberhalb der Ankunfts-SoC - genau dort
       aendert sich die Ladeleistung spuerbar (schnell im unteren
       Bereich, tapering danach, siehe `LadekurveReferenz`), sie
       markieren die natuerlichen "bis hier lohnt sich schnelles Laden
       noch"-Grenzen JEDER Ladekurve (nicht nur der Tesla-Referenzkurven
       mit ihren 20/50/80/90/100%-Stuetzstellen).

    Der A*-Kostenoptimierer (drive_time_s + echte kurvenbasierte Ladezeit
    über `_calc_ladezeit_s`, siehe `_generate_graph`) waehlt aus diesen
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


def kandidaten_mit_mindestladedauer(  # noqa: PLR0913, PLR0917 -- Mindestdauer-Streckung braucht Ladekurve, Kapazität, Mindestdauer und Cap
    kandidaten: list[float],
    arrival_soc_pct: float,
    ladekurve: ChargingCurve,
    battery_capacity_kwh: float,
    min_charging_time_s: float,
    max_charge_soc_pct: float = 100.0,
) -> list[float]:
    """Hebt Kandidaten, deren Ladezeit unter `min_charging_time_s` läge, auf das SoC an.

    Statt sie zu verwerfen, wird GENAU auf die Mindestdauer gestreckt.

    Eine echte Teilladung dauert danach entweder GAR NICHT (die parallele
    "Station überspringen"-Fahrtkante in `_add_drive_edge` bleibt
    unberührt) oder mindestens `min_charging_time_s`. Verhindert unnötig
    kurze Ladehalte (siehe Nutzer-Report: ein 1-Minuten-Stopp, gefolgt
    von einem weiteren Halt nach nur gut 10 Minuten Fahrt - beide Halte
    zusammen kosten durch Ein-/Ausparken, Stecker anschließen etc. more
    time als eine einzelne, etwas längere Ladung), ohne den Ladehalt an
    sich zu erzwingen.

    Mehrere zu kurze Roh-Kandidaten können dabei auf DASSELBE gestreckte
    Ziel-SoC abgebildet werden - per `set` dedupliziert, damit nicht
    mehrfach identische Ladekanten erzeugt werden.
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
