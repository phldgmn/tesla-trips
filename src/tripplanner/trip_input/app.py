"""FastAPI app shell for trip_input.

Contains the FastAPI instance ``app`` (uvicorn target ``tripplanner.trip_input.api:app``),
den App-Lebenszyklus (``_lifespan``), die Logger-configuration und die
``get_*_provider`` dependency getters for the ``/trips`` endpoint.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tripplanner.charging_infrastructure import (
    ChargingStationProvider,
    TeslaChargingStationProvider,
)
from tripplanner.construction.models import ConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.routing import RoutingProvider
from tripplanner.trip_input.providers_factory import (
    ProductionProviders,
    build_production_providers,
    close_production_providers,
)
from tripplanner.weather.providers import WeatherProvider

__all__ = [
    "app",
    "get_charging_provider",
    "get_construction_provider",
    "get_elevation_provider",
    "get_routing_provider",
    "get_supercharger_provider",
    "get_weather_provider",
    "health_check",
    "readiness_check",
    "require_admin_token",
]

ADMIN_TOKEN_ENV_VAR = "TRIPPLANNER_ADMIN_TOKEN"


# Keep the logger bound to the original module name so app/endpoint log
# records keep the exact same `name` they had before the split (logging
# assertions and production log output are behavior-preserving).
logger = logging.getLogger("tripplanner.trip_input.api")


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
    """Verwaltet den Lebenszyklus der prozessweiten provider-Ressourcen.

    Der GraphHopper-HTTP-Client und der Tesla-Supercharger-DB-Zugriff werden
    created once at startup (connection pooling across all
    Requests hinweg) statt pro Request new aufgebaut zu werden. Die
    GraphHopper base URL is via the environment variable `GRAPHHOPPER_URL`
    konfigurierbar (Default: `http://localhost:8989`, siehe README.md).
    """
    _configure_logging()
    providers = await build_production_providers()
    app.state.providers = providers
    try:
        await providers.routing.client.info()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "GraphHopper is not reachable at startup (%s); /trips will fail "
            "until it is up. Check GRAPHHOPPER_URL and GET /ready.",
            exc,
        )
    try:
        yield
    finally:
        await close_production_providers(providers)


app = FastAPI(title="Tesla Trip Planner API", version="0.1.0", lifespan=_lifespan)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 without echoing the rejected input.

    FastAPI's default handler returns each error's ``input``; a NaN/inf
    coordinate is not JSON-serializable and would turn the 422 into a 500.
    """
    errors = [{k: v for k, v in e.items() if k not in {"input", "ctx"}} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def get_routing_provider(request: Request) -> RoutingProvider:
    """FastAPI dependency: provides the production routing provider for `/trips`.

    Uses den in `_lifespan` erzeugten, prozessweit wiederusesen
    ``GraphHopperClient`` for real road routing via OSM data. In Tests via
    `app.dependency_overrides[get_routing_provider]` durch `FakeRoutingprovider`
    ersetzbar (siehe AGENTS.md: keine Live-Calls externer dataquellen in
    Unit-Tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.routing


def get_charging_provider(request: Request) -> ChargingStationProvider:
    """FastAPI dependency: provides the production charging station provider for `/trips`.

    Uses den in `_lifespan` erzeugten, prozessweit wiederusesen
    `TeslaChargingStationprovider` (SQLite-DB `data/tesla_superchargers.db`,
    see README.md) for real supercharger locations. In tests via
    `app.dependency_overrides[get_charging_provider]` durch eine
    `FakeChargingStationprovider`-Instanz mit angepassten Stationen ersetzbar
    (siehe AGENTS.md: keine Live-Calls externer dataquellen in Unit-Tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.charging


def get_supercharger_provider(request: Request) -> TeslaChargingStationProvider:
    """FastAPI-Dependency: the process-wide Tesla provider for `/superchargers*`.

    Same instance as `get_charging_provider` in production, but typed as the
    concrete Tesla provider because the supercharger endpoints use its
    DB/scrape methods. Tests override it via `app.dependency_overrides`.
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.charging


def require_admin_token(
    x_admin_token: str | None = Header(default=None),
) -> None:
    """Guards scrape-triggering routes with a shared secret when one is configured.

    If `TRIPPLANNER_ADMIN_TOKEN` is unset (local-only default), the check is
    skipped. Otherwise the `X-Admin-Token` header must match it.
    """
    expected = os.environ.get(ADMIN_TOKEN_ENV_VAR)
    if not expected:
        return
    if x_admin_token is None or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Admin token is missing or invalid")


def get_elevation_provider(request: Request) -> ElevationProvider:
    """FastAPI-Dependency: returns the production Elevationprovider for `/trips`.

    Uses the `Elevationprovider` created in `_lifespan` for elevation data.
    In tests, can be replaced via `app.dependency_overrides[get_elevation_provider]`
    with `FakeDataSource`.
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.elevation_provider


def get_weather_provider(request: Request) -> WeatherProvider:
    """FastAPI-Dependency: returns the production Weatherprovider for `/trips`.

    Uses the `OpenMeteoprovider` created in `_lifespan` for weather data.
    In tests, can be replaced via `app.dependency_overrides[get_weather_provider]`
    with `FakeWeatherprovider` (see AGENTS.md: no live calls to external data sources
    in unit tests).
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.weather


def get_construction_provider(request: Request) -> ConstructionProvider:
    """FastAPI dependency: provides the production construction provider for `/trips`.

    Uses den in `_lifespan` erzeugten, prozessweit wiederusesen
    `ConstructionProvider` for construction data. In Tests via
    `app.dependency_overrides[get_construction_provider]` durch
    `FakeConstructionprovider` ersetzbar.
    """
    providers: ProductionProviders = request.app.state.providers
    return providers.construction


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint.

    Allows the frontend to check whether the backend is reachable.
    """
    return {"status": "ok"}


@app.get("/ready")
async def readiness_check(request: Request) -> JSONResponse:
    """Readiness probe: 200 once GraphHopper answers `/info`, else 503.

    Unlike `/health` (process is alive), this reports whether `/trips` can
    actually route.
    """
    providers = getattr(request.app.state, "providers", None)
    if providers is None:
        return JSONResponse(status_code=503, content={"status": "starting"})
    try:
        await providers.routing.client.info()
    except (httpx.HTTPError, ValueError):
        return JSONResponse(status_code=503, content={"status": "graphhopper_unavailable"})
    return JSONResponse(content={"status": "ready"})
