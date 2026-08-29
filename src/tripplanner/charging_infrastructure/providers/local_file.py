"""Lokaler Datei-basierter ChargingStationProvider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from tripplanner.geo import Coordinate, haversine_distance_m

from ..models import (
    ChargingStation,
    ChargingStationProvider,
    ConnectorType,
    StallType,
)
from .record_mapping import _POWER_V2_MAX, _POWER_V3_MAX
from .spatial import _build_lat_bands, _stations_in_radius


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
        # Räumlicher Index über `self._stations`, siehe
        # `TeslaChargingStationProvider._lat_bands` für die Begründung.
        self._lat_bands: dict[int, list[ChargingStation]] | None = None
        self._lat_bands_source: list[ChargingStation] | None = None

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
            max_power = (
                stalls[StallType.V2] * float(_POWER_V2_MAX)
                + stalls[StallType.V3] * float(_POWER_V3_MAX)
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
                    max_ladeleistung_kw=max_power,
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
        stations = self._load_stations()
        if self._lat_bands is None or self._lat_bands_source is not stations:
            self._lat_bands = _build_lat_bands(stations)
            self._lat_bands_source = stations

        candidates = _stations_in_radius(self._lat_bands, coordinate, radius_km)
        if country_filter:
            candidates = [s for s in candidates if s.country == country_filter]

        # Sortieren nach Distanz (aufsteigend)
        paired = [(haversine_distance_m(coordinate, s.coordinate) / 1000.0, s) for s in candidates]
        paired.sort(key=lambda x: x[0])
        return [station for _, station in paired]

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
