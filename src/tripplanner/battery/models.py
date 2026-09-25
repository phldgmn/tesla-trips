"""data models for the battery module (charging and discharging behavior).

Pydantic models for modeling charging curves, battery states, and
vehicle parameters for the calculation of charge times.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from enum import Enum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator
from scipy.interpolate import PchipInterpolator

_MIN_KURVENPUNKTE = 4
_MAX_SOC_PCT = 100


class _ChargingCurveFastPath:
    """Pure Python object (no Pydantic model) for hot-path evaluation.

    A `PrivateAttr` on a Pydantic `BaseModel` runs for every access
    through Pydantic's generic `__getattr__` fallback instead of a direct
    `__dict__`/slot access - measured ~500 ns per access, vs ~25 ns
    for an attribute on a `__slots__` object like this (see
    comment at `ChargingCurve._fast`. `ladeleistung_bei_soc` and the
    two interpolation methods previously accessed up to four
    separate `PrivateAttr`s per call - at the 10^5-10^6 calls per
    `optimize_charging_plan` run (see `optimization.optimizer.
    _mittlere_ladeleistung_kw`/`battery.compute_charge_duration`) a
    notable fraction of total runtime. This bundling reduces it to
    EXACTLY ONE `PrivateAttr` access (`self._fast`) per call.
    """

    __slots__ = (
        "hermite_breaks",
        "hermite_coeffs",
        "intercepts",
        "is_hermite",
        "power",
        "slopes",
        "soc",
    )

    def __init__(  # noqa: PLR0913, PLR0917 -- alle 7 Felder sind die vollstaendigen, unteilbaren Hot-Path-Vorberechnungen einer ChargingCurve
        self,
        soc: tuple[float, ...],
        power: tuple[float, ...],
        slopes: tuple[float, ...],
        intercepts: tuple[float, ...],
        hermite_breaks: tuple[float, ...],
        hermite_coeffs: tuple[tuple[float, float, float, float], ...],
        is_hermite: bool,
    ) -> None:
        self.soc = soc
        self.power = power
        self.slopes = slopes
        self.intercepts = intercepts
        self.hermite_breaks = hermite_breaks
        self.hermite_coeffs = hermite_coeffs
        self.is_hermite = is_hermite


def _evaluate_fast_path(fast: _ChargingCurveFastPath, soc_pct: float) -> float:
    """Evaluates the charging_curve for a SoC already clamped to [0,100].

    Module-level instead of method, so that `ChargingCurve.ladeleistung_bei_soc_batch`
    `fast = self._fast` ONLY ONCE before the loop and this
    function afterwards without further `PrivateAttr`/Pydantic accesses per point
    can call (see `_ChargingCurveFastPath` docstring).
    """
    if soc_pct <= fast.soc[0]:
        return max(fast.power[0], 0.0)

    if soc_pct >= fast.soc[-1]:
        return max(fast.power[-1], 0.0)

    if fast.is_hermite:
        i = min(bisect_right(fast.hermite_breaks, soc_pct) - 1, len(fast.hermite_coeffs) - 1)
        s = soc_pct - fast.hermite_breaks[i]
        a, b, c, d = fast.hermite_coeffs[i]
        return max(((a * s + b) * s + c) * s + d, 0.0)

    i = bisect_right(fast.soc, soc_pct) - 1
    return max(fast.slopes[i] * soc_pct + fast.intercepts[i], 0.0)


class SoCState(BaseModel):
    """Batteriezustand zu einem timestamp."""

    soc_pct: float = Field(
        ..., ge=0.0, le=100.0, description="State of charge in percentage (0-100)"
    )
    timestamp: float = Field(..., description="timestamp in seconds since trip start")


class ChargingCurvePoint(BaseModel):
    """Ein Punkt auf der charging_curve: (SoC, charging_power).

    Depending on `ChargingCurve.interpolation`, the points are connected piecewise linearly
    or via shape-preserving cubic Hermite interpolation (PCHIP).
    """

    soc_pct: float = Field(..., ge=0.0, le=100.0)
    charging_power_kw: float = Field(..., ge=0.0)

    @field_validator("soc_pct")
    @classmethod
    def validate_soc(cls, v: float) -> float:
        """Ensures that soc_pct is a finite value."""
        if not isfinite(v):
            raise ValueError("soc_pct must be finite")
        return v

    @field_validator("charging_power_kw")
    @classmethod
    def validate_power(cls, v: float) -> float:
        """Ensures that charging_power_kw is finite and non-negative."""
        if not isfinite(v) or v < 0:
            raise ValueError("charging_power_kw must be finite and non-negative")
        return v


class InterpolationMethod(Enum):
    """Interpolation methods for the charging curve."""

    LINEAR = "linear"  # Piecewise linear interpolation (legacy, kinks at support points)
    HERMITE = "hermite"  # Shape-preserving cubic Hermite interpolation (PCHIP), C1-continuous


class ChargingCurve(BaseModel):
    """The vehicle's charging curve.

    The points must be defined in ascending order by soc_pct.
    Real charging curves are not monotonically decreasing: especially at low SoC
    the power initially increases due to battery pre-conditioning before it
    drops for balancing. Monotonicity is therefore deliberately not enforced.

    For SoC values outside the range of support points, the power of the
    jeweiligen Randpunkts uses:
      - SoC <= lowest support point -> power of the lowest SoC
      - SoC >= highest support point -> power of the highest SoC
    """

    model_config = ConfigDict(frozen=True)

    points: list[ChargingCurvePoint] = Field(
        ...,
        min_length=4,
        description="Mindestens 4 Punkte für sinnvolle Approximation",
    )
    interpolation: InterpolationMethod = Field(
        default=InterpolationMethod.HERMITE,
        description="Interpolationsmethode für Punktezwischenraeume",
    )

    # Reines Python-Objekt (kein Pydantic-`PrivateAttr`) mit allen für die
    # Hot-Path-Auswertung (`ladeleistung_bei_soc`) vorberechneten Werten -
    # siehe `_ChargingCurveFastPath`-Docstring für das "warum" (EIN
    # `PrivateAttr`-Zugriff statt vier pro Aufruf).
    _fast: _ChargingCurveFastPath = PrivateAttr()

    @field_validator("points")
    @classmethod
    def validate_points(
        cls,
        v: list[ChargingCurvePoint],
    ) -> list[ChargingCurvePoint]:
        """Validiert Mindestpunktzahl, SoC-Bereich und eindeutige SoC-Werte."""
        if len(v) < _MIN_KURVENPUNKTE:
            raise ValueError(f"Ladekurve benötigt mindestens {_MIN_KURVENPUNKTE} Punkte")

        sorted_points = sorted(v, key=lambda p: p.soc_pct)

        for i, p in enumerate(sorted_points):
            if p.soc_pct < 0 or p.soc_pct > _MAX_SOC_PCT:
                raise ValueError(f"Punkt {i}: soc_pct ausserhalb [0,100]")

            if i > 0 and p.soc_pct == sorted_points[i - 1].soc_pct:
                raise ValueError(
                    f"Punkt {i}: doppelter soc_pct-Wert ({p.soc_pct}) - "
                    "für die PCHIP-Interpolation sind eindeutige "
                    "SoC-Stützstellen nötig"
                )

        return sorted_points

    def model_post_init(self, __context: object) -> None:
        """Calculatet Interpolations-Koeffizienten und baut das Hot-Path-Bündel."""
        soc = tuple(p.soc_pct for p in self.points)
        power = tuple(p.charging_power_kw for p in self.points)

        slopes = []
        intercepts = []

        for i in range(len(soc) - 1):
            delta_soc = soc[i + 1] - soc[i]
            slope = (power[i + 1] - power[i]) / delta_soc
            intercept = power[i] - slope * soc[i]

            slopes.append(slope)
            intercepts.append(intercept)

        is_hermite = self.interpolation is InterpolationMethod.HERMITE
        hermite_breaks: tuple[float, ...] = ()
        hermite_coeffs: tuple[tuple[float, float, float, float], ...] = ()

        if is_hermite:
            pchip = PchipInterpolator(soc, power, extrapolate=False)
            # `pchip.c` ist ein (4, n-1)-Array: je segment i die Koeffizienten
            # [a, b, c, d] eines kubischen Polynoms in POTENZBASIS relativ zum
            # linken Stützpunkt `pchip.x[i]`, d. h.
            # power(soc) = a*s^3 + b*s^2 + c*s + d mit s = soc - pchip.x[i]
            # (scipy-Konvention für `PPoly`/`CubicHermiteSpline`, siehe
            # scipy.interpolate._interpolate.PPoly). Einmalig hier extrahiert
            # und als reine Python-Tupel gecacht, damit `_ladeleistung_hermite`
            # jeden Punkt per Horner-Schema auswerten kann, statt bei jedem
            # Aufruf durch `PchipInterpolator.__call__` (Numpy-Array-
            # Erzeugung/-Validierung pro Skalar) zu gehen.
            hermite_breaks = tuple(float(v) for v in pchip.x)
            hermite_coeffs = tuple(
                (
                    float(pchip.c[0, i]),
                    float(pchip.c[1, i]),
                    float(pchip.c[2, i]),
                    float(pchip.c[3, i]),
                )
                for i in range(pchip.c.shape[1])
            )

        self._fast = _ChargingCurveFastPath(
            soc=soc,
            power=power,
            slopes=tuple(slopes),
            intercepts=tuple(intercepts),
            hermite_breaks=hermite_breaks,
            hermite_coeffs=hermite_coeffs,
            is_hermite=is_hermite,
        )

    def ladeleistung_bei_soc(self, soc_pct: float) -> float:
        """Calculatet die charging_power (kW) für einen gegebenen SoC.

        SoC-Werte ausserhalb [0,100] werden zunaechst auf [0,100] geklemmt.
        Falls die Stützpunkte nicht exakt bei 0 bzw. 100 % beginnen/enden,
        wird für diese Bereiche die Leistung des jeweiligen aeussersten
        Stützpunkts uses.

        Args:
            soc_pct: SoC in Prozent.

        Returns:
            charging_power in kW.
        """
        soc_clamped = min(max(soc_pct, 0.0), 100.0)
        # EIN `PrivateAttr`-Zugriff - siehe `_ChargingCurveFastPath`-Docstring.
        return _evaluate_fast_path(self._fast, soc_clamped)

    def ladeleistung_bei_soc_batch(self, soc_values: Sequence[float]) -> list[float]:
        """Wie `ladeleistung_bei_soc`, aber für mehrere SoC-Werte in EINEM Aufruf.

        Holt `self._fast` NUR EINMAL statt einmal pro SoC-Wert - für caller,
        which evaluates the curve over multiple points (e.g. numerical
        Integration in `optimization.optimizer._mittlere_ladeleistung_kw`,
        Größenordnung 10^5-10^6 Punktauswertungen pro `optimize_charging_
        plan`-Lauf). Ergebnis identisch zu
        `[self.ladeleistung_bei_soc(s) for s in soc_values]`.
        """
        fast = self._fast
        return [_evaluate_fast_path(fast, min(max(s, 0.0), 100.0)) for s in soc_values]


class VehicleBatteryParameters(BaseModel):
    """Vehicle-specific battery properties.

    Vorrangig für spaetere Kalibrierung mit Fahrdaten vorgesehen.
    Default-Werte based auf typischem Model 3 LR Verhalten.
    """

    battery_capacity_kwh: float = Field(
        default=75.0,
        ge=50.0,
        le=100.0,
        description="Nutzkapazitaet der Batterie in kWh (Typ Model 3 LR: ~75 kWh)",
    )
    max_ladeleistung_kw: float = Field(
        default=250.0,
        ge=50.0,
        le=350.0,
        description="Maximale akzeptierte Ladeleistung des Fahrzeugs (kW)",
    )
    effizienz_ladeelektronik: float = Field(
        default=0.95,
        ge=0.85,
        le=1.0,
        description="Wirkungsgrad der Ladeelektronik (Verluste in der Wechselrichtereinheit)",
    )
    temperatur_korrekturfaktor: float = Field(
        default=1.0,
        ge=0.7,
        le=1.1,
        description="Multiplier for charge_duration based on battery/ambient temperature",
    )


class ChargingStop(BaseModel):
    """Result of a charging process calculation for a station stop."""

    station_id: str
    arrival_soc_pct: float
    target_soc_pct: float
    geschaetzte_ladedauer_s: float
    ankunftszeit_s: float  # seit Reisebeginn
    abfahrtszeit_s: float  # seit Reisebeginn


from .reference_curves import LadekurveReferenz as LadekurveReferenz  # noqa: E402, I001, PLC0414 -- deferred re-export avoids a circular import (reference_curves.py imports ChargingCurve/ChargingCurvePoint/InterpolationMethod from this module); explicit `as`-alias satisfies mypy's implicit_reexport=False.
