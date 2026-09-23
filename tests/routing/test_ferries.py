"""Unit-Tests für `tripplanner.routing.ferries.detect_ferries()`."""

from __future__ import annotations

import pytest

from tripplanner.routing.ferries import FERRY_BUFFER_DEG, detect_ferries
from tripplanner.routing.models import Route, RouteSegment


def _segment(
    index: int,
    start: tuple[float, float],
    end: tuple[float, float],
    road_environment: str | None,
    strassenname: str | None = None,
) -> RouteSegment:
    """Baut ein minimales RouteSegment für Fähr-Erkennungstests."""
    return RouteSegment(
        segment_index=index,
        geometrie=[start, end],
        laenge_m=1000.0,
        strassenklasse="OTHER",
        road_environment=road_environment,
        strassenname=strassenname,
        bearing_deg=0.0,
    )


class TestErkenneFaehren:
    """Tests für detect_ferries()."""

    def test_no_ferry_segments_returns_empty_list(self) -> None:
        """Eine Route ohne FERRY-Segmente liefert eine leere Liste."""
        route = Route(
            segments=[_segment(0, (54.0, 11.0), (54.1, 11.1), "ROAD")],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1)],
        )
        assert detect_ferries(route) == []

    def test_missing_road_environment_returns_empty_list(self) -> None:
        """Segmente ohne road_environment (z. B. FakeRoutingProvider) werden ignoriert."""
        route = Route(
            segments=[_segment(0, (54.0, 11.0), (54.1, 11.1), None)],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1)],
        )
        assert detect_ferries(route) == []

    def test_single_contiguous_ferry_run_grouped_into_one_segment(self) -> None:
        """Ein zusammenhängender FERRY-Lauf ergibt genau ein FerrySegment mit summierter Länge."""
        route = Route(
            segments=[
                _segment(0, (54.50, 11.22), (54.55, 11.25), "ROAD"),
                _segment(1, (54.55, 11.25), (54.60, 11.30), "FERRY", "Rødby (DK) - Puttgarden (D)"),
                _segment(2, (54.60, 11.30), (54.65, 11.35), "FERRY", "Rødby (DK) - Puttgarden (D)"),
                _segment(3, (54.65, 11.35), (54.70, 11.40), "ROAD"),
            ],
            gesamtlaenge_m=4000.0,
            geometrie=[
                (54.50, 11.22),
                (54.55, 11.25),
                (54.60, 11.30),
                (54.65, 11.35),
                (54.70, 11.40),
            ],
        )

        ferries = detect_ferries(route)

        assert len(ferries) == 1
        assert ferries[0].name == "Rødby (DK) - Puttgarden (D)"
        assert ferries[0].laenge_m == 2000.0

    def test_ferry_bbox_buffered_around_segment_geometry(self) -> None:
        """Die Bounding Box umschließt die Fährgeometrie gepuffert um FERRY_BUFFER_DEG."""
        route = Route(
            segments=[_segment(0, (54.50, 11.22), (54.60, 11.30), "FERRY", "Testfähre")],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.50, 11.22), (54.60, 11.30)],
        )

        ferries = detect_ferries(route)

        assert ferries[0].bbox_sw == pytest.approx(
            (54.50 - FERRY_BUFFER_DEG, 11.22 - FERRY_BUFFER_DEG)
        )
        assert ferries[0].bbox_ne == pytest.approx(
            (54.60 + FERRY_BUFFER_DEG, 11.30 + FERRY_BUFFER_DEG)
        )

    def test_ferry_without_strassenname_falls_back_to_default_name(self) -> None:
        """Fehlt strassenname (kein street_name von GraphHopper), wird ein
        Fallback-Name verwendet."""
        route = Route(
            segments=[_segment(0, (54.50, 11.22), (54.60, 11.30), "FERRY", None)],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.50, 11.22), (54.60, 11.30)],
        )

        ferries = detect_ferries(route)

        assert ferries[0].name == "Unnamed ferry"

    def test_two_disjoint_ferry_runs_produce_two_segments(self) -> None:
        """Zwei durch ein ROAD-Segment getrennte FERRY-Läufe ergeben zwei FaehrSegmente."""
        route = Route(
            segments=[
                _segment(0, (54.0, 11.0), (54.1, 11.1), "FERRY", "Fähre A"),
                _segment(1, (54.1, 11.1), (54.2, 11.2), "ROAD"),
                _segment(2, (54.2, 11.2), (54.3, 11.3), "FERRY", "Fähre B"),
            ],
            gesamtlaenge_m=3000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1), (54.2, 11.2), (54.3, 11.3)],
        )

        ferries = detect_ferries(route)

        assert [f.name for f in ferries] == ["Fähre A", "Fähre B"]

    def test_ferry_run_extending_to_end_of_route_is_captured(self) -> None:
        """Ein FERRY-Lauf, der bis zum letzten Segment reicht, wird nicht verworfen."""
        route = Route(
            segments=[
                _segment(0, (54.0, 11.0), (54.1, 11.1), "ROAD"),
                _segment(1, (54.1, 11.1), (54.2, 11.2), "FERRY", "Fähre am Ende"),
            ],
            gesamtlaenge_m=2000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1), (54.2, 11.2)],
        )

        ferries = detect_ferries(route)

        assert len(ferries) == 1
        assert ferries[0].name == "Fähre am Ende"
