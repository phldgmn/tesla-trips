"""Datenmodelle für das energy-Modul (physikalisches Verbrauchsmodell).

Pydantic-Modelle zur Darstellung von Fahrzeugparametern und Segment-Ergebnissen.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class VehicleEnergyParameters(BaseModel):
    """Physikalische Fahrzeugparameter für Energieberechnung.

    Alle Default-Werte basieren auf öffentlich verifizierten Spezifikationen
    des Tesla Model 3 (2024/2025).
    """

    # Aerodynamik
    drag_coefficient: float = Field(
        default=0.23,
        ge=0.0,
        description="Drag coefficient (cW) für Tesla Model 3 (Standardfassung). "
        "Neuere Generation (Highland facelift) erreicht 0.219, "
        "aber 0.23 bleibt als Standard für breite Kompatibilität.",
    )
    frontal_area_m2: float = Field(
        default=2.22,
        ge=0.0,
        description="Frontalfläche in m² (Tesla Model 3).",
    )

    # Rollwiderstand
    rolling_resistance_coefficient: float = Field(
        default=0.011,
        ge=0.0,
        le=0.02,
        description="Rollwiderstandsbeiwert c_r für Model 3 mit Standardreifen "
        "bei 2.9 bar (42 psi). Bereich 0.010-0.011 typisch.",
    )

    # Masse
    mass_kg: float = Field(
        default=1706.0,
        ge=1500.0,
        le=2200.0,
        description="Fahrzeuggewicht in kg. Basis: "
        "Rear-Wheel Drive 3,759 lbs ≈ 1,706 kg. "
        "Long Range AWD ≈ 1,828 kg, Performance ≈ 1,845 kg. "
        "Obere Grenze 2200 kg für alle Frontend-Presets.",
    )

    # Batterie & Antrieb
    battery_capacity_kwh: float = Field(
        default=62.5,
        ge=50.0,
        le=200.0,
        description="Nutzbare Batteriekapazität in kWh. "
        "Standard Range (2025 LFP): 62.5 kWh (Gesamt ca. 65 kWh). "
        "Long Range/Performance: ~75-82 kWh nutzbar. "
        "Erhöht für Testzwecke auf 200 kWh.",
    )
    wirkungsgrad_antrieb: float = Field(
        default=0.94,
        ge=0.85,
        le=0.99,
        description="Wirkungsgrad des Elektromotors (±2% Toleranz). Typische Werte: 92-96 %.",
    )
    wirkungsgrad_rekuperation: float = Field(
        default=0.75,
        ge=0.65,
        le=0.85,
        description="Gesamtwirkungsgrad für regenerative Bremsung "
        "(Kettenwirkungsgrad: Rad → Motor → Batterie ≈ 75 %).",
    )

    # Nebenverbraucher
    auxiliary_baseline_kw: float = Field(
        default=0.34,
        ge=0.0,
        le=1.0,
        description="Baseline-Verbrauch in kW bei Parke- und Standby-Bedingungen "
        "(ohne Klimaanlage/Heizung).",
    )
    klimaanlage_max_kw: float = Field(
        default=5.5,
        ge=3.0,
        le=8.0,
        description="Maximale Leistungsaufnahme der Klimaanlage (A/C). "
        "Full blast ≈ 5-7 kW, typischer Betrieb ≈ 1-4 kW.",
    )
    heizung_max_kw: float = Field(
        default=6.0,
        ge=3.0,
        le=10.0,
        description="Maximale Heizleistung (PTC-Heizer oder Wärmepumpe). "
        "PTC-Heizung (ältere Modelle): bis 7 kW. "
        "Wärmepumpe (Highland): effizienter, aber 6 kW als obere Grenze sicher.",
    )
    komforttemperatur_min_c: float = Field(
        default=18.0,
        ge=10.0,
        le=22.0,
        description="Untere Komforttemperaturgrenze (°C). "
        "Unterhalb dieses Wertes steigt Heizungsleistung linear an.",
    )
    komforttemperatur_max_c: float = Field(
        default=24.0,
        ge=20.0,
        le=28.0,
        description="Obere Komforttemperaturgrenze (°C). "
        "Darüber steigt Klimaanlagenleistung linear an.",
    )

    # Reifentyp & Dachbox
    tire_type: str = Field(
        default="standard",
        description="Reifentyp, der den Rollwiderstand moduliert.",
    )
    roof_box: bool = Field(
        default=False,
        description="Vorhandensein einer Dachbox (erhöht drag_coefficient um ~0.03-0.05).",
    )

    @field_validator("drag_coefficient")
    @classmethod
    def adjust_cw_for_dachbox(cls, v: float) -> float:
        """Passive Anpassung des cw-Werts bei Dachbox."""
        return v  # Wird in Berechnungsmethode berücksichtigt

    @model_validator(mode="after")
    def adjust_cr_for_reifentyp(self) -> VehicleEnergyParameters:
        """Passive Anpassung des Rollwiderstands für verschiedene Reifentypen."""
        typ_factors = {
            "standard": 1.0,
            "winter": 1.25,
            "low_rolling_resistance": 0.9,
            "performance": 1.1,
        }
        factor = typ_factors.get(self.tire_type, 1.0)
        self.rolling_resistance_coefficient *= factor
        return self


class SegmentEnergyResult(BaseModel):
    """Ergebnis der Energieberechnung für ein Route-Segment."""

    segment_index: int
    energiebedarf_kwh: float  # Positiv: Verbrauch, Negativ: Rekuperation (überschüssige Energie)
    rekuperation_kwh: float  # Betrag der regenerativ gewonnenen Energie (immer ≥ 0)
    energiebedarf_brutto_kwh: float  # Summe aller Verbraucher (ohne Rekuperation)
    geschwindigkeit_m_s: float  # Mittlere Geschwindigkeit im Segment (m/s)
    fahrzeit_s: float  # Fahrzeit des Segments (s)
    streckenlaenge_m: float  # Länge des Segments (m)
