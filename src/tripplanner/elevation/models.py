"""Data models for the elevation module.

All Pydantic models for elevation profiles and ascent profiles.
"""

from pydantic import BaseModel, Field, field_validator

from tripplanner.geo import Coordinate

LAT_MIN = -90
LAT_MAX = 90
LON_MIN = -180
LON_MAX = 180
HEIGHT_MIN = -100
HEIGHT_MAX = 9000


class ElevationPoint(BaseModel):
    """heightnwert an einer Koordinate.

    Args:
        coordinate: latitude, Laengengrad (WGS84, Grad)
        hoehe_m: height above sea level in meters (invalid values: -9999 → nicht belegt)
    """

    coordinate: Coordinate = Field(description="latitude, lengthngrad (WGS84, Grad)")
    hoehe_m: float = Field(
        ge=HEIGHT_MIN,
        le=HEIGHT_MAX,
        description="Elevation above sea level in meters (invalid values: -9999 → unassigned)",
    )

    @field_validator("coordinate")
    @classmethod
    def validate_koordinate(cls, v: Coordinate) -> Coordinate:
        """Validiere Koordinaten-Bereiche."""
        lat, lon = v
        if not (LAT_MIN <= lat <= LAT_MAX):
            raise ValueError(f"Latitude must be between {LAT_MIN} and {LAT_MAX} degrees")
        if not (LON_MIN <= lon <= LON_MAX):
            raise ValueError(f"Lon must be between {LON_MIN} and {LON_MAX} degrees")
        return v


class SegmentGradient(BaseModel):
    """gradient/descent je segment aus heightndifferenz und horizontaler distance.

    Args:
        segment_index: Index des Routesegments (0-basiert)
        steigung_prozent: gradient in Prozent (positive = gradient,
            negativ = descent)
        hoehendifferenz_m: heightndifferenz zwischen Start- und
            Endpunkt des segments in Metern
        horizontale_distanz_m: Horizontale distance (nicht entlang der
            Route, sondern Luftlinienprojektion) in Metern
    """

    segment_index: int = Field(ge=0, description="Index des RouteSegments (0-basiert)")
    steigung_prozent: float = Field(
        description="gradient in Prozent (positive = gradient, negativ = Gefaelle)"
    )
    hoehendifferenz_m: float = Field(
        description="Elevation difference between start and end points of the segment in meters"
    )
    horizontale_distanz_m: float = Field(
        description=(
            "Horizontale distance (nicht entlang der Route, sondern Luftlinienprojektion) in Metern"
        )
    )


class DEMTileKey(BaseModel):
    """Key for a DEM tile (coordinate BBox + CRS reference).

    Args:
        min_lat: Minimale latitude
        max_lat: Maximale latitude
        min_lon: Minimale Laenge
        max_lon: Maximale Laenge
        crs_epsg: EPSG-Code des CRS (Standard: 4326 = WGS84)
    """

    min_lat: float = Field(ge=LAT_MIN, le=LAT_MAX)
    max_lat: float = Field(ge=LAT_MIN, le=LAT_MAX)
    min_lon: float = Field(ge=LON_MIN, le=LON_MAX)
    max_lon: float = Field(ge=LON_MIN, le=LON_MAX)
    crs_epsg: int = Field(default=4326, description="EPSG-Code des CRS")


class DEMTile(BaseModel):
    """In-Memory-Representation einer DEM-Kachel mit Metadaten.

    Args:
        key: DEMTileKey with BBox and CRS
        raster_data: Raw GeoTIFF data (only for caching, not for export)
        transform: Affine transform matrix [a, b, c, d, e, f] for pixel→world
        width: latitude of the raster in pixels
        height: height of the raster in pixels
        nodata_value: Nodata-Wert (Standard: -9999)
    """

    key: DEMTileKey
    raster_data: bytes
    transform: list[float] = Field(
        description="Affine transform matrix [a, b, c, d, e, f] for pixel→world"
    )
    width: int
    height: int
    nodata_value: float = Field(default=-9999)
