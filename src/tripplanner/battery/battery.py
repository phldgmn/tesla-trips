"""Zentrale Lade- und Entlade-Logik für das battery-Modul.

Die Berechnungen basieren auf physikalischen Gesetzen (Energie = Leistung x Zeit),
angepasst an die stückweise lineare Ladekurve und Fahrzeugparameter.
"""

from __future__ import annotations

from .models import (
    ChargingCurve,
    VehicleBatteryParameters,
)


def interpolate_charging_power(
    soc_pct: float,
    charging_curve: ChargingCurve,
) -> float:
    """Berechnet die Ladeleistung (kW) für einen gegebenen SoC mittels linearer Interpolation.

    Extrapolation außerhalb des Bereichs mit dem Randwert.

    Args:
        soc_pct: SoC in Prozent (0-100)
        charging_curve: Die Ladekurve mit diskreten Punkten

    Returns:
        Ladeleistung in kW für den gegebenen SoC
    """
    return charging_curve.ladeleistung_bei_soc(soc_pct)


def compute_charge_duration(  # noqa: PLR0913, PLR0917 -- alle 6 Parameter sind fachlich nötig (Start-/Ziel-SoC, Ladeleistung, Kurve, Fahrzeugparameter, optionales Zeitlimit), siehe docs/plans/05-battery-charging-infrastructure.md
    start_soc_pct: float,
    target_soc_pct: float,
    charging_power_kw: float,
    charging_curve: ChargingCurve,
    parameters: VehicleBatteryParameters,
    max_duration_seconds: float | None = None,
) -> float:
    """Berechnet die Ladedauer in Sekunden von `start_soc_pct` bis `target_soc_pct`.

    Bei konstanter Ladeleistung `charging_power_kw`.

    Die reale Ladedauer wird durch die Kurve begrenzt: Die effektive Ladeleistung
    ist das Minimum aus `charging_power_kw` und der Kurvenleistung bei jedem SoC-Punkt.

    Algorithmus (numerische Integration über die stückweise lineare Kurve):
    1. Begrenze die Ladeleistung durch die Kurve:
       eff_leistung(soc) = min(charging_power_kw, kurven_leistung(soc))
    2. Integriere über den SoC-Bereich: ∫ dQ / eff_leistung(soc)
    3. Multipliziere mit dem Wirkungsgrad der Ladeelektronik und Temperaturfaktor

    Die Kurve selbst kann stückweise linear oder (Standard) als shape-preserving
    cubic Hermite-Interpolation (PCHIP) vorliegen, siehe `ChargingCurve` in
    models.py - für Letztere ist das Integral nicht mehr analytisch geschlossen
    lösbar, daher wird durchgehend eine numerische Rechteckregel mit feiner
    Diskretisierung verwendet. Diese funktioniert unverändert für beide
    Interpolationsarten, da sie nur `charging_curve.ladeleistung_bei_soc()`
    punktweise auswertet.

    Args:
        start_soc_pct: Start-SoC in Prozent (0-100)
        target_soc_pct: Ziel-SoC in Prozent (0-100)
        charging_power_kw: Konstante Ladeleistung (kW)
        charging_curve: Die Ladekurve des Fahrzeugs
        parameters: Fahrzeug-Batterieparameter
        max_duration_seconds: Optional: Maximale Dauer, die erreicht werden darf

    Returns:
        Ladedauer in Sekunden (0 wenn start_soc_pct >= target_soc_pct)
    """
    if start_soc_pct >= target_soc_pct:
        return 0.0

    capacity_kwh = parameters.battery_capacity_kwh
    efficiency = parameters.effizienz_ladeelektronik
    temp_factor = parameters.temperatur_korrekturfaktor

    # Begrenze die Ladeleistung durch die Kurve
    def effective_power(soc_pct: float) -> float:
        curve_power = charging_curve.ladeleistung_bei_soc(soc_pct)
        return min(charging_power_kw, curve_power)

    # Numerische Integration über den SoC-Bereich
    # Feine Diskretisierung (0.1% Schritte) für ausreichende Genauigkeit
    soc_range = target_soc_pct - start_soc_pct
    steps = int(soc_range * 10)
    steps = max(steps, 1)

    total_time_s = 0.0
    soc_delta = soc_range / steps

    for i in range(steps):
        soc = start_soc_pct + i * soc_delta
        power = effective_power(soc)
        if power <= 0:
            continue  # Vermeide Division durch Null

        # Energie für diesen SoC-Schritt
        dQ_kwh = (soc_delta / 100.0) * capacity_kwh
        # Zeit = Energie / Leistung (in Stunden), umrechnen in Sekunden
        dtime_h = dQ_kwh / power
        dtime_s = dtime_h * 3600.0
        total_time_s += dtime_s

    # Wirkungsgrad und Temperatur korrigieren
    total_time_s /= efficiency
    total_time_s *= temp_factor

    # Begrenze durch max_duration_seconds falls angegeben
    if max_duration_seconds is not None and total_time_s > max_duration_seconds:
        return max_duration_seconds

    return total_time_s
