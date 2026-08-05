"""Tests für charging_infrastructure Hilfsfunktionen (globale Provider,
Convenience-Funktionen)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from tripplanner.charging_infrastructure import charging_infrastructure as ci_module
from tripplanner.charging_infrastructure.charging_infrastructure import (
    get_charging_stations_along_route,
    get_charging_stations_in_radius,
    init_charging_infrastructure,
)
from tripplanner.charging_infrastructure.providers import LocalFileChargingStationProvider
from tripplanner.geo import Coordinate
from tripplanner.routing.models import Route, RouteSegment

# Test-Fixture-Pfad
FIXTURE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "charging_infrastructure"
    / "tesla_supercharger_snapshot.json"
)


@pytest.fixture(autouse=True)
def reset_default_provider() -> Generator[None, None, None]:
    """Setzt den globalen _DEFAULT_PROVIDER vor und nach jedem Test zurück."""
    ci_module._DEFAULT_PROVIDER = None
    yield
    ci_module._DEFAULT_PROVIDER = None


class TestInitChargingInfrastructure:
    """Tests für init_charging_infrastructure()."""

    def test_init_with_explicit_data_path(self) -> None:
        """Test Initialisierung mit explizitem data_path."""
        init_charging_infrastructure(data_path=FIXTURE_PATH)
        assert ci_module._DEFAULT_PROVIDER is not None
        assert isinstance(ci_module._DEFAULT_PROVIDER, LocalFileChargingStationProvider)
        assert ci_module._DEFAULT_PROVIDER.data_path == FIXTURE_PATH

    def test_init_with_none_uses_default_path(self) -> None:
        """Test Initialisierung ohne data_path nutzt Default-Pfad."""
        init_charging_infrastructure(data_path=None)
        assert ci_module._DEFAULT_PROVIDER is not None
        assert isinstance(ci_module._DEFAULT_PROVIDER, LocalFileChargingStationProvider)

    def test_init_idempotent_second_call_reuses_provider(self) -> None:
        """Test: Zweiter Aufruf gibt bestehenden Provider zurück (kein Neuerstellen)."""
        init_charging_infrastructure(data_path=FIXTURE_PATH)
        first_provider = ci_module._DEFAULT_PROVIDER

        init_charging_infrastructure(data_path=FIXTURE_PATH)
        second_provider = ci_module._DEFAULT_PROVIDER

        assert first_provider is second_provider

    def test_init_with_different_path_after_first_call_ignored(self) -> None:
        """Test: Bei zweitem Aufruf mit anderem Pfad wird der erste Provider beibehalten."""
        other_fixture = Path("/tmp/nonexistent.json")
        init_charging_infrastructure(data_path=FIXTURE_PATH)
        first_provider = ci_module._DEFAULT_PROVIDER

        init_charging_infrastructure(data_path=other_fixture)
        second_provider = ci_module._DEFAULT_PROVIDER

        assert first_provider is second_provider
        assert second_provider.data_path == FIXTURE_PATH


class TestGetChargingStationsInRadius:
    """Tests für get_charging_stations_in_radius()."""

    @pytest.mark.asyncio
    async def test_auto_initializes_provider(self) -> None:
        """Test: Funktion initialisiert Provider automatisch bei Bedarf."""
        assert ci_module._DEFAULT_PROVIDER is None
        init_charging_infrastructure(data_path=FIXTURE_PATH)

        coordinate: Coordinate = (52.5200, 13.4050)
        stations = await get_charging_stations_in_radius(coordinate, radius_km=10.0)

        assert ci_module._DEFAULT_PROVIDER is not None
        assert isinstance(stations, list)
        assert len(stations) > 0

    @pytest.mark.asyncio
    async def test_returns_stations_sorted_by_distance(self) -> None:
        """Test: Stationen sind nach Distanz sortiert."""
        init_charging_infrastructure(data_path=FIXTURE_PATH)
        coordinate: Coordinate = (52.5200, 13.4050)

        stations = await get_charging_stations_in_radius(coordinate, radius_km=50.0)

        assert len(stations) >= 2
        for i in range(len(stations) - 1):
            assert stations[i].distance_to_km <= stations[i + 1].distance_to_km

    @pytest.mark.asyncio
    async def test_country_filter(self) -> None:
        """Test: Länderfilter funktioniert."""
        init_charging_infrastructure(data_path=FIXTURE_PATH)
        coordinate: Coordinate = (52.5200, 13.4050)

        stations_de = await get_charging_stations_in_radius(
            coordinate, radius_km=100.0, country="DE"
        )
        stations_dk = await get_charging_stations_in_radius(
            coordinate, radius_km=100.0, country="DK"
        )

        assert all(s.country == "DE" for s in stations_de)
        assert all(s.country == "DK" for s in stations_dk)

    @pytest.mark.asyncio
    async def test_empty_result_for_small_radius(self) -> None:
        """Test: Leeres Ergebnis bei sehr kleinem Radius weit weg von Stationen."""
        init_charging_infrastructure(data_path=FIXTURE_PATH)
        coordinate: Coordinate = (0.0, 0.0)

        stations = await get_charging_stations_in_radius(coordinate, radius_km=1.0)

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
        assert ci_module._DEFAULT_PROVIDER is None

        route = self._create_test_route()
        init_charging_infrastructure(data_path=FIXTURE_PATH)

        result = await get_charging_stations_along_route(route, search_radius_km=5.0)

        assert ci_module._DEFAULT_PROVIDER is not None
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_returns_dict_with_segment_indices(self) -> None:
        """Test: Ergebnis ist Dict mit Segment-Indizes als Keys."""
        init_charging_infrastructure(data_path=FIXTURE_PATH)

        route = self._create_test_route()

        result = await get_charging_stations_along_route(route, search_radius_km=10.0)

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
        init_charging_infrastructure(data_path=FIXTURE_PATH)

        route = self._create_test_route()

        result_small = await get_charging_stations_along_route(route, search_radius_km=1.0)
        result_large = await get_charging_stations_along_route(route, search_radius_km=20.0)

        total_small = sum(len(s) for s in result_small.values())
        total_large = sum(len(s) for s in result_large.values())
        assert total_large >= total_small

    @pytest.mark.asyncio
    async def test_empty_route_segments(self) -> None:
        """Test: Route ohne Segmente liefert leeres Dict."""
        init_charging_infrastructure(data_path=FIXTURE_PATH)

        route = Route(
            segments=[],
            gesamtlaenge_m=1,
            geometrie=[],
        )

        result = await get_charging_stations_along_route(route)

        assert result == {}
