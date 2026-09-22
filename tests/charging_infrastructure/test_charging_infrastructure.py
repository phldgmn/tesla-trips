"""Tests für provider_session und die Provider-Abfragen."""

from __future__ import annotations

from pathlib import Path

import pytest

from tripplanner.charging_infrastructure.charging_infrastructure import provider_session
from tripplanner.charging_infrastructure.providers import (
    LocalFileChargingStationProvider,
    TeslaChargingStationProvider,
)
from tripplanner.geo import Coordinate
from tripplanner.routing.models import Route, RouteSegment

# Test-Fixture-Pfad
FIXTURE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "charging_infrastructure"
    / "tesla_supercharger_snapshot.json"
)


class TestProviderSession:
    """Tests für provider_session()."""

    def test_local_file_with_explicit_data_path(self) -> None:
        with provider_session(data_path=FIXTURE_PATH, provider_type="local_file") as provider:
            assert isinstance(provider, LocalFileChargingStationProvider)
            assert provider.data_path == FIXTURE_PATH

    def test_local_file_with_none_uses_default_path(self) -> None:
        with provider_session(data_path=None, provider_type="local_file") as provider:
            assert isinstance(provider, LocalFileChargingStationProvider)

    def test_tesla_db_provider_is_closed_on_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        closed: list[bool] = []
        monkeypatch.setattr(TeslaChargingStationProvider, "close", lambda self: closed.append(True))
        with provider_session(data_path=tmp_path / "t.db") as provider:
            assert isinstance(provider, TeslaChargingStationProvider)
        assert closed == [True]


class TestGetChargingStationsInRadius:
    """Tests für get_charging_stations_in_radius()."""

    @pytest.mark.asyncio
    async def test_auto_initializes_provider(self) -> None:
        """Test: Funktion initialisiert Provider automatisch bei Bedarf."""
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)

        coordinate: Coordinate = (52.5200, 13.4050)
        stations = await provider.get_stations_in_radius(coordinate, radius_km=10.0)

        assert isinstance(stations, list)
        assert len(stations) > 0

    @pytest.mark.asyncio
    async def test_returns_stations_sorted_by_distance(self) -> None:
        """Test: Stationen sind nach Distanz sortiert."""
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)
        coordinate: Coordinate = (52.5200, 13.4050)

        stations = await provider.get_stations_in_radius(coordinate, radius_km=50.0)

        assert len(stations) >= 2
        for i in range(len(stations) - 1):
            assert stations[i].distance_to_km <= stations[i + 1].distance_to_km

    @pytest.mark.asyncio
    async def test_country_filter(self) -> None:
        """Test: Länderfilter funktioniert."""
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)
        coordinate: Coordinate = (52.5200, 13.4050)

        stations_de = await provider.get_stations_in_radius(
            coordinate, radius_km=100.0, country_filter="DE"
        )
        stations_dk = await provider.get_stations_in_radius(
            coordinate, radius_km=100.0, country_filter="DK"
        )

        assert all(s.country == "DE" for s in stations_de)
        assert all(s.country == "DK" for s in stations_dk)

    @pytest.mark.asyncio
    async def test_empty_result_for_small_radius(self) -> None:
        """Test: Leeres Ergebnis bei sehr kleinem Radius weit weg von Stationen."""
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)
        coordinate: Coordinate = (0.0, 0.0)

        stations = await provider.get_stations_in_radius(coordinate, radius_km=1.0)

        assert stations == []


class TestGetChargingStationsAlongRoute:
    """Tests für get_charging_stations_along_route()."""

    def _create_test_route(self) -> Route:
        """Erstellt eine Test-Route mit allen erforderlichen Feldern."""
        return Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[(52.5200, 13.4050), (53.0, 12.0)],
                    laenge_m=50_000,
                    strassenklasse="MOTORWAY",
                    bearing_deg=315.0,
                ),
                RouteSegment(
                    segment_index=1,
                    geometrie=[(53.0, 12.0), (53.5511, 9.9937)],
                    laenge_m=30_000,
                    strassenklasse="MOTORWAY",
                    bearing_deg=280.0,
                ),
            ],
            gesamtlaenge_m=80_000,
            geometrie=[(52.5200, 13.4050), (53.0, 12.0), (53.5511, 9.9937)],
        )

    @pytest.mark.asyncio
    async def test_auto_initializes_provider(self) -> None:
        """Test: Funktion initialisiert Provider automatisch."""

        route = self._create_test_route()
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)

        result = await provider.get_stations_along_route(route, search_radius_km=5.0)

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_returns_dict_with_segment_indices(self) -> None:
        """Test: Ergebnis ist Dict mit Segment-Indizes als Keys."""
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)

        route = self._create_test_route()

        result = await provider.get_stations_along_route(route, search_radius_km=10.0)

        assert isinstance(result, dict)
        for key in result:
            assert isinstance(key, int)
            assert 0 <= key < len(route.segments)
        for stations in result.values():
            assert isinstance(stations, list)
            for key in result:
                for station in result[key]:
                    assert hasattr(station, "station_id")

    @pytest.mark.asyncio
    async def test_different_search_radius(self) -> None:
        """Test: Unterschiedliche Suchradien liefern unterschiedliche Ergebnisse."""
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)

        route = self._create_test_route()

        result_small = await provider.get_stations_along_route(route, search_radius_km=1.0)
        result_large = await provider.get_stations_along_route(route, search_radius_km=20.0)

        total_small = sum(len(s) for s in result_small.values())
        total_large = sum(len(s) for s in result_large.values())
        assert total_large >= total_small

    @pytest.mark.asyncio
    async def test_empty_route_segments(self) -> None:
        """Test: Route ohne Segmente liefert leeres Dict."""
        provider = LocalFileChargingStationProvider(FIXTURE_PATH)

        route = Route(
            segments=[],
            gesamtlaenge_m=1,
            geometrie=[],
        )

        result = await provider.get_stations_along_route(route, 2.0)

        assert result == {}
