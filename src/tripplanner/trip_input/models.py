"""Eingabemodelle für Reiseanfragen.

Datenmodelle für `trip_input`: `TripRequest`, `Waypoint`, `VehicleProfile`.
Konsumiert von `routing` (Start/Ziel/Zwischenstopps) und `energy`/`optimization`
(Fahrzeugparameter). Alle Koordinaten sind `(lat, lon)` in Dezimalgrad (WGS84),
siehe `tripplanner.geo`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

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
    geplante_abfahrt: datetime | None = Field(
        default=None,
        description="Gewünschter frühester Abfahrtszeitpunkt an diesem Wegpunkt",
    )
    ladeleistung_kw: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Vor Ort verfügbare Ladeleistung an diesem Zwischenstopp in kW (z. B. "
            "Wallbox beim Übernachtungsziel), optional. Wird nur während einer "
            "durch `aufenthaltsdauer`/`geplante_abfahrt` erzwungenen Wartezeit "
            "genutzt - ohne Wartezeit findet kein Ladevorgang statt, da kein "
            "Zeitfenster dafür existiert."
        ),
    )


class FerryExclusion(BaseModel):
    """Eine vom Nutzer zu vermeidende Fährverbindung.

    Stammt aus einer zuvor per `tripplanner.routing.erkenne_faehren()` aus einer
    berechneten Route erkannten `FaehrSegment`-Struktur (gleiche Feldnamen für
    `name`/`bbox_sw`/`bbox_no`, aber eigenständig definiert): `routing` importiert
    bereits `trip_input.models` (`TripRequest`), ein Import in Gegenrichtung würde
    einen Modul-Zyklus erzeugen. Der API-Layer (`trip_input.api`, der beide Module
    bereits importiert) konvertiert zwischen beiden Repräsentationen.
    """

    name: str = Field(
        ...,
        description="Anzeigename der Fährverbindung (aus einer vorherigen Routenberechnung)",
    )
    bbox_sw: Coordinate = Field(
        ..., description="Südwest-Ecke der (gepufferten) Bounding Box um die Fährverbindung"
    )
    bbox_no: Coordinate = Field(
        ..., description="Nordost-Ecke der (gepufferten) Bounding Box um die Fährverbindung"
    )


class FaehrZeitfenster(BaseModel):
    """Vom Nutzer vorgegebene Abfahrts-/Ankunftszeit für eine Fährverbindung.

    Zur Abstimmung der Planung mit dem tatsächlichen Fährfahrplan.
    Identifikation über `name`/`bbox_sw`/`bbox_no` wie `FerryExclusion` (aus einer
    vorherigen Routenberechnung via `tripplanner.routing.erkenne_faehren()`). Der
    API-Layer (`trip_input.api`) matcht dies gegen die frisch berechnete Route und
    reicht bei Treffer die feste Abfahrts-/Ankunftszeit als Zeitplan-Vorgabe an
    `optimization.optimizer` weiter (siehe `_matche_faehr_zeitfenster`).
    """

    name: str = Field(
        ...,
        description="Anzeigename der Fährverbindung (aus einer vorherigen Routenberechnung)",
    )
    bbox_sw: Coordinate = Field(
        ..., description="Südwest-Ecke der (gepufferten) Bounding Box um die Fährverbindung"
    )
    bbox_no: Coordinate = Field(
        ..., description="Nordost-Ecke der (gepufferten) Bounding Box um die Fährverbindung"
    )
    abfahrt: datetime = Field(..., description="Vorgegebene Abfahrtszeit der Fähre")
    ankunft: datetime = Field(..., description="Vorgegebene Ankunftszeit der Fähre")

    @field_validator("ankunft")
    @classmethod
    def _arrival_after_departure(cls, v: datetime, info: ValidationInfo) -> datetime:
        """Stellt sicher, dass die Ankunft zeitlich nach der Abfahrt liegt."""
        abfahrt = info.data.get("abfahrt")
        if abfahrt is not None and v <= abfahrt:
            raise ValueError("ankunft muss zeitlich nach abfahrt liegen")
        return v


class LadedauerVorgabe(BaseModel):
    """Vom Nutzer vorgegebene feste Ladedauer für einen Ladehalt an einer Station.

    Zur Nachjustierung des automatisch berechneten Ladeplans (z. B. anhand
    tatsächlicher Wartezeiten an der Säule oder gewünschter Pausenlänge).
    Identifikation über die stabile `station_id` (siehe
    `tripplanner.charging_infrastructure.models.ChargingStation.station_id`) statt
    Koordinate/Bounding-Box, da eine Ladestation - anders als eine Fährlinie - über
    mehrere Routenberechnungen hinweg immer dieselbe eindeutige ID behält.
    """

    station_id: str = Field(..., min_length=1, description="Eindeutige ID der Ladestation")
    ladedauer_s: int = Field(..., ge=0, description="Vorgegebene feste Ladedauer in Sekunden")


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
    alle_faehren_vermeiden: bool = Field(
        default=False,
        description=(
            "Falls True, werden alle Fährverbindungen bei der Routenberechnung "
            "vermieden (GraphHopper custom_model: road_environment == FERRY "
            "ausgeschlossen)."
        ),
    )
    autobahn_praeferenz: Literal["off", "low", "medium", "high"] = Field(
        default="off",
        description=(
            "Grad der Autobahnpräferenz bei der Routenberechnung: 'off' (keine "
            "Präferenz), 'low' (priority *1.1), 'medium' (*1.2), 'high' (*1.3) "
            "für road_class == MOTORWAY im GraphHopper custom_model, ohne "
            "Nicht-Autobahn-Routen auszuschließen (z. B. wenn ein Ladehalt "
            "abseits der Autobahn liegt)."
        ),
    )
    vermiedene_faehren: list[FerryExclusion] = Field(
        default_factory=list,
        description=(
            "Liste spezifischer, zuvor erkannter Fährverbindungen, die bei der "
            "Routenberechnung vermieden werden sollen (siehe FerryExclusion)."
        ),
    )
    faehr_zeitfenster: list[FaehrZeitfenster] = Field(
        default_factory=list,
        description=(
            "Vom Nutzer vorgegebene Abfahrts-/Ankunftszeiten für zuvor erkannte "
            "Fährverbindungen, zur Abstimmung mit dem tatsächlichen Fährfahrplan "
            "(siehe FaehrZeitfenster)."
        ),
    )
    ladedauer_vorgaben: list[LadedauerVorgabe] = Field(
        default_factory=list,
        description=(
            "Vom Nutzer vorgegebene feste Ladedauern für einzelne Ladehalte, "
            "identifiziert über die Stations-ID (siehe LadedauerVorgabe)."
        ),
    )
    praeferenzen: dict[str, object] = Field(
        default_factory=dict,
        description="Erweiterbare Nutzerpräferenzen (aktuell nicht spezifiziert)",
    )
