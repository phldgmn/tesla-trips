"""Tests for `optimization.detour_routing.precompute_detour_costs`."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from tripplanner.charging_infrastructure.models import ChargingStation, ConnectorType, StallType
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.optimization.detour_routing import (
    ON_ROUTE_THRESHOLD_M,
    precompute_detour_costs,
)
from tripplanner.optimization.station_mapping import map_stations_to_segments
from tripplanner.routing import FakeRoutingProvider
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import TripRequest, VehicleProfile

VEHICLE_PROFILE = VehicleProfile(
    mass_kg=1800.0,
    drag_coefficient=0.23,
    frontal_area_m2=2.2,
    rolling_resistance_coefficient=0.01,
    battery_capacity_kwh=60.0,
    auxiliary_baseline_kw=0.34,
    tire_type="standard",
    roof_box=False,
)


def _make_route() -> Route:
    coords = [(48.0, 11.0), (48.5, 10.0), (49.0, 9.0)]
    segments = [
        RouteSegment(
            segment_index=i,
            geometrie=[coords[i], coords[i + 1]],
            length_m=50_000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        )
        for i in range(len(coords) - 1)
    ]
    return Route(segments=segments, gesamtlaenge_m=100_000.0, geometrie=coords)


def _make_station(lat: float, lon: float, station_id: str = "s1") -> ChargingStation:
    return ChargingStation(
        station_id=station_id,
        name=station_id,
        coordinate=(lat, lon),
        stalls={StallType.V3: 4},
        max_ladeleistung_kw=250.0,
        connector_types=[ConnectorType.CCS2],
        country="DE",
    )


@pytest.mark.asyncio
async def test_on_route_threshold_is_100m() -> None:
    assert ON_ROUTE_THRESHOLD_M == 100.0


@pytest.mark.asyncio
async def test_precompute_returns_real_cost_per_station() -> None:
    route = _make_route()
    # ~5.5 km off-route (well above ON_ROUTE_THRESHOLD_M)
    station = _make_station(48.55, 10.05)
    station_segments = map_stations_to_segments([station], route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    result = await precompute_detour_costs(
        routing_provider=FakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        departure_time=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert station.station_id in result
    assert result[station.station_id].hinweg_distanz_m > 0.0
    assert result[station.station_id].hinweg_zeit_s > 0.0
    assert result[station.station_id].rueckweg_distanz_m > 0.0
    assert result[station.station_id].rueckweg_zeit_s > 0.0


@pytest.mark.asyncio
async def test_precompute_skips_on_route_stations() -> None:
    route = _make_route()
    station = _make_station(48.0, 11.0)  # exactly on route
    station_segments = map_stations_to_segments([station], route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    result = await precompute_detour_costs(
        routing_provider=FakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        departure_time=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert station.station_id not in result


@pytest.mark.asyncio
async def test_precompute_fans_out_concurrently() -> None:
    """N stations -> wall time close to ONE routing round trip, not N."""
    route = _make_route()
    stations = [_make_station(48.55, 10.0 + i * 0.01, station_id=f"s{i}") for i in range(10)]
    station_segments = map_stations_to_segments(stations, route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    class DelayedFakeRoutingProvider(FakeRoutingProvider):
        async def berechne_route(self, anfrage: TripRequest) -> Route:
            await asyncio.sleep(0.05)
            return await super().berechne_route(anfrage)

    start = asyncio.get_event_loop().time()
    result = await precompute_detour_costs(
        routing_provider=DelayedFakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        departure_time=datetime(2026, 8, 15, 8, 0, 0),
        max_concurrent_requests=20,
    )
    elapsed = asyncio.get_event_loop().time() - start

    assert len(result) == 10
    # Sequential would be 10 stations * 2 legs * 0.05s = 1.0s; concurrent
    # should be close to 1 round trip (0.05s) plus overhead.
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_precompute_omits_station_on_routing_failure() -> None:
    route = _make_route()
    failing_station = _make_station(48.55, 10.05, station_id="fails")
    ok_station = _make_station(48.55, 10.06, station_id="ok")
    station_segments = map_stations_to_segments([failing_station, ok_station], route.segments)
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    _failing_targets: set[tuple[float, float]] = {failing_station.coordinate}

    class PartiallyFailingRoutingProvider(FakeRoutingProvider):
        async def berechne_route(self, anfrage: TripRequest) -> Route:
            if anfrage.destination in _failing_targets or anfrage.start in _failing_targets:
                raise RuntimeError("simulated routing failure")
            return await super().berechne_route(anfrage)

    result = await precompute_detour_costs(
        routing_provider=PartiallyFailingRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments=station_segments,
        departure_time=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert "fails" not in result
    assert "ok" in result


@pytest.mark.asyncio
async def test_precompute_empty_station_segments_returns_empty_dict() -> None:
    route = _make_route()
    elevation_provider = ElevationProvider(data_source=FakeDataSource())

    result = await precompute_detour_costs(
        routing_provider=FakeRoutingProvider(),
        elevation_provider=elevation_provider,
        vehicle_profile=VEHICLE_PROFILE,
        route=route,
        station_segments={},
        departure_time=datetime(2026, 8, 15, 8, 0, 0),
    )

    assert result == {}
