"""Charge and discharge logic for the battery module.

Calculations based on physical laws (energy = power * time),
adjusted to the piecewise linear charging curve and vehicle parameters.
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
    """Calculates the charging_power (kW) for a given SoC using linear interpolation.

    Extrapolation beyond the range using the boundary value.

    Args:
        soc_pct: SoC in Prozent (0-100)
        charging_curve: Die charging_curve mit diskreten Punkten

    Returns:
        charging power in kW for the given SoC
    """
    return charging_curve.ladeleistung_bei_soc(soc_pct)


def compute_charge_duration(  # noqa: PLR0913, PLR0917 -- all 6 parameters are functionally necessary (start/target SoC, charging power, curve, vehicle parameters, optional time limit), see docs/plans/05-battery-charging-infrastructure.md
    start_soc_pct: float,
    target_soc_pct: float,
    charging_power_kw: float,
    charging_curve: ChargingCurve,
    parameters: VehicleBatteryParameters,
    max_duration_seconds: float | None = None,
) -> float:
    """Calculates the charge_duration in seconds from `start_soc_pct` to `target_soc_pct`.

    With constant charging_power `charging_power_kw`.

    The real charge_duration is limited by the curve: the effective charging power
    it is the minimum of `charging_power_kw` and the curve power at each SoC point.

    Algorithm (numerical integration over the piecewise linear curve):
    1. Limit charging power by the curve:
       eff_power(soc) = min(charging_power_kw, curve_power(soc))
    2. Integrate over the SoC range: ∫ dQ / eff_power(soc)
    3. Multiply by charge electronics efficiency and temperature factor

    The curve itself can be piecewise linear or (standard) as a shape-preserving
    cubic Hermite interpolation (PCHIP), see `ChargingCurve` in
    models.py - for the latter, the integral is not analytically closed
    solvable, so a numerical rectangular rule with fine
    discretization is used throughout. This works unchanged for both
    interpolation methods, only evaluates `charging_curve.ladeleistung_bei_soc()`
    pointwise.

    Args:
        start_soc_pct: Start-SoC in Prozent (0-100)
        target_soc_pct: Ziel-SoC in Prozent (0-100)
        charging_power_kw: Konstante charging_power (kW)
        charging_curve: Die charging_curve des Fahrzeugs
        parameters: vehicle-Batterieparameter
        max_duration_seconds: Optional: Maximale duration, die erreicht werden darf

    Returns:
        charge_duration in seconds (0 when start_soc_pct >= target_soc_pct)
    """
    if start_soc_pct >= target_soc_pct:
        return 0.0

    capacity_kwh = parameters.battery_capacity_kwh
    efficiency = parameters.effizienz_ladeelektronik
    temp_factor = parameters.temperatur_korrekturfaktor

    # Limit charging power by the curve
    def effective_power(soc_pct: float) -> float:
        curve_power = charging_curve.ladeleistung_bei_soc(soc_pct)
        return min(charging_power_kw, curve_power)

    # Numerical integration over the SoC range
    # Fine discretization (0.1% steps) for sufficient accuracy
    soc_range = target_soc_pct - start_soc_pct
    steps = int(soc_range * 10)
    steps = max(steps, 1)

    total_time_s = 0.0
    soc_delta = soc_range / steps

    for i in range(steps):
        soc = start_soc_pct + i * soc_delta
        power = effective_power(soc)
        if power <= 0:
            continue  # Avoid division by zero

        # energy for this SoC step
        dQ_kwh = (soc_delta / 100.0) * capacity_kwh
        dtime_h = dQ_kwh / power
        # time = energy / power (in hours), convert to seconds
        dtime_s = dtime_h * 3600.0
        total_time_s += dtime_s

    # Apply efficiency and temperature correction
    total_time_s /= efficiency
    total_time_s *= temp_factor

    # Limit by max_duration_seconds if specified
    if max_duration_seconds is not None and total_time_s > max_duration_seconds:
        return max_duration_seconds

    return total_time_s
