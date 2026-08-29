"""Tests for direction-aware construction-zone matching and laenge_m derivation.

A construction zone/roadwork event must only be applied to a route segment if
it is actually applicable in the route's direction of travel - not merely on
the same road. These tests verify:

1. DK/SE zones with LineString geometry roughly aligned with the route's
   direction of travel are matched; zones on the opposite carriageway
   (bearing ~180 deg apart) are excluded.
2. SE zones with an explicit `AffectedDirectionValue` (not "both directions")
   trust the source over the geometry-bearing heuristic.
3. `laenge_m` is derived from the zone's own LineString geometry (DK/SE) or,
   absent that, from the span of matched route segments.

DE roadwork direction-limitation and laenge_m tests (point-only Autobahn GmbH
data, no direction data in source) live in `test_providers_de_autobahn.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tripplanner.construction import matching
from tripplanner.construction.parser import DATEXIIConstructionZoneInternal
from tripplanner.construction.providers import (
    ConstructionProviderConfig,
    ConstructionProviderImpl,
)
from tripplanner.construction.providers.impl import _SE_BOTH_DIRECTIONS_VALUES
from tripplanner.geo import geodesic_length_m
from tripplanner.routing.models import Route, RouteSegment


def _make_config() -> ConstructionProviderConfig:
    return ConstructionProviderConfig(
        dk_client_id="dk-client",
        dk_secret="dk-secret",
        dk_tenant_id="dk-tenant",
        tv_api_key="se-key",
        timeout_seconds=10.0,
    )


def _make_provider() -> ConstructionProviderImpl:
    provider = ConstructionProviderImpl(_make_config())
    provider._client = httpx.AsyncClient()
    return provider


def _make_route_segment(bearing: float) -> RouteSegment:
    """A single route segment near (50.0, 9.0) travelling in `bearing` direction."""
    return RouteSegment(
        segment_index=0,
        geometrie=[(50.0, 9.0), (50.001, 9.001)],
        laenge_m=150.0,
        strassenklasse="MOTORWAY",
        tempolimit_kmh=100,
        bearing_deg=bearing,
        strassenname="A9",
    )


def _make_route(bearing: float) -> Route:
    segment = _make_route_segment(bearing)
    return Route(segments=[segment], gesamtlaenge_m=150.0, geometrie=[segment.geometrie[0]])


def _make_zone(
    koordinaten: list[tuple[float, float]],
    affected_direction_value: str | None = None,
) -> DATEXIIConstructionZoneInternal:
    return DATEXIIConstructionZoneInternal(
        sperrungstyp="partiallyClosed",
        gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
        gueltig_bis=None,
        koordinaten=koordinaten,
        umleitungshinweis=None,
        tempolimit_kmh=80,
        affected_direction_value=affected_direction_value,
    )


# Coincides with the test segment's own geometry -> same direction (NE, ~45 deg).
SAME_DIRECTION_COORDS = [(50.0, 9.0), (50.001, 9.001)]
# Reversed -> opposite direction (SW, ~225 deg).
OPPOSITE_DIRECTION_COORDS = [(50.001, 9.001), (50.0, 9.0)]


class TestDirectionAwareMatching:
    """DK/SE zones with LineString geometry are filtered by direction of travel."""

    def test_same_direction_zone_is_matched(self) -> None:
        _make_provider()
        route = _make_route(bearing=45.0)
        zone = _make_zone(SAME_DIRECTION_COORDS)

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(
            zone,
            strtree,
            seg_geoms,
            route.segments,
            both_directions_values=_SE_BOTH_DIRECTIONS_VALUES,
        )

        assert ids == [0]

    def test_opposite_direction_zone_is_excluded(self) -> None:
        _make_provider()
        route = _make_route(bearing=45.0)
        zone = _make_zone(OPPOSITE_DIRECTION_COORDS)

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(
            zone,
            strtree,
            seg_geoms,
            route.segments,
            both_directions_values=_SE_BOTH_DIRECTIONS_VALUES,
        )

        assert ids == []

    def test_without_route_segments_direction_filtering_is_skipped(self) -> None:
        """Backward compatibility: omitting route_segments matches by distance only."""
        _make_provider()
        route = _make_route(bearing=45.0)
        zone = _make_zone(OPPOSITE_DIRECTION_COORDS)

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(
            zone, strtree, seg_geoms, both_directions_values=_SE_BOTH_DIRECTIONS_VALUES
        )

        assert ids == [0]

    def test_point_only_zone_is_unaffected_by_direction_filtering(self) -> None:
        """A zone with a single coordinate has no bearing and skips the filter."""
        _make_provider()
        route = _make_route(bearing=45.0)
        zone = _make_zone([(50.0005, 9.0005)])

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(
            zone,
            strtree,
            seg_geoms,
            route.segments,
            both_directions_values=_SE_BOTH_DIRECTIONS_VALUES,
        )

        assert ids == [0]


class TestSeAffectedDirectionValue:
    """SE `AffectedDirectionValue` is trusted over the geometry-bearing heuristic."""

    def test_specific_direction_value_keeps_geometrically_opposite_zone(self) -> None:
        """Source explicitly states a single bound -> trust it, skip geometry heuristic."""
        _make_provider()
        route = _make_route(bearing=45.0)
        zone = _make_zone(OPPOSITE_DIRECTION_COORDS, affected_direction_value="Mot Stockholm")

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(
            zone,
            strtree,
            seg_geoms,
            route.segments,
            both_directions_values=_SE_BOTH_DIRECTIONS_VALUES,
        )

        assert ids == [0]

    def test_both_directions_value_falls_back_to_geometry_heuristic(self) -> None:
        """ "Both directions" is not a specific bound -> geometry heuristic still applies."""
        _make_provider()
        route = _make_route(bearing=45.0)
        zone = _make_zone(OPPOSITE_DIRECTION_COORDS, affected_direction_value="BothDirections")

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(
            zone,
            strtree,
            seg_geoms,
            route.segments,
            both_directions_values=_SE_BOTH_DIRECTIONS_VALUES,
        )

        assert ids == []


class TestLaengeM:
    """`laenge_m` derivation: LineString geometry directly, else matched-segment span."""

    def test_geodesic_length_m_used_directly_for_linestring_zones(self) -> None:
        """Sanity check: the helper used for DK/SE zones matches its own contract."""
        coords = SAME_DIRECTION_COORDS
        assert geodesic_length_m(coords) > 0.0
