"""CLI-Entry-Point für trip_input: Reiseplanung über Typer-CLI.

Aufruf: `python -m tripplanner.trip_input.cli trips [OPTIONEN]`
        `python -m tripplanner.trip_input.cli charger refresh supercharge-info`
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import ValidationError

from tripplanner.charging_infrastructure import FakeChargingStationProvider
from tripplanner.construction.providers import FakeConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.routing.providers import FakeRoutingProvider
from tripplanner.simulation.models import TripSimulationResult
from tripplanner.trip_input.api import create_trip_simulation
from tripplanner.trip_input.providers_factory import build_production_providers
from tripplanner.weather.models import WeatherDetailLevel
from tripplanner.weather.providers import FakeWeatherProvider

from .cli_charger import charger_app

# Konstanten für CLI
_EXPECTED_PARTS_COUNT = 2
_COORD_SEPARATOR = ","
_DURATION_SEPARATOR = ":"

app = typer.Typer(help="Tesla Trip Planner - CLI für Reiseplanung und Simulation")
app.add_typer(charger_app, name="charger")


def parse_coord(s: str) -> tuple[float, float]:
    """Parse a coordinate in 'lat,lon' format."""
    parts = s.split(_COORD_SEPARATOR)
    if len(parts) != _EXPECTED_PARTS_COUNT:
        raise ValueError(f"Invalid coordinate: {s}. Expected 'lat,lon'.")
    try:
        lat = float(parts[0])
        lon = float(parts[1])
        return (lat, lon)
    except ValueError as e:
        raise ValueError(f"Invalid coordinate: {s}. Must be numeric.") from e


def parse_waypoint(s: str) -> tuple[tuple[float, float], timedelta | None]:
    """Parse a waypoint in 'lat,lon:duration_min' format."""
    if _DURATION_SEPARATOR in s:
        coord_part, dur_part = s.split(_DURATION_SEPARATOR, 1)
        try:
            coord = parse_coord(coord_part)
            duration_min = int(dur_part)
            return coord, timedelta(minutes=duration_min)
        except ValueError as e:
            raise ValueError(f"Invalid duration: {dur_part}. Must be an integer.") from e
    coord = parse_coord(s)
    return coord, None


@app.command()
def trips(  # noqa: PLR0913, PLR0917
    start: Annotated[str, typer.Option(help="Start coordinate as lat,lon")],
    destination: Annotated[str, typer.Option(help="Destination coordinate as lat,lon")],
    departure_time: Annotated[
        str, typer.Option(help="Departure time in ISO format, e.g. 2026-08-15T08:30:00")
    ],
    waypoints: Annotated[
        list[str] | None,
        typer.Option(help="Waypoints as lat,lon or lat,lon:duration_min"),
    ] = None,
    start_soc_pct: Annotated[
        float, typer.Option(min=0.0, max=100.0, help="Start SoC in percent")
    ] = 80.0,
    destination_soc_pct: Annotated[
        float, typer.Option(min=0.0, max=100.0, help="Target SoC at the destination in percent")
    ] = 20.0,
    min_arrival_soc_pct: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=100.0,
            help="Minimum allowed SoC when arriving at a charging station",
        ),
    ] = 5.0,
    min_charging_time_s: Annotated[
        int,
        typer.Option(
            min=0,
            max=1800,
            help="Minimum charging session duration in seconds",
        ),
    ] = 600,
    max_charge_soc_pct: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=100.0,
            help="Maximum target SoC at regular charging stops in percent (100 = disabled)",
        ),
    ] = 100.0,
    vehicle_profile: Annotated[
        str,
        typer.Option(help="Vehicle profile name (currently unused)"),
    ] = "model3_standard",
    output_json: Annotated[
        Path | None, typer.Option(help="Path for JSON output (default: stdout)")
    ] = None,
    offline: Annotated[bool, typer.Option(help="Offline mode without production servers")] = False,
    weather_detail_level: Annotated[
        str,
        typer.Option(help="Weather detail level: off, low, medium or high"),
    ] = "high",
) -> None:
    """Plan a trip and simulate it end to end, including charging.

    The 11 data-flow steps run in order:
    1. Compute OSM routing
    2. Extract the elevation profile
    3. Split the route into segments
    4. Initial ETA estimate
    5. Fetch weather data
    6. Include construction sites
    7. Compute energy consumption
    8. Optimize the charging plan
    9. Update the ETA
    10. Simulate the trip
    11. Return the result
    """
    try:
        start_coord = parse_coord(start)
        destination_coord = parse_coord(destination)

        waypoints_list = []
        if waypoints:
            for wp in waypoints:
                coord, duration = parse_waypoint(wp)
                waypoints_list.append({"coordinate": coord, "stay_duration": duration})

        departure_time_dt = datetime.fromisoformat(departure_time)
        if weather_detail_level not in {"off", "low", "medium", "high"}:
            raise ValueError(
                f"Invalid weather detail level: {weather_detail_level}. "
                "Must be 'off', 'low', 'medium' or 'high'.",
            )
        weather_detail: WeatherDetailLevel = cast(WeatherDetailLevel, weather_detail_level)

        request = {
            "start": start_coord,
            "destination": destination_coord,
            "waypoints": waypoints_list,
            "departure_time": departure_time_dt,
            "vehicle_profile": {
                "mass_kg": 1800.0,
                "drag_coefficient": 0.23,
                "frontal_area_m2": 2.2,
                "rolling_resistance_coefficient": 0.01,
                "battery_capacity_kwh": 60.0,
                "auxiliary_baseline_kw": 0.34,
                "tire_type": "standard",
                "roof_box": False,
            },
            "preferences": {},
        }

        async def _run_trip() -> TripSimulationResult:
            if offline:
                return await create_trip_simulation(
                    request,
                    routing_provider=FakeRoutingProvider(),
                    elevation_provider=ElevationProvider(data_source=FakeDataSource()),
                    weather_provider=FakeWeatherProvider(),
                    construction_provider=FakeConstructionProvider(),
                    charging_provider=FakeChargingStationProvider(),
                    start_soc_pct=start_soc_pct,
                    destination_soc_pct=destination_soc_pct,
                    min_arrival_soc_pct=min_arrival_soc_pct,
                    min_charging_time_s=min_charging_time_s,
                    max_charge_soc_pct=max_charge_soc_pct,
                    weather_detail=weather_detail,
                )
            providers = await build_production_providers()
            return await create_trip_simulation(
                request,
                routing_provider=providers.routing,
                elevation_provider=providers.elevation_provider,
                weather_provider=providers.weather,
                construction_provider=providers.construction,
                charging_provider=providers.charging,
                start_soc_pct=start_soc_pct,
                destination_soc_pct=destination_soc_pct,
                min_arrival_soc_pct=min_arrival_soc_pct,
                min_charging_time_s=min_charging_time_s,
                max_charge_soc_pct=max_charge_soc_pct,
                weather_detail=weather_detail,
            )

        result = asyncio.run(_run_trip())

        output = {
            "gesamt_distanz_km": result.gesamt_distanz_km,
            "gesamt_fahrzeit_min": result.gesamt_fahrzeit_min,
            "gesamt_ladezeit_min": result.gesamt_ladezeit_min,
            "start_soc_pct": result.start_soc_pct,
            "target_soc_pct": result.target_soc_pct,
            "frames": [
                {
                    "timestamp": f.timestamp.isoformat(),
                    "position": list(f.position),
                    "soc_pct": f.soc_pct,
                    "zustand": f.zustand.value,
                    "speed_kmh": f.speed_kmh,
                }
                for f in result.frames
            ],
        }

        if output_json:
            output_json.write_text(json.dumps(output, indent=2))
        else:
            print(json.dumps(output, indent=2))

    except ValidationError as e:
        typer.echo(f"Validierungsfehler: {e}", err=True)
        raise typer.Exit(code=1) from None
    except ValueError as e:
        typer.echo(f"Ungültige Eingabe: {e}", err=True)
        raise typer.Exit(code=1) from None
    except Exception as e:  # noqa: BLE001 - CLI boundary: report any failure as exit code 1
        typer.echo(f"Fehler bei der Berechnung: {e}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
