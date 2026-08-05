"""Datenmodelle für das elevation-Modul.

Alle Pydantic-Modelle für Höhenprofile und Steigungsprofile.
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
    """Höhenwert an einer Koordinate.

    Args:
        koordinate: Breitengrad, Längengrad (WGS84, Grad)
        hoehe_m: Höhe über NN in Metern (ungültige Werte: -9999 → nicht belegt)
    """

    koordinate: Coordinate = Field(description="Breitengrad, Längengrad (WGS84, Grad)")
    hoehe_m: float = Field(
        ge=HEIGHT_MIN,
        le=HEIGHT_MAX,
        description="Höhe über NN in Metern (ungültige Werte: -9999 → nicht belegt)",
    )

    @field_validator("koordinate")
    @classmethod
    def validate_koordinate(cls, v: Coordinate) -> Coordinate:
        """Validiere Koordinaten-Bereiche."""
        lat, lon = v
        if not (LAT_MIN <= lat <= LAT_MAX):
            raise ValueError(f"Breitengrad muss zwischen {LAT_MIN} und {LAT_MAX} Grad liegen")
        if not (LON_MIN <= lon <= LON_MAX):
            raise ValueError(f"Längengrad muss zwischen {LON_MIN} und {LON_MAX} Grad liegen")
        return v


class SegmentGradient(BaseModel):
    """Steigung/Gefälle je Segment aus Höhendifferenz und horizontaler Distanz.

    Args:
        segment_index: Index des RouteSegments (0-basiert)
        steigung_prozent: Steigung in Prozent (positive = Steigung,
            negativ = Gefälle)
        hoehendifferenz_m: Höhendifferenz zwischen Start- und
            Endpunkt des Segments in Metern
        horizontale_distanz_m: Horizontale Distanz (nicht entlang der
            Route, sondern Luftlinienprojektion) in Metern
    """

    segment_index: int = Field(ge=0, description="Index des RouteSegments (0-basiert)")
    steigung_prozent: float = Field(
        description="Steigung in Prozent (positive = Steigung, negativ = Gefälle)"
    )
    hoehendifferenz_m: float = Field(
        description="Höhendifferenz zwischen Start- und Endpunkt des Segments in Metern"
    )
    horizontale_distanz_m: float = Field(
        description=(
            "Horizontale Distanz (nicht entlang der Route, sondern Luftlinienprojektion) in Metern"
        )
    )


class DEMTileKey(BaseModel):
    """Schlüssel für DEM-Kachel (Koordinaten-BBox + CRS-Referenz).

    Args:
        min_lat: Minimale Breite
        max_lat: Maximale Breite
        min_lon: Minimale Länge
        max_lon: Maximale Länge
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
        key: DEMTileKey mit BBox und CRS
        raster_data: Rohe GeoTIFF-Daten (nur für Caching, nicht exportieren)
        transform: Affine Transform matrix [a, b, c, d, e, f] für pixel->world
        width: Breite des Rasters in Pixel
        height: Höhe des Rasters in Pixel
        nodata_value: Nodata-Wert (Standard: -9999)
    """

    key: DEMTileKey
    raster_data: bytes
    transform: list[float] = Field(
        description="Affine Transform matrix [a, b, c, d, e, f] für pixel->world"
    )
    width: int
    height: int
    nodata_value: float = Field(default=-9999)
