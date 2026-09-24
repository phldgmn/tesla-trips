"""Regression tests for STRtree-based matching: perf fix + zero-display-bug fix.

These tests verify:
1. The STRtree (spatial index) is built exactly once per fetch_construction_zones()
   call, not once per zone / country (fixes O(n*m) perf regression).
2. DATEX II zones near (but not exactly intersecting) a route segment match
   via distance threshold (fixes DK/SE zero-match display bug).
3. Country fetches (DE/DK/SE) are scheduled concurrently via asyncio.gather.

DE roadwork point-matching (Autobahn GmbH open API) is covered in
`test_providers_de_autobahn.py`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from tripplanner.construction import matching
from tripplanner.construction.models import ConstructionZone, Land
from tripplanner.construction.parser import DATEXIIConstructionZoneInternal
from tripplanner.construction.providers import (
    ConstructionProviderConfig,
    ConstructionProviderImpl,
    FakeConstructionProvider,
)
from tripplanner.construction.providers_de_autobahn import AutobahnConstructionProvider
from tripplanner.routing.models import Route, RouteSegment

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "construction"
VALID_DK_XML = (FIXTURES_DIR / "datexii_denmark_lane_closure.xml").read_text()


def _make_config() -> ConstructionProviderConfig:
    return ConstructionProviderConfig(
        dk_client_id="dk-client",
        dk_secret="dk-secret",
        dk_tenant_id="dk-tenant",
        tv_api_key="se-key",
        timeout_seconds=10.0,
    )


def _make_provider(
    config: ConstructionProviderConfig | None = None,
) -> ConstructionProviderImpl:
    provider = ConstructionProviderImpl(config or _make_config())
    provider._client = MagicMock(spec=httpx.AsyncClient)
    provider._client.get = AsyncMock()
    provider._client.post = AsyncMock()
    return provider


def _make_n_segments(num: int, spacing_deg: float = 0.001) -> Route:
    """Build a straight-line route with ``num`` 2-vertex segments."""
    segments: list[RouteSegment] = []
    lat, lon = 50.0, 9.0
    for i in range(num):
        seg = RouteSegment(
            segment_index=i,
            geometrie=[(lat, lon), (lat + spacing_deg, lon + spacing_deg)],
            length_m=150.0,
            strassenklasse="MOTORWAY",
            speed_limit_kmh=100,
            bearing_deg=45.0,
            street_name="A9",
        )
        segments.append(seg)
        lat += spacing_deg
        lon += spacing_deg
    geom = [(s.geometrie[0][0], s.geometrie[0][1]) for s in segments]
    return Route(
        segments=segments,
        gesamtlaenge_m=num * 150.0,
        geometrie=geom,
    )


def _make_linezone(
    koordinaten: list[tuple[float, float]],
    ClosureType: str = "partiallyClosed",
) -> DATEXIIConstructionZoneInternal:
    return DATEXIIConstructionZoneInternal(
        closure_type=ClosureType,
        gueltig_von=datetime(2024, 3, 20, tzinfo=UTC),
        gueltig_bis=None,
        koordinaten=koordinaten,
        umleitungshinweis=None,
        speed_limit_kmh=80,
    )


class _DelayedDeProvider:
    """Test double for `self._de_provider` that records start/end order and sleeps.

    Isolates `TestConcurrentCountryFetch` from the default DE provider's own
    implementation (network calls, internal concurrency) so these tests
    exercise exactly one thing: that `ConstructionProviderImpl.
    fetch_construction_zones` schedules DE/DK/SE via `asyncio.gather`.
    """

    def __init__(self, order: list[str], delay: float = 0.05) -> None:
        self._order = order
        self._delay = delay

    async def fetch_construction_zones(
        self, route: Route, laender: list[Land]
    ) -> list[ConstructionZone]:
        self._order.append("DE-start")
        await asyncio.sleep(self._delay)
        self._order.append("DE-end")
        return []


# ---------------------------------------------------------------------------
# 1. STRtree built exactly once per fetch_construction_zones() call
# ---------------------------------------------------------------------------
class TestStrtreeBuiltOnce:
    """Verify STRtree is constructed exactly once, not once per zone."""

    @pytest.mark.asyncio
    async def test_strtree_construction_count_de(self) -> None:
        """DE path (Autobahn GmbH revert provider): one STRtree per call, owned by it."""
        provider = _make_provider()
        provider._de_provider = AutobahnConstructionProvider(client=provider._client)
        route = _make_n_segments(200)
        # Give each segment a street_ref so _extract_autobahn_ids finds A 1 and A 9
        for i, seg in enumerate(route.segments):
            seg.street_ref = "A 1" if i < 100 else "A 9"

        # Mock roadworks responses for the extracted IDs (A 1, A 9)
        roadworks_list = [
            {
                "coordinate": {"lat": 50.0 + i * 0.001, "long": 9.0 + i * 0.001},
                "impact": {"symbols": ["ARROW_DOWN"]},
                "display_type": "ROADWORKS",
                "startTimestamp": "2024-06-01T08:00:00+02:00",
            }
            for i in range(100)
        ]
        roadworks_resp = MagicMock(spec=httpx.Response)
        roadworks_resp.raise_for_status = MagicMock()
        roadworks_resp.json = MagicMock(return_value={"roadworks": roadworks_list})

        async def get_side(url: str, **kw: Any):
            return roadworks_resp

        provider._client.get = AsyncMock(side_effect=get_side)

        # DE-only request: ConstructionProviderImpl skips its own STRtree build
        # (only needed for DK/SE); the Autobahn provider builds exactly one.
        with patch("tripplanner.construction.matching.STRtree", autospec=True) as MockSTRtree:
            MockSTRtree.return_value = MagicMock()
            zones = await provider.fetch_construction_zones(route, [Land.DE])

        assert len(zones) == 200
        assert MockSTRtree.call_count == 1

    @pytest.mark.asyncio
    async def test_strtree_construction_count_dk(self) -> None:
        """DK path: one STRtree per fetch_construction_zones call."""
        provider = _make_provider()
        route = _make_n_segments(200)

        with patch("tripplanner.construction.matching.STRtree", autospec=True) as MockSTRtree:
            MockSTRtree.return_value = MagicMock()

            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.raise_for_status = MagicMock()
            mock_resp.text = VALID_DK_XML

            token_resp = MagicMock(spec=httpx.Response)
            token_resp.json.return_value = {
                "access_token": "tok",
                "expires_in": 3600,
            }

            provider._client.get = AsyncMock(return_value=mock_resp)
            provider._client.post = AsyncMock(return_value=token_resp)

            await provider.fetch_construction_zones(route, [Land.DK])
            assert MockSTRtree.call_count == 1

    @pytest.mark.asyncio
    async def test_strtree_construction_count_multi_country(self) -> None:
        """DE+DK+SE: ConstructionProviderImpl still builds only one STRtree, for DK/SE.

        DE is delegated to `self._de_provider` (a `FakeConstructionProvider`
        here, isolating this test from the default NRW provider's own,
        independently-owned spatial index — see `test_providers_de_datexii.py`
        and `test_providers_de_autobahn.py` for DE-specific STRtree coverage).
        """
        provider = _make_provider()
        provider._de_provider = FakeConstructionProvider([])
        route = _make_n_segments(200)

        mock_dk_resp = MagicMock(spec=httpx.Response)
        mock_dk_resp.raise_for_status = MagicMock()
        mock_dk_resp.text = VALID_DK_XML

        fixture_path = FIXTURES_DIR / "live-samples" / "se_trafikverket_live_sample.json"
        se_json_fixture = (
            json.loads(fixture_path.read_text()) if fixture_path.exists() else {"Situations": []}
        )

        mock_se_resp = MagicMock(spec=httpx.Response)
        mock_se_resp.raise_for_status = MagicMock()
        mock_se_resp.json = MagicMock(return_value=se_json_fixture)

        token_resp = MagicMock(spec=httpx.Response)
        token_resp.json.return_value = {"access_token": "tok", "expires_in": 3600}

        async def post_side(url: str, **kw: Any):
            if "login.microsoftonline.com" in url:
                return token_resp
            return mock_se_resp

        provider._client.get = AsyncMock(return_value=mock_dk_resp)
        provider._client.post = AsyncMock(side_effect=post_side)

        with patch("tripplanner.construction.matching.STRtree", autospec=True) as MockSTRtree:
            MockSTRtree.return_value = MagicMock()
            await provider.fetch_construction_zones(route, [Land.DE, Land.DK, Land.SE])

        assert MockSTRtree.call_count == 1


# ---------------------------------------------------------------------------
# 2. Zero-display-bug fix: near-but-not-exact matches
# ---------------------------------------------------------------------------
class TestNearMatchDistanceThreshold:
    """ZONE NEAR a route segment should match via distance, not exact geometry."""

    def test_dk_zone_near_segment_matches(self) -> None:
        """DK zone 50-200m from a segment endpoint should match."""
        _make_provider()
        route = _make_n_segments(1, spacing_deg=0.01)
        zone = _make_linezone([(50.0, 9.001), (50.01, 9.011)])

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(zone, strtree, seg_geoms)
        assert ids == [0]

    def test_se_zone_near_segment_matches(self) -> None:
        """SE zone 100m from segment endpoint should match."""
        _make_provider()
        route = _make_n_segments(1, spacing_deg=0.01)
        zone = _make_linezone([(50.0015, 9.0)])

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(zone, strtree, seg_geoms)
        assert ids == [0]

    def test_zone_far_from_segment_no_match(self) -> None:
        """Zone far from all segments returns empty list."""
        _make_provider()
        route = _make_n_segments(1, spacing_deg=0.01)
        zone = _make_linezone([(53.55, 9.99), (53.56, 10.0)])

        strtree, seg_geoms = matching.build_strtree(route.segments)
        ids = matching.match_zones_to_segment_ids(zone, strtree, seg_geoms)
        assert ids == []


# ---------------------------------------------------------------------------
# 4. Concurrency: country fetches scheduled via asyncio.gather
# ---------------------------------------------------------------------------
class TestConcurrentCountryFetch:
    """DE/DK/SE country fetches are scheduled concurrently, not sequentially."""

    @pytest.mark.asyncio
    async def test_fetches_are_concurrent(self) -> None:
        """All three country fetches start before any finishes."""
        provider = _make_provider()
        order: list[str] = []
        provider._de_provider = _DelayedDeProvider(order)
        route = _make_n_segments(10)

        async def _make_mock_response(
            text: str = "", json_data: dict[str, Any] | None = None
        ) -> MagicMock:
            mr = MagicMock(spec=httpx.Response)
            mr.raise_for_status = MagicMock()
            if text:
                mr.text = text
            if json_data is not None:
                mr.json = MagicMock(return_value=json_data)
            return mr

        dk_resp = await _make_mock_response(text=VALID_DK_XML)
        se_resp = await _make_mock_response(json_data={"Situations": []})
        token_resp = MagicMock(spec=httpx.Response)
        token_resp.json.return_value = {"access_token": "tok", "expires_in": 3600}

        async def get_side(url: str, **kw: Any) -> MagicMock:
            order.append("DK-start")
            await asyncio.sleep(0.05)
            order.append("DK-end")
            return dk_resp

        async def post_side(url: str, **kw: Any) -> MagicMock:
            if "login.microsoftonline.com" in url:
                order.append("TOKEN-start")
                await asyncio.sleep(0.01)
                order.append("TOKEN-end")
                return token_resp
            order.append("SE-start")
            await asyncio.sleep(0.05)
            order.append("SE-end")
            return se_resp

        provider._client.get = AsyncMock(side_effect=get_side)
        provider._client.post = AsyncMock(side_effect=post_side)

        await provider.fetch_construction_zones(route, [Land.DE, Land.DK, Land.SE])

        starts_before_first_end = sum(
            1
            for i, o in enumerate(order)
            if i < next((i for i, x in enumerate(order) if "end" in x), len(order))
            and o.endswith("-start")
        )
        assert starts_before_first_end >= 2, f"Expected concurrent starts, got order: {order}"

    @pytest.mark.asyncio
    async def test_concurrent_fetch_timing(self) -> None:
        """Sequential fetches take ~3x longer than concurrent."""
        provider = _make_provider()
        provider._de_provider = _DelayedDeProvider([])
        route = _make_n_segments(10)

        async def _slow_response(delay: float) -> MagicMock:
            mr = MagicMock(spec=httpx.Response)
            mr.raise_for_status = MagicMock()
            mr.text = VALID_DK_XML
            await asyncio.sleep(delay)
            return mr

        slow_resp = await _slow_response(0.05)
        token_resp = MagicMock(spec=httpx.Response)
        token_resp.json.return_value = {"access_token": "tok", "expires_in": 3600}

        provider._client.get = AsyncMock(return_value=slow_resp)
        provider._client.post = AsyncMock(return_value=token_resp)

        start = asyncio.get_event_loop().time()
        await provider.fetch_construction_zones(route, [Land.DE, Land.DK])
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 0.1, f"Expected <0.1s concurrent, got {elapsed:.2f}s (seems sequential)"

    @pytest.mark.asyncio
    async def test_fetch_construction_zones_uses_gather(self) -> None:
        """fetch_construction_zones uses asyncio.gather for country fetches."""
        provider = _make_provider()
        provider._de_provider = FakeConstructionProvider([])
        route = _make_n_segments(10)

        mock_dk_resp = MagicMock(spec=httpx.Response)
        mock_dk_resp.raise_for_status = MagicMock()
        mock_dk_resp.text = VALID_DK_XML

        se_resp = MagicMock(spec=httpx.Response)
        se_resp.raise_for_status = MagicMock()
        se_resp.json = MagicMock(return_value={"Situations": []})

        token_resp = MagicMock(spec=httpx.Response)
        token_resp.json.return_value = {"access_token": "tok", "expires_in": 3600}

        async def post_side(url: str, **kw: Any) -> MagicMock:
            if "login.microsoftonline.com" in url:
                return token_resp
            return se_resp

        provider._client.get = AsyncMock(return_value=mock_dk_resp)
        provider._client.post = AsyncMock(side_effect=post_side)

        original_gather = asyncio.gather
        gather_calls: list[Any] = []

        async def mock_gather(*args: Any, **kw: Any) -> Any:
            gather_calls.append(args)
            return await original_gather(*args, **kw)

        with patch("asyncio.gather", side_effect=mock_gather):
            await provider.fetch_construction_zones(route, [Land.DE, Land.DK, Land.SE])

        assert len(gather_calls) >= 1, f"Expected at least 1 gather call, got {len(gather_calls)}"
        # First call is the outer country-level gather with 3 tasks.
        outer_args = gather_calls[0]
        assert len(outer_args) == 3, f"Expected 3 concurrent country tasks, got {len(outer_args)}"


# ---------------------------------------------------------------------------
# 5. Shared distance threshold constant
# ---------------------------------------------------------------------------
class TestSharedDistanceThreshold:
    """All matching paths (DE, DK, SE) use the same distance threshold constant."""

    def test_threshold_constant_exists(self) -> None:
        """A shared threshold constant is defined and importable."""
        assert matching.MAX_DISTANCE_M > 0

    def test_threshold_applied_in_dk_se_matching(self) -> None:
        """DK/SE matching delegates to the shared STRtree segment matcher."""
        source = inspect.getsource(matching.match_zones_to_segment_ids)
        assert "match_geometry_to_segments" in source, (
            "DK/SE matching should use the shared STRtree distance matcher"
        )
