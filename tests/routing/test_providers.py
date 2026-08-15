"""Unit-Tests für `GraphHopperRoutingProvider`: Path-Mapping und Detail-Extraktion.

Nutzt aufgezeichnete Fixture-Antworten statt Live-HTTP-Calls gegen GraphHopper.
"""

from __future__ import annotations

import pytest

from tripplanner.routing.models import GraphHopperResponse
from tripplanner.routing.providers import GraphHopperRoutingProvider


@pytest.fixture
def gh_provider() -> GraphHopperRoutingProvider:
    """GraphHopperRoutingProvider ohne echten Client (nur zum Testen von _map_path_to_route)."""
    return GraphHopperRoutingProvider(client=None)  # type: ignore[arg-type]


class TestMapPathToRoute:
    """Tests für GraphHopperRoutingProvider._map_path_to_route()."""

    def test_maps_basic_response_without_details(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_basic: GraphHopperResponse,
    ) -> None:
        """Ohne Details: strassenklasse='OTHER', tempolimit/steigung/oberflaeche=None."""
        route = gh_provider._map_path_to_route(graphhopper_response_basic.paths[0])

        assert route.gesamtlaenge_m > 0
        assert len(route.segments) >= 1
        first = route.segments[0]
        assert first.strassenklasse == "OTHER"
        assert first.tempolimit_kmh is None
        assert first.oberflaeche is None
        assert first.road_environment is None
        assert first.strassenname is None

    def test_maps_response_with_details(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_details: GraphHopperResponse,
    ) -> None:
        """Mit Details: erstes Segment übernimmt road_class/max_speed/average_slope/surface."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_details.paths[0])

        first = route.segments[0]
        assert first.strassenklasse == "MOTORWAY"
        assert first.tempolimit_kmh == 130
        assert first.steigung_rohdaten == pytest.approx(1.5)
        assert first.oberflaeche == "asphalt"

    def test_all_segments_have_bearing_deg(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_details: GraphHopperResponse,
    ) -> None:
        """Jedes gemappte Segment hat ein berechnetes bearing_deg in [0, 360)."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_details.paths[0])

        for segment in route.segments:
            assert 0.0 <= segment.bearing_deg < 360.0

    def test_segment_length_computed_via_haversine(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_basic: GraphHopperResponse,
    ) -> None:
        """Segmentlängen sind positiv und summieren sich zur Gesamtlänge."""
        route = gh_provider._map_path_to_route(graphhopper_response_basic.paths[0])

        assert all(s.laenge_m > 0 for s in route.segments)
        assert route.gesamtlaenge_m == pytest.approx(sum(s.laenge_m for s in route.segments))


class TestMapPathToRouteFerryDetails:
    """Tests für road_environment/strassenname-Mapping (Grundlage der Fährerkennung)."""

    def test_ferry_segment_has_uppercased_road_environment(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_ferry: GraphHopperResponse,
    ) -> None:
        """Das Fährsegment hat road_environment='FERRY' (uppercased aus GraphHopper 'ferry')."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_ferry.paths[0])

        ferry_segments = [s for s in route.segments if s.road_environment == "FERRY"]
        assert len(ferry_segments) > 0

    def test_ferry_segment_has_strassenname_from_street_name(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_ferry: GraphHopperResponse,
    ) -> None:
        """Das Fährsegment übernimmt den Namen aus dem street_name Path-Detail."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_ferry.paths[0])

        ferry_segment = next(s for s in route.segments if s.road_environment == "FERRY")
        assert ferry_segment.strassenname == "Rødby (DK) - Puttgarden (D)"

    def test_road_segment_has_none_strassenname_when_street_name_null(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_ferry: GraphHopperResponse,
    ) -> None:
        """Ein Segment mit street_name=null im JSON wird zu strassenname=None."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_ferry.paths[0])

        assert route.segments[0].strassenname is None
        assert route.segments[0].road_environment == "ROAD"


class TestNormalizeMaxSpeed:
    """Tests für GraphHopperRoutingProvider._normalize_max_speed()."""

    def test_none_stays_none(self, gh_provider: GraphHopperRoutingProvider) -> None:
        """None (nicht verfügbar) bleibt None."""
        assert gh_provider._normalize_max_speed(None) is None

    def test_zero_maps_to_none(self, gh_provider: GraphHopperRoutingProvider) -> None:
        """0 (kein Schild, z. B. Spielstraße) wird zu None."""
        assert gh_provider._normalize_max_speed(0) is None

    def test_negative_one_maps_to_none(self, gh_provider: GraphHopperRoutingProvider) -> None:
        """-1 (nicht bekannt) wird zu None."""
        assert gh_provider._normalize_max_speed(-1) is None

    def test_positive_value_returned_as_int(self, gh_provider: GraphHopperRoutingProvider) -> None:
        """Positive Werte werden als int (km/h) zurückgegeben."""
        assert gh_provider._normalize_max_speed(130.0) == 130

    def test_string_value_coerced_to_int(self, gh_provider: GraphHopperRoutingProvider) -> None:
        """String-Werte aus dem GraphHopper-JSON werden korrekt zu int konvertiert."""
        assert gh_provider._normalize_max_speed("100") == 100
