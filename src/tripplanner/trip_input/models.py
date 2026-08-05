"""Eingabemodelle für Reiseanfragen.

Datenmodelle für `trip_input`: `TripRequest`, `Waypoint`, `VehicleProfile`.
Konsumiert von `routing` (Start/Ziel/Zwischenstopps) und `energy`/`optimization`
(Fahrzeugparameter). Alle Koordinaten sind `(lat, lon)` in Dezimalgrad (WGS84),
siehe `tripplanner.geo`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from tripplanner.geo import Coordinate


class Waypoint(BaseModel):
    """Ein Pflicht-Wegpunkt mit Koordinate und optionaler Mindestaufenthaltsdauer.

    Ein Zwischenstopp ist konzeptionell unabhängig von einem Ladestopp (siehe
    `docs/06-offene-punkte-widersprueche.md`, Punkt 4): er kann mit einem
    Ladehalt zusammenfallen, ist aber kein automatischer Ladepunkt.
    """

    koordinate: Coordinate = Field(..., description="(lat, lon) Koordinate in Dezimalgrad")
    aufenthaltsdauer: timedelta | None = Field(
        default=None, description="Optionale Mindestaufenthaltsdauer an diesem Wegpunkt"
    )


class VehicleProfile(BaseModel):
    """Physikalisches Fahrzeugprofil, konsumiert von `energy`/`optimization`.

    Feldnamen entsprechen `tripplanner.energy.models.VehicleEnergyParameters`
    (siehe `docs/plans/06-energy.md`), damit `trip_input` direkt in ein
    `VehicleEnergyParameters`-Objekt überführt werden kann.
    """

    masse_kg: float = Field(..., gt=0, description="Fahrzeugmasse inkl. Beladung in kg")
    cw_wert: float = Field(..., ge=0.0, description="Luftwiderstandsbeiwert (cW)")
    stirnflaeche_m2: float = Field(..., gt=0, description="Stirnfläche in m²")
    rollwiderstandsbeiwert: float = Field(..., ge=0.0, description="Rollwiderstandsbeiwert c_r")
    batteriekapazitaet_kwh: float = Field(
        ..., gt=0, description="Nutzbare Batteriekapazität in kWh"
    )
    nebenverbraucher_baseline_kw: float = Field(
        default=0.34, ge=0.0, description="Baseline-Leistung der Nebenverbraucher in kW"
    )
    reifentyp: Literal["standard", "winter", "low_rolling_resistance", "performance"] = Field(
        default="standard", description="Reifentyp, moduliert den Rollwiderstand"
    )
    dachbox: bool = Field(default=False, description="Vorhandensein einer Dachbox")


class TripRequest(BaseModel):
    """Vollständige Reiseanfrage: Start, Ziel, Zwischenstopps, Abfahrtszeit, Fahrzeug."""

    start: Coordinate = Field(..., description="(lat, lon) Startkoordinate in Dezimalgrad")
    ziel: Coordinate = Field(..., description="(lat, lon) Zielkoordinate in Dezimalgrad")
    zwischenstopps: list[Waypoint] = Field(
        default_factory=list,
        description="Geordnete Liste von Pflicht-Zwischenstopps zwischen Start und Ziel",
    )
    abfahrtszeit: datetime = Field(..., description="Geplante Abfahrtszeit")
    fahrzeugprofil: VehicleProfile = Field(..., description="Physikalisches Fahrzeugprofil")
    praeferenzen: dict[str, object] = Field(
        default_factory=dict,
        description="Erweiterbare Nutzerpräferenzen (aktuell nicht spezifiziert)",
    )
