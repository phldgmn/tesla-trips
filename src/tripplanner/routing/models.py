"""Datenmodelle für das routing-Modul.

Pydantic-Modelle zur Darstellung von Routen und GraphHopper-Antworten.
Alle Koordinaten sind (lat, lon) in Dezimalgrad (WGS84).
"""

from __future__ import annotations

from datetime import datetime

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
            "None wenn nicht verfügbar. Wird von `routing.ferries.detect_ferries()` "
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
    strassenref: str | None = Field(
        default=None,
        description=(
            "Straßen-/Autobahnref aus GraphHopper Path-Detail `street_ref` "
            "(z. B. 'A 5', 'A 8', 'B 3', 'K 818'); None wenn nicht verfügbar oder leer. "
            "Wird von `construction` genutzt, um Autobahn-IDs (A\d+) pro Segment "
            "zu extrahieren und gezielte Roadworks-Queries zu ermöglichen."
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
    via_point_indices: list[int] = Field(
        default_factory=list,
        description=(
            "Segment-Index jedes Zwischenstopps in Anfragereihenfolge (Index in "
            "`segments`, dessen Geometrie-Startpunkt exakt dem Zwischenstopp "
            "entspricht). Von den Routing-Providern EXAKT geliefert (bei "
            "GraphHopper aus der 'reached via point'-Instruktion, sign=5, bei "
            "`FakeRoutingProvider` aus den Teilstrecken-Grenzen) - vermeidet die "
            "Mehrdeutigkeit einer reinen Naechster-Punkt-Suche auf Routen, die "
            "sich selbst kreuzen oder in der Naehe eines fruehen Streckenab-"
            "schnitts eine Schleife drehen (siehe "
            "`NetworkXOptimizer._map_waypoints_to_segments`). Leer, wenn keine "
            "Zwischenstopps angefragt wurden."
        ),
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


class FerrySegment(BaseModel):
    """Eine in einer berechneten `Route` erkannte, zusammenhängende Fährverbindung.

    Erzeugt von `tripplanner.routing.ferries.detect_ferries()`. `bbox_sw`/`bbox_ne`
    beschreiben eine um `FERRY_BUFFER_DEG` gepufferte Bounding Box um die exakte
    Segmentgeometrie - zur Wiederverwendung als `FerryExclusion`
    (`tripplanner.trip_input.models`) in einer nachfolgenden Routenberechnung, die
    genau diese Fährverbindung vermeiden soll.
    """

    name: str = Field(
        ...,
        description=(
            "Fährname aus dem ersten nicht-leeren `strassenname` innerhalb des Laufs, "
            "'Unnamed ferry' falls GraphHopper keinen Namen liefert."
        ),
    )
    laenge_m: float = Field(
        ..., ge=0, description="Gesamtlänge aller zusammenhängenden Fährsegmente in Metern"
    )
    bbox_sw: Coordinate = Field(..., description="Südwest-Ecke der gepufferten Bounding Box")
    bbox_ne: Coordinate = Field(..., description="Nordost-Ecke der gepufferten Bounding Box")
    segment_index_start: int = Field(
        ..., ge=0, description="Index des ersten Fähr-Segments in `Route.segments`"
    )
    segment_index_end: int = Field(
        ...,
        ge=0,
        description=(
            "Index NACH dem letzten Fähr-Segment in `Route.segments` (exklusiv, "
            "wie bei Python-Slices) - `segment_index_end - 1` ist der Index des "
            "letzten Fähr-Segments."
        ),
    )
    departure: datetime | None = Field(
        default=None,
        description=(
            "Vom Nutzer vorgegebene Abfahrtszeit dieser Fährverbindung, sofern ein "
            "passendes `trip_input.models.FerryTimeWindow` in der Anfrage enthalten "
            "war (siehe `trip_input.api._match_ferry_time_windows`); sonst `None`."
        ),
    )
    arrival: datetime | None = Field(
        default=None,
        description=(
            "Vom Nutzer vorgegebene Ankunftszeit dieser Fährverbindung, analog zu `departure`."
        ),
    )
