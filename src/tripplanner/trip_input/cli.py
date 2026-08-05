"""CLI-Entry-Point für trip_input: Reiseplanung über Typer-CLI.

Aufruf: `python -m tripplanner.cli trips [OPTIONEN]`
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from tripplanner.trip_input.api import create_trip_simulation

# Konstanten für CLI
_EXPECTED_PARTS_COUNT = 2
_COORD_SEPARATOR = ","
_DURATION_SEPARATOR = ":"

app = typer.Typer(help="Tesla Trip Planner - CLI für Reiseplanung und Simulation")


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
    ziel: Annotated[str, typer.Option(help="Ziel-Koordinate als lat,lon")],
    abfahrtszeit: Annotated[
        str, typer.Option(help="Abfahrtszeit im ISO-Format z. B. 2026-08-15T08:30:00")
    ],
    zwischenstopps: Annotated[
        list[str] | None,
        typer.Option(help="Zwischenstopps als lat,lon oder lat,lon:duration_min"),
    ] = None,
    start_soc_pct: Annotated[
        float, typer.Option(min=0.0, max=100.0, help="Start-SoC in Prozent")
    ] = 80.0,
    ziel_soc_pct: Annotated[
        float, typer.Option(min=0.0, max=100.0, help="Ziel-SoC in Prozent")
    ] = 20.0,
    vehicle_profile: Annotated[
        str,
        typer.Option(help="Fahrzeugprofilname currently unused"),
    ] = "model3_standard",
    output_json: Annotated[
        Path | None, typer.Option(help="Pfad zur JSON-Ausgabe default stdout")
    ] = None,
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
        ziel_coord = parse_coord(ziel)

        zwischen = []
        if zwischenstopps:
            for wp in zwischenstopps:
                coord, dur = parse_waypoint(wp)
                zwischen.append({"koordinate": coord, "aufenthaltsdauer": dur})

        abfahrtszeit_dt = datetime.fromisoformat(abfahrtszeit)

        request = {
            "start": start_coord,
            "ziel": ziel_coord,
            "zwischenstopps": zwischen,
            "abfahrtszeit": abfahrtszeit_dt,
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

        result = asyncio.run(
            create_trip_simulation(request, start_soc_pct=start_soc_pct, ziel_soc_pct=ziel_soc_pct)
        )

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
    except Exception as e:
        typer.echo(f"Fehler bei der Berechnung: {e}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
