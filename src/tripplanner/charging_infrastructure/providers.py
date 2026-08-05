"""Provider-Implementierungen für den `charging_infrastructure`-Zugriff.

Enthält:
- `LocalFileChargingStationProvider`: Lädt Daten aus einer lokalen JSON-Datei.
- `FakeChargingStationProvider`: Fake-Provider für Tests ohne Dateizugriff.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tripplanner.geo import Coordinate, haversine_distance_m

from .models import (
    ChargingStation,
    ChargingStationProvider,
    ConnectorType,
    StallType,
)

if TYPE_CHECKING:
    from typing import Literal


class LocalFileChargingStationProvider(ChargingStationProvider):
    """Implementierung, die Ladedaten aus einer lokalen JSON-Datei liest.

    Für Tests und Produktion (solange kein Crawler implementiert ist).
    """

    def __init__(self, data_path: Path) -> None:
        """Initialisiere den Provider mit dem Pfad zur JSON-Datei.

        Args:
            data_path: Pfad zur JSON-Datei (z. B. data/supercharger_snapshot.json)
        """
        self.data_path = data_path
        self._stations: list[ChargingStation] | None = None

    def _load_stations(self) -> list[ChargingStation]:
        """Lädt und parst die JSON-Datei (lazy)."""
        if self._stations is not None:
            return self._stations

        with open(self.data_path, encoding="utf-8") as f:
            data = json.load(f)

        stations: list[ChargingStation] = []
        for item in data["stations"]:
            stalls: dict[StallType, int] = {
                StallType.V2: item["stalls"].get("V2", 0),
                StallType.V3: item["stalls"].get("V3", 0),
                StallType.V3_ULTRA: item["stalls"].get("V3Ultra", 0),
                StallType.V4: item["stalls"].get("V4", 0),
            }

            # Max Leistung berechnen: Summe aller Stalls * durchschnittliche Leistung pro Stall
            # Vereinfachung: V2=150kW, V3=250kW, V3Ultra=325kW, V4=325kW
            max_leistung = (
                stalls[StallType.V2] * 150.0
                + stalls[StallType.V3] * 250.0
                + stalls[StallType.V3_ULTRA] * 325.0
                + stalls[StallType.V4] * 325.0
            )

            # Parse datetime aus String oder use default
            erstellungsdatum_str = item.get("erstellungsdatum")
            if isinstance(erstellungsdatum_str, str):
                try:
                    # Versuche ISO-Format
                    erstellungsdatum = datetime.fromisoformat(
                        erstellungsdatum_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except ValueError:
                    erstellungsdatum = datetime.now(UTC)
            else:
                erstellungsdatum = datetime.now(UTC)

            stations.append(
                ChargingStation(
                    station_id=item["station_id"],
                    name=item["name"],
                    coordinate=(item["lat"], item["lon"]),
                    stalls=stalls,
                    max_ladeleistung_kw=max_leistung,
                    connector_types=[ConnectorType(c) for c in item["connector_types"]],
                    country=item["country"],
                    ist_24_7=item.get("ist_24_7", True),
                    status=item.get("status", "online"),
                    letzte_datenAktualisierung=erstellungsdatum,
                )
            )

        self._stations = stations
        return stations

    async def get_stations_in_radius(
        self,
        coordinate: Coordinate,
        radius_km: float,
        country_filter: Literal["DE", "DK", "SE"] | None = None,
    ) -> list[ChargingStation]:
        """Liefert alle Supercharger innerhalb des gegebenen Radius um die Koordinate.

        Nutzt `haversine_distance_m` von `tripplanner.geo` für die Distanzberechnung.

        Args:
            coordinate: (lat, lon) als Tuple (WGS84)
            radius_km: Suchradius in Kilometern (Flugdistanz)
            country_filter: Optionaler Länderfilter (DE/DK/SE)

        Returns:
            Liste von ChargingStation, sortiert nach Distanz (aufsteigend)
        """
        lat, lon = coordinate
        stations = self._load_stations()
        # Länderfilter anwenden
        if country_filter:
            stations = [s for s in stations if s.country == country_filter]

        # Distanzberechnung und Filterung
        result: list[ChargingStation] = []
        distances: list[float] = []

        for station in stations:
            slat, slon = station.coordinate
            distance_m = haversine_distance_m((lat, lon), (slat, slon))
            distance_km = distance_m / 1000.0

            if distance_km <= radius_km:
                result.append(station)
                distances.append(distance_km)

        # Sortieren nach Distanz (aufsteigend)
        # Zip distances with stations, sort, unzip
        paired = list(zip(distances, result, strict=True))
        paired.sort(key=lambda x: x[0])
        result = [station for _, station in paired]

        return result

    async def get_stations_along_route(
        self,
        route: Any,
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Sucht Supercharger entlang der Route.

        Für jedes Segment wird der Mittelpunkt berechnet und in einem Radius von
        `search_radius_km` gesucht.

        Args:
            route: Die geplante Route (muss segments-Attribut haben)
            search_radius_km: Radius um jeden Segment-Mittelpunkt

        Returns:
            Dict mapping segment_index -> liste von ChargingStation
        """
        # Importiere route nur hier um Zyklus zu vermeiden
        result: dict[int, list[ChargingStation]] = {}
        for i, segment in enumerate(route.segments):
            # Segment-Mittelpunkt als geo-mittlerer Punkt
            coords: list[Coordinate] = segment.geometrie
            if not coords:
                continue

            mid_idx = len(coords) // 2
            mid_point = coords[mid_idx]

            # Alle Stationen im Radius finden
            nearby = await self.get_stations_in_radius(mid_point, search_radius_km)
            if nearby:
                result[i] = nearby

        return result


class FakeChargingStationProvider(ChargingStationProvider):
    """Fake-Provider für Unit-Tests ohne Dateizugriff.

    Liefert feste Testdaten basierend auf dem Suchparameter.
    """

    def __init__(self, test_stations: list[ChargingStation] | None = None) -> None:
        """Initialisiere den Fake-Provider mit optionalen Test-Stationen.

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

        Filtert die eingebenen Test-Stationen nach Radius und Länderfilter.
        Sortiert nach Distanz (aufsteigend).
        """
        # Länderfilter anwenden
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

        # Sortieren nach Distanz (aufsteigend)
        paired = list(zip(distances, result, strict=True))
        paired.sort(key=lambda x: x[0])
        result = [station for _, station in paired]

        return result

    async def get_stations_along_route(
        self,
        route: Any,
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Liefert Fake-Testdaten entlang der Route.

        Simuliert, dass nur bestimmte Segmente Stationen haben.
        """
        # Importiere route nur hier um Zyklus zu vermeiden

        result: dict[int, list[ChargingStation]] = {}
        stations = self._stations

        # Simuliere: Segment 0 und 2 haben Stationen im Radius
        # Andere Segmente sind leer
        MIN_STATIONS_FOR_ROUTE = 2
        if len(stations) >= MIN_STATIONS_FOR_ROUTE:
            result[0] = [stations[0]]
            result[2] = [stations[1]]
        return result
