"""provider-Protocol und implementationen für DEM-dataquellen.

Das DEMDataSourceProtocol ermöglicht testbare heightn-dataquellen ohne
feste Abhaengigkeit von rasterio.
"""

import asyncio
import logging
import math
import struct
from pathlib import Path
from typing import Protocol, cast

import rasterio
from rasterio.errors import RasterioError
from rasterio.windows import Window
from typing_extensions import runtime_checkable

from tripplanner.elevation.models import DEMTile, DEMTileKey
from tripplanner.elevation.tile_cache import TileCache

logger = logging.getLogger(__name__)


@runtime_checkable
class DEMDataSourceProtocol(Protocol):
    """Protocol für DEM-dataquellen (für Testbarkeit).

    implementationen können echte GeoTIFF-Dateien einlesen oder
    synthetische data für Tests bereitstellen.
    """

    def get_elevation(self, lat: float, lon: float) -> float:
        """Heightnwert an einer Koordinate abfragen.

        Args:
            lat: latitude (WGS84)
            lon: Laengengrad (WGS84)

        Returns:
            heightnwert in Metern oder -9999 (nodata/unbelegt)
        """
        ...

    async def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        """Heightnwerte für mehrere Koordinaten (optimiert für Batch-Lookup).

        Args:
            coordinates: Liste von (latitude, longitude) Tupeln

        Returns:
            Liste von heightnwerten in Metern
        """
        ...

    def get_tile_at(self, lat: float, lon: float) -> DEMTile | None:
        """Ermittle die DEM-Kachel für eine Koordinate.

        Args:
            lat: latitude
            lon: Laengengrad

        Returns:
            DEMTile oder None falls keine Kachel existiert
        """
        ...

    def get_tiles_in_bbox(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> list[DEMTile]:
        """Ermittle alle DEM-Kacheln die eine BBox schneiden.

        Args:
            min_lat: Minimale latitude
            max_lat: Maximale latitude
            min_lon: Minimale Laenge
            max_lon: Maximale Laenge

        Returns:
            Liste von DEMTiles
        """
        ...


class FakeDataSource:
    """Synthetische DEM-data für Unit-Tests (kein echtes File I/O).

    Generiert deterministische heightnwerte based auf den Koordinaten
    mittels Hash-Funktion. So sind Tests reproduzierbar ohne externe
    Abhaengigkeiten.
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
        """Heightnwert an einer Koordinate abfragen.

        Deterministically based on coordinates (not random!).

        Args:
            lat: latitude (WGS84)
            lon: Laengengrad (WGS84)

        Returns:
            heightnwert im Bereich [baseline - noise_range/2, baseline + noise_range/2]
        """
        hash_val = hash((round(lat, 5), round(lon, 5))) % 1000
        noise = (hash_val / 1000.0 - 0.5) * self.noise_range
        return self.baseline + noise

    async def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
        """Heightnwerte für mehrere Koordinaten (optimiert für Batch-Lookup).

        Args:
            coordinates: Liste von (latitude, longitude) Tupeln

        Returns:
            Liste von heightnwerten in Metern
        """
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]

    def get_tile_at(self, lat: float, lon: float) -> DEMTile | None:
        """Ermittle die DEM-Kachel für eine Koordinate.

        FakeDataSource gibt eine synthetische Kachel zurück.

        Args:
            lat: latitude
            lon: Laengengrad

        Returns:
            DEMTile mit synthetischen data oder None
        """
        tile_key = DEMTileKey(
            min_lat=round(lat, 5) - 0.00005,
            max_lat=round(lat, 5) + 0.00005,
            min_lon=round(lon, 5) - 0.00005,
            max_lon=round(lon, 5) + 0.00005,
        )
        # 5x5 Pixel Raster mit linearen heightn
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
        # Linearer gradient: 100m bis 120m
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
            min_lat: Minimale latitude
            max_lat: Maximale latitude
            min_lon: Minimale Laenge
            max_lon: Maximale Laenge

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
                ``/vsicurl/``-file system gelesen (Range-Requests, kein
                Download). Jeder andere Wert wird als lokaler Basispfad
                behandelt (für Tests gegen eine lokale Test-Kachel). Default:
                der öffentliche ``copernicus-dem-30m``-Bucket.
            max_open_tiles: Maximale Anzahl offener rasterio-Datasets im
                LRU-Cache (begrenzt Speicher-/Dateihandle-consumption bei
                langlaufenden Prozessen, die viele Routen bedienen).
            cache_dir: Optional path to a local disk cache directory for
                downloaded DEM tiles.  When set and a tile exists locally,
                the local file is opened directly (no ``/vsicurl/``).  On
                a cache miss the tile is written to disk after the first
                fetch so subsequent calls (same or new process) hit disk
                instead of the network.
        """
        if base_url is None:
            base_url = self._BUCKET_BASE
        base_url = base_url.rstrip("/")
        remote = base_url.startswith(("http://", "https://"))
        self._tile_cache = TileCache(
            max_open_tiles=max_open_tiles,
            cache_dir=cache_dir,
            base_url=base_url,
            remote=remote,
        )

    # ── Facade delegators for tests that inspect private state of
    #    the original CopernicusDEMDataSource before the refactor. ──

    @property
    def _datasets(
        self,
    ) -> "dict[str, rasterio.io.DatasetReader | None]":
        """Internal LRU dataset cache (for test access)."""
        return self._tile_cache._datasets

    @_datasets.setter
    def _datasets(self, value: "dict[str, rasterio.io.DatasetReader | None]") -> None:
        self._tile_cache._datasets.clear()
        self._tile_cache._datasets.update(value)

    @property
    def _max_open_tiles(self) -> int:
        """Internal LRU cap (for test access)."""
        return self._tile_cache._max_open_tiles

    @_max_open_tiles.setter
    def _max_open_tiles(self, value: int) -> None:
        self._tile_cache._max_open_tiles = value

    def _tile_name(self, lat: float, lon: float) -> str:
        """Delegate to internal TileCache for test access."""
        return self._tile_cache._tile_name(lat, lon)

    def _tile_uri(self, lat: float, lon: float) -> str:
        """Delegate to internal TileCache for test access."""
        return self._tile_cache._tile_uri(lat, lon)

    def _schedule_cache_write(
        self,
        uri: str,
        dataset: object,
        band: object,
        lat: float,
        lon: float,
    ) -> None:
        """Delegate to internal TileCache for test access."""
        self._tile_cache._schedule_cache_write(uri, dataset, band, lat, lon)

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
        with self._tile_cache._get_tile_lock(uri):
            dataset = self._tile_cache._dataset_for_tile(uri)
            if dataset is None:
                return results
            nodata = dataset.nodata

            # ── Single bulk read of the whole band (Fix 1) ────────────
            try:
                band = dataset.read(1)
            # ValueError: dataset closed by a concurrent LRU eviction.
            except (RasterioError, OSError, ValueError):
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
                except (IndexError, ValueError, RasterioError):
                    # Out-of-bounds index, etc. → leave 0.0.
                    pass

            # ── Fix 3: Schedule disk-cache write from the in-memory band ──
            # (fire-and-forget background thread, never blocks the response)
            self._tile_cache._schedule_cache_write(uri, dataset, band, points[0][1], points[0][2])

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
        uri = self._tile_cache._tile_uri(lat, lon)
        with self._tile_cache._get_tile_lock(uri):
            dataset = self._tile_cache._dataset_for_tile(uri)
            if dataset is None:
                return 0.0
            try:
                row, col = dataset.index(lon, lat)
                if not (0 <= row < dataset.height and 0 <= col < dataset.width):
                    return 0.0
                value = dataset.read(1, window=Window(col, row, 1, 1))[0, 0]
            except (RasterioError, OSError, IndexError, ValueError):
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
            uri = self._tile_cache._tile_uri(lat, lon)
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
        self._tile_cache._max_open_tiles = max(self._tile_cache._max_open_tiles, len(tile_coords))

        # 2. Read each distinct tile concurrently (rasterio/GDAL is
        # blocking). Bounded by `_gdal_semaphore`: GDAL/PROJ thread-safety
        # under very high fan-out is not fully guaranteed, so concurrency is
        # capped rather than unbounded across however many tiles a route
        # touches. `_read_tile_bulk` does the open-or-reuse AND the read
        # under one per-tile lock (see its docstring) - a single phase, not
        # two, so a tile is never opened by one call and read by another.
        async def _read_one(uri: str, points: list[tuple[int, float, float]]) -> list[float]:
            async with self._tile_cache._gdal_semaphore:
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
        uri = self._tile_cache._tile_uri(lat, lon)
        with self._tile_cache._get_tile_lock(uri):
            dataset = self._tile_cache._dataset_for_tile(uri)
            if dataset is None:
                return None
            try:
                data = dataset.read(1)
            except (RasterioError, OSError, ValueError):
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
            min_lat: Minimale latitude
            max_lat: Maximale latitude
            min_lon: Minimale Laenge
            max_lon: Maximale Laenge

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
