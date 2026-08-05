"""Datenmodelle für das battery-Modul (Lade- und Entladeverhalten).

Pydantic-Modelle zur Modellierung von Ladekurven, Batteriezuständen und
Fahrzeugparameter für die Berechnung von Ladezeiten.
"""

from __future__ import annotations

from enum import Enum
from math import isfinite

from pydantic import BaseModel, Field, field_validator

_MIN_KURVENPUNKTE = 4
_MAX_SOC_PCT = 100


class SoCState(BaseModel):
    """Batteriezustand zu einem Zeitpunkt."""

    soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ladestand in Prozent (0-100)")
    zeitpunkt: float = Field(..., description="Zeitpunkt in Sekunden seit Reisebeginn")


class ChargingCurvePoint(BaseModel):
    """Ein Punkt auf der Ladekurve: (SoC, Ladeleistung). Die Kurve ist stückweise linear."""

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
    def validate_leistung(cls, v: float) -> float:
        """Stellt sicher, dass ladeleistung_kw endlich und nichtnegativ ist."""
        if not isfinite(v) or v < 0:
            raise ValueError("ladeleistung_kw muss endlich und nichtnegativ sein")
        return v


class InterpolationMethod(Enum):
    """Interpolationsmethoden für die Ladekurve."""

    LINEAR = "linear"  # Stückweise lineare Interpolation (Standard)
    HERMITE = "hermite"  # C1-stetig (zukünftig nutzbar)


class ChargingCurve(BaseModel):
    """Die Ladekurve des Fahrzeugs. Basierend auf typischen Tesla-Charakteristika.

    Die Punkte sind in aufsteigender Reihenfolge nach soc_pct zu definieren.
    """

    points: list[ChargingCurvePoint] = Field(
        ..., min_length=4, description="Mindestens 4 Punkte für sinnvolle Approximation"
    )
    interpolation: InterpolationMethod = Field(
        default=InterpolationMethod.LINEAR,
        description="Interpolationsmethode für Punktezwischenräume",
    )

    @field_validator("points")
    @classmethod
    def validate_points(cls, v: list[ChargingCurvePoint]) -> list[ChargingCurvePoint]:
        """Validiert Mindestpunktzahl, SoC-Bereich [0,100] und monoton fallende Leistung."""
        if len(v) < _MIN_KURVENPUNKTE:
            raise ValueError("Ladekurve benötigt mindestens 4 Punkte")
        sorted_points = sorted(v, key=lambda p: p.soc_pct)
        for i, p in enumerate(sorted_points):
            if p.soc_pct < 0 or p.soc_pct > _MAX_SOC_PCT:
                raise ValueError(f"Punkt {i}: soc_pct außerhalb [0,100]")
            if i > 0 and p.ladeleistung_kw > sorted_points[i - 1].ladeleistung_kw:
                raise ValueError("Ladeleistung darf nicht mit steigendem SoC steigen")
        return sorted_points

    def ladeleistung_bei_soc(self, soc_pct: float) -> float:
        """Berechnet die Ladeleistung (kW) für einen gegebenen SoC mittels linearer Interpolation.

        Extrapolation außerhalb des Bereichs mit dem Randwert.
        """
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
    Die Kurven sind als stückweise lineare Approximation definiert.
    """

    @staticmethod
    def model_3_lr_v3() -> ChargingCurve:
        """Referenzladekurve für Model 3 Long Range mit V3-Supercharger.

        Typische Messwerte:
        - 0-20%: ~250-280 kW
        - 20-50%: lineares Abfallen auf ~150 kW
        - 50-80%: auf ~80 kW
        - 80-100%: starkes Abbremsen auf <20 kW
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=150.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=75.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=35.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=10.0),
            ]
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
            ]
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
            ]
        )
