"""Battery module for charge and discharge behavior.

This module provides models for charging curves and functions for calculating
charge times based on physical laws and piecewise linear
charging_curven.
"""

from __future__ import annotations

from .battery import compute_charge_duration, interpolate_charging_power
from .models import (
    ChargingCurve,
    ChargingCurvePoint,
    ChargingStop,
    InterpolationMethod,
    SoCState,
    VehicleBatteryParameters,
)
from .reference_curves import LadekurveReferenz

__all__ = [
    "ChargingCurve",
    "ChargingCurvePoint",
    "ChargingStop",
    "InterpolationMethod",
    "LadekurveReferenz",
    "SoCState",
    "VehicleBatteryParameters",
    "compute_charge_duration",
    "interpolate_charging_power",
]
