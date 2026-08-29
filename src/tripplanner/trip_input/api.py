"""API-Schicht für trip_input: Orchestrierung der 11 Datenfluss-Schritte.

Diese Modul implementiert:
- `create_trip_simulation()`: Kernfunktion zur Orchestrierung aller Schritte
- FastAPI-Endpunkt `POST /trips` mit `TripRequestAPI` Request-Model
"""

from __future__ import annotations

__all__ = ["app", "create_trip_endpoint", "create_trip_simulation"]

import logging
import os
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator

from tripplanner.charging_infrastructure import (
    ChargingStation,
    ChargingStationProvider,
    StallType,
    get_all_charging_stations,
)
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.construction.models import ConstructionProvider, ConstructionZone
from tripplanner.elevation import ElevationProvider
from tripplanner.geo import haversine_distance_m
from tripplanner.routing import RoutingProvider
from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile
from tripplanner.trip_input.providers_factory import (
    ProductionProviders,
    build_production_providers,
    close_production_providers,
)
from tripplanner.weather.providers import WeatherProvider

from .pipeline import create_trip_simulation

# =============================================================================
# GraphHopper-Konfiguration
# =============================================================================

# Umgebungsvariable für die GraphHopper-Basis-URL (siehe README.md,
# .github/workflows/ci.yml).
GRAPHHOPPER_URL_ENV_VAR = "GRAPHHOPPER_URL"

# Default-Basis-URL, falls GRAPHHOPPER_URL nicht gesetzt ist.
DEFAULT_GRAPHHOPPER_BASE_URL = "http://localhost:8989"


# Distance below which two nearby construction-zone markers are merged into a
# single marker (with multiple `events`) for map display, so the user isn't
# shown near-duplicate pins for closely-spaced roadwork records on the same
# stretch of road. Deliberately larger than
# `construction.matching.MAX_DISTANCE_M` (which answers "is
# this roadwork actually on the route at all") - this constant instead
# answers "are two on-route roadworks close enough to show as one marker".
_CONSTRUCTION_ZONE_MERGE_DISTANCE_M = 5000.0


def _build_construction_zones_api(
    zones: list[ConstructionZone],
    route_segments: list[RouteSegment],
) -> list[ConstructionZoneAPI]:
    """Build grouped ConstructionZoneAPI entries from raw construction zones.

    Zones are sorted by their first affected segment index, then consecutive
    zones within ``_CONSTRUCTION_ZONE_MERGE_DISTANCE_M`` metres (haversine)
    of each other are merged into a single marker with multiple events.

    Args:
        zones: Raw construction zones from the provider.
        route_segments: Route segments for position resolution.

    Returns:
        List of ConstructionZoneAPI markers, each potentially merging nearby
        events.
    """
    valid_zones: list[ConstructionZone] = [z for z in zones if z.betroffene_segmente]
    valid_zones.sort(key=lambda z: z.betroffene_segmente[0] if z.betroffene_segmente else 0)

    construction_zones_api: list[ConstructionZoneAPI] = []
    last_position: Coordinate | None = None
    for zone in valid_zones:
        first_idx = zone.betroffene_segmente[0]
        if first_idx < 0 or first_idx >= len(route_segments):
            continue

        zone_position = route_segments[first_idx].geometrie[0]

        if (
            last_position is not None
            and haversine_distance_m(last_position, zone_position)
            <= _CONSTRUCTION_ZONE_MERGE_DISTANCE_M
        ):
            construction_zones_api[-1].events.append(
                ConstructionZoneEventAPI(
                    sperrungstyp=zone.sperrungstyp.value,
                    tempolimit_kmh=zone.tempolimit_kmh,
                    umleitungshinweis=zone.umleitungshinweis,
                    land=zone.land.value,
                    gueltig_von=zone.gueltig_von,
                    gueltig_bis=zone.gueltig_bis,
                )
            )
            last_position = zone_position
            continue

        # Start a new group
        construction_zones_api.append(
            ConstructionZoneAPI(
                position=zone_position,
                events=[
                    ConstructionZoneEventAPI(
                        sperrungstyp=zone.sperrungstyp.value,
                        tempolimit_kmh=zone.tempolimit_kmh,
                        umleitungshinweis=zone.umleitungshinweis,
                        land=zone.land.value,
                        gueltig_von=zone.gueltig_von,
                        gueltig_bis=zone.gueltig_bis,
                    )
                ],
                laenge_m=zone.laenge_m,
            ),
        )
        last_position = zone_position

    return construction_zones_api


# Kernfunktion: Orchestrierung aller 11 Schritte
# =============================================================================


# =============================================================================
# FastAPI-Endpunkt
# =============================================================================


logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Attaches a console handler to the `tripplanner` logger namespace.

    Without this, `uvicorn --reload` (see `run.sh`) never installs a handler
    for application loggers - only `uvicorn.*` loggers get one. Python's
    logging module then falls back to `logging.lastResort`, which only ever
    emits records at WARNING level or above, silently dropping every
    `logger.info(...)` pipeline-step log (see `_log_step`). The level is
    configurable via the `TRIPPLANNER_LOG_LEVEL` environment variable
    (default: `INFO`) so a slower/quieter deployment can raise it without a
    code change. Idempotent: safe to call multiple times (e.g. once per
    FastAPI TestClient lifespan cycle in tests) without installing duplicate
    handlers or duplicate log lines.
    """
    package_logger = logging.getLogger(__name__.split(".")[0])
    level_name = os.environ.get("TRIPPLANNER_LOG_LEVEL", "INFO")
    package_logger.setLevel(level_name)
    if not any(isinstance(h, logging.StreamHandler) for h in package_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        package_logger.addHandler(handler)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Verwaltet den Lebenszyklus der prozessweiten Provider-Ressourcen.

    Der GraphHopper-HTTP-Client und der Tesla-Supercharger-DB-Zugriff werden
    einmalig beim Start erzeugt (Connection-/Verbindungs-Pooling über alle
    Requests hinweg) statt pro Request neu aufgebaut zu werden. Die
    GraphHopper-Basis-URL ist über die Umgebungsvariable `GRAPHHOPPER_URL`
    konfigurierbar (Default: `http://localhost:8989`, siehe README.md).
    """
    _configure_logging()
    providers = await build_production_providers()
    app.state.providers = providers
    try:
        yield
    finally:
        await close_production_providers(providers)


app = FastAPI(title="Tesla Trip Planner API", version="0.1.0", lifespan=_lifespan)


def get_routing_provider(request: Request) -> RoutingProvider:
    """FastAPI-Dependency: liefert den produktiven RoutingProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `GraphHopperClient` für echtes Straßenrouting über OSM-Daten. In Tests via
    `app.dependency_overrides[get_routing_provider]` durch `FakeRoutingProvider`
    ersetzbar (siehe AGENTS.md: keine Live-Calls externer Datenquellen in
    Unit-Tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.routing


def get_charging_provider(request: Request) -> ChargingStationProvider:
    """FastAPI-Dependency: liefert den produktiven ChargingStationProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `TeslaChargingStationProvider` (SQLite-DB `data/tesla_superchargers.db`,
    siehe README.md) für echte Supercharger-Standorte. In Tests via
    `app.dependency_overrides[get_charging_provider]` durch eine
    `FakeChargingStationProvider`-Instanz mit angepassten Stationen ersetzbar
    (siehe AGENTS.md: keine Live-Calls externer Datenquellen in Unit-Tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.charging


def get_elevation_provider(request: Request) -> ElevationProvider:
    """FastAPI-Dependency: returns the production ElevationProvider for `/trips`.

    Uses the `ElevationProvider` created in `_lifespan` for elevation data.
    In tests, can be replaced via `app.dependency_overrides[get_elevation_provider]`
    with `FakeDataSource`.
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.elevation_provider


def get_weather_provider(request: Request) -> WeatherProvider:
    """FastAPI-Dependency: returns the production WeatherProvider for `/trips`.

    Uses the `OpenMeteoProvider` created in `_lifespan` for weather data.
    In tests, can be replaced via `app.dependency_overrides[get_weather_provider]`
    with `FakeWeatherProvider` (see AGENTS.md: no live calls to external data sources
    in unit tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.weather


def get_construction_provider(request: Request) -> ConstructionProvider:
    """FastAPI-Dependency: liefert den produktiven ConstructionProvider für `/trips`.

    Nutzt den in `_lifespan` erzeugten, prozessweit wiederverwendeten
    `ConstructionProvider` für Baustellendaten. In Tests via
    `app.dependency_overrides[get_construction_provider]` durch
    `FakeConstructionProvider` ersetzbar.
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.construction


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health-Check-Endpunkt.

    Ermöglicht dem Frontend zu prüfen, ob das Backend erreichbar ist.
    """
    return {"status": "ok"}


# ── Supercharger API ─────────────────────────────────────────────────────


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


@app.get("/superchargers")
async def list_superchargers(
    country: str | None = None,
) -> list[SuperchargerStationAPI]:
    """Listet alle Supercharger-Stationen aus der Datenbank.

    Args:
        country: Optionaler ISO-2 Laenderfilter.

    Returns:
        Liste von SuperchargerStationAPI.
    """
    stations = get_all_charging_stations()
    if country:
        stations = [s for s in stations if s.country == country]
    return [_station_to_api(s) for s in stations]


@app.get("/superchargers/{slug}")
async def get_supercharger_detail(
    slug: str,
) -> SuperchargerStationAPI:
    """Liefert Details zu einer Supercharger-Station.

    Args:
        slug: tesla_location_id (location_url_slug).

    Returns:
        SuperchargerStationAPI.

    Raises:
        HTTPException: 404 wenn Station nicht gefunden.
    """
    stations = get_all_charging_stations()
    station = next(
        (s for s in stations if _station_to_api(s).slug == slug),
        None,
    )
    if station is None:
        raise HTTPException(status_code=404, detail="Station nicht gefunden")
    return _station_to_api(station)


@app.post("/superchargers/{slug}/refresh")
async def refresh_supercharger(slug: str) -> SuperchargerStationAPI:
    """Aktualisiert eine Supercharger-Station mit frischen Daten von der Tesla API.

    Ruft die Tesla API server-seitig ueber `TeslaClient` ab (Default-
    Transport: `NodriverTeslaClient`, ein echter Chromium-Browser via CDP;
    alternativ `TeslaLocationsClient` mit curl_cffi/TLS-Fingerprint). Der
    Browser-Ansatz umgeht den Akamai-WAF-Block, dem einfache Python-HTTP-
    Clients (httpx) und zum Teil auch curl_cffi unterliegen. Ein direkter
    Cross-Origin-Fetch aus dem Frontend-JS ist keine Alternative: die
    Tesla-API liefert keine Access-Control-Allow-Origin-Header, wodurch der
    Browser das Lesen der Antwort unabhaengig vom WAF-Status verweigert.

    Args:
        slug: tesla_location_id (location_url_slug).

    Returns:
        Aktualisierte SuperchargerStationAPI.

    Raises:
        HTTPException: 404 wenn Station unbekannt oder Tesla-API keine Daten
            liefert, 502 bei WAF-Block, Netzwerkfehlern oder wenn die
            Tesla-Antwort nicht auf ChargingStation abgebildet werden kann
            (z. B. Land ausserhalb DE/DK/SE).
    """
    provider = TeslaChargingStationProvider()
    try:
        station = await provider.refresh_single_station(slug)
    except TeslaLocationsClient.CurlError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Tesla API nicht erreichbar: {e}",
        ) from e
    except ValidationError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Tesla-Antwort konnte nicht verarbeitet werden: {e}",
        ) from e
    finally:
        # Verbindung deterministisch schliessen: verhindert, dass eine
        # offene Transaktion (z. B. nach einem Fehler) den SQLite-
        # Schreibsperren-Lock fuer nachfolgende Requests blockiert.
        provider._db.close()
    if station is None:
        raise HTTPException(
            status_code=404,
            detail="Station nicht gefunden oder Tesla-API lieferte keine Daten",
        )
    return _station_to_api(station)


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


class FrameAPI(BaseModel):
    """Einzelner Simulationsframe in der API-Response."""

    zeitpunkt: str = Field(..., description="ISO-8601 Zeitpunkt")
    position: tuple[float, float] = Field(
        ..., description="(lat, lon), konsistent mit Domänenmodell"
    )
    distanz_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz vom Reisebeginn entlang der Route in Metern"
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0)
    zustand: str = Field(..., description="'FAHREN', 'LADEN' oder 'PAUSE'")
    geschwindigkeit_kmh: float = Field(..., ge=0.0)


class ChargingStopAPI(BaseModel):
    """Ladehalt in der API-Response, ein Eintrag pro tatsaechlichem Halt."""

    name: str = Field(..., description="Name der Ladestation")
    station_id: str = Field(..., description="Eindeutige ID der Ladestation")
    position: tuple[float, float] = Field(..., description="(lat, lon) der Ladestation")
    distanz_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz entlang der Route, an der abgebogen wird"
    )
    detour_geometrie: list[Coordinate] = Field(
        default_factory=list,
        description=(
            "Echte, ueber GraphHopper geroutete Geometrie von der Route zur Ladestation und "
            "zurueck (leer, falls die Detour-Route nicht ermittelt werden konnte)"
        ),
    )
    route_index_vor: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `route_geometrie`, ab dem `detour_geometrie` die Hauptroute ersetzt "
            "(None, falls `detour_geometrie` leer ist)"
        ),
    )
    route_index_nach: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `route_geometrie`, bis zu dem (inklusive) `detour_geometrie` die "
            "Hauptroute ersetzt (None, falls `detour_geometrie` leer ist)"
        ),
    )
    detour_station_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `detour_geometrie`, an dem die Ladestation tatsaechlich erreicht wird "
            "(None, falls `detour_geometrie` leer ist)"
        ),
    )
    ankunfts_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Ankunft in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC nach dem Laden in %")
    ladedauer_s: int = Field(..., ge=0, description="Ladedauer in Sekunden")
    energie_geladen_kwh: float = Field(..., ge=0.0, description="Geladene Energiemenge in kWh")
    ankunftszeit: str = Field(..., description="ISO-8601 Ankunftszeitpunkt an der Station")
    abfahrtszeit: str = Field(..., description="ISO-8601 Abfahrtszeitpunkt von der Station")
    price_per_kwh: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Applicable Tesla-owner rate per kWh at arrival time, from cached "
            "pricing data. None if no pricing data is cached yet for this station."
        ),
    )
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description=(
            "ISO-4217 currency of `price_per_kwh`/`estimated_cost`. None iff those are None."
        ),
    )
    estimated_cost: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Estimated cost of this charging stop (`energie_geladen_kwh * "
            "price_per_kwh`). None if no pricing data is cached yet for this station."
        ),
    )
    pricing_updated_utc: str | None = Field(
        default=None,
        description=(
            "ISO-8601 timestamp of the cached pricing data used for `price_per_kwh`. "
            "None if no pricing data has ever been scraped for this station."
        ),
    )


class FaehrSegmentAPI(BaseModel):
    """API-Response für eine in der berechneten Route erkannte Fährverbindung."""

    name: str = Field(..., description="Fährname (aus GraphHopper street_name oder Fallback)")
    laenge_m: float = Field(..., ge=0, description="Länge der Fährverbindung in Metern")
    bbox_sw: tuple[float, float] = Field(
        ..., description="Südwest-Ecke der gepufferten Bounding Box"
    )
    bbox_no: tuple[float, float] = Field(
        ..., description="Nordost-Ecke der gepufferten Bounding Box"
    )
    abfahrt: str | None = Field(
        default=None,
        description="Vom Nutzer vorgegebene Abfahrtszeit (ISO-8601), sofern vorhanden",
    )
    ankunft: str | None = Field(
        default=None,
        description="Vom Nutzer vorgegebene Ankunftszeit (ISO-8601), sofern vorhanden",
    )


class ChargingCostByCurrencyAPI(BaseModel):
    """Aggregated estimated charging cost in a single currency."""

    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code")
    amount: float = Field(..., ge=0.0, description="Summed cost in `currency`")


class ConstructionZoneEventAPI(BaseModel):
    """One underlying construction/roadwork event merged into a ConstructionZoneAPI marker."""

    sperrungstyp: str = Field(..., description="Art der Sperrung/Baustelle")
    tempolimit_kmh: int | None = Field(
        default=None, description="Reduziertes Tempolimit in km/h (None wenn keine Beschränkung)"
    )
    umleitungshinweis: str | None = Field(
        default=None, description="Freitext-Information zur Umleitung (optional)"
    )
    land: str = Field(..., description="Land, in dem die Baustelle liegt")
    gueltig_von: datetime = Field(..., description="Startzeitpunkt der Baustelle (ISO 8601)")
    gueltig_bis: datetime | None = Field(
        default=None, description="Endzeitpunkt der Baustelle (ISO 8601), None wenn unbestimmt"
    )


class ConstructionZoneAPI(BaseModel):
    """API-repräsentation eines Baustellen-Markers, der mehrere nahe Events zusammenfasst."""

    position: Coordinate = Field(
        ..., description="Repräsentative (lat, lon) Position (erstes Event entlang der Route)"
    )
    events: list[ConstructionZoneEventAPI] = Field(
        ..., description="Zusammengefasste Events (Länge > 1 = mehrere nahe Events gemerged)"
    )
    laenge_m: float | None = Field(
        default=None,
        description=(
            "Geschätzte Länge der betroffenen Straßenstrecke in Metern "
            "(None wenn nicht berechenbar)."
        ),
    )


class WaypointStopAPI(BaseModel):
    """Zwischenstopp-Aufenthalt in der API-Response, ein Eintrag pro Aufenthalt."""

    position: tuple[float, float] = Field(..., description="(lat, lon) des Zwischenstopps")
    distanz_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz entlang der Route bei diesem Zwischenstopp"
    )
    ankunftszeit: str = Field(..., description="ISO-8601 Ankunftszeitpunkt am Zwischenstopp")
    abfahrtszeit: str = Field(..., description="ISO-8601 Zeitpunkt der (erzwungenen) Abfahrt")
    ladeleistung_kw: float | None = Field(
        default=None, ge=0.0, description="Genutzte Ladeleistung in kW, None falls nicht geladen"
    )
    ankunfts_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Ankunft in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Abfahrt in %")
    energie_geladen_kwh: float = Field(
        ..., ge=0.0, description="Waehrend des Aufenthalts geladene Energiemenge in kWh"
    )


class TripSimulationResultAPI(BaseModel):
    """API-Response für /trips-Endpunkt."""

    gesamt_distanz_km: float = Field(..., description="Gesamtdistanz in km")
    gesamt_fahrzeit_min: float = Field(..., description="Gesamtfahrzeit in Minuten")
    gesamt_ladezeit_min: float = Field(..., description="Gesamtladezeit in Minuten")
    gesamt_wartezeit_min: float = Field(
        default=0.0,
        description=(
            "Erzwungene Wartezeit an Zwischenstopps OHNE Ladung, in Minuten "
            "(nicht in gesamt_fahrzeit_min/gesamt_ladezeit_min enthalten)"
        ),
    )
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start-SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC in %")
    frames: list[FrameAPI] = Field(..., description="Liste von Simulationsframes")
    charging_stops: list[ChargingStopAPI] = Field(
        default_factory=list, description="Ein Eintrag pro Ladehalt, fuer die Kartendarstellung"
    )
    waypoint_stops: list[WaypointStopAPI] = Field(
        default_factory=list,
        description="Ein Eintrag pro Zwischenstopp-Aufenthalt, fuer die Kartendarstellung",
    )
    route_geometrie: list[Coordinate] = Field(
        ...,
        description=(
            "Vollstaendige Streckengeometrie der berechneten Route (dichte GraphHopper-"
            "Polyline, nicht auf Simulationsframes reduziert) fuer eine winkeltreue "
            "Kartendarstellung."
        ),
    )
    erkannte_faehren: list[FaehrSegmentAPI] = Field(
        default_factory=list,
        description="In der berechneten Route erkannte Fährverbindungen (leer, falls keine)",
    )
    total_charging_cost: list[ChargingCostByCurrencyAPI] = Field(
        default_factory=list,
        description=(
            "Sum of estimated charging costs across all charging stops, grouped by "
            "currency (empty if no stop has cached pricing data; multiple entries if "
            "stops span several currencies, e.g. a DE-DK-SE trip)."
        ),
    )
    charging_stops_missing_pricing: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of charging stops excluded from `total_charging_cost` "
            "because no pricing data is cached yet for their station."
        ),
    )
    construction_zones: list[ConstructionZoneAPI] = Field(
        default_factory=list,
        description="Baustellen entlang der Route fuer die Kartendarstellung (leer, falls keine)",
    )


@app.post("/trips", response_model=TripSimulationResultAPI, status_code=201)
async def create_trip_endpoint(  # noqa: PLR0913, PLR0917
    request: TripRequestAPI,
    # B008: Depends(...) im Default ist das FastAPI-Standardidiom für Dependency
    # Injection, kein veränderliches Objekt/kein echter Bug (siehe FastAPI-Doku).
    routing_provider: RoutingProvider = Depends(get_routing_provider),  # noqa: B008
    charging_provider: ChargingStationProvider = Depends(get_charging_provider),  # noqa: B008
    weather_provider: WeatherProvider = Depends(get_weather_provider),  # noqa: B008
    construction_provider: ConstructionProvider = Depends(get_construction_provider),  # noqa: B008
    elevation_provider: ElevationProvider = Depends(get_elevation_provider),  # noqa: B008
) -> TripSimulationResultAPI:
    """Create a new trip simulation.

    Uses :func:`create_trip_simulation` to orchestrate all 11 data-flow steps.
    Routing uses the real GraphHopper server (via ``get_routing_provider``);
    without a running server the request fails with 502 (see README.md).

    ``request.wetter_detailgrad`` (``"off"``, ``"low"``, ``"medium"``,
    ``"high"``) drives weather resolution.  ``"off"`` skips the weather
    provider (``None``); ``"high"`` passes it through unchanged;
    ``"low"``/``"medium"`` also pass the provider but with reduced
    query granularity.  ``request.baustellen_beruecksichtigen`` controls
    construction-site detection independently.
    """
    # TripRequestAPI nach TripRequest konvertieren
    request_dict: dict[str, object] = {
        "start": request.start,
        "ziel": request.ziel,
        "zwischenstopps": [
            {
                "koordinate": wp.koordinate,
                "aufenthaltsdauer": timedelta(seconds=wp.aufenthaltsdauer_s)
                if wp.aufenthaltsdauer_s
                else None,
                "geplante_abfahrt": datetime.fromisoformat(wp.geplante_abfahrt)
                if wp.geplante_abfahrt
                else None,
                "ladeleistung_kw": wp.ladeleistung_kw,
            }
            for wp in request.zwischenstopps
        ],
        "abfahrtszeit": request.abfahrtszeit,
        "fahrzeugprofil": request.fahrzeugprofil.model_dump(),
        "praeferenzen": request.praeferenzen,
        "alle_faehren_vermeiden": request.alle_faehren_vermeiden,
        "vermiedene_faehren": [
            {"name": f.name, "bbox_sw": f.bbox_sw, "bbox_no": f.bbox_no}
            for f in request.vermiedene_faehren
        ],
        "faehr_zeitfenster": [
            {
                "name": f.name,
                "bbox_sw": f.bbox_sw,
                "bbox_no": f.bbox_no,
                "abfahrt": datetime.fromisoformat(f.abfahrt),
                "ankunft": datetime.fromisoformat(f.ankunft),
            }
            for f in request.faehr_zeitfenster
        ],
        "ladedauer_vorgaben": [
            {"station_id": v.station_id, "ladedauer_s": v.ladedauer_s}
            for v in request.ladedauer_vorgaben
        ],
    }

    detected_ferries: list[FaehrSegment] = []

    def _faehren_erfassen(faehren: list[FaehrSegment]) -> None:
        nonlocal detected_ferries
        detected_ferries = faehren

    route_geometrie: list[Coordinate] = []
    route_segments: list[RouteSegment] = []

    def _route_erfassen(route: Route) -> None:
        nonlocal route_geometrie, route_segments
        route_geometrie = route.geometrie
        route_segments = route.segments

    try:
        ergebnis = await create_trip_simulation(
            request_dict,
            routing_provider=routing_provider,
            charging_provider=charging_provider,
            weather_provider=(weather_provider if request.wetter_detailgrad != "off" else None),
            weather_detail=request.wetter_detailgrad,
            construction_provider=(
                construction_provider if request.baustellen_beruecksichtigen else None
            ),
            elevation_provider=elevation_provider,
            start_soc_pct=request.start_soc_pct,
            destination_soc_pct=request.ziel_soc_pct,
            mindest_ankunfts_soc_pct=request.mindest_ankunfts_soc_pct,
            mindest_ladezeit_s=request.mindest_ladezeit_s,
            max_lade_soc_pct=request.max_lade_soc_pct,
            ferry_observer=_faehren_erfassen,
            route_observer=_route_erfassen,
        )

        construction_zones_api = _build_construction_zones_api(
            ergebnis.construction_zones, route_segments
        )

        return TripSimulationResultAPI(
            gesamt_distanz_km=ergebnis.gesamt_distanz_km,
            gesamt_fahrzeit_min=ergebnis.gesamt_fahrzeit_min,
            gesamt_ladezeit_min=ergebnis.gesamt_ladezeit_min,
            gesamt_wartezeit_min=ergebnis.gesamt_wartezeit_min,
            start_soc_pct=ergebnis.start_soc_pct,
            ziel_soc_pct=ergebnis.ziel_soc_pct,
            frames=[
                FrameAPI(
                    zeitpunkt=f.zeitpunkt.isoformat(),
                    position=f.position,
                    distanz_m=f.distanz_m,
                    soc_pct=f.soc_pct,
                    zustand=f.zustand.value,
                    geschwindigkeit_kmh=f.geschwindigkeit_kmh,
                )
                for f in ergebnis.frames
            ],
            charging_stops=[
                ChargingStopAPI(
                    name=stop.name,
                    station_id=stop.station_id,
                    position=stop.position,
                    distanz_m=stop.distanz_m,
                    detour_geometrie=stop.detour_geometrie,
                    route_index_vor=stop.route_index_vor,
                    route_index_nach=stop.route_index_nach,
                    detour_station_index=stop.detour_station_index,
                    ankunfts_soc_pct=stop.ankunfts_soc_pct,
                    ziel_soc_pct=stop.ziel_soc_pct,
                    ladedauer_s=stop.ladedauer_s,
                    energie_geladen_kwh=stop.energie_geladen_kwh,
                    ankunftszeit=stop.ankunftszeit.isoformat(),
                    abfahrtszeit=stop.abfahrtszeit.isoformat(),
                    price_per_kwh=stop.price_per_kwh,
                    currency=stop.currency,
                    estimated_cost=stop.estimated_cost,
                    pricing_updated_utc=stop.pricing_updated_utc.isoformat()
                    if stop.pricing_updated_utc
                    else None,
                )
                for stop in ergebnis.charging_stops
            ],
            waypoint_stops=[
                WaypointStopAPI(
                    position=stop.position,
                    distanz_m=stop.distanz_m,
                    ankunftszeit=stop.ankunftszeit.isoformat(),
                    abfahrtszeit=stop.abfahrtszeit.isoformat(),
                    ladeleistung_kw=stop.ladeleistung_kw,
                    ankunfts_soc_pct=stop.ankunfts_soc_pct,
                    ziel_soc_pct=stop.ziel_soc_pct,
                    energie_geladen_kwh=stop.energie_geladen_kwh,
                )
                for stop in ergebnis.waypoint_stops
            ],
            route_geometrie=route_geometrie,
            erkannte_faehren=[
                FaehrSegmentAPI(
                    name=f.name,
                    laenge_m=f.laenge_m,
                    bbox_sw=f.bbox_sw,
                    bbox_no=f.bbox_no,
                    abfahrt=f.abfahrt.isoformat() if f.abfahrt else None,
                    ankunft=f.ankunft.isoformat() if f.ankunft else None,
                )
                for f in detected_ferries
            ],
            total_charging_cost=[
                ChargingCostByCurrencyAPI(currency=c.currency, amount=c.amount)
                for c in ergebnis.total_charging_cost
            ],
            charging_stops_missing_pricing=ergebnis.charging_stops_missing_pricing,
            construction_zones=construction_zones_api,
        )
    except ValueError as e:
        logger.warning("Trip simulation rejected (422): %s", e)
        raise HTTPException(
            status_code=422,
            detail=f"Route nicht durchführbar: {e!s}",
        ) from e
    except httpx.HTTPError as e:
        # Weather providers never raise here anymore (see
        # `LoadBalancedWeatherProvider`: failures fail over between
        # providers and degrade to a neutral placeholder instead of
        # propagating), so any `httpx.HTTPError` reaching this handler
        # originates from the routing (GraphHopper) call.
        logger.warning("Routing provider (GraphHopper) request failed (502): %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Routing-Server (GraphHopper) nicht erreichbar oder lieferte einen Fehler: {e}",
        ) from e
    except Exception as e:
        logger.exception(
            "Fehler bei der Routensimulation: %s", e, extra={"traceback": traceback.format_exc()}
        )
        raise HTTPException(status_code=500, detail=f"Simulation fehlgeschlagen: {e!s}") from e
