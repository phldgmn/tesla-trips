"""Unit tests for battery module functions."""

from __future__ import annotations

import pytest

from tripplanner.battery import compute_charge_duration, interpolate_charging_power
from tripplanner.battery.models import (
    ChargingCurve,
    ChargingCurvePoint,
    LadekurveReferenz,
    VehicleBatteryParameters,
)


class TestInterpolateChargingPower:
    """Tests for interpolate_charging_power function."""

    def test_interpolation_at_control_points(self, v3_curve: ChargingCurve) -> None:
        """Interpolation sollte exakte Werte an Stützstellen liefern."""
        curve = v3_curve

        # Stützstellen aus curve.points
        assert interpolate_charging_power(0.0, curve) == 280.0
        assert interpolate_charging_power(20.0, curve) == 280.0
        assert interpolate_charging_power(50.0, curve) == 150.0
        assert interpolate_charging_power(80.0, curve) == 75.0
        assert interpolate_charging_power(90.0, curve) == 35.0
        assert interpolate_charging_power(100.0, curve) == 10.0

    def test_interpolation_between_control_points(self, v3_curve: ChargingCurve) -> None:
        """Interpolation zwischen Stützstellen sollte linear sein."""
        curve = v3_curve

        # Zwischen 20% (280 kW) und 50% (150 kW)
        # Bei 35%: t = (35-20)/(50-20) = 15/30 = 0.5
        # Leistung = 280 + 0.5 * (150 - 280) = 280 - 65 = 215
        assert interpolate_charging_power(35.0, curve) == pytest.approx(215.0, abs=0.01)

        # Zwischen 80% (75 kW) und 90% (35 kW)
        # Bei 85%: t = (85-80)/(90-80) = 5/10 = 0.5
        # Leistung = 75 + 0.5 * (35 - 75) = 75 - 20 = 55
        assert interpolate_charging_power(85.0, curve) == pytest.approx(55.0, abs=0.01)

    def test_extrapolation_below_min(self, v3_curve: ChargingCurve) -> None:
        """Extrapolation unterhalb des kleinsten Stützpunkts sollte Randwert liefern."""
        curve = v3_curve
        # Minimaler Stützpunkt ist bei 0% mit 280 kW
        assert interpolate_charging_power(-10.0, curve) == 280.0
        assert interpolate_charging_power(0.0, curve) == 280.0

    def test_extrapolation_above_max(self, v3_curve: ChargingCurve) -> None:
        """Extrapolation oberhalb des größten Stützpunkts sollte Randwert liefern."""
        curve = v3_curve
        # Maximaler Stützpunkt ist bei 100% mit 10 kW
        assert interpolate_charging_power(110.0, curve) == 10.0
        assert interpolate_charging_power(100.0, curve) == 10.0


class TestComputeChargeDuration:
    """Tests for compute_charge_duration function."""

    def test_zero_duration_same_soc(self, default_parameters: VehicleBatteryParameters) -> None:
        """Ladedauer sollte 0 sein, wenn Ziel-SoC <= Start-SoC."""
        curve = LadekurveReferenz.model_3_lr_v3()

        assert compute_charge_duration(50.0, 50.0, 250.0, curve, default_parameters) == 0.0
        assert compute_charge_duration(80.0, 50.0, 250.0, curve, default_parameters) == 0.0
        assert compute_charge_duration(100.0, 0.0, 250.0, curve, default_parameters) == 0.0

    def test_zero_duration_at_boundaries(
        self, default_parameters: VehicleBatteryParameters
    ) -> None:
        """Ladedauer sollte 0 sein für 0% → 0% und 100% → 100%."""
        curve = LadekurveReferenz.model_3_lr_v3()

        assert compute_charge_duration(0.0, 0.0, 250.0, curve, default_parameters) == 0.0
        assert compute_charge_duration(100.0, 100.0, 250.0, curve, default_parameters) == 0.0

    def test_charge_duration_small_soc_ramp(
        self, default_parameters: VehicleBatteryParameters
    ) -> None:
        """Kleiner SoC-Sprung sollte kürzere Dauer als großer SoC-Sprung liefern."""
        curve = LadekurveReferenz.model_3_lr_v3()

        # 50% → 51% (1% Sprung)
        duration_small = compute_charge_duration(50.0, 51.0, 250.0, curve, default_parameters)

        # 50% → 80% (30% Sprung)
        duration_large = compute_charge_duration(50.0, 80.0, 250.0, curve, default_parameters)

        assert duration_small > 0
        assert duration_large > duration_small

    def test_charge_duration_v3_vs_v4(self, default_parameters: VehicleBatteryParameters) -> None:
        """V4-Kurve liefert kürzere Dauer (höhere Leistung im Mittelbereich)."""
        # V4-Kurve hat höhere Leistung im mittleren SoC-Bereich (250 kW vs 150 kW bei 50%)

        duration_v3 = compute_charge_duration(
            20.0, 80.0, 250.0, LadekurveReferenz.model_3_lr_v3(), default_parameters
        )
        duration_v4 = compute_charge_duration(
            20.0, 80.0, 250.0, LadekurveReferenz.model_3_lr_v4(), default_parameters
        )

        # V4 sollte schneller laden (kürzere Dauer)
        assert duration_v4 < duration_v3

    def test_charge_duration_constant_curve(
        self, default_parameters: VehicleBatteryParameters
    ) -> None:
        """Bei konstanter Leistung sollte die Dauer exakt berechenbar sein."""
        # Konstante 250 kW, 50% von 75 kWh = 37.5 kWh
        # Zeit = 37.5 kWh / 250 kW = 0.15 h = 540 s (vor Effizienzkorrektur)
        # Mit 95% Effizienz: 540 / 0.95 ≈ 568.42 s

        curve = ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, charging_power_kw=250.0),
                ChargingCurvePoint(soc_pct=25.0, charging_power_kw=250.0),
                ChargingCurvePoint(soc_pct=75.0, charging_power_kw=250.0),
                ChargingCurvePoint(soc_pct=100.0, charging_power_kw=250.0),
            ]
        )

        duration = compute_charge_duration(0.0, 50.0, 250.0, curve, default_parameters)
        expected = (0.5 * 75.0) / 250.0 * 3600 / 0.95  # ~568.42

        assert abs(duration - expected) < 1.0  # Toleranz 1 Sekunde

    def test_charge_duration_limited_by_max_duration(
        self, default_parameters: VehicleBatteryParameters
    ) -> None:
        """Ladedauer sollte durch max_duration_seconds begrenzt werden."""
        curve = LadekurveReferenz.model_3_lr_v3()

        # Unbegrenzte Dauer für 0% → 100% ist viel > 100 Sekunden
        limited = compute_charge_duration(
            0.0, 100.0, 250.0, curve, default_parameters, max_duration_seconds=100.0
        )

        assert limited == 100.0

    def test_charge_duration_beyond_capacity(
        self, default_parameters: VehicleBatteryParameters
    ) -> None:
        """Ladedauer bleibt korrekt, wenn SoC > 100% angefordert wird (auf 100% begrenzt)."""
        curve = LadekurveReferenz.model_3_lr_v3()

        duration = compute_charge_duration(80.0, 100.0, 250.0, curve, default_parameters)

        # Nur positiv, keine Exception
        assert duration > 0
        assert (
            duration < 2100
        )  # 80% → 100% dauert lange aufgrund abbremsender Kurve (ca. 33 Minuten)
