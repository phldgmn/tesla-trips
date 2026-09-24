"""Pricing-Queue-Mixin fuer TeslaChargingStationProvider."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

from tripplanner.geo import Coordinate, haversine_distance_m

from ..client import CurlError, TeslaClient, create_tesla_client
from ..database import SQLiteDatabase
from ..models import ChargingPricingTier
from ..pricing import PricingParseError, parse_pricing_tiers


class CachedPricing(NamedTuple):
    """Gespeicherte Preisdaten einer Station."""

    tiers: list[ChargingPricingTier]
    """Preistiers, leer wenn nie gescraped oder Station ohne veroeffentlichte Preise."""
    updated_utc: datetime | None
    """timestamp der juengsten gespeicherten Preiszeile, None wenn nie gescraped."""


class PricingQueueDrainResult(NamedTuple):
    """Ergebnis eines Warteschlangen-Laufs."""

    refreshed: list[str]
    """Slugs, deren Preisdaten erfolgreich aktualisiert wurden."""
    skipped_fresh: list[str]
    """Slugs, die bereits aktuell waren (defensiv erneut geprueft, siehe
    `drain_pricing_queue`) und daher nicht erneut abgerufen wurden."""
    failed: list[tuple[str, str]]
    """(Slug, Fehlermeldung)-Paare fehlgeschlagener Scrape-Versuche."""


# Maximales Alter gespeicherter Preisdaten, bevor eine Station fuer ein
# Refresh eingereiht wird (Spiegel des Legacy-tesla-pricing-`MAX_AGE_DAYS_`
# `DEFAULT`, siehe docs/Tesla-Supercharger-Detail-Scraping.md).
PRICING_MAX_AGE_DAYS_DEFAULT: int = 14


class PricingQueueMixin:
    """Stellt die Pricing-Queue-Methoden fuer den TeslaChargingStationProvider bereit.

    Diese Methoden greifen ueber ``self._db``, ``self.PRICING_MAX_AGE``,
    ``self._SLUG_RESOLUTION_MAX_DISTANCE_M`` und ``self._debug_log`` auf
    Attribute des Provider-Objekts zu (kein eigener Zustand), daher als
    Mixin statt als eigene Klasse.
    """

    _db: SQLiteDatabase  # vom Provider gesetzt
    _debug_log: Path | None
    _stations: list[Any] | None
    PRICING_MAX_AGE: timedelta  # vom Provider gesetzt
    _SLUG_RESOLUTION_MAX_DISTANCE_M: float = 500.0

    def _resolve_supercharge_info_id(self, station_id: str) -> int | None:
        """Loest eine `ChargingStation.station_id` in eine `supercharge_info_id` auf.

        `station_id` ist ueblicherweise der Slug (`tesla_location_id`); bei
        Stationen ohne Slug faellt `_db_record_to_charging_station` auf
        `str(supercharge_info_id)` zurueck, daher der Zahlen-Fallback hier.

        Args:
            station_id: `ChargingStation.station_id` (Slug oder numerischer
                Fallback).

        Returns:
            Die interne `supercharge_info_id`, oder None wenn unbekannt.
        """
        record = self._db.find_station_by_slug(station_id)
        if record is None and station_id.isdigit():
            record = self._db.find_station_by_supercharge_info_id(int(station_id))
        return record["supercharge_info_id"] if record is not None else None

    async def _resolve_numeric_slug(
        self,
        record: dict[str, Any],
        tesla_client: TeslaClient,
    ) -> str | None:
        """Loest eine stale numerische `tesla_location_id` in Teslas echten Slug auf.

        supercharge.info liefert fuer `locationId` gelegentlich einen
        veralteten rein numerischen Platzhalter statt Teslas
        `location_url_slug` (z. B. "Rødekro East, Denmark" als `"28500"` -
        `tesla.com/findus/location/supercharger/28500` liefert 404; der
        korrekte Slug ist `"rodekrosupercharger"`). Da Tesla keinen
        Lookup-Endpunkt von numerischer ID auf Slug anbietet, wird stattdessen
        Teslas eigene Standortliste (`fetch_locations`) fuer das Land der
        Station nach dem naechstgelegenen Supercharger-Eintrag durchsucht.

        Args:
            record: DB-Record-Dict der Station (mit `country_code`,
                `latitude`, `longitude`).
            tesla_client: TeslaClient fuer den API-Zugriff.

        Returns:
            Der aufgeloeste `location_url_slug`, oder `None` wenn kein
            Standort innerhalb von `_SLUG_RESOLUTION_MAX_DISTANCE_M` liegt.
        """
        country = record.get("country_code", "")
        if not country:
            return None
        coordinate: Coordinate = (record["latitude"], record["longitude"])
        try:
            locations = await tesla_client.fetch_locations(country)
        except CurlError:
            return None

        best_slug: str | None = None
        best_distance = self._SLUG_RESOLUTION_MAX_DISTANCE_M
        for loc in locations:
            if "supercharger" not in loc.get("location_type", []):
                continue
            candidate_slug: str = loc.get("location_url_slug", "")
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            if not candidate_slug or lat is None or lon is None:
                continue
            distance = haversine_distance_m(coordinate, (lat, lon))
            if distance < best_distance:
                best_distance = distance
                best_slug = candidate_slug
        return best_slug

    async def refresh_pricing(
        self,
        slug: str,
        tesla_client: TeslaClient | None = None,
    ) -> list[ChargingPricingTier]:
        """Holt und speichert aktuelle Preisdaten fuer eine einzelne Station.

        Ruft die oeffentliche Standort-Detailseite ab (siehe
        `TeslaClient.fetch_pricing_html` - NICHT die JSON-API, die
        keine Preisdaten liefert), parst die eingebetteten `chargerPricing`-
        Daten (siehe `pricing.parse_pricing_tiers`) und ersetzt die
        gespeicherten Preise der Station. Entfernt die Station anschliessend
        aus der Scrape-Warteschlange (siehe `enqueue_stations_for_pricing_
        refresh`), unabhaengig davon, ob Preisdaten gefunden wurden (eine
        Station ohne veroeffentlichte Preise bleibt sonst dauerhaft in der
        Warteschlange haengen).

        Ist `slug` rein numerisch, wird zuerst direkt gegen Teslas
        Detailseite mit der numerischen ID abgerufen - Teslas URL-Schema
        akzeptiert numerische Standort-IDs oft genauso wie den
        menschenlesbaren Slug (z. B. `.../supercharger/405657`). Schlaegt
        das fehl (HTTP 404 o. ae.) *oder* liefert es keinerlei Preistiers,
        wird `slug` zusaetzlich als stale supercharge.info-`locationId`
        behandelt und ueber `_resolve_numeric_slug` gegen Teslas eigene
        Standortliste geprueft: eine numerische ID ist ein dokumentiertes
        Platzhalter-Risiko, daher ist selbst eine fehlerfreie, aber leere
        Antwort nicht vertrauenswuerdig genug, um sie ungeprueft als "Station
        hat keine veroeffentlichten Preise" zu akzeptieren (kann sonst eine
        generische Soft-404-Seite sein statt der echten Standortseite).
        Findet sich dabei ein abweichender echter Slug, wird die DB
        aktualisiert und mit diesem Slug erneut abgerufen; andernfalls bleibt
        das urspruengliche (ggf. leere) Ergebnis bestehen.

        Args:
            slug: tesla_location_id (location_url_slug) der Station.
            tesla_client: Optionaler TeslaClient (fuer Tests).

        Returns:
            Die new gespeicherten Preistiers (kann leer sein).

        Raises:
            ValueError: Wenn `slug` keiner bekannten Station entspricht.
            CurlError: Bei curl-Fehlern, WAF-Block, oder
                wenn eine numerische ID nicht zu einem Tesla-Slug aufgeloest
                werden konnte.
            PricingParseError: Wenn die Antwort kein auswertbares
                `chargerPricing` enthaelt (siehe `parse_pricing_tiers`).
        """
        supercharge_info_id = self._resolve_supercharge_info_id(slug)
        if supercharge_info_id is None:
            raise ValueError(f"Unbekannte Station: '{slug}'")

        created = tesla_client is None
        if tesla_client is None:
            tesla_client = create_tesla_client(debug_log=self._debug_log)

        try:
            fetch_slug = slug
            if slug.isdigit():
                # Tesla's own URL scheme accepts purely numeric location IDs
                # directly (e.g. tesla.com/findus/location/supercharger/405657),
                # so try that first instead of assuming it is always a stale
                # supercharge.info placeholder - resolving it away would
                # otherwise fail on perfectly valid numeric Tesla slugs.
                html: str | None = None
                tiers: list[ChargingPricingTier] = []
                try:
                    html = await tesla_client.fetch_pricing_html(fetch_slug)
                    tiers = parse_pricing_tiers(html)
                except CurlError:
                    pass

                # A numeric slug is a documented placeholder risk: unlike a
                # confirmed human-readable slug, even a 200 response with no
                # error may be a generic/soft-404 shell page instead of the
                # real station page. Any empty result from a purely numeric
                # URL is therefore cross-checked against Tesla's own
                # nearest-neighbor slug before it is accepted as "station
                # legitimately has no published pricing".
                if not tiers:
                    record = self._db.find_station_by_supercharge_info_id(supercharge_info_id)
                    resolved = (
                        await self._resolve_numeric_slug(record, tesla_client)
                        if record is not None
                        else None
                    )
                    if resolved is None:
                        if html is None:
                            raise CurlError(
                                f"Slug '{slug}' ist ein numerischer supercharge.info-"
                                "Platzhalter: weder als eigenstaendige Tesla-URL "
                                "abrufbar, noch ein aufloesbarer Tesla-URL-Slug "
                                "(kein Standort innerhalb von "
                                f"{self._SLUG_RESOLUTION_MAX_DISTANCE_M:.0f}m gefunden)."
                            ) from None
                        # Direct fetch succeeded but had no pricing, and no
                        # better slug was found - accept as a legitimately
                        # price-free station (see `parse_pricing_tiers`).
                    elif resolved != slug:
                        self._db.update_tesla_location_id(supercharge_info_id, resolved)
                        self._stations = None
                        fetch_slug = resolved
                        html = await tesla_client.fetch_pricing_html(fetch_slug)
                        tiers = parse_pricing_tiers(html)
            else:
                html = await tesla_client.fetch_pricing_html(fetch_slug)
                tiers = parse_pricing_tiers(html)

            self._db.upsert_pricing(supercharge_info_id, [t.model_dump() for t in tiers])
        finally:
            self._db.dequeue_pricing_refresh(supercharge_info_id)
            # Eigenen Client (z. B. Chromium-Browser) deterministisch beenden.
            if created:
                await tesla_client.close()

        return tiers

    def get_cached_pricing(self, station_id: str) -> CachedPricing:
        """Liest gespeicherte Preisdaten einer Station, ohne sie new abzurufen.

        Args:
            station_id: `ChargingStation.station_id` (Slug oder numerischer
                Fallback, siehe `_resolve_supercharge_info_id`).

        Returns:
            `CachedPricing` mit den gespeicherten Tiers und dem timestamp der
            juengsten Preiszeile (leer/None, wenn nie gescraped oder Station
            unbekannt).
        """
        supercharge_info_id = self._resolve_supercharge_info_id(station_id)
        if supercharge_info_id is None:
            return CachedPricing(tiers=[], updated_utc=None)

        rows = self._db.load_pricing({supercharge_info_id}).get(supercharge_info_id, [])
        if not rows:
            return CachedPricing(tiers=[], updated_utc=None)

        tiers = [
            ChargingPricingTier(
                tier_label=row["tier_label"],
                time_label=row["time_label"],
                currency=row["currency"],
                amount=row["amount"],
                unit=row["unit"],
                idle_fee_text=row["idle_fee_text"],
            )
            for row in rows
        ]
        updated_utc = max(SQLiteDatabase.parse_iso_utc(row["last_updated_utc"]) for row in rows)
        return CachedPricing(tiers=tiers, updated_utc=updated_utc)

    def enqueue_stations_for_pricing_refresh(
        self,
        station_ids: Iterable[str],
        max_age: timedelta | None = None,
    ) -> int:
        """Reiht Stationen mit fehlenden/veralteten Preisdaten zum Scrapen ein.

        Eine Station wird eingereiht, wenn sie noch nie mit Preisdaten
        gescraped wurde, oder wenn ihre gespeicherten Preisdaten aelter als
        `max_age` sind. Aktuelle Stationen werden NICHT eingereiht, um
        unnoetigen Traffic gegen die Tesla-API/WAF zu vermeiden (siehe
        `PRICING_MAX_AGE`).

        Wird bei jeder Routen-Finalisierung fuer die tatsaechlich genutzten
        Ladehalte aufgerufen (siehe `trip_input.api._attach_charging_pricing`)
        - das eigentliche Scrapen laeuft NICHT synchron dabei, sondern
        asynchron/out-of-band (siehe `drain_pricing_queue`, CLI-Befehl
        `charger scrape-pricing`), da Requests gegen die Tesla-API zu
        slow/WAF-riskant fuer den Request/Response-Zyklus sind.

        Args:
            station_ids: `ChargingStation.station_id`-Werte, die geprueft
                werden sollen (unbekannte IDs werden stillschweigend
                uebersprungen).
            max_age: Alter, ab dem gespeicherte Preisdaten als veraltet
                gelten (Default: `PRICING_MAX_AGE`).

        Returns:
            Anzahl der tatsaechlich new eingereihten Stationen.
        """
        if max_age is None:
            max_age = self.PRICING_MAX_AGE

        supercharge_info_ids = {
            sid
            for sid in (self._resolve_supercharge_info_id(s) for s in station_ids)
            if sid is not None
        }
        if not supercharge_info_ids:
            return 0

        recency = self._db.get_pricing_recency(supercharge_info_ids)
        now = datetime.now(UTC)
        eligible = [
            sid
            for sid in supercharge_info_ids
            if sid not in recency or (now - recency[sid]) > max_age
        ]
        if not eligible:
            return 0
        return self._db.enqueue_pricing_refresh(eligible)

    def list_pricing_queue(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Liefert die Preis-Scrape-Warteschlange in Prioritaets-Reihenfolge.

        Siehe `SQLiteDatabase.load_pricing_queue` fuer die Sortierlogik: nie
        gescrapte Stationen zuerst, danach die mit den aeltesten Preisdaten.

        Args:
            limit: Optionale Obergrenze fuer die Anzahl zurueckgegebener
                Eintraege.

        Returns:
            Liste von Warteschlangen-Eintraegen (siehe `load_pricing_queue`).
        """
        return self._db.load_pricing_queue(limit=limit)

    async def drain_pricing_queue(
        self,
        limit: int | None = None,
        tesla_client: TeslaClient | None = None,
        delay_s: float = 1.5,
    ) -> PricingQueueDrainResult:
        """Arbeitet die Preis-Scrape-Warteschlange in Prioritaets-Reihenfolge ab.

        Fuer jeden Eintrag: prueft defensiv erneut die Aktualitaet (falls
        zwischenzeitlich anderweitig aktualisiert), scraped sonst per
        `refresh_pricing` und pausiert `delay_s` zwischen Requests (siehe
        Referenz-Tool in docs/Tesla-Supercharger-Detail-Scraping.md: "random
        1.0-2.5s pause between navigations"). Jeder Eintrag wird nach dem
        Versuch aus der Warteschlange entfernt, auch bei Fehlschlag - siehe
        `SQLiteDatabase.dequeue_pricing_refresh`.

        Args:
            limit: Optionale Obergrenze fuer die Anzahl abzuarbeitender
                Eintraege in diesem Lauf.
            tesla_client: Optionaler TeslaClient (fuer Tests).
            delay_s: Pause zwischen aufeinanderfolgenden Requests in Sekunden.

        Returns:
            `PricingQueueDrainResult` mit erfolgreich aktualisierten,
            uebersprungenen (bereits aktuellen) und fehlgeschlagenen Stationen.
        """
        created = tesla_client is None
        if tesla_client is None:
            tesla_client = create_tesla_client(debug_log=self._debug_log)

        try:
            entries = self.list_pricing_queue(limit=limit)
            refreshed: list[str] = []
            skipped_fresh: list[str] = []
            failed: list[tuple[str, str]] = []

            for entry in entries:
                supercharge_info_id: int = entry["supercharge_info_id"]
                slug: str | None = entry["tesla_location_id"]
                if not slug:
                    # Station ohne Slug kann nicht ueber die Detailseite
                    # abgerufen werden (URL braucht den Slug) - dauerhaft
                    # unscrapebar, aus der Warteschlange entfernen statt bei
                    # jedem Lauf erneut zu scheitern.
                    self._db.dequeue_pricing_refresh(supercharge_info_id)
                    failed.append((str(supercharge_info_id), "Station hat keine tesla_location_id"))
                    continue

                recency = self._db.get_pricing_recency({supercharge_info_id})
                if (
                    supercharge_info_id in recency
                    and (datetime.now(UTC) - recency[supercharge_info_id]) <= self.PRICING_MAX_AGE
                ):
                    self._db.dequeue_pricing_refresh(supercharge_info_id)
                    skipped_fresh.append(slug)
                    continue

                try:
                    await self.refresh_pricing(slug, tesla_client=tesla_client)
                    refreshed.append(slug)
                except (CurlError, PricingParseError) as e:
                    failed.append((slug, str(e)))

                if delay_s > 0 and entry is not entries[-1]:
                    await asyncio.sleep(delay_s)

            return PricingQueueDrainResult(
                refreshed=refreshed, skipped_fresh=skipped_fresh, failed=failed
            )
        finally:
            # Eigenen Client (z. B. Chromium-Browser) deterministisch beenden.
            if created:
                await tesla_client.close()
