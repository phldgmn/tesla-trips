"""API-Response-Modelle für die Supercharger-Endpunkte."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tripplanner.charging_infrastructure import ChargingStation, StallType


class SuperchargerStationAPI(BaseModel):
    """API-Response-Modell fuer eine Supercharger-Station."""

    slug: str = Field(..., description="tesla_location_id (location_url_slug)")
    name: str = Field(..., description="Standortname")
    latitude: float = Field(..., description="WGS84 Breitengrad")
    longitude: float = Field(..., description="WGS84 Laengengrad")
    country: str = Field(..., description="ISO-2 Laendercode")
    total_stalls: int = Field(..., description="Anzahl Ladeplaetze")
    power_kilowatt: int = Field(..., description="Maximale Ladeleistung kW")
    status: str = Field(..., description="Betriebsstatus (OPEN, TEMP_CLOSED, ...)")
    stalls_v2: int = Field(default=0)
    stalls_v3: int = Field(default=0)
    stalls_v3_ultra: int = Field(default=0)
    stalls_v4: int = Field(default=0)
    ist_24_7: bool = Field(default=True, description="24/7 zugaenglich")
    date_opened: str | None = Field(default=None, description="Eroeffnungsdatum")


class SuperchargerStationDetailAPI(SuperchargerStationAPI):
    """Detaillierte API-Response fuer eine Supercharger-Station."""

    connector_types: list[str] = Field(default_factory=list)
    last_updated_utc: str = Field(..., description="Letzte Aktualisierung ISO-8601")
    access_type: str | None = Field(default=None)
    open_to_non_tesla: bool = Field(default=False)


def _station_to_api(station: ChargingStation) -> SuperchargerStationAPI:
    """Wandelt ein ChargingStation-Modell in das API-Response-Modell um."""
    return SuperchargerStationAPI(
        slug=station.station_id,
        name=station.name.replace("Tesla Supercharger - ", ""),
        latitude=station.coordinate[0],
        longitude=station.coordinate[1],
        country=station.country,
        total_stalls=sum(station.stalls.values()) if station.stalls else 0,
        power_kilowatt=int(station.max_ladeleistung_kw),
        status=station.status,
        stalls_v2=station.stalls.get(StallType.V2, 0) if station.stalls else 0,
        stalls_v3=station.stalls.get(StallType.V3, 0) if station.stalls else 0,
        stalls_v3_ultra=station.stalls.get(StallType.V3_ULTRA, 0) if station.stalls else 0,
        stalls_v4=station.stalls.get(StallType.V4, 0) if station.stalls else 0,
        ist_24_7=station.ist_24_7 if hasattr(station, "ist_24_7") else True,
        date_opened=None,
    )
