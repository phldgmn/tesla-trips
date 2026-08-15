"""Unit-Tests für `FakeRoutingProvider` und die Route/RouteSegment-Datenmodelle."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tripplanner.routing.models import FaehrSegment, Route, RouteSegment
from tripplanner.routing.providers import FakeRoutingProvider
from tripplanner.trip_input.models import TripRequest


class TestRouteSegmentModel:
    """Tests für das RouteSegment-Pydantic-Modell."""

    def test_route_segment_requires_bearing_deg(self) -> None:
        """bearing_deg ist ein Pflichtfeld (kein Default)."""
        with pytest.raises(ValueError, match="bearing_deg"):
            RouteSegment(
                segment_index=0,
                geometrie=[(52.5, 13.4), (52.6, 13.5)],
                laenge_m=1000.0,
                strassenklasse="PRIMARY",
            )

    def test_route_segment_bearing_deg_out_of_range_rejected(self) -> None:
        """bearing_deg muss in [0, 360) liegen."""
        with pytest.raises(ValueError, match="less than 360"):
            RouteSegment(
                segment_index=0,
                geometrie=[(52.5, 13.4), (52.6, 13.5)],
                laenge_m=1000.0,
                strassenklasse="PRIMARY",
                bearing_deg=360.0,
            )

    def test_route_segment_oberflaeche_optional(self) -> None:
        """oberflaeche ist optional (None wenn GraphHopper es nicht liefert)."""
        segment = RouteSegment(
            segment_index=0,
            geometrie=[(52.5, 13.4), (52.6, 13.5)],
            laenge_m=1000.0,
            strassenklasse="PRIMARY",
            bearing_deg=45.0,
        )
        assert segment.oberflaeche is None

    def test_route_segment_road_environment_optional(self) -> None:
        """road_environment ist optional (None wenn GraphHopper es nicht liefert)."""
        segment = RouteSegment(
            segment_index=0,
            geometrie=[(52.5, 13.4), (52.6, 13.5)],
            laenge_m=1000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=45.0,
        )
        assert segment.road_environment is None

    def test_route_segment_strassenname_optional(self) -> None:
        """strassenname ist optional (None wenn GraphHopper es nicht liefert)."""
        segment = RouteSegment(
            segment_index=0,
            geometrie=[(52.5, 13.4), (52.6, 13.5)],
            laenge_m=1000.0,
            strassenklasse="MOTORWAY",
            bearing_deg=45.0,
        )
        assert segment.strassenname is None


class TestFaehrSegmentModel:
    """Tests für das FaehrSegment-Pydantic-Modell."""

    def test_faehr_segment_requires_all_fields(self) -> None:
        """FaehrSegment benötigt name, laenge_m, bbox_sw, bbox_no, segment_index_start/end."""
        segment = FaehrSegment(
            name="Rødby (DK) - Puttgarden (D)",
            laenge_m=22000.0,
            bbox_sw=(54.50, 11.22),
            bbox_no=(54.66, 11.36),
            segment_index_start=3,
            segment_index_end=7,
        )
        assert segment.name == "Rødby (DK) - Puttgarden (D)"
        assert segment.laenge_m == 22000.0
        assert segment.bbox_sw == (54.50, 11.22)
        assert segment.bbox_no == (54.66, 11.36)
        assert segment.segment_index_start == 3
        assert segment.segment_index_end == 7
        assert segment.abfahrt is None
        assert segment.ankunft is None

    def test_faehr_segment_rejects_negative_laenge(self) -> None:
        """laenge_m muss >= 0 sein."""
        with pytest.raises(ValidationError):
            FaehrSegment(
                name="X",
                laenge_m=-1.0,
                bbox_sw=(0.0, 0.0),
                bbox_no=(1.0, 1.0),
                segment_index_start=0,
                segment_index_end=1,
            )


class TestFakeRoutingProvider:
    """Tests für die Fake-Implementierung von RoutingProvider."""

    @pytest.fixture
    def provider(self) -> FakeRoutingProvider:
        return FakeRoutingProvider()

    async def test_berechne_route_returns_valid_route(
        self, provider: FakeRoutingProvider, trip_request: TripRequest
    ) -> None:
        """berechne_route liefert eine Route mit positiver Gesamtlänge und >=1 Segment."""
        route = await provider.berechne_route(trip_request)

        assert isinstance(route, Route)
        assert route.gesamtlaenge_m > 0
        assert len(route.segments) >= 1

    async def test_berechne_route_segment_has_bearing(
        self, provider: FakeRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Jedes erzeugte Segment hat ein gültiges bearing_deg in [0, 360)."""
        route = await provider.berechne_route(trip_request)

        for segment in route.segments:
            assert 0.0 <= segment.bearing_deg < 360.0

    async def test_berechne_route_start_end_match_request(
        self, provider: FakeRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Die Route beginnt am Start und endet am Ziel der Anfrage."""
        route = await provider.berechne_route(trip_request)

        assert route.geometrie[0] == trip_request.start
        assert route.geometrie[-1] == trip_request.ziel

    async def test_berechne_route_mit_waypoints_creates_segment_per_leg(
        self, provider: FakeRoutingProvider
    ) -> None:
        """Bei N Zwischenpunkten entstehen mindestens N+1 Segmente (ein Segment pro Teilstrecke)."""
        start = (52.5200, 13.4050)
        ziel = (53.5511, 9.9937)
        zwischenstopps: list[tuple[tuple[float, float], timedelta | None]] = [
            ((52.3759, 9.7320), timedelta(minutes=30)),
        ]

        route = await provider.berechne_route_mit_waypoints(start, ziel, zwischenstopps)

        assert len(route.segments) >= len(zwischenstopps) + 1

    async def test_berechne_route_with_waypoints_request_produces_more_segments(
        self,
        provider: FakeRoutingProvider,
        trip_request: TripRequest,
        trip_request_with_waypoints: TripRequest,
    ) -> None:
        """Eine TripRequest mit Zwischenstopp erzeugt mehr Segmente als ohne."""
        route_direct = await provider.berechne_route(trip_request)
        route_direct_should_have_one_segment = len(route_direct.segments)

        route_with_waypoint = await provider.berechne_route_mit_waypoints(
            trip_request_with_waypoints.start,
            trip_request_with_waypoints.ziel,
            [
                (wp.koordinate, wp.aufenthaltsdauer)
                for wp in trip_request_with_waypoints.zwischenstopps
            ],
        )

        assert len(route_with_waypoint.segments) > route_direct_should_have_one_segment

    async def test_berechne_route_bbox_contains_start_and_end(
        self, provider: FakeRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Die Bounding Box umschließt Start- und Zielkoordinate."""
        route = await provider.berechne_route(trip_request)

        assert route.bbox is not None
        min_lat, min_lon, max_lat, max_lon = route.bbox
        for lat, lon in (trip_request.start, trip_request.ziel):
            assert min_lat <= lat <= max_lat
            assert min_lon <= lon <= max_lon
