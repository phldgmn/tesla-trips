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
    """Parse Koordinate aus 'lat,lon' Format."""
    parts = s.split(_COORD_SEPARATOR)
    if len(parts) != _EXPECTED_PARTS_COUNT:
        raise ValueError(f"Ungültige Koordinate: {s}. Erwartet 'lat,lon'.")
    try:
        lat = float(parts[0])
        lon = float(parts[1])
        return (lat, lon)
    except ValueError as e:
        raise ValueError(f"Ungültige Koordinate: {s}. Muss numerisch sein.") from e


def parse_waypoint(s: str) -> tuple[tuple[float, float], timedelta | None]:
    """Parse Waypoint aus 'lat,lon:duration_min' Format."""
    if _DURATION_SEPARATOR in s:
        coord_part, dur_part = s.split(_DURATION_SEPARATOR, 1)
        try:
            coord = parse_coord(coord_part)
            duration_min = int(dur_part)
            return coord, timedelta(minutes=duration_min)
        except ValueError as e:
            raise ValueError(f"Ungültige Dauer: {dur_part}. Muss integer sein.") from e
    coord = parse_coord(s)
    return coord, None


@app.command()
def trips(  # noqa: PLR0913, PLR0917
    start: Annotated[str, typer.Option(help="Start-Koordinate als lat,lon")],
    destination: Annotated[str, typer.Option(help="Ziel-Koordinate als lat,lon")],
    departure_time: Annotated[
        str, typer.Option(help="Abfahrtszeit im ISO-Format z. B. 2026-08-15T08:30:00")
    ],
    waypoints: Annotated[
        list[str] | None,
        typer.Option(help="Zwischenstopps als lat,lon oder lat,lon:duration_min"),
    ] = None,
    start_soc_pct: Annotated[
        float, typer.Option(min=0.0, max=100.0, help="Start-SoC in Prozent")
    ] = 80.0,
    destination_soc_pct: Annotated[
        float, typer.Option(min=0.0, max=100.0, help="Ziel-SoC in Prozent")
    ] = 20.0,
    mindest_ankunfts_soc_pct: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=100.0,
            help="Minimal zulässiger SoC beim Ankommen an einer Ladestation",
        ),
    ] = 5.0,
    mindest_ladezeit_s: Annotated[
        int,
        typer.Option(
            min=0,
            max=1800,
            help="Minimale Dauer eines Ladevorgangs in Sekunden, wenn geladen wird",
        ),
    ] = 600,
    max_lade_soc_pct: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=100.0,
            help="Maximaler Ladeziel-SoC an regulären Ladehalten in Prozent (100 = deaktiviert)",
        ),
    ] = 100.0,
    vehicle_profile: Annotated[
        str,
        typer.Option(help="Fahrzeugprofilname currently unused"),
    ] = "model3_standard",
    output_json: Annotated[
        Path | None, typer.Option(help="Pfad zur JSON-Ausgabe default stdout")
    ] = None,
    offline: Annotated[bool, typer.Option(help="Offline-Modus ohne Produktionsserver")] = False,
    wetter_detailgrad: Annotated[
        str,
        typer.Option(help="Wetter-Detailgrad: off, low, medium oder high"),
    ] = "high",
) -> None:
    """Berechnet eine Reise und simuliert sie vollständig (inkl. Ladeplanung).

    Die 11 Datenfluss-Schritte werden in korrekter Reihenfolge ausgeführt:
    1. OSM-Routing berechnen
    2. Höhenprofil extrahieren
    3. Route in Segmente unterteilen
    4. Initiale ETA-Schätzung
    5. Wetterdaten abrufen
    6. Baustellen einbeziehen
    7. Energieverbrauch berechnen
    8. Ladeplan optimieren
    9. ETA aktualisieren
    10. Reise simulieren
    11. Ergebnis zurückgeben
    """
    try:
        start_coord = parse_coord(start)
        destination_coord = parse_coord(destination)

        waypoints_list = []
        if waypoints:
            for wp in waypoints:
                coord, duration = parse_waypoint(wp)
                waypoints_list.append({"koordinate": coord, "aufenthaltsdauer": duration})

        departure_time_dt = datetime.fromisoformat(departure_time)
        if wetter_detailgrad not in {"off", "low", "medium", "high"}:
            raise ValueError(
                f"Ungültiges Wetter-Detailgrad: {wetter_detailgrad}. "
                "Muss 'off', 'low', 'medium' oder 'high' sein.",
            )
        weather_detail: WeatherDetailLevel = cast(WeatherDetailLevel, wetter_detailgrad)

        request = {
            "start": start_coord,
            "ziel": destination_coord,
            "zwischenstopps": waypoints_list,
            "abfahrtszeit": departure_time_dt,
            "fahrzeugprofil": {
                "masse_kg": 1800.0,
                "cw_wert": 0.23,
                "stirnflaeche_m2": 2.2,
                "rollwiderstandsbeiwert": 0.01,
                "batteriekapazitaet_kwh": 60.0,
                "nebenverbraucher_baseline_kw": 0.34,
                "reifentyp": "standard",
                "dachbox": False,
            },
            "praeferenzen": {},
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
                    mindest_ankunfts_soc_pct=mindest_ankunfts_soc_pct,
                    mindest_ladezeit_s=mindest_ladezeit_s,
                    max_lade_soc_pct=max_lade_soc_pct,
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
                mindest_ankunfts_soc_pct=mindest_ankunfts_soc_pct,
                mindest_ladezeit_s=mindest_ladezeit_s,
                max_lade_soc_pct=max_lade_soc_pct,
                weather_detail=weather_detail,
            )

        result = asyncio.run(_run_trip())

        output = {
            "gesamt_distanz_km": result.gesamt_distanz_km,
            "gesamt_fahrzeit_min": result.gesamt_fahrzeit_min,
            "gesamt_ladezeit_min": result.gesamt_ladezeit_min,
            "start_soc_pct": result.start_soc_pct,
            "ziel_soc_pct": result.ziel_soc_pct,
            "frames": [
                {
                    "zeitpunkt": f.zeitpunkt.isoformat(),
                    "position": list(f.position),
                    "soc_pct": f.soc_pct,
                    "zustand": f.zustand.value,
                    "geschwindigkeit_kmh": f.geschwindigkeit_kmh,
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
