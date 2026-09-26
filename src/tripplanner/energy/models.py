"""data models for the energy module (physical consumption model).

Pydantic-modele zur Darstellung von Fahrzeugparametern und segment-Ergebnissen.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class VehicleEnergyParameters(BaseModel):
    """Physical vehicle parameters for energy calculation.

    All default values are based on publicly verified specifications
    des Tesla Model 3 (2024/2025).
    """

    # Aerodynamics
    drag_coefficient: float = Field(
        default=0.23,
        ge=0.0,
        description="Drag coefficient (cW) for Tesla Model 3 (Standard trim). "
        "Neuere Generation (Highland facelift) erreicht 0.219, "
        "but 0.23 remains as standard for latitude compatibility.",
    )
    frontal_area_m2: float = Field(
        default=2.22,
        ge=0.0,
        description="Frontal area in m² (Tesla Model 3).",
    )

    # rolling_resistance
    rolling_resistance_coefficient: float = Field(
        default=0.011,
        ge=0.0,
        le=0.02,
        description="Rolling resistance coefficient c_r for Model 3 with standard tires "
        "bei 2.9 bar (42 psi). Bereich 0.010-0.011 typisch.",
    )

    # Mass
    mass_kg: float = Field(
        default=1706.0,
        ge=1500.0,
        le=2200.0,
        description="vehicle_weight in kg. Basis: "
        "Rear-Wheel Drive 3,759 lbs ≈ 1,706 kg. "
        "Long Range AWD ≈ 1,828 kg, Performance ≈ 1,845 kg. "
        "Upper limit 2200 kg for all frontend presets.",
    )

    # Battery & Drive
    battery_capacity_kwh: float = Field(
        default=62.5,
        ge=50.0,
        le=200.0,
        description="Usable battery capacity in kWh. "
        "Standard Range (2025 LFP): 62.5 kWh (Gesamt ca. 65 kWh). "
        "Long Range/Performance: ~75-82 kWh nutzbar. "
        "Increased to 200 kWh for testing purposes.",
    )
    wirkungsgrad_antrieb: float = Field(
        default=0.94,
        ge=0.85,
        le=0.99,
        description="Efficiency of the electric motor (+/-2% tolerance). Typical values: 92-96%.",
    )
    wirkungsgrad_rekuperation: float = Field(
        default=0.75,
        ge=0.65,
        le=0.85,
        description="Overall efficiency for regenerative braking "
        "(Chain drive efficiency: wheel to motor to battery ≈ 75 %).",
    )

    # Nebenconsumptioner
    auxiliary_baseline_kw: float = Field(
        default=0.34,
        ge=0.0,
        le=1.0,
        description="Baseline-consumption in kW bei Parke- und Standby-Bedingungen "
        "(ohne Klimaanlage/Heizung).",
    )
    ac_max_kw: float = Field(
        default=5.5,
        ge=3.0,
        le=8.0,
        description="Maximale Leistungsaufnahme der Klimaanlage (A/C). "
        "Full blast ≈ 5-7 kW, typical operation ≈ 1-4 kW.",
    )
    heating_max_kw: float = Field(
        default=6.0,
        ge=3.0,
        le=10.0,
        description="Maximale Heizleistung (PTC-Heizer oder Waermepumpe). "
        "PTC-Heizung (aeltere Modelle): bis 7 kW. "
        "Waermepumpe (Highland): effizienter, aber 6 kW als obere Grenze sicher.",
    )
    comfort_temperature_min_c: float = Field(
        default=18.0,
        ge=10.0,
        le=22.0,
        description="Lower comfort temperature limit (degrees C). "
        "Unterhalb dieses Wertes steigt Heizungsleistung linear an.",
    )
    comfort_temperature_max_c: float = Field(
        default=24.0,
        ge=20.0,
        le=28.0,
        description="Upper comfort temperature limit (degrees C). "
        "AC power increases linearly above that.",
    )

    # Reifentyp & Dachbox
    tire_type: str = Field(
        default="standard",
        description="Reifentyp, der den rolling_resistance moduliert.",
    )
    roof_box: bool = Field(
        default=False,
        description="Presence of roof box (increases drag_coefficient by ~0.03-0.05).",
    )

    @field_validator("drag_coefficient")
    @classmethod
    def adjust_cw_for_dachbox(cls, v: float) -> float:
        """Passive Anpassung des cw-Werts bei Dachbox."""
        return v  # Considered in the calculation method

    @model_validator(mode="after")
    def adjust_cr_for_tire_type(self) -> VehicleEnergyParameters:
        """Passive adjustment of rolling resistance for different tire types."""
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
    """Result of energy calculation for a route segment."""

    segment_index: int
    energiebedarf_kwh: float  # Positive: consumption, negative: recuperation (excess energy)
    rekuperation_kwh: float  # Amount of regenerative energy recovered (immer ≥ 0)
    energiebedarf_brutto_kwh: float  # Summe aller Verbraucher (ohne recuperation)
    speed_ms: float  # Mittlere speed im Segment (m/s)
    drive_time_s: float  # drive_time_s des Segments (s)
    segment_length_m: float  # Laenge des Segments (m)
