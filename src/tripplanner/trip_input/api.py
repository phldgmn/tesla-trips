"""API-Schicht für trip_input: Orchestrierung der 11 Datenfluss-Schritte.

Diese Modul implementiert:
- `create_trip_simulation()`: Kernfunktion zur Orchestrierung aller Schritte
- FastAPI-Endpunkt `POST /trips` mit `TripRequestAPI` Request-Model
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta

import httpx
from fastapi import Depends, HTTPException
from pydantic import ValidationError

from tripplanner.charging_infrastructure import (
    CachedPricing,
    ChargingStationProvider,
    PricingParseError,
    get_all_charging_stations,
)
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.construction.models import ConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.routing import RoutingProvider
from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment
from tripplanner.weather.providers import WeatherProvider

from .app import (
    app,
    get_charging_provider,
    get_construction_provider,
    get_elevation_provider,
    get_routing_provider,
    get_weather_provider,
    logger,
)
from .pipeline import create_trip_simulation
from .schemas import (
    ChargingCostByCurrencyAPI,
    ChargingStopAPI,
    FaehrSegmentAPI,
    FrameAPI,
    SuperchargerPricingAPI,
    SuperchargerStationAPI,
    TripRequestAPI,
    TripSimulationResultAPI,
    WaypointStopAPI,
)
from .schemas.response import _build_construction_zones_api
from .schemas.superchargers import _station_to_api

__all__ = [
    "app",
    "create_trip_endpoint",
    "create_trip_simulation",
    "get_charging_provider",
    "get_construction_provider",
    "get_elevation_provider",
    "get_routing_provider",
    "get_weather_provider",
]

# =============================================================================
# GraphHopper-Konfiguration
# =============================================================================

# Umgebungsvariable für die GraphHopper-Basis-URL (siehe README.md,
# .github/workflows/ci.yml).
GRAPHHOPPER_URL_ENV_VAR = "GRAPHHOPPER_URL"

# Default-Basis-URL, falls GRAPHHOPPER_URL nicht gesetzt ist.
DEFAULT_GRAPHHOPPER_BASE_URL = "http://localhost:8989"


# ── Supercharger API ─────────────────────────────────────────────────────


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


def _cached_pricing_to_api(slug: str, cached: CachedPricing) -> SuperchargerPricingAPI:
    """Wandelt `CachedPricing` in das API-Response-Modell um."""
    return SuperchargerPricingAPI(
        slug=slug,
        tiers=cached.tiers,
        updated_utc=cached.updated_utc.isoformat() if cached.updated_utc else None,
    )


@app.get("/superchargers/{slug}/pricing")
async def get_supercharger_pricing(slug: str) -> SuperchargerPricingAPI:
    """Liest zwischengespeicherte Preisdaten einer Station, ohne sie neu abzurufen.

    Args:
        slug: tesla_location_id (location_url_slug).

    Returns:
        SuperchargerPricingAPI mit leeren `tiers` und `updated_utc=None`, wenn
        die Station unbekannt ist oder ihre Preise nie gescraped wurden.
    """
    provider = TeslaChargingStationProvider()
    try:
        cached = provider.get_cached_pricing(slug)
    finally:
        provider._db.close()
    return _cached_pricing_to_api(slug, cached)


@app.post("/superchargers/{slug}/refresh-pricing")
async def refresh_supercharger_pricing(slug: str) -> SuperchargerPricingAPI:
    """Scraped aktuelle Preisdaten einer Station von Tesla und speichert sie.

    Ruft dieselbe Tesla-Standort-Detailseite ueber `TeslaClient.fetch_
    pricing_html` ab wie `charger scrape-pricing` (Default-Transport
    `SafariTeslaClient`, steuert die laufende Safari-Instanz des Nutzers per
    AppleScript und umgeht so den Akamai-WAF ohne Fokus-Diebstahl - siehe
    `refresh_supercharger` fuer die Begruendung, warum kein Cross-Origin-
    Fetch aus dem Frontend moeglich ist).

    Args:
        slug: tesla_location_id (location_url_slug) der Station.

    Returns:
        SuperchargerPricingAPI mit den frisch gespeicherten Preisdaten
        (`tiers` kann leer sein, wenn die Station keine veroeffentlichten
        Preise hat).

    Raises:
        HTTPException: 404 wenn `slug` unbekannt ist, 502 bei WAF-Block,
            Netzwerkfehlern oder wenn die Tesla-Antwort nicht auswertbar war.
    """
    provider = TeslaChargingStationProvider()
    try:
        await provider.refresh_pricing(slug)
        cached = provider.get_cached_pricing(slug)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TeslaLocationsClient.CurlError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Tesla API nicht erreichbar: {e}",
        ) from e
    except PricingParseError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Preisdaten konnten nicht verarbeitet werden: {e}",
        ) from e
    finally:
        provider._db.close()
    return _cached_pricing_to_api(slug, cached)


# ── Trips API ──────────────────────────────────────────────────────────────


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
        "autobahn_praeferenz": request.autobahn_praeferenz,
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
