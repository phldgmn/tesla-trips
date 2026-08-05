"""Script zur Erstellung eines synthetischen GeoTIFF-Test-Tiles."""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds


def create_synthetic_dem_tile(output_path: Path) -> None:
    """Erstelle ein 5x5 Pixel GeoTIFF mit linearer Höhensteigung.

    Args:
        output_path: Pfad zur Output-Datei
    """
    # 5x5 Pixel Raster
    width = 5
    height = 5

    # BBox: ca. 11m x 11m (30m Auflösung = 150m Breite, aber wir nutzen kleinere Auflösung)
    # Für 5 Pixel mit 30m Auflösung: 5 * 30 = 150m
    # Koordinaten: 47.0°N, 8.0°E bis 47.00015°N, 8.00015°E
    # Das entspricht ca. 11m x 11m (1° Breite ~ 111km)

    # Nutze EPSG:4326 WGS84
    crs = CRS.from_epsg(4326)

    # Bounds in WGS84 Koordinaten
    min_lon = 8.0
    max_lon = 8.00015
    min_lat = 47.0
    max_lat = 47.00015

    # Transform matrix für pixel->world
    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    # Erstelle linearen Gradient: 100m bis 120m
    # Linke obere Ecke = 100m, rechte untere Ecke = 120m
    raster_data = np.zeros((height, width), dtype=np.float32)
    for row in range(height):
        for col in range(width):
            # Normalisierter Wert von 0.0 bis 1.0
            value = 100.0 + (row * width + col) / (width * height - 1) * 20.0
            raster_data[row, col] = value

    # Schreibe GeoTIFF
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=raster_data.dtype,
        crs=crs,
        transform=transform,
        nodata=-9999.0,
        compress="lzw",
    ) as dst:
        dst.write(raster_data, 1)

    print(f"Created {output_path}")
    print(f"  CRS: {crs}")
    print(f"  Size: {width}x{height} pixels")
    print(f"  Bounds: ({min_lon}, {min_lat}) -> ({max_lon}, {max_lat})")
    print(f"  Elevation range: {raster_data.min()}m - {raster_data.max()}m")


if __name__ == "__main__":
    fixtures_dir = Path(__file__).parent / "tests" / "fixtures" / "elevation"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    output_path = fixtures_dir / "copernicus_dem_test_tile.tif"
    create_synthetic_dem_tile(output_path)
