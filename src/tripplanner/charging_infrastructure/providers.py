"""Provider-Implementierungen für den `charging_infrastructure`-Zugriff.

Enthält:
- `LocalFileChargingStationProvider`: Lädt Daten aus einer lokalen JSON-Datei.
- `FakeChargingStationProvider`: Fake-Provider für Tests ohne Dateizugriff.
- `TeslaChargingStationProvider`: Provider mit SQLite-DB + supercharge.info-API.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from math import ceil, cos, pi, sqrt
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tqdm.asyncio import tqdm

from tripplanner.geo import Coordinate, haversine_distance_m
from tripplanner.routing.models import Route

from .client import SuperchargeInfoClient, TeslaLocationsClient
from .database import _COUNTRY_MAP, SQLiteDatabase
from .models import (
    ChargingStation,
    ChargingStationProvider,
    ConnectorType,
    StallType,
)

if TYPE_CHECKING:
    from typing import Literal

# Default path for the SQLite database (project-root/data/tesla_superchargers.db)
_DEFAULT_DB_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "tesla_superchargers.db"
)


_LAT_BAND_KM = 5.0
"""Breite der Breitengrad-Bänder für den räumlichen Stations-Index (siehe
`_build_lat_bands`/`_stations_in_radius`). Klein genug, um bei den in der
Praxis verwendeten Suchradien (1-15 km, siehe `get_stations_along_route`)
die pro Abfrage zu prüfende Kandidatenzahl massiv zu reduzieren - unabhängig
vom tatsächlichen `radius_km` einer konkreten Abfrage korrekt, da
`_stations_in_radius` die Anzahl der zu scannenden Bänder passend zu
`radius_km` berechnet."""

_KM_PER_LAT_DEG = 111.0
"""Näherung: 1 Breitengrad ≈ 111 km (global nahezu konstant - anders als 1
Längengrad, der mit `cos(lat)` schrumpft). Deshalb Bucketing NUR nach
Breitengrad, nicht 2D nach Breiten-/Längengrad - einfach und ohne
breitengradabhängiges Verzerrungsrisiko."""


def _build_lat_bands(
    stations: list[ChargingStation], band_km: float = _LAT_BAND_KM
) -> dict[int, list[ChargingStation]]:
    """Bucketiert Stationen nach Breitengrad-Band für schnelle Radius-Suchen.

    Ersetzt den linearen Voll-Scan über ALLE Stationen in
    `get_stations_in_radius()`: `get_stations_along_route()` ruft diese pro
    Roh-Segment auf (bei feingranularen Routen, z. B. ein Segment pro
    GraphHopper-Polyline-Punktpaar, oft tausende Aufrufe) - ohne Index ein
    O(Segmente x Stationen)-Kostenfaktor (siehe docs/plans/07-optimization.md).
    """
    band_deg = band_km / _KM_PER_LAT_DEG
    bands: dict[int, list[ChargingStation]] = {}
    for station in stations:
        lat, _lon = station.coordinate
        bands.setdefault(int(lat // band_deg), []).append(station)
    return bands


def _stations_in_radius(
    lat_bands: dict[int, list[ChargingStation]],
    coordinate: Coordinate,
    radius_km: float,
    band_km: float = _LAT_BAND_KM,
) -> list[ChargingStation]:
    """Liefert alle Stationen aus `lat_bands` innerhalb `radius_km` um `coordinate`.

    Exakt äquivalent zu einem Voll-Scan mit `haversine_distance_m` + Filter
    (siehe `_build_lat_bands`), prüft aber nur Stationen aus den
    Breitengrad-Bändern, die `coordinate` innerhalb `radius_km` überhaupt
    erreichen können - kein Genauigkeitsverlust, nur weniger Kandidaten.
    Ergebnis unsortiert und ohne Länderfilter (Aufrufer wendet beides bei
    Bedarf selbst an, wie beim bisherigen Voll-Scan).
    """
    lat, _lon = coordinate
    band_deg = band_km / _KM_PER_LAT_DEG
    center_band = int(lat // band_deg)
    band_span = max(1, ceil(radius_km / band_km))

    result: list[ChargingStation] = []
    for band in range(center_band - band_span, center_band + band_span + 1):
        for station in lat_bands.get(band, ()):
            if haversine_distance_m(coordinate, station.coordinate) / 1000.0 <= radius_km:
                result.append(station)
    return result


class LocalFileChargingStationProvider(ChargingStationProvider):
    """Implementierung, die Ladedaten aus einer lokalen JSON-Datei liest.

    Für Tests und Produktion (solange kein Crawler implementiert ist).
    """

    def __init__(self, data_path: Path) -> None:
        """Initialisiere den Provider mit dem Pfad zur JSON-Datei.

        Args:
            data_path: Pfad zur JSON-Datei (z. B. data/supercharger_snapshot.json)
        """
        self.data_path = data_path
        self._stations: list[ChargingStation] | None = None
        # Räumlicher Index über `self._stations`, siehe
        # `TeslaChargingStationProvider._lat_bands` für die Begründung.
        self._lat_bands: dict[int, list[ChargingStation]] | None = None
        self._lat_bands_source: list[ChargingStation] | None = None

    def _load_stations(self) -> list[ChargingStation]:
        """Lädt und parst die JSON-Datei (lazy)."""
        if self._stations is not None:
            return self._stations

        with open(self.data_path, encoding="utf-8") as f:
            data = json.load(f)

        stations: list[ChargingStation] = []
        for item in data["stations"]:
            stalls: dict[StallType, int] = {
                StallType.V2: item["stalls"].get("V2", 0),
                StallType.V3: item["stalls"].get("V3", 0),
                StallType.V3_ULTRA: item["stalls"].get("V3Ultra", 0),
                StallType.V4: item["stalls"].get("V4", 0),
            }

            # Max Leistung berechnen: Summe aller Stalls * durchschnittliche Leistung pro Stall
            # Vereinfachung: V2=150kW, V3=250kW, V3Ultra=325kW, V4=325kW
            max_power = (
                stalls[StallType.V2] * 150.0
                + stalls[StallType.V3] * 250.0
                + stalls[StallType.V3_ULTRA] * 325.0
                + stalls[StallType.V4] * 325.0
            )

            # Parse datetime aus String oder use default
            erstellungsdatum_str = item.get("erstellungsdatum")
            if isinstance(erstellungsdatum_str, str):
                try:
                    # Versuche ISO-Format
                    erstellungsdatum = datetime.fromisoformat(
                        erstellungsdatum_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except ValueError:
                    erstellungsdatum = datetime.now(UTC)
            else:
                erstellungsdatum = datetime.now(UTC)

            stations.append(
                ChargingStation(
                    station_id=item["station_id"],
                    name=item["name"],
                    coordinate=(item["lat"], item["lon"]),
                    stalls=stalls,
                    max_ladeleistung_kw=max_power,
                    connector_types=[ConnectorType(c) for c in item["connector_types"]],
                    country=item["country"],
                    ist_24_7=item.get("ist_24_7", True),
                    status=item.get("status", "online"),
                    letzte_datenAktualisierung=erstellungsdatum,
                )
            )

        self._stations = stations
        return stations

    async def get_stations_in_radius(
        self,
        coordinate: Coordinate,
        radius_km: float,
        country_filter: Literal["DE", "DK", "SE"] | None = None,
    ) -> list[ChargingStation]:
        """Liefert alle Supercharger innerhalb des gegebenen Radius um die Koordinate.

        Nutzt `haversine_distance_m` von `tripplanner.geo` für die Distanzberechnung.

        Args:
            coordinate: (lat, lon) als Tuple (WGS84)
            radius_km: Suchradius in Kilometern (Flugdistanz)
            country_filter: Optionaler Länderfilter (DE/DK/SE)

        Returns:
            Liste von ChargingStation, sortiert nach Distanz (aufsteigend)
        """
        stations = self._load_stations()
        if self._lat_bands is None or self._lat_bands_source is not stations:
            self._lat_bands = _build_lat_bands(stations)
            self._lat_bands_source = stations

        candidates = _stations_in_radius(self._lat_bands, coordinate, radius_km)
        if country_filter:
            candidates = [s for s in candidates if s.country == country_filter]

        # Sortieren nach Distanz (aufsteigend)
        paired = [(haversine_distance_m(coordinate, s.coordinate) / 1000.0, s) for s in candidates]
        paired.sort(key=lambda x: x[0])
        return [station for _, station in paired]

    async def get_stations_along_route(
        self,
        route: Any,
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Sucht Supercharger entlang der Route.

        Für jedes Segment wird der Mittelpunkt berechnet und in einem Radius von
        `search_radius_km` gesucht.

        Args:
            route: Die geplante Route (muss segments-Attribut haben)
            search_radius_km: Radius um jeden Segment-Mittelpunkt

        Returns:
            Dict mapping segment_index -> liste von ChargingStation
        """
        # Importiere route nur hier um Zyklus zu vermeiden
        result: dict[int, list[ChargingStation]] = {}
        for i, segment in enumerate(route.segments):
            # Segment-Mittelpunkt als geo-mittlerer Punkt
            coords: list[Coordinate] = segment.geometrie
            if not coords:
                continue

            mid_idx = len(coords) // 2
            mid_point = coords[mid_idx]

            # Alle Stationen im Radius finden
            nearby = await self.get_stations_in_radius(mid_point, search_radius_km)
            if nearby:
                result[i] = nearby

        return result


class FakeChargingStationProvider(ChargingStationProvider):
    """Fake-Provider für Unit-Tests ohne Dateizugriff.

    Liefert feste Testdaten basierend auf dem Suchparameter.
    """

    def __init__(self, test_stations: list[ChargingStation] | None = None) -> None:
        """Initialisiere den Fake-Provider mit optionalen Test-Stationen.

        Args:
            test_stations: Liste von Test-Stationen (default: einige Dummy-Stationen)
        """
        if test_stations is None:
            test_stations = [
                ChargingStation(
                    station_id="test-berlin-1",
                    name="Tesla Supercharger - Berlin Alexanderplatz",
                    coordinate=(52.5234, 13.4114),
                    stalls={StallType.V3: 8, StallType.V3_ULTRA: 4},
                    max_ladeleistung_kw=3250.0,
                    connector_types=[ConnectorType.NACS, ConnectorType.CCS2],
                    country="DE",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
                ChargingStation(
                    station_id="test-berlin-2",
                    name="Tesla Supercharger - Berlin Potsdamer Platz",
                    coordinate=(52.5083, 13.3797),
                    stalls={StallType.V3: 6},
                    max_ladeleistung_kw=1500.0,
                    connector_types=[ConnectorType.CCS2],
                    country="DE",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
                ChargingStation(
                    station_id="test-kopenhagen-1",
                    name="Tesla Supercharger - Kopenhagen Zentrum",
                    coordinate=(55.6761, 12.5683),
                    stalls={StallType.V3: 4, StallType.V4: 4},
                    max_ladeleistung_kw=2600.0,
                    connector_types=[ConnectorType.CCS2, ConnectorType.TYPE2],
                    country="DK",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
                ChargingStation(
                    station_id="test-malmo-1",
                    name="Tesla Supercharger - Malmö Urban",
                    coordinate=(55.5941, 13.0039),
                    stalls={StallType.V3: 8},
                    max_ladeleistung_kw=2000.0,
                    connector_types=[ConnectorType.CCS2],
                    country="SE",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
            ]

        self._stations = test_stations

    async def get_stations_in_radius(
        self,
        coordinate: Coordinate,
        radius_km: float,
        country_filter: Literal["DE", "DK", "SE"] | None = None,
    ) -> list[ChargingStation]:
        """Liefert Fake-Testdaten.

        Filtert die eingebenen Test-Stationen nach Radius und Länderfilter.
        Sortiert nach Distanz (aufsteigend).
        """
        # Länderfilter anwenden
        stations = self._stations
        if country_filter:
            stations = [s for s in stations if s.country == country_filter]
        # Distanzberechnung und Filterung
        result: list[ChargingStation] = []
        distances: list[float] = []
        for station in stations:
            distance_m = haversine_distance_m(coordinate, station.coordinate)
            distance_km = distance_m / 1000.0

            if distance_km <= radius_km:
                result.append(station)
                distances.append(distance_km)

        # Sortieren nach Distanz (aufsteigend)
        paired = list(zip(distances, result, strict=True))
        paired.sort(key=lambda x: x[0])
        result = [station for _, station in paired]

        return result

    async def get_stations_along_route(
        self,
        route: Route,
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Liefert Fake-Testdaten entlang der Route.

        Verteilt alle konfigurierten Stationen entlang der Route basierend auf
        der Entfernung zum Segment-Mittelpunkt.
        """
        result: dict[int, list[ChargingStation]] = {}
        stations = self._stations
        if not stations or not route.segments:
            return result

        # Für jede Station das nächste Segment anhand der Midpoint-Distanz finden
        for station in stations:
            best_seg_idx = 0
            best_dist = float("inf")
            for seg in route.segments:
                # Need at least 2 points to interpolate midpoint
                MIN_POINTS_FOR_MIDPOINT = 2
                if len(seg.geometrie) < MIN_POINTS_FOR_MIDPOINT:
                    continue
                mid_idx = len(seg.geometrie) // 2
                midpoint = seg.geometrie[mid_idx]
                # Haversine-Näherung: Distanz in Metern
                dlat = (station.coordinate[0] - midpoint[0]) * 111.32 * 1000
                dlon = (
                    (station.coordinate[1] - midpoint[1])
                    * 111.32
                    * 1000
                    * cos(midpoint[0] * pi / 180)
                )
                dist_m = sqrt(dlat * dlat + dlon * dlon)
                if dist_m < best_dist:
                    best_dist = dist_m
                    best_seg_idx = seg.segment_index
            # Assign station to nearest segment (no distance filter for fake provider)
            result.setdefault(best_seg_idx, []).append(station)

        return result


class TeslaChargingStationProvider(ChargingStationProvider):
    """Supercharger-Daten aus SQLite-DB mit supercharge.info-API-Refresh.

    Default: liest aus data/tesla_superchargers.db (erzeugt Datenbank bei
    erstmaligem Zugriff automatisch und lädt initiale Daten).

    Usage:
        provider = TeslaChargingStationProvider()
        stations = await provider.get_stations_in_radius((52.5, 13.4), 10)
        await provider.refresh()
    """

    # Valid countries that the current ChargingStation model supports
    _VALID_COUNTRIES: frozenset[str] = frozenset({"DE", "DK", "SE"})

    # Stall-type power thresholds (kW per stall)
    _POWER_V2_MAX: int = 150
    _POWER_V3_MAX: int = 250
    _POWER_V3_ULTRA_MAX: int = 350

    def __init__(
        self,
        db_path: Path | None = None,
        client: SuperchargeInfoClient | None = None,
        debug_log: Path | None = None,
    ) -> None:
        """Initialisiert den Provider.

        Args:
            db_path: Pfad zur SQLite-DB. Default: data/tesla_superchargers.db
            client: Optionaler HTTP-Client (für Tests mit Mock). Sonst auto.
            debug_log: Optionaler Dateipfad fuer Request/Response-Debug-Log.
        """
        if db_path is None:
            db_path = _DEFAULT_DB_PATH
        self._db = SQLiteDatabase(db_path)
        self._db.initialize()
        self._client = client
        self._debug_log = debug_log
        self._stations: list[ChargingStation] | None = None
        # Räumlicher Index über `self._stations` (siehe `_build_lat_bands`).
        # `_lat_bands_source` hält die Identität der Stationsliste, aus der
        # `_lat_bands` gebaut wurde - ändert sich `self._stations` (Reload
        # nach `refresh()`/`update_station()` o.ä., die den Cache auf `None`
        # setzen), erkennt `get_stations_in_radius()` das automatisch über
        # den Identitätsvergleich und baut den Index neu, ohne dass jede
        # Cache-Invalidierungsstelle den Index separat zurücksetzen müsste.
        self._lat_bands: dict[int, list[ChargingStation]] | None = None
        self._lat_bands_source: list[ChargingStation] | None = None

    def close(self) -> None:
        """Schließt die zugrunde liegende SQLite-Verbindung.

        Aufrufer (z. B. `trip_input.api._lifespan`), die den Provider
        prozessweit wiederverwenden, MÜSSEN dies beim Shutdown aufrufen, um
        die Datenbankverbindung sauber freizugeben.
        """
        self._db.close()

    async def refresh(self) -> int:
        """Holt aktuelle Daten von supercharge.info und schreibt sie in die DB.

        1. Fetch all sites via API
        2. Filtere auf Europe (address.region == "Europe")
        3. Mappe jedes Site auf DB-Record-Format
        4. Rufe SQLiteDatabase.replace_all_stations() auf

        Returns:
            Anzahl der gespeicherten Stationen
        """
        if self._client is None:
            self._client = SuperchargeInfoClient(debug_log=self._debug_log)

        raw_sites = await self._client.fetch_all_sites()

        euro_sites = [s for s in raw_sites if s.get("address", {}).get("region") == "Europe"]

        db_records = [self._site_to_db_record(s) for s in euro_sites]
        self._db.replace_all_stations(db_records)
        self._stations = None  # invalidate cache

        return len(db_records)

    async def refresh_from_tesla_api(  # noqa: PLR0912
        self,
        countries: list[str] | None = None,
        tesla_client: TeslaLocationsClient | None = None,
        enrich_details: bool = False,
        resume_from_slug: str | None = None,
        delay_s: float = 0.5,
    ) -> int:
        """Holt aktuelle Supercharger-Daten von der Tesla Locations-API.

        Phase 1 (immer): Holt die Standortliste (get-locations), filtert auf
        Supercharger, erzeugt Basis-Datensaetze (UUID, Slug, Koordinaten, Typ)
        und speichert sie sofort in die SQLite-Datenbank.

        Phase 2 (optional, enrich_details=True): Ruft fuer jeden Standort die
        Detaildaten ab (get-location-details) und reichert die DB-Datensaetze
        mit Stallzahlen, Ladeleistung, Oeffnungszeiten etc. an. Zeigt einen
        tqdm-Progress-Bar an.

        Bei 403 (WAF-Block) wird Phase 2 sofort abgebrochen, die bisher
        angereicherten Daten bleiben erhalten, und ein CurlError mit dem
        fehlgeschlagenen Slug wird ausgeloest (fuer Resume mit --resume-from).

        Args:
            countries: Liste der ISO-2-Laendercodes (default: DE, DK, SE)
            tesla_client: Optionaler TeslaLocationsClient
            enrich_details: Wenn True, werden Detaildaten abgerufen
            resume_from_slug: Slug, ab dem in Phase 2 weitergemacht werden
                soll (alle vorherigen werden uebersprungen)
            delay_s: Verzoegerung zwischen erfolgreichen Detail-Requests

        Returns:
            Anzahl der gespeicherten Stationen

        Raises:
            TeslaLocationsClient.CurlError: Bei 403 (WAF-Block) in Phase 2
        """
        if countries is None:
            countries = ["DE", "DK", "SE"]
        if tesla_client is None:
            tesla_client = TeslaLocationsClient(debug_log=self._debug_log)

        # --- Phase 1: Standortliste abrufen und Basis-Datensaetze speichern ---
        all_records = await self._fetch_tesla_locations(countries, tesla_client)

        if not all_records:
            return 0

        # Phase-1-Daten sofort in DB schreiben
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
        except TeslaLocationsClient.CurlError:
            # Teilweise angereicherte Daten trotzdem speichern
            if enriched != all_records:
                self._db.replace_all_stations(enriched)
                self._stations = None
            raise

        if enriched != all_records:
            self._db.replace_all_stations(enriched)
            self._stations = None

        return total

    async def _fetch_tesla_locations(
        self,
        countries: list[str],
        tesla_client: TeslaLocationsClient,
    ) -> list[dict[str, Any]]:
        """Phase 1: Holt Standortliste und erzeugt Basis-Datensaetze.

        Args:
            countries: Liste der ISO-2-Laendercodes
            tesla_client: TeslaLocationsClient

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
                record = self._tesla_location_to_db_record(loc, country)
                all_records.append(record)
        return all_records

    async def _enrich_stations(  # noqa: PLR0913, PLR0917
        self,
        enriched: list[dict[str, Any]],
        loc_by_slug: dict[str, dict[str, Any]],
        tesla_client: TeslaLocationsClient,
        delay_s: float,
        start_idx: int,
        total: int,
    ) -> list[dict[str, Any]]:
        """Holt Detaildaten fuer alle Stationen (Phase 2).

        Args:
            enriched: Liste der Basis-Datensaetze (wird inline modifiziert)
            loc_by_slug: Mapping slug -> locations-Dict (fuer inHkMoTw)
            tesla_client: TeslaLocationsClient
            delay_s: Verzoegerung zwischen Requests
            start_idx: Start-Index (fuer Resume)
            total: Gesamtanzahl

        Returns:
            Angereicherte Liste (gleiche Referenz wie enriched)

        Raises:
            TeslaLocationsClient.CurlError: Bei 403 (WAF-Block)
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
                    enriched_record = self._tesla_detail_to_db_record(
                        detail,
                        enriched[idx]["country_code"],
                    )
                    enriched[idx] = enriched_record
                    tqdm.write(f"  ok  {slug}")
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
            except TeslaLocationsClient.CurlError:
                tqdm.write(f"  403 {slug} - WAF-Block, breche ab")
                raise TeslaLocationsClient.CurlError(
                    f"WAF-Block bei Slug '{slug}'. Setze --resume-from {slug} fort."
                ) from None
            except Exception:
                tqdm.write(f"  err {slug} - ueberspringe")
                continue
        return enriched

    @staticmethod
    def _tesla_location_to_db_record(
        loc: dict[str, Any],
        country: str,
    ) -> dict[str, Any]:
        """Wandelt einen Tesla-Locations-Listeneintrag in ein DB-Record-Dict.

        Wird verwendet wenn keine Detaildaten verfuegbar sind.

        Args:
            loc: Dict aus fetch_locations() (mit _slug und _uuid als Fallback)
            country: ISO-2 Laendercode

        Returns:
            DB-Record-Dict mit verfuegbaren Feldern
        """
        uuid_str: str = loc.get("_uuid", loc.get("uuid", "0"))
        supercharge_info_id = TeslaChargingStationProvider._parse_int(uuid_str, 0)
        slug: str = loc.get("_slug", loc.get("location_url_slug", ""))
        lat: float = loc.get("latitude", 0.0)
        lon: float = loc.get("longitude", 0.0)
        now = datetime.now(UTC).isoformat()

        return {
            "supercharge_info_id": supercharge_info_id,
            "tesla_location_id": slug,
            "site_name": f"Tesla Supercharger - {country}",
            "latitude": lat,
            "longitude": lon,
            "country_code": country,
            "stalls_v2": 0,
            "stalls_v3": 0,
            "stalls_v3_ultra": 0,
            "stalls_v4": 0,
            "total_stalls": 0,
            "power_kilowatt": 250,
            "status": "OPEN",
            "connector_types": json.dumps(["ccs2"]),
            "ist_24_7": 1,
            "date_opened": None,
            "last_updated_utc": now,
        }

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        """Versucht einen Wert als int zu parsen, Fallback auf default."""
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _tesla_coords(
        sc: dict[str, Any],
        key_data: dict[str, Any],
    ) -> tuple[float, float]:
        """Extrahiert Koordinaten aus Tesla-API-Detail.

        Bevorzugt actual_latitude/actual_longitude aus supercharger_function,
        fallback auf geo_point aus key_data.
        """
        lat_str: str = sc.get("actual_latitude", "")
        lon_str: str = sc.get("actual_longitude", "")
        if lat_str and lon_str:
            try:
                return (float(lat_str), float(lon_str))
            except (ValueError, TypeError):
                pass
        geo = key_data.get("geo_point", {})
        return (geo.get("lat", 0.0), geo.get("lon", 0.0))

    @staticmethod
    def _tesla_detail_to_db_record(
        detail: dict[str, Any],
        country: str,
    ) -> dict[str, Any]:
        """Wandelt ein Tesla-API-Detail-Dict in ein DB-Record-Dict um.

        Args:
            detail: Detail-Dict von fetch_location_details()
            country: ISO-2 Ländercode

        Returns:
            DB-Record-Dict für replace_all_stations
        """
        sc = detail.get("supercharger_function", {})
        marketing = detail.get("marketing", {})
        key_data = detail.get("key_data", {})

        # ID (uuid), stall count, power
        supercharge_info_id = TeslaChargingStationProvider._parse_int(detail.get("_uuid", "0"), 0)
        total_stalls = TeslaChargingStationProvider._parse_int(sc.get("num_charger_stalls", "0"), 0)
        power = TeslaChargingStationProvider._parse_int(sc.get("installed_full_power", "250"), 250)

        # Coordinates
        latitude, longitude = TeslaChargingStationProvider._tesla_coords(sc, key_data)

        # Site name: display_name from marketing
        site_name: str = marketing.get("display_name", marketing.get("common_name", ""))

        # Status
        status_map: dict[str, str] = {
            "Open": "OPEN",
            "Closed": "TEMP_CLOSED",
            "Coming Soon": "CONSTRUCTION",
            "Permit": "PERMIT",
        }
        status_name = key_data.get("status", {}).get("name", "Open")
        status: str = status_map.get(status_name, "OPEN")

        # Stall type distribution: derive from power
        _v2 = TeslaChargingStationProvider._POWER_V2_MAX
        _v3 = TeslaChargingStationProvider._POWER_V3_MAX
        _v3u = TeslaChargingStationProvider._POWER_V3_ULTRA_MAX
        stalls_v2 = total_stalls if power <= _v2 else 0
        stalls_v3 = total_stalls if _v2 < power <= _v3 else 0
        stalls_v3_ultra = total_stalls if _v3 < power <= _v3u else 0
        stalls_v4 = total_stalls if power > _v3u else 0

        # Date opened from functions
        functions = detail.get("functions", [])
        date_opened: str | None = None
        for fn in functions:
            od = fn.get("opening_date")
            if od:
                date_opened = od
                break

        # Connector types: default CCS2 for European superchargers
        connector_types = json.dumps(["ccs2"])
        if sc.get("open_to_non_tesla", False):
            connector_types = json.dumps(["ccs2", "nacs"])

        now = datetime.now(UTC).isoformat()

        return {
            "supercharge_info_id": supercharge_info_id,
            "tesla_location_id": detail.get("_slug", ""),
            "site_name": site_name or f"Tesla Supercharger - {country}",
            "latitude": latitude,
            "longitude": longitude,
            "country_code": country,
            "stalls_v2": stalls_v2,
            "stalls_v3": stalls_v3,
            "stalls_v3_ultra": stalls_v3_ultra,
            "stalls_v4": stalls_v4,
            "total_stalls": total_stalls,
            "power_kilowatt": power,
            "status": status,
            "connector_types": connector_types,
            "ist_24_7": 1,
            "date_opened": date_opened,
            "last_updated_utc": now,
        }

    def _load_stations_from_db(self) -> list[ChargingStation]:
        """Lädt Stationen aus der DB und wandelt sie in ChargingStation um.

        Filtert auf Länder, die vom aktuellen ChargingStation-Modell
        unterstützt werden (DE, DK, SE).
        """
        records = self._db.load_stations(
            country_filter=self._VALID_COUNTRIES  # type: ignore[arg-type]
        )
        return [self._db_record_to_charging_station(r) for r in records]

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
            country_filter: Optionaler Länderfilter (DE/DK/SE)

        Returns:
            Liste von ChargingStation, sortiert nach Distanz (aufsteigend)
        """
        if self._stations is None:
            self._stations = self._load_stations_from_db()
        # Breitengrad-Index neu aufbauen, falls `self._stations` seit dem
        # letzten Aufbau neu geladen wurde (Identitätsvergleich statt
        # Invalidierung an jeder `self._stations = None`-Stelle, siehe
        # `__init__`).
        if self._lat_bands is None or self._lat_bands_source is not self._stations:
            self._lat_bands = _build_lat_bands(self._stations)
            self._lat_bands_source = self._stations

        candidates = _stations_in_radius(self._lat_bands, coordinate, radius_km)
        if country_filter:
            candidates = [s for s in candidates if s.country == country_filter]

        # Sortieren nach Distanz (aufsteigend)
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
            search_radius_km: Radius um jeden Segment-Mittelpunkt

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

    @staticmethod
    def _site_to_db_record(site: dict[str, Any]) -> dict[str, Any]:
        """Wandelt ein supercharge.info-Site-Dict in ein DB-Record-Dict um.

        Args:
            site: Roh-Dict von der supercharge.info-API

        Returns:
            DB-Record-Dict für replace_all_stations
        """
        gps = site["gps"]
        address = site.get("address", {})
        plugs = site.get("plugs", {})

        available_plugs = {"nacs", "ccs1", "ccs2", "type2", "gbt", "chademo", "tpc"}
        connector_types = json.dumps(
            [k for k, v in plugs.items() if v and v > 0 and k in available_plugs] or ["ccs2"]
        )

        return {
            "supercharge_info_id": site["id"],
            "tesla_location_id": site.get("locationId"),
            "site_name": site["name"],
            "latitude": gps["latitude"],
            "longitude": gps["longitude"],
            "country_code": _COUNTRY_MAP.get(address.get("country", ""), "XX"),
            "stalls_v2": site.get("stalls", {}).get("v2", 0),
            "stalls_v3": site.get("stalls", {}).get("v3", 0),
            "stalls_v3_ultra": 0,
            "stalls_v4": site.get("stalls", {}).get("v4", 0),
            "total_stalls": site.get("stallCount", 0),
            "power_kilowatt": site.get("powerKilowatt", 250),
            "status": site.get("status", "OPEN"),
            "connector_types": connector_types,
            "ist_24_7": 1,
            "date_opened": site.get("dateOpened"),
            "last_updated_utc": datetime.now(UTC).isoformat(),
        }

    async def refresh_single_station(
        self,
        slug: str,
        country: str | None = None,
        tesla_client: TeslaLocationsClient | None = None,
    ) -> ChargingStation | None:
        """Ruft Detaildaten fuer eine einzelne Station von der Tesla API ab.

        Holt frische Daten von get-location-details fuer den gegebenen
        Slug, aktualisiert den DB-Eintrag und liefert das aktualisierte
        ChargingStation-Modell zurueck.

        Args:
            slug: tesla_location_id (location_url_slug)
            country: ISO-2 Laendercode (wird aus DB ermittelt wenn None)
            tesla_client: Optionaler TeslaLocationsClient

        Returns:
            Aktualisierte ChargingStation oder None bei Fehler.

        Raises:
            TeslaLocationsClient.CurlError: Bei 403 (WAF-Block)
        """
        if tesla_client is None:
            tesla_client = TeslaLocationsClient(debug_log=self._debug_log)

        # Bestehenden Eintrag laden: liefert Country-Fallback und die stabile
        # supercharge_info_id. Diese MUSS erhalten bleiben, da sie ein
        # unabhaengiger interner Schluessel ist (urspruenglich aus
        # supercharge.info) und Teslas eigene trtId-Nummerierung einem
        # komplett anderen ID-Raum entstammt - ein blindes Uebernehmen
        # der trtId als supercharge_info_id kollidiert leicht mit der
        # ID einer anderen, bereits existierenden Station (UNIQUE-Constraint).
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

        db_record = self._tesla_detail_to_db_record(detail, country)
        if existing is not None:
            db_record["supercharge_info_id"] = existing["supercharge_info_id"]

        # In DB speichern
        self._db.update_station(db_record)
        self._stations = None  # Cache invalidieren

        # Zurueck in ChargingStation konvertieren
        return self._db_record_to_charging_station(db_record)

    def get_all_stations(self) -> list[ChargingStation]:
        """Liefert alle Stationen aus der lokalen DB.

        Returns:
            Liste aller ChargingStation-Eintraege.
        """
        if self._stations is None:
            self._stations = self._load_stations_from_db()
        return list(self._stations)

    @staticmethod
    def _db_record_to_charging_station(
        record: dict[str, Any],
    ) -> ChargingStation:
        """Wandelt ein DB-Record-Dict in ein ChargingStation-Modell um.

        Args:
            record: DB-Record-Dict aus load_stations()

        Returns:
            ChargingStation-Modell
        """
        power = max(record.get("power_kilowatt", 250), 1)
        stalls_v3 = record.get("stalls_v3", 0)

        _V3_MAX = TeslaChargingStationProvider._POWER_V3_MAX
        _V3_ULTRA_MAX = TeslaChargingStationProvider._POWER_V3_ULTRA_MAX
        stalls: dict[StallType, int] = {
            StallType.V2: record.get("stalls_v2", 0),
            StallType.V3: stalls_v3 if power <= _V3_MAX else 0,
            StallType.V3_ULTRA: stalls_v3 if _V3_MAX < power <= _V3_ULTRA_MAX else 0,
            StallType.V4: record.get("stalls_v4", 0),
        }

        # Parse connector types from JSON
        connector_raw: str = record.get("connector_types", "[]")
        try:
            plug_names: list[str] = json.loads(connector_raw)
        except (json.JSONDecodeError, TypeError):
            plug_names = ["ccs2"]

        connector_map: dict[str, ConnectorType] = {
            "nacs": ConnectorType.NACS,
            "ccs1": ConnectorType.CCS1,
            "ccs2": ConnectorType.CCS2,
            "type2": ConnectorType.TYPE2,
            "gbt": ConnectorType.GB_T,
            "chademo": ConnectorType.CHADEMO,
            "tpc": ConnectorType.TESLA,
        }
        connector_types = [connector_map.get(p, ConnectorType.CCS2) for p in plug_names]

        # Status-Mapping (supercharge.info -> ChargingStation)
        status_map: dict[str, str] = {
            "OPEN": "online",
            "CONSTRUCTION": "wartung",
            "PERMIT": "online",
            "TEMP_CLOSED": "temporaer_geschlossen",
            "PLAN": "online",
        }
        status: str = status_map.get(record.get("status", "OPEN"), "online")

        total_stalls = max(record.get("total_stalls", 0), 1)
        country_code: str = record.get("country_code", "XX")

        # max_ladeleistung_kw is capped at 5000 to match ChargingStation validation
        max_power: float = min(float(total_stalls * power), 5000.0)

        return ChargingStation(
            station_id=record.get("tesla_location_id") or str(record["supercharge_info_id"]),
            name=f"Tesla Supercharger - {record['site_name']}",
            coordinate=(record["latitude"], record["longitude"]),
            stalls=stalls,
            max_ladeleistung_kw=max_power,
            connector_types=connector_types,
            country=country_code,
            ist_24_7=bool(record.get("ist_24_7", 1)),
            status=status,
            letzte_datenAktualisierung=datetime.now(UTC),
        )
