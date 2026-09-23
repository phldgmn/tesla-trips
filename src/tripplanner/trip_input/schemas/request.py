"""API-Request-Schemata für den /trips-Endpunkt."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tripplanner.trip_input.models import VehicleProfile
from tripplanner.trip_input.schemas.base import CamelCaseAPI

Lat = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Lon = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
LatLon = tuple[Lat, Lon]

MAX_WAYPOINTS = 25
MAX_LIST_ITEMS = 50


class WaypointAPI(BaseModel):
    """API-Request für Zwischenstopp."""

    model_config = ConfigDict(extra="forbid")

    koordinate: LatLon = Field(..., description="(lat, lon) Koordinate in Dezimalgrad")
    aufenthaltsdauer_s: int | None = Field(
        None, ge=0, description="Mindestaufenthaltsdauer in Sekunden"
    )
    geplante_abfahrt: datetime | None = Field(
        None,
        description="Gewünschter frühester Abfahrtszeitpunkt (ISO-8601)",
    )
    ladeleistung_kw: float | None = Field(
        None,
        ge=0.0,
        le=350.0,
        description=("Vor Ort verfügbare Ladeleistung an diesem Zwischenstopp in kW, optional"),
    )


class FaehrAusschlussAPI(CamelCaseAPI):
    """API-Request für eine zu vermeidende, zuvor erkannte Fährverbindung."""

    name: str = Field(..., description="Anzeigename der Fährverbindung")
    bbox_sw: LatLon = Field(..., description="Südwest-Ecke der Bounding Box")
    bbox_ne: LatLon = Field(..., description="Nordost-Ecke der Bounding Box")


class FaehrZeitfensterAPI(CamelCaseAPI):
    """API-Request für einen vorgegebenen Fährfahrplan (Abfahrt/Ankunft)."""

    name: str = Field(..., description="Anzeigename der Fährverbindung")
    bbox_sw: LatLon = Field(..., description="Südwest-Ecke der Bounding Box")
    bbox_ne: LatLon = Field(..., description="Nordost-Ecke der Bounding Box")
    abfahrt: datetime = Field(..., description="Vorgegebene Abfahrtszeit (ISO-8601)")
    ankunft: datetime = Field(..., description="Vorgegebene Ankunftszeit (ISO-8601)")


class LadedauerVorgabeAPI(CamelCaseAPI):
    """API-Request für eine vom Nutzer vorgegebene feste Ladedauer an einer Station."""

    station_id: str = Field(..., min_length=1, description="Eindeutige ID der Ladestation")
    charging_duration_s: int = Field(
        ..., ge=0, description="Vorgegebene feste Ladedauer in Sekunden"
    )


class PreferencesAPI(CamelCaseAPI):
    """Nutzerpräferenzen. Derzeit ohne Felder; unbekannte Schlüssel werden abgelehnt."""


class TripRequestAPI(CamelCaseAPI):
    """API-Request für /trips-Endpunkt."""

    start: LatLon = Field(..., description="(lat, lon) Startkoordinate")
    destination: LatLon = Field(..., description="(lat, lon) Zielkoordinate")
    waypoints: list[WaypointAPI] = Field(
        default_factory=list, max_length=MAX_WAYPOINTS, description="Liste von Zwischenstopps"
    )
    departure_time: datetime = Field(
        ..., description="ISO-8601 Abfahrtszeit (z. B. '2026-08-15T08:30:00')"
    )
    vehicle_profile: VehicleProfile = Field(..., description="Physikalisches Fahrzeugprofil")
    start_soc_pct: float = Field(80.0, ge=0.0, le=100.0, description="Start-SoC in Prozent")
    target_soc_pct: float = Field(20.0, ge=0.0, le=100.0, description="Ziel-SoC in Prozent")
    min_arrival_soc_pct: float = Field(
        5.0,
        ge=0.0,
        le=100.0,
        description=(
            "Minimal zulässiger SoC beim Ankommen an einer Ladestation "
            "(darf niedriger sein als die allgemeine Sicherheitsreserve auf "
            "offener Strecke, da dort garantiert nachgeladen wird)"
        ),
    )
    min_charging_time_s: int = Field(
        600,
        ge=0,
        le=1800,
        description=(
            "Minimale Dauer eines einzelnen Ladevorgangs in Sekunden, wenn "
            "geladen wird (verhindert unnötig kurze Ladehalte, ohne den "
            "Ladehalt an sich zu erzwingen)"
        ),
    )
    max_charge_soc_pct: float = Field(
        100.0,
        ge=0.0,
        le=100.0,
        description=(
            "Upper limit for the target SoC at regular charging stops "
            "(Supercharger stations) in percent. 100.0 = disabled."
        ),
    )
    preferences: PreferencesAPI = Field(
        default_factory=PreferencesAPI, description="Nutzerpräferenzen (derzeit keine)"
    )
    avoid_all_ferries: bool = Field(
        default=False, description="Falls True, werden alle Fährverbindungen vermieden"
    )
    highway_preference: Literal["off", "low", "medium", "high"] = Field(
        default="off",
        description=(
            "Autobahnpräferenz-Stufe: 'off' (keine Präferenz), 'low' (priority-Boost "
            "*1.1), 'medium' (*1.2), 'high' (*1.3) für road_class == MOTORWAY, "
            "ohne Nicht-Autobahn-Routen auszuschließen."
        ),
    )
    avoided_ferries: list[FaehrAusschlussAPI] = Field(
        default_factory=list,
        max_length=MAX_LIST_ITEMS,
        description=(
            "Liste spezifischer, zuvor erkannter Fährverbindungen, die vermieden werden sollen"
        ),
    )
    ferry_time_windows: list[FaehrZeitfensterAPI] = Field(
        default_factory=list,
        max_length=MAX_LIST_ITEMS,
        description=(
            "Vom Nutzer vorgegebene Abfahrts-/Ankunftszeiten für zuvor erkannte Fährverbindungen"
        ),
    )
    charging_duration_specifications: list[LadedauerVorgabeAPI] = Field(
        default_factory=list,
        max_length=MAX_LIST_ITEMS,
        description="Vom Nutzer vorgegebene feste Ladedauern für einzelne Ladehalte",
    )
    weather_detail_level: Literal["off", "low", "medium", "high"] = Field(
        default="high",
        description=(
            "Weather detail level: 'off', 'low', 'medium', or 'high'. "
            "'low'/'medium' use coarser weather resolution and complete faster; "
            "'off' skips weather entirely (placeholder values); 'high' uses "
            "per-segment weather (default, exact behavior matching the legacy "
            "wetter_beruecksichtigen=True)."
        ),
    )

    @model_validator(mode="before")
    @staticmethod
    def _map_legacy_wetter_boolean(data: dict[str, object]) -> dict[str, object]:
        """Map legacy wetter_beruecksichtigen boolean to weather_detail_level.

        Handles both the old field name (wetter_beruecksichtigen: bool) and
        defensively: the new field name with a boolean value from clients
        that send the new field with the old type.
        """
        if not isinstance(data, dict):
            return data

        # Legacy field name: wetter_beruecksichtigen -> weather_detail_level
        if "wetter_beruecksichtigen" in data and "weatherDetailLevel" not in data:
            raw = data.pop("wetter_beruecksichtigen")
            if isinstance(raw, bool):
                data["weatherDetailLevel"] = "high" if raw else "off"
            else:
                raise ValueError(
                    f"wetter_beruecksichtigen must be a boolean (True/False), "
                    f"got {raw!r}. Use weatherDetailLevel instead."
                )

        # Defensive: wetter_detailgrad sent as a raw JSON boolean
        if data.get("weatherDetailLevel") is True:
            data["weatherDetailLevel"] = "high"
        elif data.get("weatherDetailLevel") is False:
            data["weatherDetailLevel"] = "off"

        return data

    consider_construction_sites: bool = Field(
        default=True,
        description=(
            "Falls False, wird der Baustellen-Provider für diese Berechnung "
            "übersprungen (keine Geschwindigkeitsreduktion durch Baustellen), um "
            "die Berechnungsdauer zu reduzieren."
        ),
    )
