"""Reference charging curves for known Tesla models / configurations.

Pure data definitions (community measurement values) - no hot-path logic. The
class is re-exported from `models.charging_curve_reference` so that existing
imports (`tripplanner.battery.models.charging_curve_reference`) continue to resolve.
"""

from __future__ import annotations

from .models import ChargingCurve, ChargingCurvePoint, InterpolationMethod


class LadekurveReferenz:
    """Reference charging curves for common Tesla models / configurations.

    Basierend auf Community-Messdaten (Forums, supercharge.info).
    """

    @staticmethod
    def model_3_lr_v3() -> ChargingCurve:
        """Coarse 6-point approximation for Model 3 Long Range with V3-Supercharger.

        Typical measured values:
        - 0-20%: ~250-280 kW
        - 20-50%: linear decrease to ~150 kW
        - 50-80%: down to ~80 kW
        - 80-100%: strong tapering to <20 kW

        For a significantly finer, more realistic curve (incl. initial
        power increase due to preconditioning) siehe `model_3_lr_v3_measured()`.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, charging_power_kw=280.0),
                ChargingCurvePoint(soc_pct=20.0, charging_power_kw=280.0),
                ChargingCurvePoint(soc_pct=50.0, charging_power_kw=150.0),
                ChargingCurvePoint(soc_pct=80.0, charging_power_kw=75.0),
                ChargingCurvePoint(soc_pct=90.0, charging_power_kw=35.0),
                ChargingCurvePoint(soc_pct=100.0, charging_power_kw=10.0),
            ],
            interpolation=InterpolationMethod.LINEAR,
        )

    @staticmethod
    def model_3_sr() -> ChargingCurve:
        """fine-grained realistic reference charging curve for Model 3 SR (V3-Supercharger).

        25 support points,
        connected via PCHIP (`InterpolationMethod.HERMITE`) statt linear. Bildet
        in particular the initial power increase due to battery-
        Vorkonditionierung ab: Rampe von ~50 kW bei 0% SoC auf einen Peak von
        ~170 kW bei 9-10% SoC, danach Abfall zum Balancing hin auf 9 kW bei 100%.

        Note: In the original analysis from which these support points come,
        were used accordingly `effizienz_ladeelektronik=0.92` und
        `max_ladeleistung_kw=250.0` in `VehicleBatteryParameters` use
        (instead of the general defaults 0.95 / 250.0) - if needed accordingly
        anpassen, insbesondere `effizienz_ladeelektronik`.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, charging_power_kw=50.0),
                ChargingCurvePoint(soc_pct=4.0, charging_power_kw=120.0),
                ChargingCurvePoint(soc_pct=5.0, charging_power_kw=140.0),
                ChargingCurvePoint(soc_pct=6.0, charging_power_kw=150.0),
                ChargingCurvePoint(soc_pct=7.0, charging_power_kw=160.0),
                ChargingCurvePoint(soc_pct=9.0, charging_power_kw=170.0),
                ChargingCurvePoint(soc_pct=10.0, charging_power_kw=170.0),
                ChargingCurvePoint(soc_pct=13.0, charging_power_kw=163.0),
                ChargingCurvePoint(soc_pct=15.0, charging_power_kw=155.0),
                ChargingCurvePoint(soc_pct=20.0, charging_power_kw=140.0),
                ChargingCurvePoint(soc_pct=25.0, charging_power_kw=132.0),
                ChargingCurvePoint(soc_pct=30.0, charging_power_kw=125.0),
                ChargingCurvePoint(soc_pct=35.0, charging_power_kw=122.0),
                ChargingCurvePoint(soc_pct=40.0, charging_power_kw=117.0),
                ChargingCurvePoint(soc_pct=45.0, charging_power_kw=111.0),
                ChargingCurvePoint(soc_pct=50.0, charging_power_kw=103.0),
                ChargingCurvePoint(soc_pct=55.0, charging_power_kw=96.0),
                ChargingCurvePoint(soc_pct=60.0, charging_power_kw=88.0),
                ChargingCurvePoint(soc_pct=65.0, charging_power_kw=82.0),
                ChargingCurvePoint(soc_pct=70.0, charging_power_kw=73.0),
                ChargingCurvePoint(soc_pct=75.0, charging_power_kw=66.0),
                ChargingCurvePoint(soc_pct=80.0, charging_power_kw=56.0),
                ChargingCurvePoint(soc_pct=85.0, charging_power_kw=52.0),
                ChargingCurvePoint(soc_pct=90.0, charging_power_kw=45.0),
                ChargingCurvePoint(soc_pct=95.0, charging_power_kw=37.0),
                ChargingCurvePoint(soc_pct=100.0, charging_power_kw=9.0),
            ],
            interpolation=InterpolationMethod.HERMITE,
        )

    @staticmethod
    def model_3_lr_v4() -> ChargingCurve:
        """Reference charging curve for Model 3 Long Range with V4-Supercharger.

        V4 enables longer high-performance operation.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, charging_power_kw=325.0),
                ChargingCurvePoint(soc_pct=20.0, charging_power_kw=325.0),
                ChargingCurvePoint(soc_pct=50.0, charging_power_kw=250.0),
                ChargingCurvePoint(soc_pct=80.0, charging_power_kw=120.0),
                ChargingCurvePoint(soc_pct=90.0, charging_power_kw=60.0),
                ChargingCurvePoint(soc_pct=100.0, charging_power_kw=15.0),
            ],
            interpolation=InterpolationMethod.LINEAR,
        )

    @staticmethod
    def model_y_performance_v3() -> ChargingCurve:
        """Model Y Performance (larger battery, but similar curve shape)."""
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, charging_power_kw=260.0),
                ChargingCurvePoint(soc_pct=20.0, charging_power_kw=260.0),
                ChargingCurvePoint(soc_pct=50.0, charging_power_kw=140.0),
                ChargingCurvePoint(soc_pct=80.0, charging_power_kw=70.0),
                ChargingCurvePoint(soc_pct=90.0, charging_power_kw=30.0),
                ChargingCurvePoint(soc_pct=100.0, charging_power_kw=8.0),
            ],
            interpolation=InterpolationMethod.LINEAR,
        )
