"""CLI-Entry-Point für trip_input: Reiseplanung über Typer-CLI.

Aufruf: `python -m tripplanner.trip_input.cli trips [OPTIONEN]`
        `python -m tripplanner.trip_input.cli charger refresh supercharge-info`
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.trip_input.api import create_trip_simulation

# Konstanten für CLI
_EXPECTED_PARTS_COUNT = 2
_COORD_SEPARATOR = ","
_DURATION_SEPARATOR = ":"

app = typer.Typer(help="Tesla Trip Planner - CLI für Reiseplanung und Simulation")
charger_app = typer.Typer(help="Supercharger-Daten von externen Quellen verwalten")
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


@charger_app.command()
def refresh(  # noqa: PLR0913, PLR0917
    source: Annotated[
        str,
        typer.Argument(help="Datenquelle: 'supercharge-info' oder 'tesla'"),
    ],
    countries: Annotated[
        str,
        typer.Option("--countries", "-c", help="Länder (Komma-getrennt, default DE,DK,SE)"),
    ] = "DE,DK,SE",
    enrich: Annotated[
        bool,
        typer.Option("--enrich", "-e", help="Tesla-Detaildaten abrufen (Stallzahlen, Leistung)"),
    ] = False,
    resume_from: Annotated[
        str | None,
        typer.Option("--resume-from", "-r", help="Ab diesem Slug fortfahren (nach 403-Abbruch)"),
    ] = None,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", "-d", help="Pfad zur SQLite-Datenbank"),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Request/Response-Debug-Log in charger_debug.log"),
    ] = False,
) -> None:
    """Aktualisiert Supercharger-Daten von einer externen Quelle.

    Die Daten werden in die lokale SQLite-Datenbank geladen und stehen
    danach offline für Routenplanung zur Verfügung.

    Bei Quelle 'tesla' wird zunaechst die Standortliste (get-locations)
    abgerufen und gespeichert. Mit --enrich werden zusaetzlich Detaildaten
    pro Station geholt (tqdm-Progress-Bar). Bei 403 wird abgebrochen und
    der fehlgeschlagene Slug ausgegeben. Mit --resume-from kann spaeter
    ab diesem Slug fortgefahren werden.

    Beispiele:
        python -m tripplanner.trip_input.cli charger refresh supercharge-info
        python -m tripplanner.trip_input.cli charger refresh tesla
        python -m tripplanner.trip_input.cli charger refresh tesla --enrich
        python -m tripplanner.trip_input.cli charger refresh tesla
            --enrich --resume-from rhudensupercharger
        python -m tripplanner.trip_input.cli charger refresh supercharge-info
            --debug
    """
    valid_sources = {"supercharge-info", "tesla"}
    if source not in valid_sources:
        typer.echo(
            f"Ungültige Quelle '{source}'. Erlaubt: {', '.join(sorted(valid_sources))}",
            err=True,
        )
        raise typer.Exit(code=1)

    country_list = [c.strip() for c in countries.split(",") if c.strip()]
    if not country_list:
        typer.echo("Mindestens ein Land muss angegeben werden.", err=True)
        raise typer.Exit(code=1)

    db_path_obj = Path(db_path) if db_path else None

    debug_log: Path | None = None
    if debug:
        debug_log = Path.cwd() / "charger_debug.log"
        # Leere Datei anlegen (vorherigen Inhalt verwerfen)
        debug_log.write_text(f"=== charger refresh {source} {datetime.now(UTC).isoformat()} ===\n")
        typer.echo(f"Debug-Log: {debug_log}")

    typer.echo(f"Lade Supercharger-Daten von '{source}' für Länder: {', '.join(country_list)}...")

    async def _run_refresh() -> int:
        provider = TeslaChargingStationProvider(
            db_path=db_path_obj,
            debug_log=debug_log,
        )
        if source == "supercharge-info":
            return await provider.refresh()
        return await provider.refresh_from_tesla_api(
            countries=country_list,
            enrich_details=enrich,
            resume_from_slug=resume_from,
            delay_s=0.5,
        )

    try:
        count = asyncio.run(_run_refresh())
    except TeslaLocationsClient.CurlError as e:
        typer.echo(
            f"Tesla API nicht erreichbar: {e}\n"
            "Hinweis: Die Tesla API ist durch Akamai WAF geschuetzt. "
            "Bei wiederholten Fehlversuchen wird die IP temporaer "
            "blockiert.\n"
            "Alternativ 'charger refresh supercharge-info' nutzen.",
            err=True,
        )
        raise typer.Exit(code=1) from e
    except Exception as e:
        typer.echo(f"Fehler beim Aktualisieren: {e}", err=True)
        raise typer.Exit(code=1) from e

    if count > 0:
        typer.echo(f"Erfolgreich {count} Stationen geladen und gespeichert.")
        return

    if source == "tesla":
        typer.echo(
            "Keine Stationen geladen. Die Tesla API ist ggf. nicht "
            "erreichbar (Rate-Limit oder WAF-Block).\n"
            "Alternativ: 'charger refresh supercharge-info' "
            "nutzen (kein WAF).",
            err=True,
        )
    else:
        typer.echo("Keine Stationen gefunden.", err=True)

    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
