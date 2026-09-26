"""implementation des ChargingStationprovider mit SQLite-DB + supercharge.info-API."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tqdm.asyncio import tqdm

from tripplanner.geo import Coordinate, haversine_distance_m

from ..client import CurlError, SuperchargeInfoClient, TeslaClient, create_tesla_client
from ..database import SQLiteDatabase
from ..models import (
    ChargingStation,
    ChargingStationProvider,
)
from .pricing_queue import PricingQueueMixin
from .record_mapping import (
    db_record_to_charging_station,
    site_to_db_record,
    tesla_detail_to_db_record,
    tesla_location_to_db_record,
)
from .spatial import _build_lat_bands, _stations_in_radius

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from typing import Literal

# Default path for the SQLite database (project-root/data/tesla_superchargers.db)
_DEFAULT_DB_PATH: Path = Path(__file__).resolve().parents[4] / "data" / "tesla_superchargers.db"


class TeslaChargingStationProvider(ChargingStationProvider, PricingQueueMixin):
    """Supercharger-data aus SQLite-DB mit supercharge.info-API-Refresh.

    Default: liest aus data/tesla_superchargers.db (erzeugt databank bei
    erstmaligem Zugriff automatisch und loads initiale data).

    Usage:
        provider = TeslaChargingStationprovider()
        stations = await provider.get_stations_in_radius((52.5, 13.4), 10)
        await provider.refresh()
    """

    # Valid countries that the current ChargingStation model supports
    _VALID_COUNTRIES: frozenset[str] = frozenset({"DE", "DK", "SE"})

    # Default staleness threshold for cached pricing data before it is
    # queued for re-scraping (mirrors the legacy tesla-pricing tool's
    # MAX_AGE_DAYS_DEFAULT, see docs/Tesla-Supercharger-Detail-Scraping.md).
    PRICING_MAX_AGE: timedelta = timedelta(days=14)

    # Max distance (meters) for matching a station's stored coordinates to a
    # Tesla directory entry when resolving a stale numeric `tesla_location_id`
    # (see `_resolve_numeric_slug`). supercharge.info and Tesla's own
    # coordinates for the same physical site normally agree within a few
    # tens of meters; this margin tolerates minor drift without risking a
    # false match against a nearby, unrelated Supercharger.
    _SLUG_RESOLUTION_MAX_DISTANCE_M: float = 500.0

    # --- Abwaertskompatible Aliase fuer die nach ``record_mapping``
    # --- ausgelagerten Mapping-Static-Methoden (Tests importieren sie teils
    # --- weiterhin ueber ``TeslaChargingStationprovider._<name>``).

    @staticmethod
    def _tesla_detail_to_db_record(
        detail: dict[str, Any],
        country: str,
    ) -> dict[str, Any]:
        """Delegiert an ``record_mapping.tesla_detail_to_db_record``."""
        from .record_mapping import tesla_detail_to_db_record as _fn

        return _fn(detail, country)

    def __init__(
        self,
        db_path: Path | None = None,
        client: SuperchargeInfoClient | None = None,
        debug_log: Path | None = None,
    ) -> None:
        """Initialisiert den provider.

        Args:
            db_path: Pfad zur SQLite-DB. Default: data/tesla_superchargers.db
            client: Optional HTTP client (for tests with mock). Otherwise automatic.
            debug_log: Optionaler Dateipfad fuer Request/Response-Debug-Log.
        """
        if db_path is None:
            db_path = _DEFAULT_DB_PATH
        self._db = SQLiteDatabase(db_path)
        self._db.initialize()
        self._client = client
        self._debug_log = debug_log
        self._stations: list[ChargingStation] | None = None
        # Spatial index over `self._stations` (siehe `_build_lat_bands`).
        # `_lat_bands_source` holds the identity of the station list from which
        # `_lat_bands` gebaut wurde - aendert sich `self._stations` (Reload
        # nach `refresh()`/`update_station()` o.ae., die den Cache auf `None`
        # setting), recognizes `get_stations_in_radius()` which automatically
        # the identity comparison and rebuilds the index, without each
        # cache invalidation point would have to reset the index separately.
        self._lat_bands: dict[int, list[ChargingStation]] | None = None
        self._lat_bands_source: list[ChargingStation] | None = None
        # Serialises on-demand Tesla scrapes (browser/curl sessions) triggered
        # via the API so repeated calls cannot spawn many browsers at once.
        self.scrape_slot = asyncio.Semaphore(1)

    def close(self) -> None:
        """Schliesst die zugrunde liegende SQLite-connection.

        caller (z. B. `trip_input.api._lifespan`), die den provider
        reused process-wide, MUST call this on shutdown to
        die databankverbindung sauber freizugeben.
        """
        self._db.close()

    async def refresh(self) -> int:
        """Holt aktuelle data von supercharge.info und schreibt sie in die DB.

        1. Fetch all sites via API
        2. filter auf Europe (address.region == "Europe")
        3. Mappe jedes Site auf DB-Record-format
        4. Rufe SQLiteDatabase.replace_all_stations() auf

        Returns:
            Anzahl der gespeicherten Stationen
        """
        if self._client is None:
            self._client = SuperchargeInfoClient(debug_log=self._debug_log)

        raw_sites = await self._client.fetch_all_sites()

        euro_sites = [s for s in raw_sites if s.get("address", {}).get("region") == "Europe"]

        db_records = [site_to_db_record(s) for s in euro_sites]
        self._db.replace_all_stations(db_records)
        self._stations = None  # invalidate cache

        return len(db_records)

    async def refresh_from_tesla_api(  # noqa: PLR0912
        self,
        countries: list[str] | None = None,
        tesla_client: TeslaClient | None = None,
        enrich_details: bool = False,
        resume_from_slug: str | None = None,
        delay_s: float = 0.5,
    ) -> int:
        """Holt aktuelle Supercharger-data von der Tesla Locations-API.

        Phase 1 (immer): Holt die Standortliste (get-locations), filtert auf
        Supercharger, erzeugt Basis-datasaetze (UUID, Slug, Koordinaten, Typ)
        und speichert sie sofort in die SQLite-databank.

        Phase 2 (optional, enrich_details=True): Ruft fuer jeden Standort die
        Detaildaten ab (get-location-details) und reichert die DB-datasaetze
        mit Stallzahlen, charging_power, Oeffnungszeiten etc. an. Zeigt einen
        tqdm-Progress-Bar an.

        Bei 403 (WAF-Block) wird Phase 2 sofort abgebrochen, die bisher
        angereicherten data bleiben erhalten, und ein CurlError mit dem
        fehlgeschlagenen Slug wird ausgeloest (fuer Resume mit --resume-from).

        Args:
            countries: Liste der ISO-2-Laendercodes (default: DE, DK, SE)
            tesla_client: Optionaler TeslaClient
            enrich_details: Wenn True, werden Detaildaten abgerufen
            resume_from_slug: Slug, ab dem in Phase 2 weitergemacht werden
                soll (alle vorherigen werden uebersprungen)
            delay_s: Verzoegerung zwischen erfolgreichen Detail-Requests

        Returns:
            Anzahl der gespeicherten Stationen

        Raises:
            CurlError: Bei 403 (WAF-Block) in Phase 2
        """
        if countries is None:
            countries = ["DE", "DK", "SE"]
        created = tesla_client is None
        if tesla_client is None:
            tesla_client = create_tesla_client(debug_log=self._debug_log)

        try:
            # --- Phase 1: Standortliste abrufen und Basis-datasaetze speichern ---
            all_records = await self._fetch_tesla_locations(countries, tesla_client)

            if not all_records:
                return 0

            # Phase-1-data sofort in DB schreiben
            self._db.replace_all_stations(all_records)
            self._stations = None
            total = len(all_records)

            # --- Phase 2 (optional): Detaildaten anreichern ---
            if not enrich_details:
                return total

            # Bestimme Start-Index fuer Resume
            start_idx = 0
            if resume_from_slug:
                for i, r in enumerate(all_records):
                    if r.get("tesla_location_id") == resume_from_slug:
                        start_idx = i + 1
                        break

            # Original-Locations nach slug indexieren (fuer inHkMoTw)
            loc_by_slug: dict[str, dict[str, Any]] = {}
            for country in countries:
                locations = await tesla_client.fetch_locations(country)
                for loc in locations:
                    s = loc.get("location_url_slug", "")
                    if s:
                        loc_by_slug[s] = loc

            enriched: list[dict[str, Any]] = list(all_records)

            try:
                enriched = await self._enrich_stations(
                    enriched,
                    loc_by_slug,
                    tesla_client,
                    delay_s,
                    start_idx,
                    total,
                )
            except CurlError:
                # Teilweise angereicherte data trotzdem speichern
                if enriched != all_records:
                    self._db.replace_all_stations(enriched)
                    self._stations = None
                raise

            if enriched != all_records:
                self._db.replace_all_stations(enriched)
                self._stations = None

            return total
        finally:
            # deterministically stop own client (e.g. Chromium browser).
            if created:
                await tesla_client.close()

    async def _fetch_tesla_locations(
        self,
        countries: list[str],
        tesla_client: TeslaClient,
    ) -> list[dict[str, Any]]:
        """Phase 1: Holt Standortliste und erzeugt Basis-datasaetze.

        Args:
            countries: Liste der ISO-2-Laendercodes
            tesla_client: TeslaClient

        Returns:
            Liste von DB-Record-Dicts (Dedupliziert nach slug)
        """
        all_records: list[dict[str, Any]] = []
        seen_slugs: set[str] = set()
        for country in countries:
            locations = await tesla_client.fetch_locations(country)
            superchargers = [
                loc
                for loc in locations
                if "supercharger" in loc.get("location_type", []) and not loc.get("inCN", False)
            ]
            for loc in superchargers:
                slug: str = loc.get("location_url_slug", "")
                if not slug or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                record = tesla_location_to_db_record(loc, country)
                all_records.append(record)
        return all_records

    async def _enrich_stations(  # noqa: PLR0913, PLR0917
        self,
        enriched: list[dict[str, Any]],
        loc_by_slug: dict[str, dict[str, Any]],
        tesla_client: TeslaClient,
        delay_s: float,
        start_idx: int,
        total: int,
    ) -> list[dict[str, Any]]:
        """Holt Detaildaten fuer alle Stationen (Phase 2).

        Args:
            enriched: Liste der Basis-datasaetze (wird inline modifiziert)
            loc_by_slug: Mapping slug -> locations-Dict (fuer inHkMoTw)
            tesla_client: TeslaClient
            delay_s: Verzoegerung zwischen Requests
            start_idx: Start-Index (fuer Resume)
            total: Gesamtanzahl

        Returns:
            Angereicherte Liste (gleiche Referenz wie enriched)

        Raises:
            CurlError: Bei 403 (WAF-Block)
        """
        for idx in tqdm(
            range(start_idx, total),
            desc="Enrich details",
            unit="station",
            leave=True,
        ):
            slug = enriched[idx].get("tesla_location_id", "")
            loc = loc_by_slug.get(slug, {})
            try:
                detail = await tesla_client.fetch_location_details(
                    slug,
                    in_hk_mo_tw=loc.get("inHkMoTw", False),
                )
                if detail:
                    detail["_uuid"] = loc.get("uuid", "")
                    detail["_slug"] = slug
                    enriched_record = tesla_detail_to_db_record(
                        detail,
                        enriched[idx]["country_code"],
                    )
                    enriched[idx] = enriched_record
                    tqdm.write(f"  ok  {slug}")
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
            except CurlError:
                tqdm.write(f"  403 {slug} - WAF-Block, breche ab")
                raise CurlError(
                    f"WAF-Block bei Slug '{slug}'. Setze --resume-from {slug} fort."
                ) from None
            except Exception:
                _logger.debug("Enrichment of %s failed", slug, exc_info=True)
                tqdm.write(f"  err {slug} - ueberspringe")
                continue
        return enriched

    def _load_stations_from_db(self) -> list[ChargingStation]:
        """Laedt Stationen aus der DB und wandelt sie in ChargingStation um.

        Filtert auf Laender, die vom aktuellen ChargingStation-model
        be supported (DE, DK, SE), as well as on actually operational
        Stationen (`status == "OPEN"`). Stationen mit Status `CONSTRUCTION`
        ("Coming Soon"), `PERMIT` oder `PLAN` existieren noch nicht physisch
        (z. B. "Torsvik, Sweden", "Quickborn, Germany") bzw. sind reine
        delivery centers under construction (e.g. "Ringsted, Denmark") and must therefore
        nicht als charging_stop-Kandidat in Routing/Scraping auftauchen - siehe
        `db_record_to_charging_station`'s `status_map` for the values that
        `CONSTRUCTION`/`PERMIT`/`PLAN` can take.
        """
        records = self._db.load_stations(
            country_filter=self._VALID_COUNTRIES  # type: ignore[arg-type]
        )
        return [db_record_to_charging_station(r) for r in records if self._is_listed(r)]

    def _is_listed(self, record: dict[str, Any]) -> bool:
        """True for operational (`status == "OPEN"`) stations in a supported country."""
        return (
            record.get("country_code") in self._VALID_COUNTRIES
            and str(record.get("status") or "OPEN").upper() == "OPEN"
        )

    def get_station_by_slug(self, slug: str) -> ChargingStation | None:
        """Looks up one listed station by its `station_id` via an indexed DB query.

        `station_id` is the `tesla_location_id`, or the numeric
        `supercharge_info_id` for stations without one (see
        `db_record_to_charging_station`).
        """
        record = self._db.find_station_by_slug(slug)
        if record is None and slug.isdigit():
            record = self._db.find_station_by_supercharge_info_id(int(slug))
            if record is not None and record.get("tesla_location_id"):
                record = None
        if record is None or not self._is_listed(record):
            return None
        return db_record_to_charging_station(record)

    def get_stations_by_country(self, country: str) -> list[ChargingStation]:
        """Listed stations for one ISO-2 country, filtered in SQL."""
        if country not in self._VALID_COUNTRIES:
            return []
        records = self._db.load_stations(country_filter={country})
        return [db_record_to_charging_station(r) for r in records if self._is_listed(r)]

    def get_station_last_updated(self, slug: str) -> datetime | None:
        """`last_updated_utc` of the station row for `slug`, or None if unknown."""
        record = self._db.find_station_by_slug(slug)
        if record is None or not record.get("last_updated_utc"):
            return None
        return self._db.parse_iso_utc(record["last_updated_utc"])

    async def get_stations_in_radius(
        self,
        coordinate: Coordinate,
        radius_km: float,
        country_filter: Literal["DE", "DK", "SE"] | None = None,
    ) -> list[ChargingStation]:
        """Liefert alle Supercharger innerhalb des gegebenen Radius um die Koordinate.

        Args:
            coordinate: (lat, lon) als Tuple (WGS84)
            radius_km: Suchradius in Kilometern (Flugdistanz)
            country_filter: Optionaler Laenderfilter (DE/DK/SE)

        Returns:
            Liste von ChargingStation, sortiert nach distance (aufsteigend)
        """
        if self._stations is None:
            self._stations = self._load_stations_from_db()
        # latitude-Index new aufbauen, falls `self._stations` seit dem
        # letzten Aufbau new geladen wurde (Identitaetsvergleich statt
        # Invalidierung an jeder `self._stations = None`-Stelle, siehe
        # `__init__`).
        if self._lat_bands is None or self._lat_bands_source is not self._stations:
            self._lat_bands = _build_lat_bands(self._stations)
            self._lat_bands_source = self._stations

        candidates = _stations_in_radius(self._lat_bands, coordinate, radius_km)
        if country_filter:
            candidates = [s for s in candidates if s.country == country_filter]

        # Sortieren nach distance (aufsteigend)
        paired = [(haversine_distance_m(coordinate, s.coordinate) / 1000.0, s) for s in candidates]
        paired.sort(key=lambda x: x[0])
        return [station for _, station in paired]

    async def get_stations_along_route(
        self,
        route: Any,
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Sucht Supercharger entlang der Route.

        Args:
            route: Die geplante Route (muss segments-Attribut haben)
            search_radius_km: Radius um jeden segment-Mittelpunkt

        Returns:
            Dict mapping segment_index -> liste von ChargingStation
        """
        result: dict[int, list[ChargingStation]] = {}
        for i, segment in enumerate(route.segments):
            coords: list[Coordinate] = segment.geometrie
            if not coords:
                continue

            mid_idx = len(coords) // 2
            mid_point = coords[mid_idx]

            nearby = await self.get_stations_in_radius(mid_point, search_radius_km)
            if nearby:
                result[i] = nearby

        return result

    async def refresh_single_station(
        self,
        slug: str,
        country: str | None = None,
        tesla_client: TeslaClient | None = None,
    ) -> ChargingStation | None:
        """Ruft Detaildaten fuer eine einzelne Station von der Tesla API ab.

        Holt frische data von get-location-details fuer den gegebenen
        Slug, aktualisiert den DB-Eintrag und liefert das aktualisierte
        ChargingStation-model zurueck.

        Args:
            slug: tesla_location_id (location_url_slug)
            country: ISO-2 Laendercode (wird aus DB ermittelt wenn None)
            tesla_client: Optionaler TeslaClient

        Returns:
            Aktualisierte ChargingStation oder None bei Fehler.

        Raises:
            CurlError: Bei 403 (WAF-Block)
        """
        created = tesla_client is None
        if tesla_client is None:
            tesla_client = create_tesla_client(debug_log=self._debug_log)

        try:
            # Bestehenden Eintrag laden: liefert Country-Fallback und die
            # stabile supercharge_info_id. Diese MUSS erhalten bleiben, da
            # sie ein unabhaengiger interner Schluessel ist (urspruenglich
            # aus supercharge.info) und Teslas eigene trtId-Nummerierung
            # einem komplett anderen ID-Raum entstammt - ein blindes
            # Uebernehmen der trtId als supercharge_info_id kollidiert
            # leicht mit der ID einer anderen, bereits existierenden
            # Station (UNIQUE-Constraint).
            existing = self._db.find_station_by_slug(slug)
            if country is None:
                if existing is None:
                    return None
                country = existing.get("country_code", "DE")

            # Detaildaten abrufen
            detail = await tesla_client.fetch_location_details(slug)
            if not detail:
                return None

            # In DB-Record umwandeln
            detail["_slug"] = slug
            detail["_uuid"] = detail.get("trtId", "0")

            db_record = tesla_detail_to_db_record(detail, country)
            if existing is not None:
                db_record["supercharge_info_id"] = existing["supercharge_info_id"]

            # In DB speichern
            self._db.update_station(db_record)
            self._stations = None  # Cache invalidieren

            # Zurueck in ChargingStation konvertieren
            return db_record_to_charging_station(db_record)
        finally:
            # deterministically stop own client (e.g. Chromium browser).
            if created:
                await tesla_client.close()

    def get_all_stations(self) -> list[ChargingStation]:
        """Liefert alle Stationen aus der lokalen DB.

        Returns:
            Liste aller ChargingStation-Eintraege.
        """
        if self._stations is None:
            self._stations = self._load_stations_from_db()
        return list(self._stations)
