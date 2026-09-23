"""Unit-Tests für `GraphHopperRoutingProvider`: Path-Mapping und Detail-Extraktion.

Nutzt aufgezeichnete Fixture-Antworten statt Live-HTTP-Calls gegen GraphHopper.
"""

from __future__ import annotations

import polyline
import pytest

from tripplanner.routing.models import GraphHopperPath, GraphHopperResponse
from tripplanner.routing.providers import GraphHopperRoutingProvider
from tripplanner.trip_input.models import FerryExclusion, TripRequest


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
        assert first.steigung_rohdaten is None
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
        assert first.strassenname is None
        assert first.strassenref == "A 5"

    def test_normalizes_lowercase_road_class_to_uppercase(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_details: GraphHopperResponse,
    ) -> None:
        """GraphHopper liefert `road_class` klein geschrieben (z. B. "motorway") -
        wird auf Grossschreibung normalisiert, damit z. B.
        `providers_de_autobahn._extract_autobahn_ids`'s `!= "MOTORWAY"`-Vergleich
        funktioniert (live gegen den echten GraphHopper-Server verifiziert:
        `road_class` liefert dort tatsaechlich Kleinbuchstaben, nicht wie in
        dieser handgeschriebenen Fixture)."""
        path = graphhopper_response_with_details.paths[0]
        lowercase_path = path.model_copy(
            update={
                "details": {
                    **path.details,
                    "road_class": [[0, 1, "motorway"], [1, 2, "primary"]],
                }
            }
        )
        route = gh_provider._map_path_to_route(lowercase_path)
        assert route.segments[0].strassenklasse == "MOTORWAY"
        assert route.segments[1].strassenklasse == "PRIMARY"

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


class TestMapPathToRouteViaPointIndices:
    """Tests für die Extraktion von `Route.via_point_indices` aus GraphHoppers
    'reached via point'-Instruktionen (sign=5) - siehe Docstring dort."""

    def test_extracts_via_point_index_from_reached_via_instruction(
        self, gh_provider: GraphHopperRoutingProvider
    ) -> None:
        """Eine sign=5-Instruktion liefert den exakten Koordinaten-Index als
        Segment-Index, unabhängig von geometrischer Nähe anderer Punkte."""
        coords = [(52.0 + i * 0.01, 13.0) for i in range(10)]
        path = GraphHopperPath(
            distance=1000.0,
            time=60_000,
            points=polyline.encode(coords),
            points_encoded=True,
            instructions=[
                {"sign": 0, "interval": [0, 4], "text": "start", "distance": 400, "time": 20000},
                {
                    "sign": 5,
                    "interval": [4, 4],
                    "text": "Waypoint 1",
                    "distance": 0,
                    "time": 0,
                },
                {"sign": 4, "interval": [4, 9], "text": "arrive", "distance": 500, "time": 30000},
            ],
        )

        route = gh_provider._map_path_to_route(path)

        assert route.via_point_indices == [4]

    def test_no_via_point_instructions_yields_empty_list(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_basic: GraphHopperResponse,
    ) -> None:
        """Eine Antwort ohne Zwischenstopps (keine sign=5-Instruktion) liefert
        eine leere `via_point_indices`-Liste statt eines Fehlers."""
        route = gh_provider._map_path_to_route(graphhopper_response_basic.paths[0])

        assert route.via_point_indices == []


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


class TestBuildCustomModel:
    """Tests für GraphHopperRoutingProvider._build_custom_model()."""

    def test_no_avoidance_and_no_speed_profile_returns_none(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Ohne Fährvermeidung und ohne use_custom_model wird kein custom_model gebaut."""
        assert gh_provider._build_custom_model(trip_request) is None

    def test_avoid_all_ferries_adds_ferry_priority_rule(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """avoid_all_ferries=True fügt eine road_environment==FERRY Priority-Regel hinzu."""
        anfrage = trip_request.model_copy(update={"avoid_all_ferries": True})

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is not None
        assert {"if": "road_environment == FERRY", "multiply_by": 0.0} in custom_model["priority"]
        assert "areas" not in custom_model

    def test_avoided_ferries_adds_area_and_priority_rule(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Jede vermiedene Fähre erzeugt eine GeoJSON-Area und eine in_<id> Priority-Regel."""
        ausschluss = FerryExclusion(
            name="Rødby (DK) - Puttgarden (D)",
            bbox_sw=(54.50, 11.22),
            bbox_ne=(54.66, 11.36),
        )
        anfrage = trip_request.model_copy(update={"avoided_ferries": [ausschluss]})

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is not None
        assert {
            "if": "in_faehre_0 && road_environment == FERRY",
            "multiply_by": 0.0,
        } in custom_model["priority"]
        area = custom_model["areas"]["faehre_0"]
        assert area["type"] == "Feature"
        assert area["geometry"]["type"] == "Polygon"
        ring = area["geometry"]["coordinates"][0]
        assert ring[0] == [11.22, 54.50]  # [lon, lat] Reihenfolge (GeoJSON)
        assert ring[0] == ring[-1]  # geschlossener Ring

    def test_use_custom_model_and_ferry_avoidance_combined(self, trip_request: TripRequest) -> None:
        """use_custom_model=True und Fährvermeidung wirken gemeinsam auf dieselbe priority-Liste."""
        provider = GraphHopperRoutingProvider(client=None, use_custom_model=True)  # type: ignore[arg-type]
        anfrage = trip_request.model_copy(update={"avoid_all_ferries": True})

        custom_model = provider._build_custom_model(anfrage)

        assert custom_model is not None
        assert custom_model["distance_influence"] == 0.0
        assert {"if": "road_class == MOTORWAY", "multiply_by": 1.0} in custom_model["priority"]
        assert {"if": "road_environment == FERRY", "multiply_by": 0.0} in custom_model["priority"]

    def test_two_simultaneous_avoided_ferries_produces_two_areas_and_two_priority_rules(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Zwei vermiedene Fähren erzeugen zwei GeoJSON-Areas und zwei priority-Regeln."""
        exclusion0 = FerryExclusion(
            name="Fähre A",
            bbox_sw=(54.50, 11.22),
            bbox_ne=(54.66, 11.36),
        )
        exclusion1 = FerryExclusion(
            name="Fähre B",
            bbox_sw=(54.20, 9.80),
            bbox_ne=(54.30, 9.90),
        )
        anfrage = trip_request.model_copy(update={"avoided_ferries": [exclusion0, exclusion1]})

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is not None
        # Two areas with stable IDs
        assert "faehre_0" in custom_model["areas"]
        assert "faehre_1" in custom_model["areas"]
        # Two priority rules conjoined with road_environment == FERRY
        assert {
            "if": "in_faehre_0 && road_environment == FERRY",
            "multiply_by": 0.0,
        } in custom_model["priority"]
        assert {
            "if": "in_faehre_1 && road_environment == FERRY",
            "multiply_by": 0.0,
        } in custom_model["priority"]

    @pytest.mark.parametrize(
        ("level", "erwarteter_multiplikator"),
        [("low", 1.1), ("medium", 1.2), ("high", 1.3)],
    )
    def test_prefer_motorways_adds_priority_boost_per_level(
        self,
        gh_provider: GraphHopperRoutingProvider,
        trip_request: TripRequest,
        level: str,
        erwarteter_multiplikator: float,
    ) -> None:
        """highway_preference in {low, medium, high} fügt die passende road_class==MOTORWAY
        Priority-Regel (1.1/1.2/1.3) hinzu."""
        anfrage = trip_request.model_copy(update={"highway_preference": level})

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is not None
        rule = next(r for r in custom_model["priority"] if r["if"] == "road_class == MOTORWAY")
        assert rule["multiply_by"] == erwarteter_multiplikator
        assert "areas" not in custom_model

    def test_prefer_motorways_off_adds_no_priority_rule(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """highway_preference='off' (Standard) fügt keine MOTORWAY-Priority-Regel hinzu."""
        anfrage = trip_request.model_copy(update={"highway_preference": "off"})

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is None

    def test_prefer_motorways_and_avoid_ferries_combined(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """highway_preference und avoid_all_ferries wirken gemeinsam, nicht exklusiv."""
        anfrage = trip_request.model_copy(
            update={"highway_preference": "high", "avoid_all_ferries": True}
        )

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is not None
        assert {"if": "road_environment == FERRY", "multiply_by": 0.0} in custom_model["priority"]
        assert any(
            r["if"] == "road_class == MOTORWAY" and r["multiply_by"] > 1.0
            for r in custom_model["priority"]
        )
