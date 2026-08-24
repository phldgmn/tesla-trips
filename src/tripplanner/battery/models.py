"""Datenmodelle für das battery-Modul (Lade- und Entladeverhalten).

Pydantic-Modelle zur Modellierung von Ladekurven, Batteriezuständen und
Fahrzeugparameter für die Berechnung von Ladezeiten.
"""

from __future__ import annotations

from enum import Enum
from math import isfinite

from pydantic import BaseModel, Field, PrivateAttr, field_validator
from scipy.interpolate import PchipInterpolator

_MIN_KURVENPUNKTE = 4
_MAX_SOC_PCT = 100


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
    ladeleistung_kw: float = Field(..., ge=0.0)

    @field_validator("soc_pct")
    @classmethod
    def validate_soc(cls, v: float) -> float:
        """Stellt sicher, dass soc_pct ein endlicher Wert ist."""
        if not isfinite(v):
            raise ValueError("soc_pct muss endlich sein")
        return v

    @field_validator("ladeleistung_kw")
    @classmethod
    def validate_power(cls, v: float) -> float:
        """Stellt sicher, dass ladeleistung_kw endlich und nichtnegativ ist."""
        if not isfinite(v) or v < 0:
            raise ValueError("ladeleistung_kw muss endlich und nichtnegativ sein")
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
    """

    points: list[ChargingCurvePoint] = Field(
        ..., min_length=4, description="Mindestens 4 Punkte für sinnvolle Approximation"
    )
    interpolation: InterpolationMethod = Field(
        default=InterpolationMethod.HERMITE,
        description="Interpolationsmethode für Punktezwischenräume",
    )

    # Lazily gebaute und gecachte PCHIP-Interpolationsfunktion (nur bei Bedarf, siehe
    # _get_pchip()). Kein Pydantic-Feld, daher nicht Teil von Validierung/Serialisierung.
    _pchip: PchipInterpolator | None = PrivateAttr(default=None)

    @field_validator("points")
    @classmethod
    def validate_points(cls, v: list[ChargingCurvePoint]) -> list[ChargingCurvePoint]:
        """Validiert Mindestpunktzahl, SoC-Bereich [0,100] und eindeutige SoC-Stützstellen.

        Die Leistung selbst muss NICHT monoton fallen (siehe Klassen-Docstring) -
        nur eindeutige, aufsteigend sortierbare SoC-Werte sind für die
        PCHIP-Interpolation erforderlich.
        """
        if len(v) < _MIN_KURVENPUNKTE:
            raise ValueError(f"Ladekurve benötigt mindestens {_MIN_KURVENPUNKTE} Punkte")
        sorted_points = sorted(v, key=lambda p: p.soc_pct)
        for i, p in enumerate(sorted_points):
            if p.soc_pct < 0 or p.soc_pct > _MAX_SOC_PCT:
                raise ValueError(f"Punkt {i}: soc_pct außerhalb [0,100]")
            if i > 0 and p.soc_pct == sorted_points[i - 1].soc_pct:
                raise ValueError(
                    f"Punkt {i}: doppelter soc_pct-Wert ({p.soc_pct}) - für die "
                    "PCHIP-Interpolation sind eindeutige SoC-Stützstellen nötig"
                )
        return sorted_points

    def _get_pchip(self) -> PchipInterpolator:
        """Baut die PCHIP-Interpolationsfunktion einmalig und cached sie auf der Instanz."""
        if self._pchip is None:
            soc = [p.soc_pct for p in self.points]
            power = [p.ladeleistung_kw for p in self.points]
            # extrapolate=False, da ladeleistung_bei_soc() vorher auf [0,100] klemmt
            # und die Stützpunkte den Bereich [0,100] abdecken müssen.
            self._pchip = PchipInterpolator(soc, power, extrapolate=False)
        return self._pchip

    def _ladeleistung_linear(self, soc_pct: float) -> float:
        """Stückweise lineare Interpolation (Legacy-Verhalten, `InterpolationMethod.LINEAR`)."""
        points = self.points
        if soc_pct <= points[0].soc_pct:
            return points[0].ladeleistung_kw
        if soc_pct >= points[-1].soc_pct:
            return points[-1].ladeleistung_kw

        for i in range(len(points) - 1):
            p1, p2 = points[i], points[i + 1]
            if p1.soc_pct <= soc_pct <= p2.soc_pct:
                t = (soc_pct - p1.soc_pct) / (p2.soc_pct - p1.soc_pct)
                return p1.ladeleistung_kw + t * (p2.ladeleistung_kw - p1.ladeleistung_kw)

        return points[-1].ladeleistung_kw

    def ladeleistung_bei_soc(self, soc_pct: float) -> float:
        """Berechnet die Ladeleistung (kW) für einen gegebenen SoC.

        Nutzt je nach `interpolation` entweder stückweise lineare Interpolation
        oder eine shape-preserving cubic Hermite-Interpolation (PCHIP). Letztere
        verbindet die Stützpunkte glatt (C1-stetig) ohne Knicke und ohne
        Überschwinger zwischen den Punkten und bildet damit reale Ladekurven -
        inklusive eines anfänglichen Leistungsanstiegs bei niedrigem SoC -
        deutlich realistischer ab als eine lineare Näherung.

        SoC-Werte außerhalb [0,100] werden auf den Randwert geklemmt
        (Extrapolation mit dem Randwert, wie bei der linearen Variante).

        Args:
            soc_pct: SoC in Prozent (0-100, wird bei Bedarf geklemmt)

        Returns:
            Ladeleistung in kW für den gegebenen SoC
        """
        soc_clamped = min(max(soc_pct, 0.0), 100.0)

        if self.interpolation is InterpolationMethod.HERMITE:
            power = float(self._get_pchip()(soc_clamped))
            return max(power, 0.0)

        return self._ladeleistung_linear(soc_clamped)


class VehicleBatteryParameters(BaseModel):
    """Fahrzeug-spezifische Batterieeigenschaften.

    Vorrangig für spätere Kalibrierung mit Fahrdaten vorgesehen.
    Default-Werte basierend auf typischem Model 3 LR Verhalten.
    """

    batteriekapazitaet_kwh: float = Field(
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
    ankunfts_soc_pct: float
    ziel_soc_pct: float
    geschaetzte_ladedauer_s: float
    ankunftszeit_s: float  # seit Reisebeginn
    abfahrtszeit_s: float  # seit Reisebeginn


class LadekurveReferenz:
    """Referenz-Ladekurven für gängige Tesla-Modelle / Konfigurationen.

    Basierend auf Community-Messdaten (Forums, supercharge.info).
    """

    @staticmethod
    def model_3_lr_v3() -> ChargingCurve:
        """Grobe 6-Punkte-Näherung für Model 3 Long Range mit V3-Supercharger.

        Typische Messwerte:
        - 0-20%: ~250-280 kW
        - 20-50%: lineares Abfallen auf ~150 kW
        - 50-80%: auf ~80 kW
        - 80-100%: starkes Abbremsen auf <20 kW

        Für eine deutlich feinere, realistischere Kurve (inkl. anfänglichem
        Leistungsanstieg durch Vorkonditionierung) siehe `model_3_lr_v3_measured()`.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=150.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=75.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=35.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=10.0),
            ],
            interpolation=InterpolationMethod.LINEAR,
        )

    @staticmethod
    def model_3_lr_v3_measured() -> ChargingCurve:
        """Feingranulare, realistische Referenzladekurve für Model 3 LR (V3-Supercharger).

        25 Stützpunkte statt der groben 6-Punkte-Näherung in `model_3_lr_v3()`,
        verbunden per PCHIP (`InterpolationMethod.HERMITE`) statt linear. Bildet
        insbesondere den anfänglichen Leistungsanstieg durch Batterie-
        Vorkonditionierung ab: Rampe von ~50 kW bei 0% SoC auf einen Peak von
        ~170 kW bei 9-10% SoC, danach Abfall zum Balancing hin auf 9 kW bei 100%.

        Hinweis: In der Ursprungsanalyse, aus der diese Stützpunkte stammen,
        wurden dazu passend `effizienz_ladeelektronik=0.92` und
        `max_ladeleistung_kw=250.0` in `VehicleBatteryParameters` verwendet
        (statt der allgemeinen Defaults 0.95 / 250.0) - bei Bedarf entsprechend
        anpassen, insbesondere `effizienz_ladeelektronik`.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=50.0),
                ChargingCurvePoint(soc_pct=4.0, ladeleistung_kw=120.0),
                ChargingCurvePoint(soc_pct=5.0, ladeleistung_kw=140.0),
                ChargingCurvePoint(soc_pct=6.0, ladeleistung_kw=150.0),
                ChargingCurvePoint(soc_pct=7.0, ladeleistung_kw=160.0),
                ChargingCurvePoint(soc_pct=9.0, ladeleistung_kw=170.0),
                ChargingCurvePoint(soc_pct=10.0, ladeleistung_kw=170.0),
                ChargingCurvePoint(soc_pct=13.0, ladeleistung_kw=163.0),
                ChargingCurvePoint(soc_pct=15.0, ladeleistung_kw=155.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=140.0),
                ChargingCurvePoint(soc_pct=25.0, ladeleistung_kw=132.0),
                ChargingCurvePoint(soc_pct=30.0, ladeleistung_kw=125.0),
                ChargingCurvePoint(soc_pct=35.0, ladeleistung_kw=122.0),
                ChargingCurvePoint(soc_pct=40.0, ladeleistung_kw=117.0),
                ChargingCurvePoint(soc_pct=45.0, ladeleistung_kw=111.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=103.0),
                ChargingCurvePoint(soc_pct=55.0, ladeleistung_kw=96.0),
                ChargingCurvePoint(soc_pct=60.0, ladeleistung_kw=88.0),
                ChargingCurvePoint(soc_pct=65.0, ladeleistung_kw=82.0),
                ChargingCurvePoint(soc_pct=70.0, ladeleistung_kw=73.0),
                ChargingCurvePoint(soc_pct=75.0, ladeleistung_kw=66.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=56.0),
                ChargingCurvePoint(soc_pct=85.0, ladeleistung_kw=52.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=45.0),
                ChargingCurvePoint(soc_pct=95.0, ladeleistung_kw=37.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=9.0),
            ],
            interpolation=InterpolationMethod.HERMITE,
        )

    @staticmethod
    def model_3_lr_v4() -> ChargingCurve:
        """Referenzladekurve für Model 3 Long Range mit V4-Supercharger (325 kW Installation).

        V4 ermöglicht längeren Hochleistungsbetrieb.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=325.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=325.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=250.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=120.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=60.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=15.0),
            ],
            interpolation=InterpolationMethod.LINEAR,
        )

    @staticmethod
    def model_y_performance_v3() -> ChargingCurve:
        """Model Y Performance (größere Batterie, aber similarer Kurvenverlauf)."""
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=260.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=260.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=140.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=70.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=30.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=8.0),
            ],
            interpolation=InterpolationMethod.LINEAR,
        )
