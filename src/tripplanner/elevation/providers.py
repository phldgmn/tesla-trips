"""Provider-Protocol und Implementierungen für DEM-Datenquellen.

Das DEMDataSourceProtocol ermöglicht testbare Höhen-Datenquellen ohne
feste Abhängigkeit von rasterio.
"""

import asyncio
import logging
import math
import struct
from collections import OrderedDict
from typing import Protocol, cast

import rasterio
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

    def __init__(self, *, base_url: str | None = None, max_open_tiles: int = 16) -> None:
        """Initialisiere CopernicusDEMDataSource.

        Args:
            base_url: Override für die Bucket-Basis-URL. Werte, die mit
                `http://`/`https://` beginnen, werden über GDALs
                `/vsicurl/`-Dateisystem gelesen (Range-Requests, kein
                Download). Jeder andere Wert wird als lokaler Basispfad
                behandelt (für Tests gegen eine lokale Test-Kachel). Default:
                der öffentliche `copernicus-dem-30m`-Bucket.
            max_open_tiles: Maximale Anzahl offener rasterio-Datasets im
                LRU-Cache (begrenzt Speicher-/Dateihandle-Verbrauch bei
                langlaufenden Prozessen, die viele Routen bedienen).
        """
        self._base_url = (base_url or self._BUCKET_BASE).rstrip("/")
        self._remote = self._base_url.startswith(("http://", "https://"))
        self._max_open_tiles = max_open_tiles
        self._datasets: OrderedDict[str, rasterio.io.DatasetReader | None] = OrderedDict()

    def _tile_name(self, lat: float, lon: float) -> str:
        """Berechne den Copernicus-DEM-Kachelnamen für die 1x1-Grad-Zelle einer Koordinate."""
        lat_band = math.floor(lat)
        lon_band = math.floor(lon)
        ns = "N" if lat_band >= 0 else "S"
        ew = "E" if lon_band >= 0 else "W"
        return f"Copernicus_DSM_COG_10_{ns}{abs(lat_band):02d}_00_{ew}{abs(lon_band):03d}_00_DEM"

    def _tile_uri(self, lat: float, lon: float) -> str:
        """Baue die GDAL-lesbare URI (vsicurl oder lokaler Pfad) für eine Kachel."""
        name = self._tile_name(lat, lon)
        path = f"{self._base_url}/{name}/{name}.tif"
        return f"/vsicurl/{path}" if self._remote else path

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

    async def get_elevations_batch(  # noqa: PLR0912
        self, coordinates: list[tuple[float, float]]
    ) -> list[float]:
        """Höhenwerte für mehrere Koordinaten abfragen (optimiert für Batch-Lookup).

        Gruppiert Koordinaten nach 1°-DEM-Kachel, öffnet die benötigten Kacheln
        concurrent via ``asyncio.to_thread`` (GDAL/rasterio ist blockierend) und
        liest die Pixelwerte aus den gecachten Datasets.

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

        # 3. Read pixel values and reconstruct in original order
        results: list[float] = [0.0] * len(coordinates)
        for uri, dataset in zip(tile_coords, datasets, strict=True):
            if dataset is None:
                continue
            for idx, lat, lon in tile_coords[uri]:
                try:
                    row, col = dataset.index(lon, lat)
                    if not (0 <= row < dataset.height and 0 <= col < dataset.width):
                        continue
                    value = dataset.read(1, window=Window(col, row, 1, 1))[0, 0]
                    value_f = float(value)
                    if dataset.nodata is not None and value_f == dataset.nodata:
                        continue
                    results[idx] = value_f
                except Exception:
                    # Dataset may have been evicted (closed) during the
                    # concurrent open phase (step 2).  Re-open directly
                    # (bypassing the LRU cache) to avoid eviction cascades,
                    # matching the old sequential path's self-healing
                    # behaviour.  Only fall back to 0.0m when the re-read
                    # also fails.
                    if dataset is not None and getattr(dataset, "closed", False):
                        try:
                            re = await asyncio.to_thread(rasterio.open, uri)
                            try:
                                row, col = re.index(lon, lat)
                                if 0 <= row < re.height and 0 <= col < re.width:
                                    value = re.read(1, window=Window(col, row, 1, 1))[0, 0]
                                    value_f = float(value)
                                    if re.nodata is not None and value_f == re.nodata:
                                        pass  # fall through to 0.0
                                    else:
                                        results[idx] = value_f
                                        continue
                            finally:
                                re.close()
                        except Exception:
                            pass  # fall through to warning
                    logger.warning(
                        "DEM-Range-Request für (%s, %s) fehlgeschlagen - Fallback 0.0m",
                        lat,
                        lon,
                    )
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
