"""Fixtures for battery tests."""

from __future__ import annotations

import pytest
from tripplanner.battery.models import (
    ChargingCurve,
    ChargingCurvePoint,
    LadekurveReferenz,
    VehicleBatteryParameters,
)


@pytest.fixture
def default_parameters() -> VehicleBatteryParameters:
    """Default vehicle battery parameters (Model 3 LR)."""
    return VehicleBatteryParameters(
        battery_capacity_kwh=75.0,
        max_ladeleistung_kw=250.0,
        effizienz_ladeelektronik=0.95,
        temperatur_korrekturfaktor=1.0,
    )


@pytest.fixture
def v3_curve() -> ChargingCurve:
    """V3-Supercharger Ladekurve für Model 3 LR."""
    return LadekurveReferenz.model_3_lr_v3()


@pytest.fixture
def v4_curve() -> ChargingCurve:
    """V4-Supercharger Ladekurve für Model 3 LR."""
    return LadekurveReferenz.model_3_lr_v4()


@pytest.fixture
def constant_curve() -> ChargingCurve:
    """Konstante Ladekurve (250 kW über gesamten SoC-Bereich)."""
    return ChargingCurve(
        points=[
            ChargingCurvePoint(soc_pct=0.0, charging_power_kw=250.0),
            ChargingCurvePoint(soc_pct=100.0, charging_power_kw=250.0),
        ]
    )
