"""Provider-Protocol und Implementierungen für DEM-Datenquellen.

Das DEMDataSourceProtocol ermöglicht testbare Höhen-Datenquellen ohne
feste Abhängigkeit von rasterio.
"""

import asyncio
import logging
import math
import os
import struct
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Protocol, cast

import rasterio
import rasterio.shutil
from rasterio.windows import Window
from typing_extensions import runtime_checkable

from tripplanner.elevation.models import DEMTile, DEMTileKey

logger = logging.getLogger(__name__)


@runtime_checkable
class DEMDataSourceProtocol(Protocol):
    """Protocol für DEM-Datenquellen (für Testbarkeit).

    Implementierungen können echte GeoTIFF-Dateien einlesen oder
    synthetische Daten für Tests bereitstellen.
    """

    def get_elevation(self, lat: float, lon: float) -> float:
        """Höhenwert an einer Koordinate abfragen.

        Args:
            lat: Breitengrad (WGS84)
            lon: Längengrad (WGS84)

        Returns:
            Höhenwert in Metern oder -9999 (nodata/unbelegt)
        """
        ...

    async def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        """Höhenwerte für mehrere Koordinaten (optimiert für Batch-Lookup).

        Args:
            coordinates: Liste von (breitengrad, laengengrad) Tupeln

        Returns:
            Liste von Höhenwerten in Metern
        """
        ...

    def get_tile_at(self, lat: float, lon: float) -> DEMTile | None:
        """Ermittle die DEM-Kachel für eine Koordinate.

        Args:
            lat: Breitengrad
            lon: Längengrad

        Returns:
            DEMTile oder None falls keine Kachel existiert
        """
        ...

    def get_tiles_in_bbox(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> list[DEMTile]:
        """Ermittle alle DEM-Kacheln die eine BBox schneiden.

        Args:
            min_lat: Minimale Breite
            max_lat: Maximale Breite
            min_lon: Minimale Länge
            max_lon: Maximale Länge

        Returns:
            Liste von DEMTiles
        """
        ...


class FakeDataSource:
    """Synthetische DEM-Daten für Unit-Tests (kein echtes File I/O).

    Generiert deterministische Höhenwerte basierend auf den Koordinaten
    mittels Hash-Funktion. So sind Tests reproduzierbar ohne externe
    Abhängigkeiten.
    """

    def __init__(self, baseline_elevation: float = 100.0, noise_range: float = 5.0) -> None:
        """Initialisiere FakeDataSource.

        Args:
            baseline_elevation: Basishöhe in Metern
            noise_range: Maximale Abweichung von der Basishöhe (±noise_range/2)
        """
        self.baseline = baseline_elevation
        self.noise_range = noise_range

    def get_elevation(self, lat: float, lon: float) -> float:
        """Höhenwert an einer Koordinate abfragen.

        Deterministisch basierend auf Koordinaten (nicht zufällig!).

        Args:
            lat: Breitengrad (WGS84)
            lon: Längengrad (WGS84)

        Returns:
            Höhenwert im Bereich [baseline - noise_range/2, baseline + noise_range/2]
        """
        hash_val = hash((round(lat, 5), round(lon, 5))) % 1000
        noise = (hash_val / 1000.0 - 0.5) * self.noise_range
        return self.baseline + noise

    async def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        """Höhenwerte für mehrere Koordinaten (optimiert für Batch-Lookup).

        Args:
            coordinates: Liste von (breitengrad, laengengrad) Tupeln

        Returns:
            Liste von Höhenwerten in Metern
        """
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]

    def get_tile_at(self, lat: float, lon: float) -> DEMTile | None:
        """Ermittle die DEM-Kachel für eine Koordinate.

        FakeDataSource gibt eine synthetische Kachel zurück.

        Args:
            lat: Breitengrad
            lon: Längengrad

        Returns:
            DEMTile mit synthetischen Daten oder None
        """
        tile_key = DEMTileKey(
            min_lat=round(lat, 5) - 0.00005,
            max_lat=round(lat, 5) + 0.00005,
            min_lon=round(lon, 5) - 0.00005,
            max_lon=round(lon, 5) + 0.00005,
        )
        # 5x5 Pixel Raster mit linearen Höhen
        transform = [
            round(lon, 5) - 0.00005,  # a - x-origin
            0.00002,  # b - x-pixel-size
            0.0,  # c - x-rotation
            round(lat, 5) + 0.00005,  # d - y-origin
            0.0,  # e - y-rotation
            -0.00002,  # f - y-pixel-size
        ]
        width = 5
        height = 5
        # Erstelle synthetic raster data (bytes)
        # Linearer Gradient: 100m bis 120m
        raster_data = b""
        for row in range(height):
            for col in range(width):
                value = 100.0 + (row * width + col) / (width * height - 1) * 20.0
                raster_data += struct.pack("<f", value)

        return DEMTile(
            key=tile_key,
            raster_data=raster_data,
            transform=transform,
            width=width,
            height=height,
            nodata_value=-9999.0,
        )

    def get_tiles_in_bbox(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> list[DEMTile]:
        """Ermittle alle DEM-Kacheln die eine BBox schneiden.

        FakeDataSource gibt eine synthetische Kachel zurück.

        Args:
            min_lat: Minimale Breite
            max_lat: Maximale Breite
            min_lon: Minimale Länge
            max_lon: Maximale Länge

        Returns:
            Liste von DEMTiles (hier immer genau eine synthetische Kachel)
        """
        tile = self.get_tile_at((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
        return [cast(DEMTile, tile)]


def copernicus_tile_name(lat: float, lon: float) -> str:
    """Compute the Copernicus GLO-30 DEM tile name for a coordinate's 1x1-degree cell.

    Shared between `CopernicusDEMDataSource` (runtime lookups) and
    `scripts/fetch_dem_tiles.py` (bulk pre-download) so the naming scheme
    can never drift between the two.

    Args:
        lat: Latitude (WGS84).
        lon: Longitude (WGS84).

    Returns:
        Tile name, e.g. ``Copernicus_DSM_COG_10_N52_00_E013_00_DEM``.
    """
    lat_band = math.floor(lat)
    lon_band = math.floor(lon)
    ns = "N" if lat_band >= 0 else "S"
    ew = "E" if lon_band >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_band):02d}_00_{ew}{abs(lon_band):03d}_00_DEM"


class CopernicusDEMDataSource:
    """Reads Copernicus DEM GLO-30 tiles from the public AWS Open Data bucket.

    Uses GDAL's `/vsicurl/` virtual filesystem (HTTP range requests via
    rasterio) against `copernicus-dem-30m.s3.amazonaws.com` - no AWS
    credentials and no local bulk download needed; only the byte ranges of
    the tile windows actually queried are fetched.

    The tile naming scheme was verified against the live bucket listing
    (not guessed) for tiles covering Berlin, Copenhagen, and Stockholm, e.g.:
    `Copernicus_DSM_COG_10_N52_00_E013_00_DEM/Copernicus_DSM_COG_10_N52_00_E013_00_DEM.tif`.
    Note that the `_10_` segment is Copernicus' internal product-family code,
    not a resolution marker - it is the correct tile name for the GLO-30
    (30m) product hosted in this particular bucket.

    Coordinates with no covering tile (e.g. open sea - ocean areas have no
    tiles at all, see docs/plans/02-elevation.md) or where the HTTP range
    request fails (network outage) fall back to 0.0m instead of raising, so
    that ferry/sea segments never crash the pipeline.
    """

    _BUCKET_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        max_open_tiles: int = 16,
        cache_dir: Path | None = None,
    ) -> None:
        """Initialisiere CopernicusDEMDataSource.

        Args:
            base_url: Override für die Bucket-Basis-URL. Werte, die mit
                ``http://``/``https://`` beginnen, werden über GDALs
                ``/vsicurl/``-Dateisystem gelesen (Range-Requests, kein
                Download). Jeder andere Wert wird als lokaler Basispfad
                behandelt (für Tests gegen eine lokale Test-Kachel). Default:
                der öffentliche ``copernicus-dem-30m``-Bucket.
            max_open_tiles: Maximale Anzahl offener rasterio-Datasets im
                LRU-Cache (begrenzt Speicher-/Dateihandle-Verbrauch bei
                langlaufenden Prozessen, die viele Routen bedienen).
            cache_dir: Optional path to a local disk cache directory for
                downloaded DEM tiles.  When set and a tile exists locally,
                the local file is opened directly (no ``/vsicurl/``).  On
                a cache miss the tile is written to disk after the first
                fetch so subsequent calls (same or new process) hit disk
                instead of the network.
        """
        # ── Fix 2: GDAL / vsicurl tuning ──────────────────────────────
        # Set once at construction so every GDAL worker thread picks them
        # up.  Using os.environ.setdefault lets tests / callers override.
        if base_url is None:
            base_url = self._BUCKET_BASE
        self._base_url = base_url.rstrip("/")
        self._remote = self._base_url.startswith(("http://", "https://"))
        if self._remote:
            os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
            os.environ.setdefault("CPL_VSIL_CURL_USE_HEAD", "NO")
            os.environ.setdefault("VSI_CACHE", "TRUE")
            os.environ.setdefault("VSI_CACHE_SIZE", "67108864")  # 64 MB
            os.environ.setdefault("GDAL_HTTP_VERSION", "2")
            os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

        self._max_open_tiles = max_open_tiles
        self._datasets: OrderedDict[str, rasterio.io.DatasetReader | None] = OrderedDict()
        # Guards all `_datasets` mutations (pop/insert/evict). Never closes a
        # dataset unconditionally - see `_get_tile_lock`/`_evict_over_cap` for
        # why eviction is lock-guarded instead.
        self._lock = threading.Lock()
        # Per-tile locks serializing ALL access (open-or-reuse, read, index)
        # to one tile's dataset - see `_get_tile_lock`.
        self._tile_locks: dict[str, threading.Lock] = {}
        # Bounds how many GDAL calls (open/read) run concurrently across
        # worker threads. GDAL/PROJ thread-safety under very high fan-out
        # is not fully guaranteed; this trades a little theoretical
        # concurrency for a version-independent safety margin. Local-disk
        # tile reads are fast enough that this is still seconds, not
        # minutes, for a realistic route.
        self._gdal_semaphore = asyncio.Semaphore(8)

        # ── Fix 3: local-disk tile cache ──────────────────────────────
        self._cache_dir: Path | None = None
        self._cache: dict[str, str] = {}  # tile_name → local_path
        if cache_dir is not None:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache = self._find_cached_tiles(self._cache_dir)

    def _find_cached_tiles(self, cache_dir: Path) -> dict[str, str]:
        """Scan *cache_dir* for existing GeoTIFF tiles (called once).

        Args:
            cache_dir: Local cache directory to scan.

        Returns:
            Mapping from Copernicus tile name to local file path.
        """
        try:
            return {p.stem: str(p) for p in cache_dir.rglob("*.tif") if p.is_file()}
        except OSError:
            return {}

    def _tile_name(self, lat: float, lon: float) -> str:
        """Berechne den Copernicus-DEM-Kachelnamen für die 1x1-Grad-Zelle einer Koordinate."""
        return copernicus_tile_name(lat, lon)

    def _tile_uri(self, lat: float, lon: float) -> str:
        """Baue die GDAL-lesbare URI (vsicurl oder lokaler Pfad) für eine Kachel.

        Fix 3 — when *cache_dir* is set and a local copy exists, returns the
        local path directly so no ``/vsicurl/`` network call is made.
        """
        name = self._tile_name(lat, lon)
        # Fix 3 — local cache hit → open local file directly
        if self._cache_dir is not None and name in self._cache:
            return self._cache[name]
        # Remote vsicurl or local base
        path = f"{self._base_url}/{name}/{name}.tif"
        return f"/vsicurl/{path}" if self._remote else path

    def _schedule_cache_write(
        self,
        uri: str,
        dataset: rasterio.io.DatasetReader,
        band: object,
        lat: float,
        lon: float,
    ) -> None:
        """Fire-and-forget a disk-cache write for a freshly-fetched tile.

        Writes directly from the **already-fetched** in-memory band array
        instead of re-reading the source dataset (avoids a second, slow
        GDAL translate/copy pass over the network — this was the cause of
        the elevation step regressing to multiple minutes once the
        previously-broken `rasterio.shutil` import was fixed: every tile
        paid for a full extra decode+recompress+re-fetch on top of the
        bulk read that had already happened).

        Runs the actual write in a **daemon background thread**, not
        awaited and not on the request's critical path, so a slow or
        failing disk write can never add latency to route calculation.
        Cheap metadata (profile dict, tile name, target path) is captured
        synchronously here — before the thread starts — so the background
        write never touches `dataset` (which may close once the caller
        returns) and never races on it.

        Args:
            uri: GDAL-readable tile URI.
            dataset: The already-open dataset (for its raster profile only).
            band: The full band already read via `dataset.read(1)`.
            lat: Latitude for tile name resolution.
            lon: Longitude for tile name resolution.
        """
        if self._cache_dir is None:
            return
        # Only cache remote tiles (local tiles are already on disk).
        if not uri.startswith("/vsicurl/"):
            return
        name = self._tile_name(lat, lon)
        if name in self._cache:
            return  # already cached by a previous call
        local_path = self._cache_dir / f"{name}.tif"
        try:
            profile = dict(dataset.profile)
        except Exception:
            logger.warning("DEM-Tile %s: Profil konnte nicht gelesen werden", name)
            return
        threading.Thread(
            target=self._write_band_to_cache,
            args=(name, local_path, band, profile),
            daemon=True,
        ).start()

    def _write_band_to_cache(
        self, name: str, local_path: Path, band: object, profile: dict[str, object]
    ) -> None:
        """Write an in-memory band array to a local GeoTIFF (runs in a background thread).

        Failure is silently logged — it must never affect the elevation
        values already returned to the caller.

        Args:
            name: Copernicus tile name (cache key).
            local_path: Destination path under `cache_dir`.
            band: The full band array to write.
            profile: rasterio dataset profile (driver/dtype/crs/transform/...).
        """
        try:
            with rasterio.open(str(local_path), "w", **profile) as dst:
                dst.write(band, 1)
            self._cache[name] = str(local_path)
        except Exception:
            logger.warning(
                "DEM-Tile %s konnte nicht zwischengespeichert werden",
                name,
                exc_info=True,
            )

    def _get_tile_lock(self, uri: str) -> threading.Lock:
        """Per-tile lock serializing all read access to one tile's dataset.

        Held for the ENTIRE open-or-reuse + `read()` + index duration in
        `_read_tile_bulk` / `get_elevation` / `get_tile_at`. Guarantees:

        1. No two threads ever call `.read()` on the same tile's dataset
           concurrently (rasterio/GDAL `DatasetReader` objects are not
           documented thread-safe for concurrent reads).
        2. Eviction (`_evict_over_cap`) only `.close()`s a tile whose lock it
           can acquire WITHOUT blocking - i.e. no thread is currently
           reading it - so a dataset can never be closed out from under an
           in-flight `read()`.

        Before this, `get_elevations_batch` only protected a batch's OWN
        tiles from eviction (by growing `_max_open_tiles` to cover them).
        That was safe as long as elevation lookups ran as a single logical
        batch per trip (the main route's `_step_2_extract_elevation_profile`
        call). Once `precompute_detour_costs` started firing ~60 concurrent,
        overlapping `get_elevations_batch` calls (one per detour leg), a
        DIFFERENT batch's eviction could `.close()` a tile THIS batch was
        mid-`read()` on - a use-after-close that crashed the process
        (SIGSEGV/SIGABRT) on the Gummersbach->Hagfors repro route.
        """
        with self._lock:
            lock = self._tile_locks.get(uri)
            if lock is None:
                lock = threading.Lock()
                self._tile_locks[uri] = lock
            return lock

    def _evict_over_cap(self, skip_uri: str) -> None:
        """Evict LRU-oldest tiles until at/under `_max_open_tiles`.

        Called under `self._lock` (from `_dataset_for_tile`, right after
        inserting `skip_uri`). Only closes a tile whose per-tile lock it can
        acquire WITHOUT blocking - i.e. nothing is currently reading it (see
        `_get_tile_lock`). A tile with an in-flight read is left in the
        cache and simply reconsidered on the next insertion; this is safe
        (bounded by however many tiles are concurrently in flight) and is
        what prevents the close-under-read crash.
        """
        for candidate_uri in list(self._datasets):
            if len(self._datasets) <= self._max_open_tiles:
                return
            if candidate_uri == skip_uri:
                continue
            lock = self._tile_locks.get(candidate_uri)
            if lock is not None and not lock.acquire(blocking=False):
                continue  # a reader is mid-read on this tile - never evict it
            try:
                dataset = self._datasets.pop(candidate_uri, None)
                if dataset is not None:
                    dataset.close()
            finally:
                if lock is not None:
                    lock.release()

    def _dataset_for(self, lat: float, lon: float) -> rasterio.io.DatasetReader | None:
        """Return an open dataset for the tile at (lat, lon), or None.

        Datasets are cached in an LRU of at most `max_open_tiles` entries
        (failed lookups are cached as None too, so a request is not retried
        for every point in the same missing tile). Delegates to
        `_dataset_for_tile` - see there for the eviction logic. Callers MUST
        hold `self._get_tile_lock(uri)` for the duration of any subsequent
        read (see `_get_tile_lock`).
        """
        return self._dataset_for_tile(self._tile_uri(lat, lon))

    def _dataset_for_tile(self, uri: str) -> rasterio.io.DatasetReader | None:
        """Open or return cached dataset for a tile URI (thread-safe for batch).

        Callers MUST hold `self._get_tile_lock(uri)` for the duration of any
        subsequent read - this method only guards the LRU dict itself, not
        the dataset's contents (see `_get_tile_lock`).

        Args:
            uri: Tile URI to open or look up in the LRU cache.

        Returns:
            An open ``DatasetReader`` or ``None`` if opening failed.
        """
        with self._lock:
            if uri in self._datasets:
                dataset = self._datasets.pop(uri)
                self._datasets[uri] = dataset
                return dataset

            try:
                dataset = rasterio.open(uri)
            except Exception:
                logger.warning("DEM-Kachel %s konnte nicht geöffnet werden - Fallback 0.0m", uri)
                dataset = None

            self._datasets[uri] = dataset
            self._evict_over_cap(skip_uri=uri)
            return dataset

    # ── Fix 1: private bulk-read helper ──────────────────────────────

    def _read_tile_bulk(
        self,
        uri: str,
        points: list[tuple[int, float, float]],
    ) -> list[float]:
        """Read elevation values for *points* via one bulk ``dataset.read(1)``.

        Runs under `self._get_tile_lock(uri)` for its ENTIRE duration -
        open-or-reuse, the bulk read, and per-point indexing - so no other
        thread can ever be mid-`read()` on the same dataset (no concurrent-
        read race) and eviction can never close it out from under this read
        (no close-under-read race; see `_get_tile_lock`). This makes the
        prior "eviction self-heal" (reopen a bypass temp dataset if the LRU
        one got closed mid-flight) unnecessary - a `_dataset_for_tile`
        result returned while holding this lock cannot be concurrently
        closed - so that fallback path was removed.

        Args:
            uri: GDAL-readable tile URI (``/vsicurl/...`` or local path).
            points: ``[(result_index, lat, lon), ...]`` per coordinate
                that falls into this tile.

        Returns:
            List of elevation floats — one per entry in *points*.
            Nodata pixels are returned as ``0.0``, as are out-of-bounds
            and failed reads.
        """
        if not points:
            return []

        results: list[float] = [0.0] * len(points)
        with self._get_tile_lock(uri):
            dataset = self._dataset_for_tile(uri)
            if dataset is None:
                return results
            nodata = dataset.nodata

            # ── Single bulk read of the whole band (Fix 1) ────────────
            try:
                band = dataset.read(1)
            except Exception:
                logger.warning(
                    "DEM-Tile %s konnte nicht gelesen werden - Fallback 0.0m",
                    uri,
                )
                return results

            # ── Per-point indexing — pure numpy, zero further I/O ─────
            for local_idx, (_global_idx, lat, lon) in enumerate(points):
                try:
                    row, col = dataset.index(lon, lat)
                    if not (0 <= row < dataset.height and 0 <= col < dataset.width):
                        continue
                    value = band[row, col]
                    value_f = float(value)
                    if nodata is not None and value_f == nodata:
                        continue
                    results[local_idx] = value_f
                except Exception:
                    # Out-of-bounds index, etc. → leave 0.0.
                    pass

            # ── Fix 3: Schedule disk-cache write from the in-memory band ──
            # (fire-and-forget background thread, never blocks the response)
            self._schedule_cache_write(uri, dataset, band, points[0][1], points[0][2])

        return results

    def get_elevation(self, lat: float, lon: float) -> float:
        """Query the elevation value at one coordinate.

        Args:
            lat: Latitude (WGS84)
            lon: Longitude (WGS84)

        Returns:
            Elevation in meters, or 0.0 if no tile is available or the range
            request fails (see the class docstring).
        """
        uri = self._tile_uri(lat, lon)
        with self._get_tile_lock(uri):
            dataset = self._dataset_for_tile(uri)
            if dataset is None:
                return 0.0
            try:
                row, col = dataset.index(lon, lat)
                if not (0 <= row < dataset.height and 0 <= col < dataset.width):
                    return 0.0
                value = dataset.read(1, window=Window(col, row, 1, 1))[0, 0]
            except Exception:
                logger.warning(
                    "DEM-Range-Request für (%s, %s) fehlgeschlagen - Fallback 0.0m", lat, lon
                )
                return 0.0
            value_f = float(value)
            if dataset.nodata is not None and value_f == dataset.nodata:
                return 0.0
            return value_f

    async def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        """Query elevation values for multiple coordinates (optimized batch lookup).

        Groups coordinates by 1° DEM tile, reads each tile concurrently via
        ``asyncio.to_thread`` (GDAL/rasterio is blocking) - one bulk
        ``read(1)`` per tile, then indexes every pixel value with pure numpy
        array access, zero further I/O. `_read_tile_bulk` does the
        open-or-reuse AND the read under one per-tile lock (see its
        docstring), so a tile is never opened by one call and read by
        another.

        Fix 1 - the old path did ``dataset.read(window=Window(...))`` for
        *every single* point (118 s). Now each tile does exactly **one**
        bulk ``read(1)`` (roughly 3600x3600 int16 pixels), started
        concurrently via ``asyncio.gather``.

        Args:
            coordinates: List of (lat, lon) tuples

        Returns:
            List of elevation values in the same order as `coordinates`
        """
        if not coordinates:
            return []

        # 1. Group coordinates by tile URI
        tile_coords: dict[str, list[tuple[int, float, float]]] = {}
        for i, (lat, lon) in enumerate(coordinates):
            uri = self._tile_uri(lat, lon)
            tile_coords.setdefault(uri, []).append((i, lat, lon))

        if not tile_coords:
            return []

        # Grow the cap to cover this batch's own distinct tiles, so a single
        # large batch (e.g. the main route, which touches every tile it
        # needs in ONE call) doesn't evict-and-reopen its own tiles
        # mid-flight. Purely a perf nicety now, not a safety requirement:
        # eviction of ANY tile - this batch's or a concurrent batch's - is
        # always lock-guarded against an in-flight read (see
        # `_get_tile_lock`).
        self._max_open_tiles = max(self._max_open_tiles, len(tile_coords))

        # 2. Read each distinct tile concurrently (rasterio/GDAL is
        # blocking). Bounded by `_gdal_semaphore`: GDAL/PROJ thread-safety
        # under very high fan-out is not fully guaranteed, so concurrency is
        # capped rather than unbounded across however many tiles a route
        # touches. `_read_tile_bulk` does the open-or-reuse AND the read
        # under one per-tile lock (see its docstring) - a single phase, not
        # two, so a tile is never opened by one call and read by another.
        async def _read_one(uri: str, points: list[tuple[int, float, float]]) -> list[float]:
            async with self._gdal_semaphore:
                return await asyncio.to_thread(self._read_tile_bulk, uri, points)

        gathered = await asyncio.gather(
            *[_read_one(uri, pts) for uri, pts in tile_coords.items()],
        )

        # Scatter per-tile result lists back into flat coordinate order.
        results: list[float] = [0.0] * len(coordinates)
        for tile_results, pts in zip(gathered, tile_coords.values(), strict=True):
            for local_idx, (global_idx, _lat, _lon) in enumerate(pts):
                results[global_idx] = tile_results[local_idx]

        return results

    def get_tile_at(self, lat: float, lon: float) -> DEMTile | None:
        """Determine the DEM tile for a coordinate.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            DEMTile with the real raster data, or None if no tile is
            available or the read fails.
        """
        uri = self._tile_uri(lat, lon)
        with self._get_tile_lock(uri):
            dataset = self._dataset_for_tile(uri)
            if dataset is None:
                return None
            try:
                data = dataset.read(1)
            except Exception:
                logger.warning(
                    "DEM-Kachel-Raster für (%s, %s) konnte nicht gelesen werden", lat, lon
                )
                return None

            raster_data = b"".join(struct.pack("<f", float(v)) for v in data.flatten())
            transform = dataset.transform
            bounds = dataset.bounds
            transform_list = [
                transform.a,
                transform.b,
                transform.c,
                transform.d,
                transform.e,
                transform.f,
            ]
            return DEMTile(
                key=DEMTileKey(
                    min_lat=bounds.bottom,
                    max_lat=bounds.top,
                    min_lon=bounds.left,
                    max_lon=bounds.right,
                ),
                raster_data=raster_data,
                transform=transform_list,
                width=dataset.width,
                height=dataset.height,
                nodata_value=dataset.nodata if dataset.nodata is not None else -9999.0,
            )

    def get_tiles_in_bbox(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> list[DEMTile]:
        """Ermittle alle DEM-Kacheln die eine BBox schneiden.

        Args:
            min_lat: Minimale Breite
            max_lat: Maximale Breite
            min_lon: Minimale Länge
            max_lon: Maximale Länge

        Returns:
            Liste der verfügbaren DEMTiles (fehlende Kacheln werden übersprungen)
        """
        tiles: list[DEMTile] = []
        seen: set[tuple[int, int]] = set()
        lat_band = math.floor(min_lat)
        while lat_band <= math.floor(max_lat):
            lon_band = math.floor(min_lon)
            while lon_band <= math.floor(max_lon):
                key = (lat_band, lon_band)
                if key not in seen:
                    seen.add(key)
                    tile = self.get_tile_at(lat_band + 0.5, lon_band + 0.5)
                    if tile is not None:
                        tiles.append(tile)
                lon_band += 1
            lat_band += 1
        return tiles
