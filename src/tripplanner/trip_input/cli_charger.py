"""CLI-Entry-Punkt für die Supercharger-Datenverwaltung.

Enthält den Typer-Sub-App ``charger_app`` (``refresh``/``pricing-queue``/
``scrape-pricing``), der in ``cli.py`` unter dem Unterbefehl ``charger``
registriert wird.

Aufruf: `python -m tripplanner.trip_input.cli charger refresh supercharge-info`
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.clients.common import default_debug_log_path
from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)

charger_app = typer.Typer(help="Supercharger-Daten von externen Quellen verwalten")


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
        typer.Option("--debug", help="Request/Response-Debug-Log in .cache/logs/charger_debug.log"),
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
        debug_log = default_debug_log_path()
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
        typer.Option("--debug", help="Request/Response-Debug-Log in .cache/logs/charger_debug.log"),
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
    `SafariTeslaClient`, steuert die laufende Safari-Instanz des Nutzers per
    AppleScript und umgeht so den Akamai-WAF) - derselbe WAF-Umgehungs-
    Mechanismus wie `charger refresh tesla`, daher denselben Hinweisen zu
    Rate-Limits/WAF-Bloecken unterworfen.

    Beispiele:
        python -m tripplanner.trip_input.cli charger scrape-pricing
        python -m tripplanner.trip_input.cli charger scrape-pricing --limit 20
    """
    db_path_obj = Path(db_path) if db_path else None
    debug_log: Path | None = None
    if debug:
        debug_log = default_debug_log_path()
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
