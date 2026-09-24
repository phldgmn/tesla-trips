"""Tests für construction-Provider: fetch_construction_zones und FakeProvider."""

import pytest
from pydantic import ValidationError
from tripplanner.construction.models import (
    ClosureType,
    ConstructionZone,
    Land,
)
from tripplanner.construction.providers import FakeConstructionProvider
from tripplanner.routing.models import Route, RouteSegment


@pytest.fixture
def test_route() -> Route:
    """Erstelle Test-Route mit einem Segment."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.5200, 13.4050), (52.5210, 13.4060)],
        length_m=150.0,
        strassenklasse="PRIMARY",
        speed_limit_kmh=100,
        bearing_deg=35.0,
    )
    return Route(
        segments=[segment],
        gesamtlaenge_m=150.0,
        geometrie=[(52.5200, 13.4050), (52.5210, 13.4060)],
    )


@pytest.fixture
def fake_provider() -> FakeConstructionProvider:
    """Erstelle FakeProvider mit Test-construction_zones."""
    zones = [
        ConstructionZone(
            betroffene_segmente=[0],
            speed_limit_kmh=80,
            closure_type=ClosureType.TEMPORARY_SPEED_LIMIT,
            umleitungshinweis="construction_zone, Vorsicht!",
            land=Land.DE,
            gueltig_von="2024-03-20T21:01:00+00:00",
            gueltig_bis="2024-03-21T03:00:00+00:00",
        ),
        ConstructionZone(
            betroffene_segmente=[],
            speed_limit_kmh=60,
            closure_type=ClosureType.LANE_CLOSED,
            umleitungshinweis=None,
            land=Land.DK,
            gueltig_von="2024-03-17T06:00:00+00:00",
            gueltig_bis=None,
        ),
    ]
    return FakeConstructionProvider(zones)


@pytest.mark.asyncio
async def test_fake_provider_returns_test_zones(
    fake_provider: FakeConstructionProvider,
) -> None:
    """FakeProvider gibt vorkonfigurierte Test-construction_zones zurück."""
    route = Route(
        segments=[],
        gesamtlaenge_m=1.0,
        geometrie=[],
    )

    zones = await fake_provider.fetch_construction_zones(route, [Land.DE, Land.DK])

    assert len(zones) == 2


@pytest.mark.asyncio
async def test_fake_provider_filters_by_land(
    fake_provider: FakeConstructionProvider,
    test_route: Route,
) -> None:
    """FakeProvider filtert nach Ländern."""
    zones = await fake_provider.fetch_construction_zones(test_route, [Land.DE])

    assert len(zones) == 1
    assert zones[0].land == Land.DE


@pytest.mark.asyncio
async def test_fake_provider_no_filter(
    fake_provider: FakeConstructionProvider,
    test_route: Route,
) -> None:
    """FakeProvider gibt alle construction_zones zurück wenn kein Land-Filter."""
    zones = await fake_provider.fetch_construction_zones(test_route, [])

    assert len(zones) == 2


def test_construction_zone_tempolimit_required_for_partial_closure() -> None:
    """Validierung: speed_limit_kmh required für PARTIALLY_CLOSED."""
    with pytest.raises(ValidationError) as exc_info:
        ConstructionZone(
            betroffene_segmente=[0],
            speed_limit_kmh=None,
            closure_type=ClosureType.PARTIALLY_CLOSED,
            umleitungshinweis="Test",
            land=Land.DE,
            gueltig_von="2024-03-20T21:01:00+00:00",
            gueltig_bis="2024-03-21T03:00:00+00:00",
        )

    assert "speed_limit_kmh muss gesetzt sein" in str(exc_info.value)


def test_construction_zone_tempolimit_required_for_lane_closed() -> None:
    """Validierung: speed_limit_kmh required für LANE_CLOSED."""
    with pytest.raises(ValidationError) as exc_info:
        ConstructionZone(
            betroffene_segmente=[0],
            speed_limit_kmh=None,
            closure_type=ClosureType.LANE_CLOSED,
            umleitungshinweis="Test",
            land=Land.DE,
            gueltig_von="2024-03-20T21:01:00+00:00",
            gueltig_bis="2024-03-21T03:00:00+00:00",
        )

    assert "speed_limit_kmh muss gesetzt sein" in str(exc_info.value)


def test_construction_zone_tempolimit_required_for_reduced_lanes() -> None:
    """Validierung: speed_limit_kmh required für REDUCED_LANES."""
    with pytest.raises(ValidationError) as exc_info:
        ConstructionZone(
            betroffene_segmente=[0],
            speed_limit_kmh=None,
            closure_type=ClosureType.REDUCED_LANES,
            umleitungshinweis="Test",
            land=Land.DE,
            gueltig_von="2024-03-20T21:01:00+00:00",
            gueltig_bis="2024-03-21T03:00:00+00:00",
        )

    assert "speed_limit_kmh muss gesetzt sein" in str(exc_info.value)


def test_construction_zone_tempolimit_not_required_for_full_closure() -> None:
    """Validierung: speed_limit_kmh nicht required für FULLY_CLOSED."""
    zone = ConstructionZone(
        betroffene_segmente=[0],
        speed_limit_kmh=None,
        closure_type=ClosureType.FULLY_CLOSED,
        umleitungshinweis="Test",
        land=Land.DE,
        gueltig_von="2024-03-20T21:01:00+00:00",
        gueltig_bis="2024-03-21T03:00:00+00:00",
    )
    assert zone.closure_type == ClosureType.FULLY_CLOSED
    assert zone.speed_limit_kmh is None


def test_construction_zone_tempolimit_not_required_for_temp_speed_limit_with_value() -> None:
    """Validierung: speed_limit_kmh required für TEMPORARY_SPEED_LIMIT (mit Wert)."""
    zone = ConstructionZone(
        betroffene_segmente=[0],
        speed_limit_kmh=80,
        closure_type=ClosureType.TEMPORARY_SPEED_LIMIT,
        umleitungshinweis="Test",
        land=Land.DE,
        gueltig_von="2024-03-20T21:01:00+00:00",
        gueltig_bis="2024-03-21T03:00:00+00:00",
    )
    assert zone.closure_type == ClosureType.TEMPORARY_SPEED_LIMIT
    assert zone.speed_limit_kmh == 80


def test_construction_zone_validation_max_tempolimit() -> None:
    """Validierung: speed_limit_kmh darf nicht > 200 sein."""
    with pytest.raises(ValidationError) as exc_info:
        ConstructionZone(
            betroffene_segmente=[0],
            speed_limit_kmh=201,
            closure_type=ClosureType.TEMPORARY_SPEED_LIMIT,
            umleitungshinweis="Test",
            land=Land.DE,
            gueltig_von="2024-03-20T21:01:00+00:00",
            gueltig_bis="2024-03-21T03:00:00+00:00",
        )

    assert "201" in str(exc_info.value)


def test_construction_zone_validation_min_tempolimit() -> None:
    """Validierung: speed_limit_kmh darf nicht < 0 sein."""
    with pytest.raises(ValidationError) as exc_info:
        ConstructionZone(
            betroffene_segmente=[0],
            speed_limit_kmh=-1,
            closure_type=ClosureType.TEMPORARY_SPEED_LIMIT,
            umleitungshinweis="Test",
            land=Land.DE,
            gueltig_von="2024-03-20T21:01:00+00:00",
            gueltig_bis="2024-03-21T03:00:00+00:00",
        )

    assert "-1" in str(exc_info.value)
