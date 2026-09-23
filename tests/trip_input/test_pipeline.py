"""Tests for `trip_input.pipeline._step_9_update_eta`.

Verifies that the ETA update step folds both regular charging-stop durations
(`ChargingPlan.ladehalte`) AND forced waypoint-wait durations
(`ChargingPlan.zwischenstopp_aufenthalte`) into per-segment elapsed time.
Without the latter, every segment after a mandatory waypoint wait (e.g. an
overnight stay, see `Waypoint.stay_duration`/`planned_departure`) would get
an ETA-based weather-query timestamp that ignores the wait entirely.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tripplanner.charging_infrastructure.models import ChargingStation, ConnectorType, StallType
from tripplanner.optimization.models import ChargingPlan, ChargingStop, ZwischenstoppAufenthalt
from tripplanner.routing.models import Coordinate, RouteSegment
from tripplanner.trip_input.pipeline import _step_9_update_eta

ABFAHRTSZEIT = datetime(2026, 8, 15, 8, 0, 0)


def _make_segment(index: int, laenge_m: float = 50_000.0) -> RouteSegment:
    """Creates a minimal `RouteSegment` for testing."""
    start: Coordinate = (52.0 + index * 0.1, 13.0)
    end: Coordinate = (52.0 + (index + 1) * 0.1, 13.0)
    return RouteSegment(
        segment_index=index,
        geometrie=[start, end],
        laenge_m=laenge_m,
        strassenklasse="MOTORWAY",
        bearing_deg=0.0,
    )


def _make_station(station_id: str = "station-1") -> ChargingStation:
    """Creates a minimal `ChargingStation` for testing."""
    return ChargingStation(
        station_id=station_id,
        name="Test Supercharger",
        coordinate=(52.0, 13.0),
        stalls={StallType.V3: 4},
        max_ladeleistung_kw=150.0,
        connector_types=[ConnectorType.CCS2],
        country="DE",
    )


def _empty_plan() -> ChargingPlan:
    return ChargingPlan(ladehalte=[], gesamtreisezeit_s=0)


class TestStep9UpdateEtaChargingStops:
    """Regular Supercharger stops (`ChargingPlan.ladehalte`) add charge time."""

    def test_charging_stop_duration_added_at_its_segment(self) -> None:
        segments = [_make_segment(i) for i in range(3)]
        segment_eta = [(seg, timedelta(hours=1)) for seg in segments]
        plan = ChargingPlan(
            ladehalte=[
                ChargingStop(
                    station=_make_station(),
                    segment_index=1,
                    arrival_soc_pct=20.0,
                    target_soc_pct=80.0,
                    geschaetzte_ladedauer_s=1800,
                    ankunftszeit=ABFAHRTSZEIT + timedelta(hours=1),
                    departure_time=ABFAHRTSZEIT + timedelta(hours=1, minutes=30),
                )
            ],
            gesamtreisezeit_s=0,
        )

        result = _step_9_update_eta(segment_eta, plan)

        assert result[0][1] == timedelta(hours=1)
        assert result[1][1] == timedelta(hours=1, minutes=30)
        assert result[2][1] == timedelta(hours=1)

    def test_no_charging_stops_leaves_eta_unchanged(self) -> None:
        segments = [_make_segment(i) for i in range(2)]
        segment_eta = [(seg, timedelta(minutes=45)) for seg in segments]

        result = _step_9_update_eta(segment_eta, _empty_plan())

        assert [d for _, d in result] == [timedelta(minutes=45), timedelta(minutes=45)]


class TestStep9UpdateEtaWaypointWaits:
    """Forced waypoint waits (`ChargingPlan.zwischenstopp_aufenthalte`) add wait time.

    Regression: without this, ETA (and therefore weather-query timestamps,
    see `fetch_weather_by_detail`) downstream of a mandatory waypoint wait
    (e.g. an overnight stay) would ignore the wait entirely.
    """

    def test_waypoint_wait_duration_added_after_its_segment(self) -> None:
        segments = [_make_segment(i) for i in range(3)]
        segment_eta = [(seg, timedelta(hours=1)) for seg in segments]
        arrival = ABFAHRTSZEIT + timedelta(hours=1)
        departure = arrival + timedelta(hours=10)  # overnight wait
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=0,
            zwischenstopp_aufenthalte=[
                ZwischenstoppAufenthalt(
                    coordinate=(52.1, 13.0),
                    segment_index=1,
                    ankunftszeit=arrival,
                    departure_time=departure,
                    arrival_soc_pct=50.0,
                    target_soc_pct=50.0,
                )
            ],
        )

        result = _step_9_update_eta(segment_eta, plan)

        assert result[0][1] == timedelta(hours=1)
        assert result[1][1] == timedelta(hours=11)  # 1h travel + 10h wait
        assert result[2][1] == timedelta(hours=1)

    def test_downstream_eta_timestamp_includes_the_wait(self) -> None:
        """The segment AFTER the wait must see the post-wait timestamp."""
        segments = [_make_segment(i) for i in range(3)]
        segment_eta = [(seg, timedelta(hours=1)) for seg in segments]
        arrival = ABFAHRTSZEIT + timedelta(hours=1)
        departure = arrival + timedelta(hours=10)
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=0,
            zwischenstopp_aufenthalte=[
                ZwischenstoppAufenthalt(
                    coordinate=(52.1, 13.0),
                    segment_index=1,
                    ankunftszeit=arrival,
                    departure_time=departure,
                    arrival_soc_pct=50.0,
                    target_soc_pct=50.0,
                )
            ],
        )

        result = _step_9_update_eta(segment_eta, plan)

        # ETA at the START of segment 2 = sum of durations of segments 0, 1.
        # The waypoint sits at segment 1's start (arrival == pre-wait ETA
        # there); the vehicle then waits, then still has to drive segment
        # 1's own original travel time to reach the start of segment 2.
        eta_at_segment_2 = ABFAHRTSZEIT + result[0][1] + result[1][1]
        assert eta_at_segment_2 == departure + timedelta(hours=1)

    def test_waypoint_wait_and_charging_stop_at_different_segments_both_apply(self) -> None:
        segments = [_make_segment(i) for i in range(3)]
        segment_eta = [(seg, timedelta(hours=1)) for seg in segments]
        plan = ChargingPlan(
            ladehalte=[
                ChargingStop(
                    station=_make_station(),
                    segment_index=0,
                    arrival_soc_pct=20.0,
                    target_soc_pct=80.0,
                    geschaetzte_ladedauer_s=1800,
                    ankunftszeit=ABFAHRTSZEIT,
                    departure_time=ABFAHRTSZEIT + timedelta(minutes=30),
                )
            ],
            gesamtreisezeit_s=0,
            zwischenstopp_aufenthalte=[
                ZwischenstoppAufenthalt(
                    coordinate=(52.2, 13.0),
                    segment_index=2,
                    ankunftszeit=ABFAHRTSZEIT + timedelta(hours=3),
                    departure_time=ABFAHRTSZEIT + timedelta(hours=6),
                    arrival_soc_pct=50.0,
                    target_soc_pct=50.0,
                )
            ],
        )

        result = _step_9_update_eta(segment_eta, plan)

        assert result[0][1] == timedelta(hours=1, minutes=30)
        assert result[1][1] == timedelta(hours=1)
        assert result[2][1] == timedelta(hours=4)  # 1h travel + 3h wait
