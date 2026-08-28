"""CLI-Entry-Point für trip_input: Reiseplanung über Typer-CLI.

Aufruf: `python -m tripplanner.trip_input.cli trips [OPTIONEN]`
        `python -m tripplanner.trip_input.cli charger refresh supercharge-info`
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import ValidationError

# Konstanten für CLI
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.providers import (
    FakeChargingStationProvider,
    TeslaChargingStationProvider,
)
from tripplanner.construction.providers import FakeConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.routing.providers import FakeRoutingProvider
from tripplanner.simulation.models import TripSimulationResult
from tripplanner.trip_input.api import create_trip_simulation
from tripplanner.trip_input.providers_factory import build_production_providers
from tripplanner.weather.models import WeatherDetailLevel
from tripplanner.weather.providers import FakeWeatherProvider

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


@charger_app.command("pricing-queue")
def pricing_queue(
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", help="Max. Anzahl anzuzeigender Eintraege"),
    ] = None,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", "-d", help="Pfad zur SQLite-Datenbank"),
    ] = None,
) -> None:
    """Zeigt die Preis-Scrape-Warteschlange, ohne sie abzuarbeiten.

    Stationen werden bei jeder Routen-Finalisierung (siehe `POST /trips`,
    `ttp trips`) automatisch eingereiht, wenn ihre Preisdaten fehlen oder
    aelter als `TeslaChargingStationProvider.PRICING_MAX_AGE` sind. Dieser
    Befehl zeigt nur den aktuellen Stand; zum Abarbeiten siehe
    `charger scrape-pricing`.

    Beispiel:
        python -m tripplanner.trip_input.cli charger pricing-queue
    """
    provider = TeslaChargingStationProvider(db_path=Path(db_path) if db_path else None)
    try:
        entries = provider.list_pricing_queue(limit=limit)
    finally:
        provider._db.close()

    if not entries:
        typer.echo("Warteschlange ist leer.")
        return

    for entry in entries:
        slug = entry["tesla_location_id"] or f"(supercharge_info_id={entry['supercharge_info_id']})"
        recency = entry["pricing_last_updated_utc"] or "nie aktualisiert"
        typer.echo(f"{slug:35s} {entry['site_name']:35s} {entry['country_code']:3s} {recency}")
    typer.echo(f"\n{len(entries)} Station(en) in der Warteschlange.")


@charger_app.command("scrape-pricing")
def scrape_pricing(
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", help="Max. Anzahl Stationen in diesem Lauf"),
    ] = None,
    delay: Annotated[
        float,
        typer.Option("--delay", help="Pause zwischen Requests in Sekunden"),
    ] = 1.5,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", "-d", help="Pfad zur SQLite-Datenbank"),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Request/Response-Debug-Log in charger_debug.log"),
    ] = False,
    retry_failed: Annotated[
        bool,
        typer.Option("--retry-failed", help="Fehlgeschlagene sofort wieder einreihen"),
    ] = False,
) -> None:
    """Arbeitet die Preis-Scrape-Warteschlange ab (siehe `charger pricing-queue`).

    Reihenfolge: Stationen ohne jegliche Preisdaten zuerst, danach die mit
    den aeltesten gecachten Preisdaten. Jede Station wird gegen die
    oeffentliche Tesla-Standort-Detailseite abgerufen (siehe
    `TeslaClient.fetch_pricing_html` - Default-Transport
    `NodriverTeslaClient`, ein echter Chromium-Browser via CDP, der den
    Akamai-WAF umgeht) - derselbe WAF-Umgehungs-Mechanismus wie
    `charger refresh tesla`, daher denselben Hinweisen zu Rate-Limits/
    WAF-Bloecken unterworfen.

    Beispiele:
        python -m tripplanner.trip_input.cli charger scrape-pricing
        python -m tripplanner.trip_input.cli charger scrape-pricing --limit 20
    """
    db_path_obj = Path(db_path) if db_path else None
    debug_log: Path | None = None
    if debug:
        debug_log = Path.cwd() / "charger_debug.log"
        debug_log.write_text(f"=== charger scrape-pricing {datetime.now(UTC).isoformat()} ===\n")
        typer.echo(f"Debug-Log: {debug_log}")

    provider = TeslaChargingStationProvider(db_path=db_path_obj, debug_log=debug_log)

    try:
        pending = len(provider.list_pricing_queue())
        if pending == 0:
            typer.echo("Warteschlange ist leer, nichts zu tun.")
            return
        run_count = min(limit, pending) if limit is not None else pending
        typer.echo(f"Arbeite {run_count} von {pending} ab...")

        result = asyncio.run(provider.drain_pricing_queue(limit=limit, delay_s=delay))

        if retry_failed:
            provider.enqueue_stations_for_pricing_refresh([id for id, _ in result.failed])
    finally:
        provider._db.close()

    typer.echo(
        f"{len(result.refreshed)} aktualisiert, "
        f"{len(result.skipped_fresh)} bereits aktuell uebersprungen, "
        f"{len(result.failed)} fehlgeschlagen."
    )
    for slug, error in result.failed:
        typer.echo(f"  Fehler bei {slug}: {error}", err=True)

    if result.failed and not result.refreshed:
        typer.echo(
            "Hinweis: Alle Scrape-Versuche sind fehlgeschlagen - die "
            "Tesla-Standort-Detailseite (Preisquelle) liegt hinter einer "
            "Akamai-Absicherung (WAF/Rate-Limit), die gerade auch den "
            "Browser-basierten Abruf blockiert. Bitte nach einer Pause "
            "(mehrere Minuten) erneut versuchen oder den Scrape von einem "
            "regulaeren macOS-Rechner aus starten.",
            err=True,
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
