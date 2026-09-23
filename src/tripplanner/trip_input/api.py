"""API-Schicht für trip_input: Orchestrierung der 11 Datenfluss-Schritte.

Diese Modul implementiert:
- `create_trip_simulation()`: Kernfunktion zur Orchestrierung aller Schritte
- FastAPI-Endpunkt `POST /trips` mit `TripRequestAPI` Request-Model
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Path, Response
from pydantic import ValidationError

from tripplanner.charging_infrastructure import (
    CachedPricing,
    ChargingStationProvider,
    PricingParseError,
    TeslaChargingStationProvider,
)
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.construction.models import ConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.routing import RoutingProvider
from tripplanner.routing.models import Coordinate, FerrySegment, Route, RouteSegment
from tripplanner.trip_input.models import TripInfeasibleError
from tripplanner.weather.providers import WeatherProvider

from .app import (
    app,
    get_charging_provider,
    get_construction_provider,
    get_elevation_provider,
    get_routing_provider,
    get_supercharger_provider,
    get_weather_provider,
    logger,
    require_admin_token,
)
from .pipeline import create_trip_simulation
from .schemas import (
    ChargingCostByCurrencyAPI,
    ChargingStopAPI,
    FerrySegmentAPI,
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


def _opaque_http_error(status_code: int, message: str, exc: BaseException) -> HTTPException:
    """Logs `exc` with a correlation id and returns an HTTPException without internals.

    The client only sees `message` plus the id; the full exception and
    traceback go to the server log so the two can be matched up.
    """
    error_id = uuid.uuid4().hex[:12]
    logger.exception("%s [%s]", message, error_id, exc_info=exc)
    return HTTPException(status_code=status_code, detail=f"{message} (Fehler-ID {error_id})")


# ── Supercharger API ─────────────────────────────────────────────────────


SlugPath = Annotated[str, Path(pattern=r"^[a-z0-9-]{1,100}$")]

# Minimum age of stored data before an on-demand refresh scrapes Tesla again.
REFRESH_COOLDOWN = timedelta(
    minutes=float(os.environ.get("TRIPPLANNER_REFRESH_COOLDOWN_MIN", "10"))
)


def _is_fresh(updated_utc: datetime | None) -> bool:
    """True if data updated at `updated_utc` is within `REFRESH_COOLDOWN`."""
    return updated_utc is not None and datetime.now(UTC) - updated_utc < REFRESH_COOLDOWN


@app.get("/superchargers")
async def list_superchargers(
    country: str | None = None,
    provider: TeslaChargingStationProvider = Depends(get_supercharger_provider),  # noqa: B008
) -> list[SuperchargerStationAPI]:
    """Listet alle Supercharger-Stationen aus der Datenbank.

    Args:
        country: Optionaler ISO-2 Laenderfilter (in SQL angewendet).
        provider: Prozessweiter Tesla-Provider (DI).

    Returns:
        Liste von SuperchargerStationAPI.
    """
    stations = provider.get_stations_by_country(country) if country else provider.get_all_stations()
    return [_station_to_api(s) for s in stations]


@app.get("/superchargers/{slug}")
async def get_supercharger_detail(
    slug: SlugPath,
    provider: TeslaChargingStationProvider = Depends(get_supercharger_provider),  # noqa: B008
) -> SuperchargerStationAPI:
    """Liefert Details zu einer Supercharger-Station.

    Args:
        slug: tesla_location_id (location_url_slug).
        provider: Prozessweiter Tesla-Provider (DI).

    Returns:
        SuperchargerStationAPI.

    Raises:
        HTTPException: 404 wenn Station nicht gefunden.
    """
    station = provider.get_station_by_slug(slug)
    if station is None:
        raise HTTPException(status_code=404, detail="Station nicht gefunden")
    return _station_to_api(station)


@app.post("/superchargers/{slug}/refresh", dependencies=[Depends(require_admin_token)])
async def refresh_supercharger(
    slug: SlugPath,
    response: Response,
    provider: TeslaChargingStationProvider = Depends(get_supercharger_provider),  # noqa: B008
) -> SuperchargerStationAPI:
    """Aktualisiert eine Supercharger-Station mit frischen Daten von der Tesla API.

    Ruft die Tesla API server-seitig ueber `TeslaClient` ab (Default-
    Transport: `NodriverTeslaClient`, ein echter Chromium-Browser via CDP;
    alternativ `TeslaLocationsClient` mit curl_cffi/TLS-Fingerprint). Der
    Browser-Ansatz umgeht den Akamai-WAF-Block, dem einfache Python-HTTP-
    Clients (httpx) und zum Teil auch curl_cffi unterliegen. Ein direkter
    Cross-Origin-Fetch aus dem Frontend-JS ist keine Alternative: die
    Tesla-API liefert keine Access-Control-Allow-Origin-Header, wodurch der
    Browser das Lesen der Antwort unabhaengig vom WAF-Status verweigert.

    Nur ein Scrape laeuft gleichzeitig (`scrape_slot`); wurde die Station
    vor weniger als `REFRESH_COOLDOWN` aktualisiert, wird der gespeicherte
    Stand ohne Scrape geliefert (Header `X-Cache: HIT`).

    Args:
        slug: tesla_location_id (location_url_slug).
        response: Response (fuer den `X-Cache`-Header).
        provider: Prozessweiter Tesla-Provider (DI).

    Returns:
        Aktualisierte SuperchargerStationAPI.

    Raises:
        HTTPException: 401 ohne gueltiges Admin-Token (falls konfiguriert),
            404 wenn Station unbekannt oder Tesla-API keine Daten
            liefert, 502 bei WAF-Block, Netzwerkfehlern oder wenn die
            Tesla-Antwort nicht auf ChargingStation abgebildet werden kann
            (z. B. Land ausserhalb DE/DK/SE).
    """
    async with provider.scrape_slot:
        if _is_fresh(provider.get_station_last_updated(slug)):
            cached_station = provider.get_station_by_slug(slug)
            if cached_station is not None:
                response.headers["X-Cache"] = "HIT"
                return _station_to_api(cached_station)
        try:
            station = await provider.refresh_single_station(slug)
        except TeslaLocationsClient.CurlError as e:
            raise _opaque_http_error(502, "Tesla API nicht erreichbar", e) from e
        except ValidationError as e:
            raise _opaque_http_error(502, "Tesla-Antwort konnte nicht verarbeitet werden", e) from e
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
async def get_supercharger_pricing(
    slug: SlugPath,
    provider: TeslaChargingStationProvider = Depends(get_supercharger_provider),  # noqa: B008
) -> SuperchargerPricingAPI:
    """Liest zwischengespeicherte Preisdaten einer Station, ohne sie neu abzurufen.

    Args:
        slug: tesla_location_id (location_url_slug).
        provider: Prozessweiter Tesla-Provider (DI).

    Returns:
        SuperchargerPricingAPI mit leeren `tiers` und `updated_utc=None`, wenn
        die Station unbekannt ist oder ihre Preise nie gescraped wurden.
    """
    return _cached_pricing_to_api(slug, provider.get_cached_pricing(slug))


@app.post("/superchargers/{slug}/refresh-pricing", dependencies=[Depends(require_admin_token)])
async def refresh_supercharger_pricing(
    slug: SlugPath,
    response: Response,
    provider: TeslaChargingStationProvider = Depends(get_supercharger_provider),  # noqa: B008
) -> SuperchargerPricingAPI:
    """Scraped aktuelle Preisdaten einer Station von Tesla und speichert sie.

    Ruft dieselbe Tesla-Standort-Detailseite ueber `TeslaClient.fetch_
    pricing_html` ab wie `charger scrape-pricing` (Default-Transport
    `SafariTeslaClient`, steuert die laufende Safari-Instanz des Nutzers per
    AppleScript und umgeht so den Akamai-WAF ohne Fokus-Diebstahl - siehe
    `refresh_supercharger` fuer die Begruendung, warum kein Cross-Origin-
    Fetch aus dem Frontend moeglich ist).

    Gleiche Serialisierung und Cooldown wie `refresh_supercharger`.

    Args:
        slug: tesla_location_id (location_url_slug) der Station.
        response: Response (fuer den `X-Cache`-Header).
        provider: Prozessweiter Tesla-Provider (DI).

    Returns:
        SuperchargerPricingAPI mit den frisch gespeicherten Preisdaten
        (`tiers` kann leer sein, wenn die Station keine veroeffentlichten
        Preise hat).

    Raises:
        HTTPException: 401 ohne gueltiges Admin-Token (falls konfiguriert),
            404 wenn `slug` unbekannt ist, 502 bei WAF-Block,
            Netzwerkfehlern oder wenn die Tesla-Antwort nicht auswertbar war.
    """
    async with provider.scrape_slot:
        cached = provider.get_cached_pricing(slug)
        if _is_fresh(cached.updated_utc):
            response.headers["X-Cache"] = "HIT"
            return _cached_pricing_to_api(slug, cached)
        try:
            await provider.refresh_pricing(slug)
            cached = provider.get_cached_pricing(slug)
        except ValueError as e:
            raise HTTPException(status_code=404, detail="Station nicht gefunden") from e
        except TeslaLocationsClient.CurlError as e:
            raise _opaque_http_error(502, "Tesla API nicht erreichbar", e) from e
        except PricingParseError as e:
            raise _opaque_http_error(502, "Preisdaten konnten nicht verarbeitet werden", e) from e
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

    ``request.weather_detail_level`` (``"off"``, ``"low"``, ``"medium"``,
    ``"high"``) drives weather resolution.  ``"off"`` skips the weather
    provider (``None``); ``"high"`` passes it through unchanged;
    ``"low"``/``"medium"`` also pass the provider but with reduced
    query granularity.  ``request.consider_construction_sites`` controls
    construction-site detection independently.
    """
    # TripRequestAPI nach TripRequest konvertieren
    request_dict: dict[str, object] = {
        "start": request.start,
        "destination": request.destination,
        "waypoints": [
            {
                "coordinate": wp.coordinate,
                "stay_duration": timedelta(seconds=wp.stay_duration_s)
                if wp.stay_duration_s
                else None,
                "planned_departure": wp.planned_departure,
                "charging_power_kw": wp.charging_power_kw,
            }
            for wp in request.waypoints
        ],
        "departure_time": request.departure_time,
        "vehicle_profile": request.vehicle_profile.to_domain().model_dump(),
        "preferences": request.preferences.model_dump(),
        "avoid_all_ferries": request.avoid_all_ferries,
        "highway_preference": request.highway_preference,
        "avoided_ferries": [
            {"name": f.name, "bbox_sw": f.bbox_sw, "bbox_ne": f.bbox_ne}
            for f in request.avoided_ferries
        ],
        "ferry_time_windows": [
            {
                "name": f.name,
                "bbox_sw": f.bbox_sw,
                "bbox_ne": f.bbox_ne,
                "departure": f.departure,
                "arrival": f.arrival,
            }
            for f in request.ferry_time_windows
        ],
        "charging_duration_specifications": [
            {"station_id": v.station_id, "charging_duration_s": v.charging_duration_s}
            for v in request.charging_duration_specifications
        ],
    }

    detected_ferries: list[FerrySegment] = []

    def _record_ferries(ferries: list[FerrySegment]) -> None:
        nonlocal detected_ferries
        detected_ferries = ferries

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
            weather_provider=(weather_provider if request.weather_detail_level != "off" else None),
            weather_detail=request.weather_detail_level,
            construction_provider=(
                construction_provider if request.consider_construction_sites else None
            ),
            elevation_provider=elevation_provider,
            start_soc_pct=request.start_soc_pct,
            destination_soc_pct=request.target_soc_pct,
            min_arrival_soc_pct=request.min_arrival_soc_pct,
            min_charging_time_s=request.min_charging_time_s,
            max_charge_soc_pct=request.max_charge_soc_pct,
            ferry_observer=_record_ferries,
            route_observer=_route_erfassen,
        )

        construction_zones_api = _build_construction_zones_api(
            ergebnis.construction_zones, route_segments
        )

        return TripSimulationResultAPI(
            total_distance_km=ergebnis.gesamt_distanz_km,
            total_driving_time_min=ergebnis.gesamt_fahrzeit_min,
            total_charging_time_min=ergebnis.gesamt_ladezeit_min,
            total_waiting_time_min=ergebnis.gesamt_wartezeit_min,
            start_soc_pct=ergebnis.start_soc_pct,
            target_soc_pct=ergebnis.target_soc_pct,
            frames=[
                FrameAPI(
                    timestamp=f.zeitpunkt.isoformat(),
                    position=f.position,
                    distance_m=f.distanz_m,
                    soc_pct=f.soc_pct,
                    state=f.zustand.value,
                    speed_kmh=f.geschwindigkeit_kmh,
                    temperature_c=f.temperatur_c,
                    wind_speed_ms=f.windgeschwindigkeit_ms,
                    wind_direction_deg=f.windrichtung_deg,
                    precipitation_mm=f.niederschlag_mm,
                )
                for f in ergebnis.frames
            ],
            charging_stops=[
                ChargingStopAPI(
                    name=stop.name,
                    station_id=stop.station_id,
                    position=stop.position,
                    distance_m=stop.distanz_m,
                    detour_geometry=stop.detour_geometrie,
                    route_index_before=stop.route_index_vor,
                    route_index_after=stop.route_index_nach,
                    detour_station_index=stop.detour_station_index,
                    arrival_soc_pct=stop.arrival_soc_pct,
                    target_soc_pct=stop.target_soc_pct,
                    charging_duration_s=stop.charging_duration_s,
                    energy_charged_kwh=stop.energie_geladen_kwh,
                    arrival_time=stop.ankunftszeit.isoformat(),
                    departure_time=stop.departure_time.isoformat(),
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
                    distance_m=stop.distanz_m,
                    arrival_time=stop.ankunftszeit.isoformat(),
                    departure_time=stop.departure_time.isoformat(),
                    charging_power_kw=stop.charging_power_kw,
                    arrival_soc_pct=stop.arrival_soc_pct,
                    target_soc_pct=stop.target_soc_pct,
                    energy_charged_kwh=stop.energie_geladen_kwh,
                )
                for stop in ergebnis.waypoint_stops
            ],
            route_geometry=route_geometrie,
            detected_ferries=[
                FerrySegmentAPI(
                    name=f.name,
                    length_m=f.laenge_m,
                    bbox_sw=f.bbox_sw,
                    bbox_ne=f.bbox_ne,
                    departure=f.departure.isoformat() if f.departure else None,
                    arrival=f.arrival.isoformat() if f.arrival else None,
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
    except TripInfeasibleError as e:
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
        raise _opaque_http_error(
            502, "Routing-Server (GraphHopper) nicht erreichbar oder lieferte einen Fehler", e
        ) from e
    except Exception as e:
        raise _opaque_http_error(500, "Simulation fehlgeschlagen", e) from e
