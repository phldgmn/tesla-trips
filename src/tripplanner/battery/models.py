"""Datenmodelle für das battery-Modul (Lade- und Entladeverhalten).

Pydantic-Modelle zur Modellierung von Ladekurven, Batteriezuständen und
Fahrzeugparameter für die Berechnung von Ladezeiten.
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
    """Reines Python-Objekt (kein Pydantic-Modell) für die Hot-Path-Auswertung.

    Ein `PrivateAttr` auf einem Pydantic-`BaseModel` läuft für jeden Zugriff
    durch Pydantics generischen `__getattr__`-Fallback statt eines direkten
    `__dict__`/Slot-Zugriffs - gemessen ~500 ns pro Zugriff, gegenüber ~25 ns
    für ein Attribut auf einem `__slots__`-Objekt wie diesem (siehe
    Kommentar bei `ChargingCurve._fast`). `ladeleistung_bei_soc` und die
    beiden Interpolationsmethoden griffen vorher pro Aufruf auf bis zu vier
    separate `PrivateAttr`s zu - bei den 10^5-10^6 Aufrufen pro
    `optimize_charging_plan`-Lauf (siehe `optimization.optimizer.
    _mittlere_ladeleistung_kw`/`battery.compute_charge_duration`) ein
    spürbarer Anteil der Gesamtlaufzeit. Diese Bündelung reduziert das auf
    GENAU EINEN `PrivateAttr`-Zugriff (`self._fast`) pro Aufruf.
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
    """Wertet die Ladekurve für einen bereits auf [0,100] geklemmten SoC aus.

    Modulebene statt Methode, damit `ChargingCurve.ladeleistung_bei_soc_batch`
    `fast = self._fast` NUR EINMAL vor der Schleife abrufen und diese
    Funktion danach ohne weitere `PrivateAttr`-/Pydantic-Zugriffe pro Punkt
    aufrufen kann (siehe `_ChargingCurveFastPath`-Docstring).
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
    """Batteriezustand zu einem Zeitpunkt."""

    soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ladestand in Prozent (0-100)")
    zeitpunkt: float = Field(..., description="Zeitpunkt in Sekunden seit Reisebeginn")


class ChargingCurvePoint(BaseModel):
    """Ein Punkt auf der Ladekurve: (SoC, Ladeleistung).

    Je nach `ChargingCurve.interpolation` werden die Punkte stückweise linear
    oder mittels shape-preserving cubic Hermite-Interpolation (PCHIP) verbunden.
    """

    soc_pct: float = Field(..., ge=0.0, le=100.0)
    charging_power_kw: float = Field(..., ge=0.0)

    @field_validator("soc_pct")
    @classmethod
    def validate_soc(cls, v: float) -> float:
        """Stellt sicher, dass soc_pct ein endlicher Wert ist."""
        if not isfinite(v):
            raise ValueError("soc_pct muss endlich sein")
        return v

    @field_validator("charging_power_kw")
    @classmethod
    def validate_power(cls, v: float) -> float:
        """Stellt sicher, dass charging_power_kw endlich und nichtnegativ ist."""
        if not isfinite(v) or v < 0:
            raise ValueError("charging_power_kw muss endlich und nichtnegativ sein")
        return v


class InterpolationMethod(Enum):
    """Interpolationsmethoden für die Ladekurve."""

    LINEAR = "linear"  # Stückweise lineare Interpolation (Legacy, Knicke an Stützpunkten)
    HERMITE = "hermite"  # Shape-preserving cubic Hermite-Interpolation (PCHIP), C1-stetig


class ChargingCurve(BaseModel):
    """Die Ladekurve des Fahrzeugs.

    Die Punkte sind in aufsteigender Reihenfolge nach soc_pct zu definieren.
    Reale Ladekurven sind nicht monoton fallend: insbesondere bei niedrigem SoC
    steigt die Leistung durch Batterie-Vorkonditionierung zunächst an, bevor sie
    zum Balancing hin abfällt. Monotonie wird daher bewusst nicht erzwungen.

    Für SoC-Werte außerhalb des Bereichs der Stützpunkte wird die Leistung des
    jeweiligen Randpunkts verwendet:
      - SoC <= niedrigster Stützpunkt -> Leistung des niedrigsten SoC
      - SoC >= höchster Stützpunkt -> Leistung des höchsten SoC
    """

    model_config = ConfigDict(frozen=True)

    points: list[ChargingCurvePoint] = Field(
        ...,
        min_length=4,
        description="Mindestens 4 Punkte für sinnvolle Approximation",
    )
    interpolation: InterpolationMethod = Field(
        default=InterpolationMethod.HERMITE,
        description="Interpolationsmethode für Punktezwischenräume",
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
                raise ValueError(f"Punkt {i}: soc_pct außerhalb [0,100]")

            if i > 0 and p.soc_pct == sorted_points[i - 1].soc_pct:
                raise ValueError(
                    f"Punkt {i}: doppelter soc_pct-Wert ({p.soc_pct}) - "
                    "für die PCHIP-Interpolation sind eindeutige "
                    "SoC-Stützstellen nötig"
                )

        return sorted_points

    def model_post_init(self, __context: object) -> None:
        """Berechnet Interpolations-Koeffizienten und baut das Hot-Path-Bündel."""
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
            # `pchip.c` ist ein (4, n-1)-Array: je Segment i die Koeffizienten
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
        """Berechnet die Ladeleistung (kW) für einen gegebenen SoC.

        SoC-Werte außerhalb [0,100] werden zunächst auf [0,100] geklemmt.
        Falls die Stützpunkte nicht exakt bei 0 bzw. 100 % beginnen/enden,
        wird für diese Bereiche die Leistung des jeweiligen äußersten
        Stützpunkts verwendet.

        Args:
            soc_pct: SoC in Prozent.

        Returns:
            Ladeleistung in kW.
        """
        soc_clamped = min(max(soc_pct, 0.0), 100.0)
        # EIN `PrivateAttr`-Zugriff - siehe `_ChargingCurveFastPath`-Docstring.
        return _evaluate_fast_path(self._fast, soc_clamped)

    def ladeleistung_bei_soc_batch(self, soc_values: Sequence[float]) -> list[float]:
        """Wie `ladeleistung_bei_soc`, aber für mehrere SoC-Werte in EINEM Aufruf.

        Holt `self._fast` NUR EINMAL statt einmal pro SoC-Wert - für Aufrufer,
        die die Kurve über mehrere Punkte auswerten (z. B. numerische
        Integration in `optimization.optimizer._mittlere_ladeleistung_kw`,
        Größenordnung 10^5-10^6 Punktauswertungen pro `optimize_charging_
        plan`-Lauf). Ergebnis identisch zu
        `[self.ladeleistung_bei_soc(s) for s in soc_values]`.
        """
        fast = self._fast
        return [_evaluate_fast_path(fast, min(max(s, 0.0), 100.0)) for s in soc_values]


class VehicleBatteryParameters(BaseModel):
    """Fahrzeug-spezifische Batterieeigenschaften.

    Vorrangig für spätere Kalibrierung mit Fahrdaten vorgesehen.
    Default-Werte basierend auf typischem Model 3 LR Verhalten.
    """

    battery_capacity_kwh: float = Field(
        default=75.0,
        ge=50.0,
        le=100.0,
        description="Nutzkapazität der Batterie in kWh (Typ Model 3 LR: ~75 kWh)",
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
        description="Multiplikator für Ladedauer basierend auf Batterie-/Umgebungstemperatur",
    )


class ChargingStop(BaseModel):
    """Resultat einer Ladevorgangs-Berechnung für einen Station-Halt."""

    station_id: str
    arrival_soc_pct: float
    target_soc_pct: float
    geschaetzte_ladedauer_s: float
    ankunftszeit_s: float  # seit Reisebeginn
    abfahrtszeit_s: float  # seit Reisebeginn


from .reference_curves import LadekurveReferenz as LadekurveReferenz  # noqa: E402, I001, PLC0414 -- deferred re-export avoids a circular import (reference_curves.py imports ChargingCurve/ChargingCurvePoint/InterpolationMethod from this module); explicit `as`-alias satisfies mypy's implicit_reexport=False.
