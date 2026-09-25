"""GraphHopper-Routing-Anbieter: Protokoll, Konstanten und konkrete implementation."""

from __future__ import annotations

from typing import Protocol

import httpx
import polyline

from tripplanner.geo import bearing_deg, haversine_distance_m
from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.models import (
    Coordinate,
    GraphHopperPath,
    Route,
    RouteSegment,
)
from tripplanner.routing.providers.custom_model import build_custom_model
from tripplanner.trip_input.models import TripRequest

# GraphHopper-Instruktions-`sign`-Wert fuer "reached via point" - siehe
# `GraphHopperRoutingprovider._map_path_to_route`.
VIA_POINT_REACHED_SIGN = 5


class RoutingProvider(Protocol):
    """Interface für Routing-Anbieter. Ermöglicht Fake-implementationen für Tests."""

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Calculatet eine Route für die gegebene TripRequest."""
        ...


class GraphHopperRoutingProvider:
    """Konkrete implementation über GraphHopper HTTP API."""

    # Alle Path-Details, die `Routesegment` optional konsumiert (siehe models.py).
    _ALLE_PATH_DETAILS: tuple[str, ...] = (
        "road_class",
        "max_speed",
        "average_slope",
        "surface",
        "road_environment",
    )
    # `street_name` liest OSM-Namen direkt (z. B. ferry_linen-Relationen wie
    # "Rødby (DK) - Puttgarden (D)") und ist - anders als road_class/surface/
    # etc. - KEIN `graph.encoded_values`-Eintrag: taucht nie in `/info` auf
    # und würde von `_ermittele_verfuegbare_path_details()`s Verfügbarkeits-
    # filter faelschlich verworfen. Wird deshalb unabhaengig vom Filter immer
    # angefragt (live gegen den Projekt-GraphHopper-Server verifiziert:
    # Detail wird korrekt geliefert, siehe graphhopper_response_with_ferry.json).
    _IMMER_VERFUEGBARE_DETAILS: tuple[str, ...] = ("street_name", "street_ref")

    def __init__(self, client: GraphHopperClient, use_custom_model: bool = False):
        """Initialisiert den GraphHopper Routing provider.

        Args:
            client: GraphHopperClient für HTTP Kommunikation
            use_custom_model: Falls True, benutzerdefiniertes vehicle_profile verwenden
        """
        self.client = client
        self.use_custom_model = use_custom_model
        self._verfuegbare_details: list[str] | None = None

    async def _ermittele_verfuegbare_path_details(self) -> list[str]:
        """Ermittelt, welche Path-Details der verbundene Server unterstützt.

        Nicht jeder GraphHopper-Server hat `average_slope`/`surface` als
        `graph.encoded_values` konfiguriert (z. B. `average_slope` setzt eine
        aktivierte Elevation-source voraus, siehe README.md). Werden nicht
        unterstützte Details angefragt, lehnt GraphHopper die *gesamte*
        `/route`-request mit HTTP 400 ab. Da alle affected
        `Routesegment`-Felder ohnehin optional sind (siehe models.py, `None`
        wenn nicht verfügbar), wird hier defensiv nur das angefragt, was der
        Server laut `/info` tatsaechlich liefert - Ergebnis wird pro
        provider-Instanz gecacht, da sich die Server-configuration waehrend
        eines Prozesslaufs nicht aendert.
        """
        if self._verfuegbare_details is None:
            try:
                info = await self.client.info()
                encoded_values_raw = info.get("encoded_values", {})
                encoded_values = (
                    set(encoded_values_raw) if isinstance(encoded_values_raw, dict) else set()
                )
            except httpx.HTTPError:
                # /info nicht erreichbar/nicht unterstützt (z. B. aeltere
                # GraphHopper-Version) - im Zweifel alle Details anfragen wie
                # bisher; ein tatsaechlicher connectionsfehler tritt dann beim
                # folgenden `route()`-Aufruf ohnehin erneut auf.
                self._verfuegbare_details = list(self._ALLE_PATH_DETAILS)
            else:
                self._verfuegbare_details = [
                    d for d in self._ALLE_PATH_DETAILS if d in encoded_values
                ]
        return self._verfuegbare_details

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Calculatet eine Route für eine TripRequest (inkl. Zwischenstopps)."""
        # Umwandlung TripRequest → GraphHopper Parameter
        points = (
            [anfrage.start] + [wp.coordinate for wp in anfrage.waypoints] + [anfrage.destination]
        )

        details_list = [
            *await self._ermittele_verfuegbare_path_details(),
            *self._IMMER_VERFUEGBARE_DETAILS,
        ]

        custom_model = self._build_custom_model(anfrage)

        response = await self.client.route(
            points=points,
            profile="car",
            # elevation=False: der `polyline`-Decoder unterstützt nur 2D
            # (lat, lon) - eine 3D-kodierte Polyline (mit Elevation) würde
            # `polyline.decode()` falsch ausrichten und zum Absturz bringen.
            # `Routesegment.geometrie` ist ohnehin nur (lat, lon); gradient
            # wird separat vom `elevation`-Modul aus DEM-Kacheln berechnet.
            elevation=False,
            details=details_list,
            custom_model=custom_model,
        )

        # Mapping GraphHopperResponse → Route
        return self._map_path_to_route(response.paths[0])

    def _build_custom_model(self, anfrage: TripRequest) -> dict[str, object] | None:
        """Build optional GraphHopper `custom_model` from speed_limit_kmh and ferry preferences."""
        return build_custom_model(self.use_custom_model, anfrage)

    def _map_path_to_route(self, path: GraphHopperPath) -> Route:
        """Mapped GraphHopperPath zu Route mit Routesegments."""
        # Dekodiere Polyline
        coordinates: list[Coordinate] = polyline.decode(path.points)

        segments: list[RouteSegment] = []
        total_distance = 0.0
        full_geometrie = coordinates

        # Extrahiere Details für jedes segment. GraphHopper liefert je Detail
        # eine Liste von (start_punkt_idx, end_punkt_idx, wert)-Intervallen,
        # die zusammenhaengende Geometrie-Abschnitte mit gleichem Wert
        # zusammenfassen - kein flacher Wert pro edge (siehe models.py).
        road_classes = path.details.get("road_class", [])
        max_speeds = path.details.get("max_speed", [])
        average_slopes = path.details.get("average_slope", [])
        surfaces = path.details.get("surface", [])
        road_environments = path.details.get("road_environment", [])
        street_names = path.details.get("street_name", [])
        street_refs = path.details.get("street_ref", [])

        # Erstelle ein segment pro Edge (zwischen zwei aufeinanderfolgenden Points)
        for i in range(len(coordinates) - 1):
            start_coord = coordinates[i]
            end_coord = coordinates[i + 1]

            # calculate Laenge des segments (Haversine distance)
            length_m = haversine_distance_m(start_coord, end_coord)
            total_distance += length_m

            # Extrahiere segment-Attribute aus den interval-Details
            strassenklasse_raw = self._wert_fuer_edge(road_classes, i)
            # GraphHopper's `road_class` detail returns lowercase OSM values
            # (e.g. "motorway"), but this codebase's convention (see
            # Routesegment.strassenklasse docstring, FakeRoutingprovider's
            # hardcoded "PRIMARY"/"OTHER") is uppercase - normalize here so
            # every consumer (e.g. providers_de_autobahn._extract_autobahn_ids's
            # `!= "MOTORWAY"` check) can compare case-sensitively.
            strassenklasse = (
                str(strassenklasse_raw).upper() if strassenklasse_raw is not None else "OTHER"
            )
            speed_limit_kmh = self._normalize_max_speed(self._wert_fuer_edge(max_speeds, i))
            steigung_raw = self._wert_fuer_edge(average_slopes, i)
            steigung_rohdaten = float(steigung_raw) if steigung_raw is not None else None
            road_environment_raw = self._wert_fuer_edge(road_environments, i)
            road_environment = str(road_environment_raw).upper() if road_environment_raw else None
            strassenname_raw = self._wert_fuer_edge(street_names, i)
            street_name = str(strassenname_raw) if strassenname_raw else None
            strassenref_raw = self._wert_fuer_edge(street_refs, i)
            street_ref = str(strassenref_raw) if strassenref_raw else None
            oberflaeche_raw = self._wert_fuer_edge(surfaces, i)
            surface = str(oberflaeche_raw) if oberflaeche_raw is not None else None

            # calculate Bearing für das segment
            bearing = bearing_deg(start_coord, end_coord)

            segment = RouteSegment(
                segment_index=i,
                geometrie=[start_coord, end_coord],
                length_m=length_m,
                strassenklasse=strassenklasse,
                surface=surface,
                speed_limit_kmh=speed_limit_kmh,
                steigung_rohdaten=steigung_rohdaten,
                road_environment=road_environment,
                street_name=street_name,
                street_ref=street_ref,
                bearing_deg=bearing,
            )
            segments.append(segment)

        # Bounding box berechnen
        if coordinates:
            lats = [c[0] for c in coordinates]
            lons = [c[1] for c in coordinates]
            bbox = (min(lats), min(lons), max(lats), max(lons))
        else:
            bbox = None

        # Exakter segment-Index jedes Zwischenstopps: GraphHopper markiert das
        # Erreichen eines Via-Punkts (jeder in `points` uebergebene Punkt
        # zwischen Start und Ziel) mit einer eigenen Instruktion `sign == 5`
        # ("reached via point"), deren `interval` exakt auf den Koordinaten-
        # Index dieses Punkts zeigt - eine reine Naechster-Punkt-Suche waere
        # auf sich selbst kreuzenden/schleifenden Routen mehrdeutig (siehe
        # `Route.via_point_indices`-Docstring).
        via_point_indices = [
            int(instr["interval"][0])
            for instr in path.instructions
            if isinstance(instr, dict) and instr.get("sign") == VIA_POINT_REACHED_SIGN
        ]

        return Route(
            segments=segments,
            gesamtlaenge_m=total_distance,
            geometrie=full_geometrie,
            bbox=bbox,
            via_point_indices=via_point_indices,
        )

    def _normalize_max_speed(self, value: str | float | None) -> int | None:
        """Normalisiert GraphHopper max_speed Werte.

        - None → None (nicht verfügbar)
        - 0 → None (kein Schild, z. B. playstreets)
        - -1 → None (nicht bekannt)
        - positive Werte → int (km/h)
        """
        if value is None:
            return None
        speed = float(value)
        if speed <= 0:
            return None
        return int(speed)

    @staticmethod
    def _wert_fuer_edge(
        intervalle: list[tuple[int, int, str | float | None]], edge_index: int
    ) -> str | float | None:
        """Liefert den Detail-Wert für edge `edge_index` aus GraphHopper-Intervallen.

        GraphHopper liefert Path-Details als sortierte, lückenlose Liste von
        `(start_punkt_idx, end_punkt_idx, wert)`-Intervallen statt eines
        flachen Werts pro edge - mehrere aufeinanderfolgende edges mit
        gleichem Wert werden zu einem interval zusammengefasst.

        Args:
            intervalle: Liste von (start, end, wert)-Tripeln für ein Detail.
            edge_index: Index der edge (zwischen Punkt `edge_index` und
                `edge_index + 1`).

        Returns:
            Der Wert des Intervalls, das `edge_index` enthaelt, oder `None`
            wenn kein passendes interval existiert.
        """
        for start, end, wert in intervalle:
            if start <= edge_index < end:
                return wert
        return None
