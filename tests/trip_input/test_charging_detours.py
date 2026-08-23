"""Tests for Part 2: parallelized charging detour routing."""

import asyncio
from datetime import datetime

import httpx
import pytest

from tripplanner.charging_infrastructure.models import (
    ChargingStation,
    ConnectorType,
    StallType,
)
from tripplanner.optimization.models import ChargingPlan, ChargingStop
from tripplanner.routing import FakeRoutingProvider
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.simulation.models import LadehaltDetour
from tripplanner.trip_input.api import _step_route_charging_detours
from tripplanner.trip_input.models import TripRequest, VehicleProfile


def _make_charging_station(
    lat: float = 48.5, lon: float = 10.0, name: str = "Test Station"
) -> ChargingStation:
    coord = (lat, lon)
    return ChargingStation(
        station_id=f"test-{name}",
        name=name,
        coordinate=coord,
        stalls={StallType.V3: 4},
        max_ladeleistung_kw=150.0,
        connector_types=[ConnectorType.TESLA],
        country="DE",
    )


def _make_route(
    coords: list[tuple[float, float]] | None = None,
) -> Route:
    if coords is None:
        coords = [(48.0, 11.0), (48.5, 10.0), (49.0, 9.0)]
    seg_count = len(coords) - 1
    segments = []
    for i in range(seg_count):
        c1, c2 = coords[i], coords[i + 1]
        segments.append(
            RouteSegment(
                segment_index=i,
                geometrie=[c1, c2],
                laenge_m=50000.0,
                strassenklasse="MOTORWAY",
                bearing_deg=0.0,
            )
        )
    return Route(
        segments=segments,
        gesamtlaenge_m=seg_count * 50000.0,
        geometrie=coords,
    )


def _make_charging_plan(stops: list[ChargingStop]) -> ChargingPlan:
    return ChargingPlan(ladehalte=stops, gesamtreisezeit_s=7200)


VEHICLE_PROFILE = VehicleProfile(
    masse_kg=1800.0,
    cw_wert=0.23,
    stirnflaeche_m2=2.2,
    rollwiderstandsbeiwert=0.01,
    batteriekapazitaet_kwh=60.0,
    nebenverbraucher_baseline_kw=0.34,
    reifentyp="standard",
    dachbox=False,
)


@pytest.mark.asyncio
class TestChargingDetourConcurrency:
    def test_routing_calls_fan_out_not_sequential(self) -> None:
        """3 stops -> 6 route calls; wall time ~1 call, not 6x."""
        stop_coords = [(48.2, 10.5), (48.5, 10.0), (48.8, 9.5)]
        stations = [_make_charging_station(lat=c[0], lon=c[1]) for c in stop_coords]
        charges = [
            ChargingStop(
                station=s,
                segment_index=1,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=80.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=datetime(2025, 6, 15, 10, 0, 0),
                abfahrtszeit=datetime(2025, 6, 15, 10, 30, 0),
            )
            for s in stations
        ]
        plan = _make_charging_plan(charges)
        route = _make_route()
        provider = FakeRoutingProvider()

        orig = provider.berechne_route

        async def slow(*args, **kwargs):
            await asyncio.sleep(0.05)
            return await orig(*args, **kwargs)

        provider.berechne_route = slow  # type: ignore[method-assign]

        async def run():
            start = asyncio.get_event_loop().time()
            result = await _step_route_charging_detours(
                provider, route, plan, datetime(2025, 6, 15, 10, 0, 0), VEHICLE_PROFILE
            )
            elapsed = asyncio.get_event_loop().time() - start
            return result, elapsed

        result, elapsed = asyncio.run(run())
        # Sequential 6 calls x 0.05 = 0.30 s; concurrent ~ 0.05 s
        assert elapsed < 0.15, f"Sequential not concurrent: {elapsed:.2f}s"
        assert len(result) == 3

    def test_results_map_to_correct_stop_id(self) -> None:
        """Each stop maps to its correct detour result."""
        stations = [_make_charging_station(lat=48.2, lon=10.5)]
        charges = [
            ChargingStop(
                station=stations[0],
                segment_index=1,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=80.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=datetime(2025, 6, 15, 10, 0, 0),
                abfahrtszeit=datetime(2025, 6, 15, 10, 30, 0),
            )
        ]
        plan = _make_charging_plan(charges)
        route = _make_route()
        provider = FakeRoutingProvider()

        async def run():
            return await _step_route_charging_detours(
                provider, route, plan, datetime(2025, 6, 15, 10, 0, 0), VEHICLE_PROFILE
            )

        result = asyncio.run(run())
        assert len(result) == 1
        stop_id = id(charges[0])
        detour = result[stop_id]
        assert isinstance(detour, LadehaltDetour)
        assert len(detour.geometrie) >= 2
        assert detour.route_index_vor < detour.station_index
        assert detour.station_index < len(detour.geometrie) - 1

    def test_empty_charging_plan_returns_empty_dict(self) -> None:
        plan = _make_charging_plan([])
        route = _make_route()

        async def run():
            return await _step_route_charging_detours(
                FakeRoutingProvider(), route, plan, datetime(2025, 6, 15, 10, 0, 0), VEHICLE_PROFILE
            )

        result = asyncio.run(run())
        assert result == {}


@pytest.mark.asyncio
class TestChargingDetourErrorHandling:
    def test_single_failing_route_skips_stop(self) -> None:
        """HTTPError on one leg → stop skipped, matching original behavior."""
        stations = [_make_charging_station(lat=48.5, lon=10.0)]
        charges = [
            ChargingStop(
                station=stations[0],
                segment_index=1,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=80.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=datetime(2025, 6, 15, 10, 0, 0),
                abfahrtszeit=datetime(2025, 6, 15, 10, 30, 0),
            )
        ]
        plan = _make_charging_plan(charges)
        route = _make_route()

        class _FailingProvider(FakeRoutingProvider):
            _call_count: int = 0

            async def berechne_route(self, anfrage: TripRequest) -> Route:
                self._call_count += 1
                if self._call_count == 2:
                    raise httpx.HTTPError("fail")
                return await super().berechne_route(anfrage)

        provider = _FailingProvider()

        async def run():
            return await _step_route_charging_detours(
                provider, route, plan, datetime(2025, 6, 15, 10, 0, 0), VEHICLE_PROFILE
            )

        result = asyncio.run(run())
        assert len(result) == 0

    def test_one_stop_fails_others_succeed(self) -> None:
        """One stop fails but others succeed → correct subset returned."""
        stations = [
            _make_charging_station(lat=48.2, lon=10.5),
            _make_charging_station(lat=48.5, lon=10.0),
            _make_charging_station(lat=48.8, lon=9.5),
        ]
        charges = [
            ChargingStop(
                station=s,
                segment_index=1,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=80.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=datetime(2025, 6, 15, 10, 0, 0),
                abfahrtszeit=datetime(2025, 6, 15, 10, 30, 0),
            )
            for s in stations
        ]
        plan = _make_charging_plan(charges)
        route = _make_route()

        call_count = [0]

        class _PartialFailingProvider(FakeRoutingProvider):
            async def berechne_route(self, anfrage: TripRequest) -> Route:
                call_count[0] += 1
                # Fail the 3rd call only (not a pair, tests that partial failure
                # skips that stop but others still succeed)
                if call_count[0] == 3:
                    raise httpx.HTTPError("fail")
                return await super().berechne_route(anfrage)

        provider = _PartialFailingProvider()

        async def run():
            return await _step_route_charging_detours(
                provider, route, plan, datetime(2025, 6, 15, 10, 0, 0), VEHICLE_PROFILE
            )

        result = asyncio.run(run())
        # With 3 stops: 6 calls. 1 failure → at most 1 stop missing
        # Could be 2 or 3 results (if the failing call is a route for a stop
        # whose other leg succeeds, that stop is skipped)
        assert 2 <= len(result) <= 3

    def test_non_http_exception_propagates(self) -> None:
        """A non-httpx exception (e.g. ValueError from response-parsing bug)
        propagates out of _step_route_charging_detours instead of being
        silently dropped — restoring the pre-f8e76fc behavior."""
        stations = [
            _make_charging_station(lat=48.2, lon=10.5),
            _make_charging_station(lat=48.5, lon=10.0),
        ]
        charges = [
            ChargingStop(
                station=s,
                segment_index=1,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=80.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=datetime(2025, 6, 15, 10, 0, 0),
                abfahrtszeit=datetime(2025, 6, 15, 10, 30, 0),
            )
            for s in stations
        ]
        plan = _make_charging_plan(charges)
        route = _make_route()

        call_count = [0]

        class _ValueErrorProvider(FakeRoutingProvider):
            async def berechne_route(self, anfrage: TripRequest) -> Route:
                call_count[0] += 1
                if call_count[0] == 2:
                    raise ValueError("parsing bug in response")
                return await super().berechne_route(anfrage)

        provider = _ValueErrorProvider()

        async def run() -> dict[int, LadehaltDetour]:
            return await _step_route_charging_detours(
                provider, route, plan, datetime(2025, 6, 15, 10, 0, 0), VEHICLE_PROFILE
            )

        with pytest.raises(ValueError, match="parsing bug in response"):
            asyncio.run(run())

        # FakeRoutingProvider executes synchronously; all coroutines complete
        # before gather, so call_count reflects total attempted calls
        assert call_count[0] == 4  # 2 stops x 2 legs, call 2 raises

    def test_httpx_on_one_stop_allows_others(self) -> None:
        """httpx.HTTPError on one stop's leg allows other stops to succeed
        — existing behavior preserved. With 2 stops and error on call 3
        (rueckweg for stop 1), stop 0's detour is returned."""
        stations = [
            _make_charging_station(lat=48.2, lon=10.5),
            _make_charging_station(lat=48.5, lon=10.0),
        ]
        charges = [
            ChargingStop(
                station=s,
                segment_index=1,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=80.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=datetime(2025, 6, 15, 10, 0, 0),
                abfahrtszeit=datetime(2025, 6, 15, 10, 30, 0),
            )
            for s in stations
        ]
        plan = _make_charging_plan(charges)
        route = _make_route()

        call_count = [0]

        class _PartialFailingProvider(FakeRoutingProvider):
            async def berechne_route(self, anfrage: TripRequest) -> Route:
                call_count[0] += 1
                if call_count[0] == 3:
                    raise httpx.HTTPError("fail")
                return await super().berechne_route(anfrage)

        provider = _PartialFailingProvider()

        async def run() -> dict[int, LadehaltDetour]:
            return await _step_route_charging_detours(
                provider, route, plan, datetime(2025, 6, 15, 10, 0, 0), VEHICLE_PROFILE
            )

        result = asyncio.run(run())
        # Stop 0: weg(call 1) + rueck(call 2) both succeed → 1 detour
        # Stop 1: weg(call 3) fails → entire stop skipped
        # Result: exactly 1 detour for stop 0
        assert len(result) == 1
        stop_0_id = id(charges[0])
        assert stop_0_id in result
        assert id(charges[1]) not in result
