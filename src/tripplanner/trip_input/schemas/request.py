"""API-Request-Schemata für den /trips-Endpunkt."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from tripplanner.trip_input.models import VehicleProfile


class WaypointAPI(BaseModel):
    """API-Request für Zwischenstopp."""

    koordinate: tuple[float, float] = Field(..., description="(lat, lon) Koordinate in Dezimalgrad")
    aufenthaltsdauer_s: int | None = Field(
        None, ge=0, description="Mindestaufenthaltsdauer in Sekunden"
    )
    geplante_abfahrt: str | None = Field(
        None,
        description="Gewünschter frühester Abfahrtszeitpunkt (ISO-8601)",
    )
    ladeleistung_kw: float | None = Field(
        None,
        ge=0.0,
        description=("Vor Ort verfügbare Ladeleistung an diesem Zwischenstopp in kW, optional"),
    )


class FaehrAusschlussAPI(BaseModel):
    """API-Request für eine zu vermeidende, zuvor erkannte Fährverbindung."""

    name: str = Field(..., description="Anzeigename der Fährverbindung")
    bbox_sw: tuple[float, float] = Field(..., description="Südwest-Ecke der Bounding Box")
    bbox_no: tuple[float, float] = Field(..., description="Nordost-Ecke der Bounding Box")


class FaehrZeitfensterAPI(BaseModel):
    """API-Request für einen vorgegebenen Fährfahrplan (Abfahrt/Ankunft)."""

    name: str = Field(..., description="Anzeigename der Fährverbindung")
    bbox_sw: tuple[float, float] = Field(..., description="Südwest-Ecke der Bounding Box")
    bbox_no: tuple[float, float] = Field(..., description="Nordost-Ecke der Bounding Box")
    abfahrt: str = Field(..., description="Vorgegebene Abfahrtszeit (ISO-8601)")
    ankunft: str = Field(..., description="Vorgegebene Ankunftszeit (ISO-8601)")


class LadedauerVorgabeAPI(BaseModel):
    """API-Request für eine vom Nutzer vorgegebene feste Ladedauer an einer Station."""

    station_id: str = Field(..., min_length=1, description="Eindeutige ID der Ladestation")
    ladedauer_s: int = Field(..., ge=0, description="Vorgegebene feste Ladedauer in Sekunden")


class TripRequestAPI(BaseModel):
    """API-Request für /trips-Endpunkt."""

    start: tuple[float, float] = Field(..., description="(lat, lon) Startkoordinate")
    ziel: tuple[float, float] = Field(..., description="(lat, lon) Zielkoordinate")
    zwischenstopps: list[WaypointAPI] = Field(
        default_factory=list, description="Liste von Zwischenstopps"
    )
    abfahrtszeit: str = Field(
        ..., description="ISO-8601 Abfahrtszeit (z. B. '2026-08-15T08:30:00')"
    )
    fahrzeugprofil: VehicleProfile = Field(..., description="Physikalisches Fahrzeugprofil")
    start_soc_pct: float = Field(80.0, ge=0.0, le=100.0, description="Start-SoC in Prozent")
    ziel_soc_pct: float = Field(20.0, ge=0.0, le=100.0, description="Ziel-SoC in Prozent")
    mindest_ankunfts_soc_pct: float = Field(
        5.0,
        ge=0.0,
        le=100.0,
        description=(
            "Minimal zulässiger SoC beim Ankommen an einer Ladestation "
            "(darf niedriger sein als die allgemeine Sicherheitsreserve auf "
            "offener Strecke, da dort garantiert nachgeladen wird)"
        ),
    )
    mindest_ladezeit_s: int = Field(
        600,
        ge=0,
        le=1800,
        description=(
            "Minimale Dauer eines einzelnen Ladevorgangs in Sekunden, wenn "
            "geladen wird (verhindert unnötig kurze Ladehalte, ohne den "
            "Ladehalt an sich zu erzwingen)"
        ),
    )
    max_lade_soc_pct: float = Field(
        100.0,
        ge=0.0,
        le=100.0,
        description=(
            "Upper limit for the target SoC at regular charging stops "
            "(Supercharger stations) in percent. 100.0 = disabled."
        ),
    )
    praeferenzen: dict[str, object] = Field(default_factory=dict, description="Nutzerpräferenzen")
    alle_faehren_vermeiden: bool = Field(
        default=False, description="Falls True, werden alle Fährverbindungen vermieden"
    )
    autobahn_bevorzugen: bool = Field(
        default=False,
        description=(
            "Falls True, werden Autobahnen leicht bevorzugt (GraphHopper priority-Boost "
            "für road_class == MOTORWAY), ohne Nicht-Autobahn-Routen auszuschließen."
        ),
    )
    vermiedene_faehren: list[FaehrAusschlussAPI] = Field(
        default_factory=list,
        description=(
            "Liste spezifischer, zuvor erkannter Fährverbindungen, die vermieden werden sollen"
        ),
    )
    faehr_zeitfenster: list[FaehrZeitfensterAPI] = Field(
        default_factory=list,
        description=(
            "Vom Nutzer vorgegebene Abfahrts-/Ankunftszeiten für zuvor erkannte Fährverbindungen"
        ),
    )
    ladedauer_vorgaben: list[LadedauerVorgabeAPI] = Field(
        default_factory=list,
        description="Vom Nutzer vorgegebene feste Ladedauern für einzelne Ladehalte",
    )
    wetter_detailgrad: Literal["off", "low", "medium", "high"] = Field(
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
        """Map legacy wetter_beruecksichtigen boolean to wetter_detailgrad.

        Handles both the old field name (wetter_beruecksichtigen: bool) and
        defensively: the new field name with a boolean value from clients
        that send the new field with the old type.
        """
        if not isinstance(data, dict):
            return data

        # Legacy field name: wetter_beruecksichtigen -> wetter_detailgrad
        if "wetter_beruecksichtigen" in data and "wetter_detailgrad" not in data:
            raw = data.pop("wetter_beruecksichtigen")
            if isinstance(raw, bool):
                data["wetter_detailgrad"] = "high" if raw else "off"
            else:
                raise ValueError(
                    f"wetter_beruecksichtigen must be a boolean (True/False), "
                    f"got {raw!r}. Use wetter_detailgrad instead."
                )

        # Defensive: wetter_detailgrad sent as a raw JSON boolean
        if data.get("wetter_detailgrad") is True:
            data["wetter_detailgrad"] = "high"
        elif data.get("wetter_detailgrad") is False:
            data["wetter_detailgrad"] = "off"

        return data

    baustellen_beruecksichtigen: bool = Field(
        default=True,
        description=(
            "Falls False, wird der Baustellen-Provider für diese Berechnung "
            "übersprungen (keine Geschwindigkeitsreduktion durch Baustellen), um "
            "die Berechnungsdauer zu reduzieren."
        ),
    )
