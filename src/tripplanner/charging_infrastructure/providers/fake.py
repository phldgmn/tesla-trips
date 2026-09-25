"""Fake-ChargingStationprovider fuer Unit-Tests."""

from __future__ import annotations

from datetime import UTC, datetime
from math import cos, pi, sqrt
from typing import TYPE_CHECKING

from tripplanner.geo import Coordinate, haversine_distance_m
from tripplanner.routing.models import Route

from ..models import (
    ChargingStation,
    ChargingStationProvider,
    ConnectorType,
    StallType,
)

if TYPE_CHECKING:
    from typing import Literal


class FakeChargingStationProvider(ChargingStationProvider):
    """Fake-provider für Unit-Tests ohne Dateizugriff.

    Liefert feste Testdaten based auf dem Suchparameter.
    """

    def __init__(self, test_stations: list[ChargingStation] | None = None) -> None:
        """Initialisiere den Fake-provider mit optionalen Test-Stationen.

        Args:
            test_stations: Liste von Test-Stationen (default: einige Dummy-Stationen)
        """
        if test_stations is None:
            test_stations = [
                ChargingStation(
                    station_id="test-berlin-1",
                    name="Tesla Supercharger - Berlin Alexanderplatz",
                    coordinate=(52.5234, 13.4114),
                    stalls={StallType.V3: 8, StallType.V3_ULTRA: 4},
                    max_ladeleistung_kw=3250.0,
                    connector_types=[ConnectorType.NACS, ConnectorType.CCS2],
                    country="DE",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
                ChargingStation(
                    station_id="test-berlin-2",
                    name="Tesla Supercharger - Berlin Potsdamer Platz",
                    coordinate=(52.5083, 13.3797),
                    stalls={StallType.V3: 6},
                    max_ladeleistung_kw=1500.0,
                    connector_types=[ConnectorType.CCS2],
                    country="DE",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
                ChargingStation(
                    station_id="test-kopenhagen-1",
                    name="Tesla Supercharger - Kopenhagen Zentrum",
                    coordinate=(55.6761, 12.5683),
                    stalls={StallType.V3: 4, StallType.V4: 4},
                    max_ladeleistung_kw=2600.0,
                    connector_types=[ConnectorType.CCS2, ConnectorType.TYPE2],
                    country="DK",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
                ChargingStation(
                    station_id="test-malmo-1",
                    name="Tesla Supercharger - Malmö Urban",
                    coordinate=(55.5941, 13.0039),
                    stalls={StallType.V3: 8},
                    max_ladeleistung_kw=2000.0,
                    connector_types=[ConnectorType.CCS2],
                    country="SE",
                    letzte_datenAktualisierung=datetime.now(UTC),
                ),
            ]

        self._stations = test_stations

    async def get_stations_in_radius(
        self,
        coordinate: Coordinate,
        radius_km: float,
        country_filter: Literal["DE", "DK", "SE"] | None = None,
    ) -> list[ChargingStation]:
        """Liefert Fake-Testdaten.

        Filtert die eingebenen Test-Stationen nach Radius und Laenderfilter.
        Sortiert nach distance (aufsteigend).
        """
        # Laenderfilter anwenden
        stations = self._stations
        if country_filter:
            stations = [s for s in stations if s.country == country_filter]
        # Distanzberechnung und Filterung
        result: list[ChargingStation] = []
        distances: list[float] = []
        for station in stations:
            distance_m = haversine_distance_m(coordinate, station.coordinate)
            distance_km = distance_m / 1000.0

            if distance_km <= radius_km:
                result.append(station)
                distances.append(distance_km)

        # Sortieren nach distance (aufsteigend)
        paired = list(zip(distances, result, strict=True))
        paired.sort(key=lambda x: x[0])
        result = [station for _, station in paired]

        return result

    async def get_stations_along_route(
        self,
        route: Route,
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Liefert Fake-Testdaten entlang der Route.

        Verteilt alle konfigurierten Stationen entlang der Route based auf
        der distance zum segment-Mittelpunkt.
        """
        result: dict[int, list[ChargingStation]] = {}
        stations = self._stations
        if not stations or not route.segments:
            return result

        # Für jede Station das naechste segment anhand der Midpoint-distance finden
        for station in stations:
            best_seg_idx = 0
            best_dist = float("inf")
            for seg in route.segments:
                # Need at least 2 points to interpolate midpoint
                MIN_POINTS_FOR_MIDPOINT = 2
                if len(seg.geometrie) < MIN_POINTS_FOR_MIDPOINT:
                    continue
                mid_idx = len(seg.geometrie) // 2
                midpoint = seg.geometrie[mid_idx]
                # Haversine-Naeherung: distance in Metern
                dlat = (station.coordinate[0] - midpoint[0]) * 111.32 * 1000
                dlon = (
                    (station.coordinate[1] - midpoint[1])
                    * 111.32
                    * 1000
                    * cos(midpoint[0] * pi / 180)
                )
                dist_m = sqrt(dlat * dlat + dlon * dlon)
                if dist_m < best_dist:
                    best_dist = dist_m
                    best_seg_idx = seg.segment_index
            # Assign station to nearest segment (no distance filter for fake provider)
            result.setdefault(best_seg_idx, []).append(station)

        return result
