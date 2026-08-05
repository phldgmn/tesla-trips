"""Battery-Modul für Lade- und Entladeverhalten.

Dieses Modul bietet Modelle für Ladekurven und Funktionen zur Berechnung
von Ladezeiten basierend auf physikalischen Gesetzen und stückweise linearen
Ladekurven.
"""

from __future__ import annotations

from .battery import compute_charge_duration, interpolate_charging_power
from .models import (
    ChargingCurve,
    ChargingCurvePoint,
    ChargingStop,
    InterpolationMethod,
    LadekurveReferenz,
    SoCState,
    VehicleBatteryParameters,
)

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
