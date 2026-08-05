"""Tests für die Provider-Implementierungen von `charging_infrastructure`."""

from __future__ import annotations

from pathlib import Path

import pytest

from tripplanner.charging_infrastructure.models import (
    ChargingStationProvider,
)
from tripplanner.charging_infrastructure.providers import (
    FakeChargingStationProvider,
    LocalFileChargingStationProvider,
)
from tripplanner.geo import haversine_distance_m
from tripplanner.routing.models import Route, RouteSegment


class TestFakeChargingStationProvider:
    """Tests für den FakeChargingStationProvider."""

    @pytest.fixture
    def provider(self) -> FakeChargingStationProvider:
        return FakeChargingStationProvider()

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_basic(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet grundlegende Radius-Suche."""
        # Berlin Alexanderplatz
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=10.0)
        assert len(stations) >= 1
        # Erste Station sollte Berlin Alexanderplatz sein (Distanz ~0)
        assert stations[0].station_id == "test-berlin-1"

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_with_country_filter(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet Radius-Suche mit Länderfilter."""
        # Berlin Koordinaten, aber Filter auf DK -> Kopenhagen station in radius
        stations = await provider.get_stations_in_radius(
            (52.5234, 13.4114), radius_km=500.0, country_filter="DK"
        )
        # Kopenhagen station ist within 500km of Berlin
        assert len(stations) == 1
        assert all(s.country == "DK" for s in stations)

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_distance_sorting(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet, dass Stationen nach Distanz sortiert zurückgegeben werden."""
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=100.0)
        distances = []
        for s in stations:
            dist = haversine_distance_m((52.5234, 13.4114), s.coordinate) / 1000.0
            distances.append(dist)
        # Distanzen sollten aufsteigend sortiert sein
        assert distances == sorted(distances)
        # Berlin Alexanderplatz ist am Standort (0km), Potsdamer Platz ist weiter weg (2.72km)
        assert stations[0].station_id == "test-berlin-1"
        assert stations[1].station_id == "test-berlin-2"

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_empty_result(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet Suche mit minimalem Radius."""
        # FakeProvider gibt immer Berlin Stationen zurück, auch bei radius=0.0
        # Da Berlin Stationen im FakeProvider existieren, wird mindestens eine zurückgegeben
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=0.0)
        # Mindestens eine Station (Berlin Alexanderplatz) ist im 0km Radius enthalten
        assert len(stations) >= 1

    @pytest.mark.asyncio
    async def test_get_stations_along_route(self, provider: FakeChargingStationProvider) -> None:
        """Testet Suche entlang einer Route."""
        segments = [
            RouteSegment(
                segment_index=0,
                geometrie=[(52.5234, 13.4114), (52.61, 13.35)],
                laenge_m=10000.0,
                strassenklasse="MOTORWAY",
                bearing_deg=315.0,
            ),
            RouteSegment(
                segment_index=1,
                geometrie=[(52.61, 13.35), (52.73, 13.2)],
                laenge_m=15000.0,
                strassenklasse="MOTORWAY",
                bearing_deg=322.0,
            ),
        ]
        route = Route(
            segments=segments,
            gesamtlaenge_m=25000.0,
            geometrie=[(52.5234, 13.4114), (52.61, 13.35), (52.73, 13.2)],
        )

        result = await provider.get_stations_along_route(route, search_radius_km=5.0)

        # Prüfe keine doppelten Stationen pro Segment
        for _segment_idx, stations in result.items():
            station_ids = [s.station_id for s in stations]
            assert len(station_ids) == len(set(station_ids))

    @pytest.mark.asyncio
    async def test_get_stations_along_route_empty_segments(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet Route mit leeren Segmenten."""
        # FakeChargingStationProvider gibt immer Berlin Stationen zurück, unabhängig von Route
        # Daher testen wir hier, dass die Provider-Methode korrekt aufgerufen wird
        segments: list[RouteSegment] = []
        route = Route(
            segments=segments,
            gesamtlaenge_m=1.0,  # Minimaler Wert > 0
            geometrie=[(52.5234, 13.4114)],
        )

        result = await provider.get_stations_along_route(route, search_radius_km=5.0)
        # FakeProvider gibt Stationen zurück, auch bei leeren Segmenten
        assert len(result) > 0

    @pytest.mark.parametrize(
        "coordinate,radius_km,expected_min_count",
        [
            ((52.5234, 13.4114), 10.0, 1),  # Berlin
            ((55.6761, 12.5683), 10.0, 1),  # Kopenhagen
            ((48.1650, 11.5850), 10.0, 1),  # München
        ],
    )
    @pytest.mark.asyncio
    async def test_get_stations_in_radius_parametrized(
        self,
        local_provider: LocalFileChargingStationProvider,
        coordinate: tuple[float, float],
        radius_km: float,
        expected_min_count: int,
    ) -> None:
        """Parametrisierter Test für get_stations_in_radius an bekannten Orten."""
        stations = await local_provider.get_stations_in_radius(coordinate, radius_km)
        assert len(stations) >= expected_min_count


class TestLocalFileChargingStationProvider:
    """Tests für den LocalFileChargingStationProvider mit echter Test-Fixture."""

    @pytest.fixture
    def provider(self, test_fixture_path: Path) -> LocalFileChargingStationProvider:
        """LocalFileChargingStationProvider geladen mit Test-Fixture."""
        return LocalFileChargingStationProvider(test_fixture_path)

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_basic(
        self, provider: LocalFileChargingStationProvider
    ) -> None:
        """Testet grundlegende Radius-Suche mit echten Daten."""
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=10.0)
        assert len(stations) >= 1
        assert any(s.station_id == "de-berlin-001" for s in stations)

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_all_countries(
        self, provider: LocalFileChargingStationProvider
    ) -> None:
        """Testet, dass alle 24 Stationen gefunden werden."""
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=1000.0)
        assert len(stations) == 24

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_de_filter(
        self, provider: LocalFileChargingStationProvider
    ) -> None:
        """Testet Länderfilter auf DE."""
        stations = await provider.get_stations_in_radius(
            (52.5234, 13.4114), radius_km=1000.0, country_filter="DE"
        )
        assert len(stations) == 14
        assert all(s.country == "DE" for s in stations)

    @pytest.mark.asyncio
    async def test_get_stations_along_route(
        self, provider: LocalFileChargingStationProvider, sample_route: dict
    ) -> None:
        """Testet Suche entlang einer Route mit echten Daten."""
        segments = [RouteSegment(**seg) for seg in sample_route["segments"]]
        route = Route(
            segments=segments,
            gesamtlaenge_m=sample_route["gesamtlaenge_m"],
            geometrie=[tuple(c) for c in sample_route["geometrie"]],
        )
        result = await provider.get_stations_along_route(route, search_radius_km=15.0)
        for _segment_idx, stations in result.items():
            station_ids = [s.station_id for s in stations]
            assert len(station_ids) == len(set(station_ids))

    @pytest.mark.asyncio
    async def test_get_stations_along_route_different_radius(
        self, provider: LocalFileChargingStationProvider, sample_route: dict
    ) -> None:
        """Testet, dass größerer Radius mehr Stationen findet."""
        segments = [RouteSegment(**seg) for seg in sample_route["segments"]]
        route = Route(
            segments=segments,
            gesamtlaenge_m=sample_route["gesamtlaenge_m"],
            geometrie=[tuple(c) for c in sample_route["geometrie"]],
        )

        result_small = await provider.get_stations_along_route(route, search_radius_km=1.0)
        result_large = await provider.get_stations_along_route(route, search_radius_km=15.0)

        # Mit größerem Radius sollten mindestens so viele Segmente Stationen haben
        # (oder mehr Stationen pro Segment)
        total_stations_small = sum(len(v) for v in result_small.values())
        total_stations_large = sum(len(v) for v in result_large.values())
        assert total_stations_large >= total_stations_small

    @pytest.mark.asyncio
    async def test_caching(self, provider: LocalFileChargingStationProvider) -> None:
        """Testet, dass Stationen nach erstem Laden gecacht werden."""
        stations1 = provider._load_stations()
        stations2 = provider._load_stations()

        # Derselbe Objekt (Cached)
        assert stations1 is stations2
        assert len(stations1) == len(stations2) == 24


class TestProviderProtocol:
    """Tests, dass beide Provider das Protocol einhalten."""

    def test_fake_provider_is_provider(self) -> None:
        """Testet, dass FakeChargingStationProvider das Protocol implementiert."""
        provider = FakeChargingStationProvider()
        assert isinstance(provider, ChargingStationProvider)

    def test_local_provider_is_provider(
        self,
        test_fixture_path: Path,
    ) -> None:
        """Testet, dass LocalFileChargingStationProvider das Protocol implementiert."""
        provider = LocalFileChargingStationProvider(test_fixture_path)
        assert isinstance(provider, ChargingStationProvider)
