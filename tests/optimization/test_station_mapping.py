"""Tests for `optimization.station_mapping`."""

from __future__ import annotations

from tripplanner.charging_infrastructure.models import ChargingStation, ConnectorType, StallType
from tripplanner.optimization.station_mapping import (
    map_station_to_segment,
    map_stations_to_segments,
)
from tripplanner.routing.models import RouteSegment


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


def _make_segments() -> list[RouteSegment]:
    return [
        RouteSegment(
            segment_index=0,
            geometrie=[(52.0, 13.0), (52.1, 13.0)],
            laenge_m=11_000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        ),
        RouteSegment(
            segment_index=1,
            geometrie=[(52.1, 13.0), (52.2, 13.0)],
            laenge_m=11_000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=0.0,
        ),
    ]


def test_map_station_to_segment_picks_nearest() -> None:
    segments = _make_segments()
    station = _make_station(52.19, 13.0)  # closest to segment 1's endpoint

    seg_idx, dist_m = map_station_to_segment(station, segments)

    assert seg_idx == 1
    assert dist_m < 2000.0


def test_map_stations_to_segments_groups_by_segment() -> None:
    segments = _make_segments()
    near_seg0 = _make_station(52.0, 13.0, station_id="a")
    near_seg1 = _make_station(52.2, 13.0, station_id="b")

    result = map_stations_to_segments([near_seg0, near_seg1], segments)

    assert result[0][0][0].station_id == "a"
    assert result[1][0][0].station_id == "b"
