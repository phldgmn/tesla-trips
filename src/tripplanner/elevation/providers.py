"""Provider-Protocol und Implementierungen für DEM-Datenquellen.

Das DEMDataSourceProtocol ermöglicht testbare Höhen-Datenquellen ohne
feste Abhängigkeit von rasterio.
"""

import struct
from typing import Protocol, cast

from typing_extensions import runtime_checkable

from tripplanner.elevation.models import DEMTile, DEMTileKey


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

    def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
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

    def get_elevations_batch(self, coordinates: list[tuple[float, float]]) -> list[float]:
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
