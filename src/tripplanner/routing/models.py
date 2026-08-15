"""Datenmodelle für das routing-Modul.

Pydantic-Modelle zur Darstellung von Routen und GraphHopper-Antworten.
Alle Koordinaten sind (lat, lon) in Dezimalgrad (WGS84).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Repräsentiert eine Koordinate (Breitengrad, Längengrad)
Coordinate = tuple[float, float]
"""Eine WGS84-Koordinate als (lat, lon) in Dezimalgrad."""


class RouteSegment(BaseModel):
    """Ein Segment der Route mit allen für nachgelagerte Module relevanten Attributen."""

    segment_index: int = Field(..., description="Nullbasierter Index dieses Segments in der Route")
    geometrie: list[Coordinate] = Field(
        ..., description="Liste von (lat, lon) Koordinaten, die das Segment beschreiben"
    )
    laenge_m: float = Field(..., ge=0, description="Länge des Segments in Metern")
    strassenklasse: str = Field(
        ..., description="Straßenklasse (MOTORWAY, TRUNK, PRIMARY, SECONDARY, TRACK, etc.)"
    )
    oberflaeche: str | None = Field(
        default=None,
        description=(
            "Straßenbelag aus GraphHopper Path-Detail `surface` (z. B. asphalt, gravel, "
            "dirt), None wenn nicht verfügbar. Wird von `energy` für den Rollwiderstands-"
            "Faktor konsumiert (siehe docs/plans/06-energy.md, Abschnitt 5.1.1)."
        ),
    )
    tempolimit_kmh: int | None = Field(
        default=None, ge=0, description="Tempolimit in km/h (None wenn nicht verfügbar)"
    )
    steigung_rohdaten: float | None = Field(
        default=None, ge=-100, le=100, description="Steigung in Prozent (None wenn nicht verfügbar)"
    )
    bearing_deg: float = Field(
        ...,
        ge=0.0,
        lt=360.0,
        description="Fahrtrichtung (Bearing) am Segmentanfang in Grad (0°=Nord, 90°=Ost), "
        "vom routing-Modul aus Start-/Endkoordinate des Segments berechnet "
        "(Vorwärtsazimut, WGS84-Großkreis). Wird von `wind` zur Windkomponenten-"
        "Projektion konsumiert.",
    )
    road_environment: str | None = Field(
        default=None,
        description=(
            "Umgebungstyp aus GraphHopper Path-Detail `road_environment` (ROAD, "
            "FERRY, BRIDGE, TUNNEL, FORD, OTHER), normalisiert auf Großbuchstaben; "
            "None wenn nicht verfügbar. Wird von `routing.faehren.erkenne_faehren()` "
            "genutzt, um Fährabschnitte der Route zu erkennen."
        ),
    )
    strassenname: str | None = Field(
        default=None,
        description=(
            "Straßen-/Fährlinienname aus GraphHopper Path-Detail `street_name` "
            "(z. B. 'Rødby (DK) - Puttgarden (D)' für eine Fähre); None wenn "
            "nicht verfügbar oder leer."
        ),
    )


class Route(BaseModel):
    """Die gesamte berechnete Route mit Metadaten."""

    segments: list[RouteSegment] = Field(
        ..., description="Liste aller Route-Segmente in Fahrtrichtung"
    )
    gesamtlaenge_m: float = Field(..., ge=0, description="Gesamtlänge der Route in Metern")
    geometrie: list[Coordinate] = Field(
        ..., description="Vollständige Geometrie der Route als Liste von Koordinaten"
    )
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="Bounding box [min_lat, min_lon, max_lat, max_lon] (optional)"
    )


class GraphHopperResponse(BaseModel):
    """Interne Darstellung einer GraphHopper /route API Antwort (nur zur internen Verarbeitung)."""

    paths: list[GraphHopperPath]
    info: GraphHopperInfo


class GraphHopperPath(BaseModel):
    """Ein Pfad (in der Regel nur einer) aus der GraphHopper Antwort."""

    distance: float  # Meter
    time: int  # Millisekunden
    points: str  # Encodierte Polyline (points_encoded=True)
    points_encoded: bool = True
    details: dict[str, list[tuple[int, int, str | float | None]]] = Field(
        default_factory=dict,
        description=(
            "Path-Details wie road_class, max_speed, average_slope, surface. "
            "GraphHopper liefert je Detail eine Liste von "
            "(start_punkt_idx, end_punkt_idx, wert)-Intervallen, die "
            "zusammenhängende Geometrie-Abschnitte mit gleichem Wert "
            "zusammenfassen - kein flacher Wert pro Kante."
        ),
    )
    instructions: list[object] = Field(default_factory=list)


class GraphHopperInfo(BaseModel):
    """Meta-Informationen zur GraphHopper Antwort."""

    copyrights: list[str] = Field(default_factory=list)
    hints: list[dict[str, object]] = Field(default_factory=list)
    took: int  # Millisekunden


class FaehrSegment(BaseModel):
    """Eine in einer berechneten `Route` erkannte, zusammenhängende Fährverbindung.

    Erzeugt von `tripplanner.routing.faehren.erkenne_faehren()`. `bbox_sw`/`bbox_no`
    beschreiben eine um `FAEHR_PUFFER_GRAD` gepufferte Bounding Box um die exakte
    Segmentgeometrie - zur Wiederverwendung als `FaehrAusschluss`
    (`tripplanner.trip_input.models`) in einer nachfolgenden Routenberechnung, die
    genau diese Fährverbindung vermeiden soll.
    """

    name: str = Field(
        ...,
        description=(
            "Fährname aus `strassenname` des ersten Segments des Laufs, "
            "'Unbenannte Fähre' falls GraphHopper keinen Namen liefert."
        ),
    )
    laenge_m: float = Field(
        ..., ge=0, description="Gesamtlänge aller zusammenhängenden Fährsegmente in Metern"
    )
    bbox_sw: Coordinate = Field(..., description="Südwest-Ecke der gepufferten Bounding Box")
    bbox_no: Coordinate = Field(..., description="Nordost-Ecke der gepufferten Bounding Box")
