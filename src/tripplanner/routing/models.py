"""data models for the routing module.

Pydantic-modele zur Darstellung von Routen und GraphHopper-responseen.
Alle Koordinaten sind (lat, lon) in Dezimalgrad (WGS84).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Repraesentiert eine Koordinate (latitude, Laengengrad)
Coordinate = tuple[float, float]
"""Eine WGS84-Koordinate als (lat, lon) in Dezimalgrad."""


class RouteSegment(BaseModel):
    """A segment of the route with all attributes relevant to downstream modules."""

    segment_index: int = Field(..., description="Nullbasierter Index dieses Segments in der Route")
    geometrie: list[Coordinate] = Field(
        ..., description="Liste von (lat, lon) Koordinaten, die das Segment beschreiben"
    )
    length_m: float = Field(..., ge=0, description="length des Segments in Metern")
    strassenklasse: str = Field(
        ..., description="road class (MOTORWAY, TRUNK, PRIMARY, SECONDARY, TRACK, etc.)"
    )
    surface: str | None = Field(
        default=None,
        description=(
            "Road surface from GraphHopper path detail `surface` (z. B. asphalt, gravel, "
            "dirt), None if unavailable. Used by \`energy\` for the rolling resistance-"
            "Faktor konsumiert (siehe docs/plans/06-energy.md, Abschnitt 5.1.1)."
        ),
    )
    speed_limit_kmh: int | None = Field(
        default=None, ge=0, description="speed_limit_kmh in km/h (None if unavailable)"
    )
    steigung_rohdaten: float | None = Field(
        default=None, ge=-100, le=100, description="gradient in Prozent (None if unavailable)"
    )
    bearing_deg: float = Field(
        ...,
        ge=0.0,
        lt=360.0,
        description="heading (Bearing) am Segmentanfang in Grad (0°=Nord, 90°=Ost), "
        "vom routing-Modul aus Start-/Endkoordinate des Segments berechnet "
        "(Vorwaertsazimut, WGS84-Grosskreis). Wird von `wind` zur Wind components-"
        "Projektion konsumiert.",
    )
    road_environment: str | None = Field(
        default=None,
        description=(
            "Umgebungstyp aus GraphHopper Path-Detail `road_environment` (ROAD, "
            "FERRY, BRIDGE, TUNNEL, FORD, OTHER), normalisiert auf Grossbuchstaben; "
            "None if unavailable. Wird von `routing.ferries.detect_ferries()` "
            "genutzt, um Faehrabschnitte der Route zu erkennen."
        ),
    )
    street_name: str | None = Field(
        default=None,
        description=(
            "Road/ferry line name from GraphHopper path detail `street_name` "
            "(e.g. 'Rødby (DK) - Puttgarden (D)' for a ferry); None if "
            "unavailable or empty."
        ),
    )
    street_ref: str | None = Field(
        default=None,
        description=(
            "Road/highway reference from GraphHopper path detail `street_ref` "
            "(z. B. 'A 5', 'A 8', 'B 3', 'K 818'); None if unavailable oder leer. "
            "Used by ``construction`` to extract Autobahn IDs (A\d+) per segment "
            "to extract and targeted roadwork queries to enable."
        ),
    )


class Route(BaseModel):
    """Die gesamte berechnete Route mit Metadaten."""

    segments: list[RouteSegment] = Field(..., description="Liste aller Route-Segmente in heading")
    gesamtlaenge_m: float = Field(..., ge=0, description="Gesamtlaenge der Route in Metern")
    geometrie: list[Coordinate] = Field(
        ..., description="Vollstaendige Geometrie der Route als Liste von Koordinaten"
    )
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="Bounding box [min_lat, min_lon, max_lat, max_lon] (optional)"
    )
    via_point_indices: list[int] = Field(
        default_factory=list,
        description=(
            "Segment-Index jedes Zwischenstopps in Anfragereihenfolge (Index in "
            "`segments`, dessen Geometrie-Startpunkt exakt dem Zwischenstopp "
            "entspricht). Von den Routing providern EXAKT geliefert (bei "
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
    """Interne Darstellung einer GraphHopper /route API response (nur zur internen Verarbeitung)."""

    paths: list[GraphHopperPath]
    info: GraphHopperInfo


class GraphHopperPath(BaseModel):
    """Ein Pfad (in der Regel nur einer) aus der GraphHopper response."""

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
            "zusammenhaengende Geometrie-Abschnitte mit gleichem Wert "
            "zusammenfassen - kein flacher Wert pro Kante."
        ),
    )
    instructions: list[object] = Field(default_factory=list)


class GraphHopperInfo(BaseModel):
    """Meta-Informationen zur GraphHopper response."""

    copyrights: list[str] = Field(default_factory=list)
    hints: list[dict[str, object]] = Field(default_factory=list)
    took: int  # Millisekunden


class FerrySegment(BaseModel):
    """Eine in einer berechneten `Route` erkannte, zusammenhaengende Faehrverbindung.

    Erzeugt von `tripplanner.routing.ferries.detect_ferries()`. `bbox_sw`/`bbox_ne`
    beschreiben eine um `FERRY_BUFFER_DEG` gepufferte Bounding Box um die exakte
    segmentgeometrie - zur Wiederverwendung als `FerryExclusion`
    (`tripplanner.trip_input.models`) in einer nachfolgenden Routenberechnung, die
    genau diese Faehrverbindung vermeiden soll.
    """

    name: str = Field(
        ...,
        description=(
            "Faehrname aus dem ersten nicht-leeren `street_name` innerhalb des Laufs, "
            "'Unnamed ferry' falls GraphHopper keinen Namen liefert."
        ),
    )
    length_m: float = Field(
        ..., ge=0, description="Gesamtlaenge aller zusammenhaengenden Faehrsegmente in Metern"
    )
    bbox_sw: Coordinate = Field(..., description="southwest corner of the padded bounding box")
    bbox_ne: Coordinate = Field(..., description="Nordost-Ecke der gepufferten Bounding Box")
    segment_index_start: int = Field(
        ..., ge=0, description="Index des ersten Faehr-Segments in `Route.segments`"
    )
    segment_index_end: int = Field(
        ...,
        ge=0,
        description=(
            "Index NACH dem letzten Faehr-Segment in `Route.segments` (exklusiv, "
            "wie bei Python-Slices) - `segment_index_end - 1` ist der Index des "
            "letzten Faehr-Segments."
        ),
    )
    departure: datetime | None = Field(
        default=None,
        description=(
            "Vom Nutzer vorgegebene departure_time dieser Faehrverbindung, sofern ein "
            "passendes `trip_input.models.FerryTimeWindow` in der Anfrage enthalten "
            "war (siehe `trip_input.api._match_ferry_time_windows`); sonst `None`."
        ),
    )
    arrival: datetime | None = Field(
        default=None,
        description=(
            "Vom Nutzer vorgegebene arrival_time dieser ferry connection, analog zu `departure`."
        ),
    )
