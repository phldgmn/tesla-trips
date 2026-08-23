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

    def _dataset_for(self, lat: float, lon: float) -> rasterio.io.DatasetReader | None:
        """Liefere ein offenes Dataset für die Kachel an (lat, lon), oder None.

        Datasets werden in einem LRU von maximal `max_open_tiles` Einträgen
        gecacht (auch fehlgeschlagene Lookups, als None gecacht, damit nicht
        für jeden Punkt derselben fehlenden Kachel erneut ein Request
        versucht wird).
        """
        uri = self._tile_uri(lat, lon)
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
        if len(self._datasets) > self._max_open_tiles:
            _, evicted = self._datasets.popitem(last=False)
            if evicted is not None:
                evicted.close()
        return dataset

    def _dataset_for_tile(self, uri: str) -> rasterio.io.DatasetReader | None:
        """Open or return cached dataset for a tile URI (thread-safe for batch).

        Same LRU caching logic as `_dataset_for` but takes a URI directly
        to avoid constructing one in the thread per call.

        Args:
            uri: Tile URI to open or look up in the LRU cache.

        Returns:
            An open ``DatasetReader`` or ``None`` if opening failed.
        """
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
        if len(self._datasets) > self._max_open_tiles:
            _, evicted = self._datasets.popitem(last=False)
            if evicted is not None:
                evicted.close()
        return dataset

    # ── Fix 1: private bulk-read helper ──────────────────────────────

    def _read_tile_bulk(
        self,
        uri: str,
        points: list[tuple[int, float, float]],
        nodata: float | None,
    ) -> list[float]:
        """Read elevation values for *points* via one bulk ``dataset.read(1)``.

        This is the heart of Fix 1.  It runs *inside* ``asyncio.to_thread``
        from ``get_elevations_batch`` and is responsible for:

        1. Opening (or reusing from the LRU) the tile dataset.
        2. Reading the **entire band** in a single ``read(1)`` call.
        3. Indexing every point with pure numpy array lookup — zero further
           I/O.
        4. **Eviction self-heal**: if the LRU dataset was evicted (closed)
           between step 1 and the bulk read, open a temporary dataset
           directly (bypassing the LRU), do one bulk ``read(1)`` on it,
           use it for all points in this tile, then ``close()`` it.
        5. Writing the tile to disk cache (Fix 3) after success.

        Args:
            uri: GDAL-readable tile URI (``/vsicurl/...`` or local path).
            points: ``[(result_index, lat, lon), ...]`` per coordinate
                that falls into this tile.
            nodata: The dataset's nodata sentinel value, or ``None``.

        Returns:
            List of elevation floats — one per entry in *points*.
            Nodata pixels are returned as ``0.0``, as are out-of-bounds
            and failed reads.
        """
        if not points:
            return []

        results: list[float] = [0.0] * len(points)
        dataset: rasterio.io.DatasetReader | None = None
        temp_dataset: rasterio.io.DatasetReader | None = None

        # ── Step A: Open or reuse from LRU ────────────────────────────
        try:
            dataset = self._dataset_for_tile(uri)
        except Exception:
            # LRU itself is broken — fall through to temp open.
            dataset = None

        if dataset is None or getattr(dataset, "closed", False):
            # ── Eviction self-heal (Fix 1): open temp, bypass LRU ────
            try:
                temp_dataset = rasterio.open(uri)
                dataset = temp_dataset
            except Exception:
                logger.warning(
                    "DEM-Tile %s konnte nicht geöffnet werden - Fallback 0.0m",
                    uri,
                )
                return results

        # ── Step B: Single bulk read of the whole band (Fix 1) ───────
        try:
            band = dataset.read(1)
        except Exception:
            logger.warning(
                "DEM-Tile %s konnte nicht gelesen werden - Fallback 0.0m",
                uri,
            )
            return results

        # ── Step C: Per-point indexing — pure numpy, zero I/O ────────
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

        # ── Close temporary dataset (not the LRU one) ────────────────
        if temp_dataset is not None and dataset is temp_dataset:
            temp_dataset.close()

        return results

    def get_elevation(self, lat: float, lon: float) -> float:
        """Höhenwert an einer Koordinate abfragen.

        Args:
            lat: Breitengrad (WGS84)
            lon: Längengrad (WGS84)

        Returns:
            Höhenwert in Metern, oder 0.0 falls keine Kachel verfügbar ist
            oder der Range-Request fehlschlägt (siehe Docstring der Klasse).
        """
        dataset = self._dataset_for(lat, lon)
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
        """Höhenwerte für mehrere Koordinaten abfragen (optimiert für Batch-Lookup).

        Gruppiert Koordinaten nach 1°-DEM-Kachel, öffnet die benötigten Kacheln
        concurrent via ``asyncio.to_thread`` (GDAL/rasterio ist blockierend),
        liest pro Kachel **genau einen** Bulk-``read(1)`` und indexiert
        danach alle Pixelwerte mit reinem Numpy-Zugriff — null weitere I/O.

        Fix 1 — der alte Pfad machte ``dataset.read(window=Window(...))``
        für *jeden* einzelnen Punkt (118 s).  Jetzt macht jede Kachel
        genau **einen** Bulk-``read(1)`` (ca. 3600x3600 int16-Pixel),
        gestartet parallel über ``asyncio.gather``.

        Args:
            coordinates: Liste von (lat, lon)-Tupeln

        Returns:
            Liste von Höhenwerten in derselben Reihenfolge wie `coordinates`
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

        # 2. Open each distinct tile concurrently (rasterio/GDAL is blocking)
        async def _load_dataset(uri: str) -> rasterio.io.DatasetReader | None:
            return await asyncio.to_thread(self._dataset_for_tile, uri)

        datasets = await asyncio.gather(
            *[_load_dataset(uri) for uri in tile_coords],
        )

        # 3. For each tile, do ONE bulk read inside a worker thread,
        #    all tiles' bulk reads running concurrently via asyncio.gather.
        async def _read_tile_and_collect(
            uri: str,
            points: list[tuple[int, float, float]],
            dataset: rasterio.io.DatasetReader | None,
        ) -> list[float]:
            """Run bulk read for one tile and return per-point results."""
            nodata = dataset.nodata if dataset is not None else None
            return await asyncio.to_thread(self._read_tile_bulk, uri, points, nodata)

        gathered = await asyncio.gather(
            *[
                _read_tile_and_collect(uri, pts, ds)
                for (uri, pts), ds in zip(tile_coords.items(), datasets, strict=True)
            ],
        )

        # Scatter per-tile result lists back into flat coordinate order.
        results: list[float] = [0.0] * len(coordinates)
        for tile_results, pts in zip(gathered, tile_coords.values(), strict=True):
            for local_idx, (global_idx, _lat, _lon) in enumerate(pts):
                results[global_idx] = tile_results[local_idx]

        return results

    def get_tile_at(self, lat: float, lon: float) -> DEMTile | None:
        """Ermittle die DEM-Kachel für eine Koordinate.

        Args:
            lat: Breitengrad
            lon: Längengrad

        Returns:
            DEMTile mit den echten Rasterdaten, oder None falls keine Kachel
            verfügbar ist oder das Lesen fehlschlägt.
        """
        dataset = self._dataset_for(lat, lon)
        if dataset is None:
            return None
        try:
            data = dataset.read(1)
        except Exception:
            logger.warning("DEM-Kachel-Raster für (%s, %s) konnte nicht gelesen werden", lat, lon)
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
