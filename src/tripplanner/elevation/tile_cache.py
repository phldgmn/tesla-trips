"""Tile cache and LRU dataset management for Copernicus DEM.

Extracted from ``CopernicusDEMDataSource`` to decouple tile caching,
LRU dataset management, and per-tile locking from the HTTP bulk-read
and elevation-probing logic in ``providers.py``.
"""

import asyncio
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

import rasterio
from rasterio.errors import RasterioError

logger = logging.getLogger(__name__)


class TileCache:
    """Tile cache, LRU dataset management, and per-tile locking for Copernicus DEM.

    Responsibilities:

    * Scan and maintain a disk cache of downloaded GeoTIFF tiles.
    * Build GDAL-readable URIs (``/vsicurl/`` or local paths).
    * Per-tile locking that serialises all read access to one tile's
      dataset and guards eviction so a dataset is never closed out from
      under an in-flight ``read()``.
    * LRU-cached open ``DatasetReader`` objects with bounded count.
    * Fire-and-forget background writes of freshly-fetched tiles to
      local disk.

    This class is internal to the elevation subsystem and is not
    exported.
    """

    def __init__(
        self,
        *,
        max_open_tiles: int,
        cache_dir: Path | None,
        base_url: str,
        remote: bool,
    ) -> None:
        """Initialise the tile cache.

        Args:
            max_open_tiles: Maximum number of open rasterio datasets
                in the LRU cache.
            cache_dir: Optional path to a local disk cache directory
                for downloaded DEM tiles.
            base_url: Base URL or local path prefix for tile URIs.
            remote: Whether the base URL points to a remote location
                (triggers GDAL /vsicurl tuning).
        """
        self._max_open_tiles = max_open_tiles
        self._datasets: OrderedDict[str, rasterio.io.DatasetReader | None] = OrderedDict()
        self._lock = threading.Lock()
        self._tile_locks: dict[str, threading.Lock] = {}
        self._gdal_semaphore = asyncio.Semaphore(8)
        self._cache_dir: Path | None = None
        self._cache: dict[str, str] = {}
        self._base_url = base_url.rstrip("/")
        self._remote = remote

        if self._remote:
            os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
            os.environ.setdefault("CPL_VSIL_CURL_USE_HEAD", "NO")
            os.environ.setdefault("VSI_CACHE", "TRUE")
            os.environ.setdefault("VSI_CACHE_SIZE", "67108864")  # 64 MB
            os.environ.setdefault("GDAL_HTTP_VERSION", "2")
            os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

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
        """Calculate the Copernicus DEM tile name for the 1x1 degree cell of a coordinate."""
        from tripplanner.elevation.providers import copernicus_tile_name

        return copernicus_tile_name(lat, lon)

    def _tile_uri(self, lat: float, lon: float) -> str:
        """Build the GDAL-readable URI (vsicurl or local path) for a tile.

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
        except (RasterioError, OSError):
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
            except (RasterioError, OSError):
                logger.warning("DEM tile %s could not be opened - fallback to 0.0m", uri)
                dataset = None

            self._datasets[uri] = dataset
            self._evict_over_cap(skip_uri=uri)
            return dataset
