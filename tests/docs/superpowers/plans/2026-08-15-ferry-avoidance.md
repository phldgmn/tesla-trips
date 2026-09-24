# Ferry Avoidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users avoid ferries entirely, or avoid one specific ferry crossing detected in a previously computed route, when planning a trip.

**Architecture:** Extend GraphHopper's `custom_model` mechanism end-to-end. No static ferry registry: ferries are detected from the `road_environment`/`street_name` path details GraphHopper already returns on a computed route, surfaced to the user, and — if excluded — re-sent as a small buffered GeoJSON exclusion area on the next request. Full design/rationale (including live-validated GraphHopper request/response payloads): `docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md`.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic 2 (backend), React 19 / TypeScript / Vite (frontend), GraphHopper 11.0 (routing engine, live at `localhost:8989` via `docker-compose.yml`).

## Global Constraints

- `uv run hk check --all` MUST pass (lint, format, type-check) before any commit.
- `uv run pytest -m "not integration"` MUST pass fully; coverage on `src/tripplanner/` MUST stay ≥ 85%.
- NEVER use `# noqa`/`# type: ignore` to silence a new warning without a line-level justification comment.
- All new public functions/methods need full type annotations (mypy `--strict`) and Google-style docstrings (ruff `D` rules, `ignore = ["D203", "D213"]`).
- Coordinates are `(lat, lon)` everywhere in domain code; GeoJSON conversion to `[lon, lat]` happens ONLY at the GraphHopper `custom_model.areas` serialization boundary (one of the three documented external conversion points).
- `custom_model` requests to GraphHopper MUST include `"ch.disable": true` (server runs profile `car` in CH/"speed mode"; confirmed live — GraphHopper rejects `custom_model` otherwise with `"The 'custom_model' parameter is currently not supported for speed mode..."`).
- No hardcoded ferry coordinates/registry anywhere in this plan.
- Frontend request/response payload fields mirror the backend's snake_case field names 1:1 (e.g. `start_soc_pct`) — no camelCase translation layer for wire fields (see `frontend/src/types/trip-request.ts`).
- Do not touch `docker-compose.yml`, `frontend/src/components/Map.tsx`, `scripts/prepare_osm_extract.sh`, or any other currently-uncommitted files in the working tree — those are the user's own in-progress work, unrelated to this feature.

---

### Task 1: `RouteSegment` ferry fields + `FaehrSegment` model

**Files:**

- Modify: `src/tripplanner/routing/models.py:41-99` (insert new `RouteSegment` fields after `bearing_deg`; add `FaehrSegment` class at end of file)
- Modify: `src/tripplanner/routing/__init__.py` (export `FaehrSegment`)
- Test: `tests/routing/test_routing.py` (extend `TestRouteSegmentModel`)

**Interfaces:**

- Produces: `RouteSegment.road_environment: str | None`, `RouteSegment.strassenname: str | None`, `FaehrSegment(name: str, laenge_m: float, bbox_sw: Coordinate, bbox_no: Coordinate)` — all consumed by Task 4/5/9.

- [ ] **Step 1: Write the failing tests**

Add to `tests/routing/test_routing.py`, inside `class TestRouteSegmentModel:` (after `test_route_segment_oberflaeche_optional`):

```python
def test_route_segment_road_environment_optional(self) -> None:
    """road_environment is optional (None when GraphHopper does not supply it)."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.5, 13.4), (52.6, 13.5)],
        laenge_m=1000.0,
        strassenklasse="MOTORWAY",
        bearing_deg=45.0,
    )
    assert segment.road_environment is None


def test_route_segment_strassenname_optional(self) -> None:
    """strassenname is optional (None when GraphHopper does not supply it)."""
    segment = RouteSegment(
        segment_index=0,
        geometrie=[(52.5, 13.4), (52.6, 13.5)],
        laenge_m=1000.0,
        strassenklasse="MOTORWAY",
        bearing_deg=45.0,
    )
    assert segment.strassenname is None
```

Add a new test class at the end of `tests/routing/test_routing.py`:

```python
class TestFaehrSegmentModel:
    """Tests for the FaehrSegment Pydantic model."""

    def test_faehr_segment_requires_all_fields(self) -> None:
        """FaehrSegment requires name, laenge_m, bbox_sw, bbox_no."""
        segment = FaehrSegment(
            name="Rødby (DK) - Puttgarden (D)",
            laenge_m=22000.0,
            bbox_sw=(54.50, 11.22),
            bbox_no=(54.66, 11.36),
        )
        assert segment.name == "Rødby (DK) - Puttgarden (D)"
        assert segment.laenge_m == 22000.0
        assert segment.bbox_sw == (54.50, 11.22)
        assert segment.bbox_no == (54.66, 11.36)

    def test_faehr_segment_rejects_negative_laenge(self) -> None:
        """laenge_m must be >= 0."""
        with pytest.raises(ValidationError):
            FaehrSegment(name="X", laenge_m=-1.0, bbox_sw=(0.0, 0.0), bbox_no=(1.0, 1.0))
```

Update the import line at the top of `tests/routing/test_routing.py`:

```python
from pydantic import ValidationError

from tripplanner.routing.models import FaehrSegment, Route, RouteSegment
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/routing/test_routing.py -v`
Expected: FAIL — `RouteSegment` rejects unknown fields / `FaehrSegment` not defined.

- [ ] **Step 3: Implement `RouteSegment` fields**

In `src/tripplanner/routing/models.py`, insert after the `bearing_deg` field (after line 49, before the blank line preceding `class Route`):

```python
    road_environment: str | None = Field(
        default=None,
        description=(
            "Environment type from GraphHopper Path Detail `road_environment` (ROAD, "
            "FERRY, BRIDGE, TUNNEL, FORD, OTHER), normalized to uppercase; "
            "None when unavailable. Used by `routing.faehren.erkenne_faehren()` "
            "used to detect ferry segments of the route."
        ),
    )
    strassenname: str | None = Field(
        default=None,
        description=(
            "Road/ferry-line name from GraphHopper Path Detail `street_name` "
            "(e.g. 'Rødby (DK) - Puttgarden (D)' for a ferry); None when "
            "unavailable or empty."
        ),
    )
```

- [ ] **Step 4: Implement `FaehrSegment`**

Append to the end of `src/tripplanner/routing/models.py`:

```python
class FaehrSegment(BaseModel):
    """A detected, contiguous ferry connection found in a computed `Route`. 

    Produced by `tripplanner.routing.faehren.erkenne_faehren()`. `bbox_sw`/`bbox_no`
    describe a bounding box buffered by `FAEHR_PUFFER_GRAD` around the exact 
    segment geometry - for reuse as `FaehrAusschluss`
    (`tripplanner.trip_input.models`) in einer nachfolgenden Routenberechnung, die
    intended to avoid exactly this ferry connection. 
    """

    name: str = Field(
        ...,
        description=(
            "Ferry name from `strassenname` of the first segment of the run, "
            "'Unbenannte Fähre' if GraphHopper does not supply a name."
        ),
    )
    laenge_m: float = Field(
        ..., ge=0, description="Total length of all contiguous ferry segments in meters"
    )
    bbox_sw: Coordinate = Field(..., description="Southwest corner of the buffered bounding box")
    bbox_no: Coordinate = Field(..., description="Northeast corner of the buffered bounding box")
```

- [ ] **Step 5: Export `FaehrSegment` from the package**

In `src/tripplanner/routing/__init__.py`, add `FaehrSegment` to the import from `.models` and to `__all__`:

```python
from tripplanner.routing.models import (
    Coordinate,
    FaehrSegment,
    GraphHopperPath,
    GraphHopperResponse,
    Route,
    RouteSegment,
)
```

```python
__all__ = [
    "Coordinate",
    "FaehrSegment",
    "FakeRoutingProvider",
    "GraphHopperClient",
    "GraphHopperPath",
    "GraphHopperResponse",
    "GraphHopperRoutingProvider",
    "Route",
    "RouteSegment",
    "RoutingProvider",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/routing/test_routing.py -v`
Expected: PASS

- [ ] **Step 7: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/routing/models.py src/tripplanner/routing/__init__.py tests/routing/test_routing.py
git commit -m "feat(routing): add road_environment/strassenname to RouteSegment, add FaehrSegment

Groundwork for dynamic ferry detection - no ferry-detection logic yet,
just the data carried per segment and the detected-ferry result type."
```

---

### Task 2: `FaehrAusschluss` + `TripRequest` ferry-avoidance fields

**Files:**

- Modify: `src/tripplanner/trip_input/models.py:61-75` (add `FaehrAusschluss` class, add two `TripRequest` fields)
- Test: `tests/trip_input/test_models.py` if it exists, else add inline validation tests to a new `tests/trip_input/test_models.py`

**Interfaces:**

- Consumes: `tripplanner.geo.Coordinate` (already imported in `trip_input/models.py`).
- Produces: `FaehrAusschluss(name: str, bbox_sw: Coordinate, bbox_no: Coordinate)`, `TripRequest.alle_faehren_vermeiden: bool` (default `False`), `TripRequest.vermiedene_faehren: list[FaehrAusschluss]` (default `[]`) — consumed by Task 6 (`GraphHopperRoutingProvider._build_custom_model`) and Task 9 (API layer).

- [ ] **Step 1: Check for an existing model test file**

Run: `test -f tests/trip_input/test_models.py && echo EXISTS || echo MISSING`

If `MISSING`, create `tests/trip_input/test_models.py` with this header:

```python
"""Unit tests for `tripplanner.trip_input.models`."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from tripplanner.trip_input.models import FaehrAusschluss, TripRequest, VehicleProfile


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    "Example vehicle profile for TripRequest tests."
    return VehicleProfile(
        masse_kg=1706.0,
        cw_wert=0.23,
        stirnflaeche_m2=2.22,
        rollwiderstandsbeiwert=0.011,
        batteriekapazitaet_kwh=62.5,
    )
```

If `EXISTS`, read the file first and add the import of `FaehrAusschluss` to its existing `from tripplanner.trip_input.models import ...` line instead of duplicating the import block.

- [ ] **Step 2: Write the failing tests**

Append to `tests/trip_input/test_models.py`:

```python
class TestFaehrAusschluss:
    """Tests for the FaehrAusschluss Pydantic model."""

    def test_faehr_ausschluss_requires_name_and_bbox(self) -> None:
        """FaehrAusschluss requires name, bbox_sw, bbox_no."""
        ausschluss = FaehrAusschluss(
            name="Rødby (DK) - Puttgarden (D)",
            bbox_sw=(54.50, 11.22),
            bbox_no=(54.66, 11.36),
        )
        assert ausschluss.name == "Rødby (DK) - Puttgarden (D)"
        assert ausschluss.bbox_sw == (54.50, 11.22)
        assert ausschluss.bbox_no == (54.66, 11.36)


class TestTripRequestFaehrPraeferenzen:
    """Tests for the ferry-avoidance fields of TripRequest."""

    def test_alle_faehren_vermeiden_defaults_to_false(
        self, vehicle_profile: VehicleProfile
    ) -> None:
        """alle_faehren_vermeiden defaults to False."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            ziel=(53.5511, 9.9937),
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
            fahrzeugprofil=vehicle_profile,
        )
        assert anfrage.alle_faehren_vermeiden is False
        assert anfrage.vermiedene_faehren == []

    def test_vermiedene_faehren_accepts_faehr_ausschluss_list(
        self, vehicle_profile: VehicleProfile
    ) -> None:
        """vermiedene_faehren accepts a list of FaehrAusschluss."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            ziel=(53.5511, 9.9937),
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
            fahrzeugprofil=vehicle_profile,
            alle_faehren_vermeiden=True,
            vermiedene_faehren=[
                FaehrAusschluss(name="Testfähre", bbox_sw=(54.0, 11.0), bbox_no=(55.0, 12.0))
            ],
        )
        assert anfrage.alle_faehren_vermeiden is True
        assert len(anfrage.vermiedene_faehren) == 1
        assert anfrage.vermiedene_faehren[0].name == "Testfähre"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/trip_input/test_models.py -v`
Expected: FAIL — `FaehrAusschluss` not defined / `TripRequest` rejects the fields.

- [ ] **Step 4: Implement `FaehrAusschluss` and the `TripRequest` fields**

In `src/tripplanner/trip_input/models.py`, insert a new class after `Waypoint` (after line 34, before `class VehicleProfile`):

```python
class FaehrAusschluss(BaseModel):
    """A ferry connection to be avoided by the user. 

    Originates from a `FaehrSegment` structure previously detected via `tripplanner.routing.erkenne_faehren()` from a 
    computed route (same field names for 
    `name`/`bbox_sw`/`bbox_no`, but independently defined): `routing` already imports 
    already `trip_input.models` (`TripRequest`); an import in the reverse direction would 
    create a module cycle. The API layer (`trip_input.api`, which already imports both modules 
    already imports both) converts between the two representations. 
    """

    name: str = Field(
        ...,
        description="Display name of the ferry connection (from a previous route calculation)", 
    )
    bbox_sw: Coordinate = Field(
        ..., description="Southwest corner of the (buffered) bounding box around the ferry connection"
    )
    bbox_no: Coordinate = Field(
        ..., description="Northeast corner of the (buffered) bounding box around the ferry connection"
    )
```

In `class TripRequest`, insert after the `fahrzeugprofil` field (after line 71, before `praeferenzen`):

```python
    alle_faehren_vermeiden: bool = Field(
        default=False,
        description=(
            "When True, all ferry connections are excluded during route calculation 
            (GraphHopper custom_model: road_environment == FERRY "
            "excluded)."
        ),
    )
    vermiedene_faehren: list[FaehrAusschluss] = Field(
        default_factory=list,
        description=(
            "List of specific, previously detected ferry connections to be excluded during the 
            "route calculation (see FaehrAusschluss)."
        ),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/trip_input/test_models.py -v`
Expected: PASS

- [ ] **Step 6: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/trip_input/models.py tests/trip_input/test_models.py
git commit -m "feat(trip_input): add FaehrAusschluss and TripRequest ferry fields

alle_faehren_vermeiden (global toggle) and vermiedene_faehren (specific,
previously-detected crossings) - both default to no avoidance, no
behavior change for existing callers."
```

---

### Task 3: `GraphHopperClient` auto `ch.disable`

**Files:**

- Modify: `src/tripplanner/routing/client.py:70-71`
- Test: `tests/routing/test_client.py`

**Interfaces:**

- Produces: `GraphHopperClient.route(..., custom_model=...)` now always sends `"ch.disable": true` in the payload when `custom_model` is truthy; unchanged otherwise. No signature change.

- [ ] **Step 1: Write the failing tests**

Add to `tests/routing/test_client.py`, as a new class after `TestRouteErrors`:

```python
class TestChDisable:
    """Tests for automatic `ch.disable` on custom_model requests. 

    GraphHopper rejects `custom_model` while the profile is in CH ("speed 
    mode") - verified live against the project's GraphHopper server
    (Fehler: "The 'custom_model' parameter is currently not supported for
    speed mode, you need to disable speed mode with `ch.disable=true`.").
    """

    @pytest.mark.asyncio
    async def test_ch_disable_set_when_custom_model_present(self) -> None:
        """`ch.disable=True` is set as soon as `custom_model` is passed."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route(
                [(52.5200, 13.4050), (53.5511, 9.9937)],
                custom_model={
                    "priority": [{"if": "road_environment == FERRY", "multiply_by": 0.0}]
                },
            )
            assert captured[0]["ch.disable"] is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ch_disable_absent_when_custom_model_none(self) -> None:
        """Without custom_model, `ch.disable` is not sent (unchanged behavior)."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route([(52.5200, 13.4050), (53.5511, 9.9937)])
            assert "ch.disable" not in captured[0]
        finally:
            await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/routing/test_client.py::TestChDisable -v`
Expected: FAIL — `"ch.disable" in captured[0]` is `False`.

- [ ] **Step 3: Implement**

In `src/tripplanner/routing/client.py`, replace lines 70-71:

```python
        if custom_model:
            payload["custom_model"] = custom_model
```

with:

```python
        if custom_model:
            payload["custom_model"] = custom_model
            # GraphHopper rejects `custom_model` while the profile is in CH
            # ("speed mode") - verified live against the project's GraphHopper server
            # verifiziert (Fehler: "The 'custom_model' parameter is currently
            # not supported for speed mode, you need to disable speed mode
            # with `ch.disable=true`."). Must be set for EVERY custom_model request
            # regardless of the use case. 
            payload["ch.disable"] = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/routing/test_client.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones — this must not regress them)

- [ ] **Step 5: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/routing/client.py tests/routing/test_client.py
git commit -m "fix(routing): auto-set ch.disable when sending a custom_model

GraphHopper's car profile runs in CH/speed-mode and rejects any
custom_model request without ch.disable=true; verified live against
the project's GraphHopper server. Required for ferry avoidance
(Task 6) and any future custom_model use."
```

---

### Task 4: `GraphHopperRoutingProvider` — request and map `road_environment`/`strassenname`

**Files:**

- Modify: `src/tripplanner/routing/providers.py:159,261-311` (extend `_ALLE_PATH_DETAILS`, add always-on details, extend `_map_path_to_route`)
- Create: `tests/fixtures/routing/graphhopper_response_with_ferry.json`
- Modify: `tests/routing/conftest.py` (new fixture)
- Test: `tests/routing/test_providers.py`

**Interfaces:**

- Consumes: `RouteSegment.road_environment`/`strassenname` (Task 1).
- Produces: `GraphHopperRoutingProvider._map_path_to_route()` now populates `road_environment` (uppercased) and `strassenname` per segment; `_ALLE_PATH_DETAILS` includes `"road_environment"`; new class attribute `_IMMER_VERFUEGBARE_DETAILS = ("street_name",)`.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/routing/graphhopper_response_with_ferry.json`. This is the **exact response captured live** from the project's GraphHopper server for `points=[[11.2270,54.5033],[11.3453,54.6558]]` (Puttgarden → Rødby, the Fehmarnbelt ferry crossing), `details=["road_environment","street_name"]`:

```json
{
  "paths": [
    {
      "distance": 22266.258,
      "time": 4143128,
      "points_encoded": true,
      "points": "actkIc|ocAdAfA|AvBXf@Lf@Dz@n@l@CwAFuBAa@CYCOGQMQg@k@yAoAu@g@{LsIiKaMon@oa@ig@qTyn@y]qlDwrBcgLaiGyrAqfAon@{hBeYgr@{I_QsFmPsEkLsAsDe@mAU]QOUKSEUA]HW@UAWGUKUQeAoA{@cBqBiEk@{Aw@{Bg@{@_@_@Wc@}@kCi@gAS}@c@uAAQ@SBEx@kAeCmHeAcDmAoDWg@We@]]wBoBKj@KbAk@nC{BdKjEzDj@n@lA`Br@lAdCtFrIhS\\z@JZNt@BZAh@ATEVI\\Wp@k@jAgCfFrAnCxE|JArA?l@Nv@nDcCLAJLVz@?VCReBxEQ\\aAfA`@jB",
      "details": {
        "road_environment": [[0, 15, "road"], [15, 29, "ferry"], [29, 67, "road"], [67, 68, "bridge"], [68, 101, "road"]],
        "street_name": [[0, 15, null], [15, 29, "Rødby (DK) - Puttgarden (D)"], [29, 52, "Sydmotorvejen"], [52, 58, null], [58, 65, "Færgestationsvej"], [65, 69, "Færgevej"], [69, 85, "Jøncksvej"], [85, 86, "Søpavillonvej"], [86, 101, "Vestre Kaj"]]
      },
      "instructions": []
    }
  ],
  "info": {
    "copyrights": ["GraphHopper", "OpenStreetMap contributors"],
    "hints": [],
    "took": 2
  }
}
```

- [ ] **Step 2: Add the fixture to conftest**

In `tests/routing/conftest.py`, add after the `graphhopper_response_with_details` fixture:

```python
@pytest.fixture
def graphhopper_response_with_ferry() -> GraphHopperResponse:
    """Real GraphHopper response (Puttgarden -> Rødby) with road_environment/street_name."""
    data = json.loads((FIXTURES_DIR / "graphhopper_response_with_ferry.json").read_text())
    return GraphHopperResponse.model_validate(data)
```

- [ ] **Step 3: Write the failing tests**

Add to `tests/routing/test_providers.py`, as a new class after `TestMapPathToRoute`:

```python
class TestMapPathToRouteFerryDetails:
    """Tests for road_environment/strassenname mapping (basis of ferry detection)."""

    def test_ferry_segment_has_uppercased_road_environment(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_ferry: GraphHopperResponse,
    ) -> None:
        """The ferry segment has road_environment='FERRY' (uppercased from GraphHopper 'ferry')."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_ferry.paths[0])

        ferry_segments = [s for s in route.segments if s.road_environment == "FERRY"]
        assert len(ferry_segments) > 0

    def test_ferry_segment_has_strassenname_from_street_name(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_ferry: GraphHopperResponse,
    ) -> None:
        """The ferry segment takes the name from the street_name Path Detail."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_ferry.paths[0])

        ferry_segment = next(s for s in route.segments if s.road_environment == "FERRY")
        assert ferry_segment.strassenname == "Rødby (DK) - Puttgarden (D)"

    def test_road_segment_has_none_strassenname_when_street_name_null(
        self,
        gh_provider: GraphHopperRoutingProvider,
        graphhopper_response_with_ferry: GraphHopperResponse,
    ) -> None:
        """A segment with street_name=null in the JSON becomes strassenname=None."""
        route = gh_provider._map_path_to_route(graphhopper_response_with_ferry.paths[0])

        assert route.segments[0].strassenname is None
        assert route.segments[0].road_environment == "ROAD"
```

Also extend the existing `test_maps_basic_response_without_details` in `class TestMapPathToRoute` (the fixture has no `road_environment`/`street_name` details at all) by adding two lines at the end of its body:

```python
        assert first.road_environment is None
        assert first.strassenname is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/routing/test_providers.py -v`
Expected: FAIL — `road_environment`/`strassenname` are always `None` (not populated yet).

- [ ] **Step 5: Implement — extend path details**

In `src/tripplanner/routing/providers.py`, replace line 159:

```python
    _ALLE_PATH_DETAILS: tuple[str, ...] = ("road_class", "max_speed", "average_slope", "surface")
```

with:

```python
    _ALLE_PATH_DETAILS: tuple[str, ...] = (
        "road_class",
        "max_speed",
        "average_slope",
        "surface",
        "road_environment",
    )
    # `street_name` reads OSM names directly (e.g. ferry-line relations like 
    # "Rødby (DK) - Puttgarden (D)") und ist - anders als road_class/surface/
    # etc. - NOT a `graph.encoded_values` entry: never appears in `/info` and
    # would be incorrectly discarded by `_ermittele_verfuegbare_path_details()`'s availability 
    # filter. Therefore always requested independently of the filter 
    # (verified live against the project's GraphHopper server: 
    # detail is correctly supplied, see graphhopper_response_with_ferry.json). 
    _IMMER_VERFUEGBARE_DETAILS: tuple[str, ...] = ("street_name",)
```

- [ ] **Step 6: Implement — extract in `_map_path_to_route`**

In `_map_path_to_route`, after the line `surfaces = path.details.get("surface", [])` (around line 277), add:

```python
        road_environments = path.details.get("road_environment", [])
        street_names = path.details.get("street_name", [])
```

Inside the `for i in range(len(coordinates) - 1):` loop, after the existing `oberflaeche = ...` line (around line 295), add:

```python
            road_environment_raw = self._wert_fuer_edge(road_environments, i)
            road_environment = str(road_environment_raw).upper() if road_environment_raw else None
            strassenname_raw = self._wert_fuer_edge(street_names, i)
            strassenname = str(strassenname_raw) if strassenname_raw else None
```

In the `RouteSegment(...)` constructor call inside that loop, add the two new fields:

```python
            segment = RouteSegment(
                segment_index=i,
                geometrie=[start_coord, end_coord],
                laenge_m=laenge_m,
                strassenklasse=strassenklasse,
                oberflaeche=oberflaeche,
                tempolimit_kmh=tempolimit_kmh,
                steigung_rohdaten=steigung_rohdaten,
                bearing_deg=bearing,
                road_environment=road_environment,
                strassenname=strassenname,
            )
```

- [ ] **Step 7: Use both detail lists when calling GraphHopper**

Both `berechne_route` and `berechne_route_mit_waypoints` currently do:

```python
        details_list = await self._ermittele_verfuegbare_path_details()
```

Replace **both** occurrences with:

```python
        details_list = [
            *await self._ermittele_verfuegbare_path_details(),
            *self._IMMER_VERFUEGBARE_DETAILS,
        ]
```

(This makes `street_name` always requested from both entry points; `berechne_route_mit_waypoints` keeps its existing behavior otherwise — no `custom_model` handling is added to it in this task, that's Task 6, scoped to `berechne_route` only per the design.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/routing/ -v`
Expected: PASS (all routing tests, including Tasks 1-3's)

- [ ] **Step 9: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/routing/providers.py tests/routing/conftest.py tests/routing/test_providers.py tests/fixtures/routing/graphhopper_response_with_ferry.json
git commit -m "feat(routing): map road_environment/street_name into RouteSegment

Requests road_environment (filtered by server capability, like the
existing details) and street_name (always requested - not gated by
graph.encoded_values) from GraphHopper. Groundwork for ferry
detection; fixture captured live against the project's GraphHopper
server (Puttgarden<->Rødby)."
```

---

### Task 5: `routing/faehren.py` — `erkenne_faehren()`

**Files:**

- Create: `src/tripplanner/routing/faehren.py`
- Modify: `src/tripplanner/routing/__init__.py` (export `erkenne_faehren`)
- Test: `tests/routing/test_faehren.py`

**Interfaces:**

- Consumes: `Route`, `RouteSegment.road_environment`/`strassenname`/`geometrie`/`laenge_m` (Task 1).
- Produces: `erkenne_faehren(route: Route) -> list[FaehrSegment]`, `FAEHR_PUFFER_GRAD: float` — consumed by Task 9 (API layer).

- [ ] **Step 1: Write the failing tests**

Create `tests/routing/test_faehren.py`:

```python
"""Unit tests for `tripplanner.routing.faehren.erkenne_faehren()`."""

from __future__ import annotations

import pytest

from tripplanner.routing.faehren import FAEHR_PUFFER_GRAD, erkenne_faehren
from tripplanner.routing.models import Route, RouteSegment


def _segment(
    index: int,
    start: tuple[float, float],
    end: tuple[float, float],
    road_environment: str | None,
    strassenname: str | None = None,
) -> RouteSegment:
    """Builds a minimal RouteSegment for ferry detection tests."""
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
    """Tests for erkenne_faehren()."""

    def test_no_ferry_segments_returns_empty_list(self) -> None:
        """A route without FERRY segments returns an empty list."""
        route = Route(
            segments=[_segment(0, (54.0, 11.0), (54.1, 11.1), "ROAD")],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1)],
        )
        assert erkenne_faehren(route) == []

    def test_missing_road_environment_returns_empty_list(self) -> None:
        """Segments without road_environment (e.g. FakeRoutingProvider) are ignored."""
        route = Route(
            segments=[_segment(0, (54.0, 11.0), (54.1, 11.1), None)],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1)],
        )
        assert erkenne_faehren(route) == []

    def test_single_contiguous_ferry_run_grouped_into_one_segment(self) -> None:
        """A contiguous FERRY run yields exactly one FaehrSegment with summed length."""
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

        faehren = erkenne_faehren(route)

        assert len(faehren) == 1
        assert faehren[0].name == "Rødby (DK) - Puttgarden (D)"
        assert faehren[0].laenge_m == 2000.0

    def test_ferry_bbox_buffered_around_segment_geometry(self) -> None:
        """The bounding box encloses the ferry geometry buffered by FAEHR_PUFFER_GRAD."""
        route = Route(
            segments=[_segment(0, (54.50, 11.22), (54.60, 11.30), "FERRY", "Testfähre")],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.50, 11.22), (54.60, 11.30)],
        )

        faehren = erkenne_faehren(route)

        assert faehren[0].bbox_sw == pytest.approx(
            (54.50 - FAEHR_PUFFER_GRAD, 11.22 - FAEHR_PUFFER_GRAD)
        )
        assert faehren[0].bbox_no == pytest.approx(
            (54.60 + FAEHR_PUFFER_GRAD, 11.30 + FAEHR_PUFFER_GRAD)
        )

    def test_ferry_without_strassenname_falls_back_to_default_name(self) -> None:
        """When strassenname is missing (no street_name from GraphHopper), a fallback name is used."""
        route = Route(
            segments=[_segment(0, (54.50, 11.22), (54.60, 11.30), "FERRY", None)],
            gesamtlaenge_m=1000.0,
            geometrie=[(54.50, 11.22), (54.60, 11.30)],
        )

        faehren = erkenne_faehren(route)

        assert faehren[0].name == "Unbenannte Fähre"

    def test_two_disjoint_ferry_runs_produce_two_segments(self) -> None:
        """Two FERRY runs separated by a ROAD segment yield two FaehrSegments."""
        route = Route(
            segments=[
                _segment(0, (54.0, 11.0), (54.1, 11.1), "FERRY", "Fähre A"),
                _segment(1, (54.1, 11.1), (54.2, 11.2), "ROAD"),
                _segment(2, (54.2, 11.2), (54.3, 11.3), "FERRY", "Fähre B"),
            ],
            gesamtlaenge_m=3000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1), (54.2, 11.2), (54.3, 11.3)],
        )

        faehren = erkenne_faehren(route)

        assert [f.name for f in faehren] == ["Fähre A", "Fähre B"]

    def test_ferry_run_extending_to_end_of_route_is_captured(self) -> None:
        """A FERRY run that extends to the last segment is not discarded."""
        route = Route(
            segments=[
                _segment(0, (54.0, 11.0), (54.1, 11.1), "ROAD"),
                _segment(1, (54.1, 11.1), (54.2, 11.2), "FERRY", "Fähre am Ende"),
            ],
            gesamtlaenge_m=2000.0,
            geometrie=[(54.0, 11.0), (54.1, 11.1), (54.2, 11.2)],
        )

        faehren = erkenne_faehren(route)

        assert len(faehren) == 1
        assert faehren[0].name == "Fähre am Ende"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/routing/test_faehren.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tripplanner.routing.faehren'`

- [ ] **Step 3: Implement**

Create `src/tripplanner/routing/faehren.py`:

```python
"""Detection of ferry connections in a computed `Route`. 

No static ferry register: ferry segments are detected exclusively from the
`road_environment`/`strassenname` fields, which `GraphHopperRoutingProvider`
extracts per segment from the GraphHopper Path Details `road_environment`/`street_name`
extrahiert (siehe `docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md`).
"""

from __future__ import annotations

from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment

FAEHR_PUFFER_GRAD: float = 0.005
"Buffering (in decimal degrees, approx. 500 m at the latitudes DE/DK/SE) around the
exact segment geometry of a detected ferry connection, so that the Custom Model Area built from it
covers the entire ferry line reliably."

_UNBENANNTE_FAEHRE = "Unbenannte Fähre"


def erkenne_faehren(route: Route) -> list[FaehrSegment]:
    """Groups contiguous ferry segments of a route into `FaehrSegment` entries. 

    Iterates once linearly over `route.segments` and combines consecutive
    segments with `road_environment == "FERRY"` into one `FaehrSegment` each
    (name from the first available `strassenname` of the run, else 
    "Unbenannte Fähre"; length as the sum of `laenge_m`; bounding box from all
    involved `geometry` coordinates, buffered by `FAEHR_PUFFER_GRAD`. 

    Args:
        route: Eine bereits berechnete Route (z. B. aus `RoutingProvider.berechne_route()`).

    Returns:
        List of detected ferry connections in driving direction. Empty if the route
        contains no ferry segments or `road_environment` was unavailable
        (z. B. `FakeRoutingProvider`-Routen).
    """
    ergebnis: list[FaehrSegment] = []
    aktueller_lauf: list[RouteSegment] = []

    def _lauf_abschliessen() -> None:
        if aktueller_lauf:
            ergebnis.append(_lauf_zu_faehrsegment(aktueller_lauf))

    for segment in route.segments:
        if segment.road_environment == "FERRY":
            aktueller_lauf.append(segment)
        else:
            _lauf_abschliessen()
            aktueller_lauf = []
    _lauf_abschliessen()

    return ergebnis


def _lauf_zu_faehrsegment(lauf: list[RouteSegment]) -> FaehrSegment:
    """Builds a `FaehrSegment` from a contiguous run of ferry `RouteSegment`s."""
    name = next((s.strassenname for s in lauf if s.strassenname), None) or _UNBENANNTE_FAEHRE
    laenge_m = sum(s.laenge_m for s in lauf)

    koordinaten: list[Coordinate] = [koord for s in lauf for koord in s.geometrie]
    lats = [k[0] for k in koordinaten]
    lons = [k[1] for k in koordinaten]

    return FaehrSegment(
        name=name,
        laenge_m=laenge_m,
        bbox_sw=(min(lats) - FAEHR_PUFFER_GRAD, min(lons) - FAEHR_PUFFER_GRAD),
        bbox_no=(max(lats) + FAEHR_PUFFER_GRAD, max(lons) + FAEHR_PUFFER_GRAD),
    )
```

- [ ] **Step 4: Export from the package**

In `src/tripplanner/routing/__init__.py`, add the import and `__all__` entry:

```python
from tripplanner.routing.faehren import erkenne_faehren
```

```python
__all__ = [
    "Coordinate",
    "FaehrSegment",
    "FakeRoutingProvider",
    "GraphHopperClient",
    "GraphHopperPath",
    "GraphHopperResponse",
    "GraphHopperRoutingProvider",
    "Route",
    "RouteSegment",
    "RoutingProvider",
    "erkenne_faehren",
]
```

Also update the module docstring's "Exportiert" list at the top of `__init__.py` to mention `FaehrSegment`/`erkenne_faehren`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/routing/ -v`
Expected: PASS

- [ ] **Step 6: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/routing/faehren.py src/tripplanner/routing/__init__.py tests/routing/test_faehren.py
git commit -m "feat(routing): add erkenne_faehren() to group ferry segments

Groups contiguous FERRY-environment RouteSegments into FaehrSegment
entries (name, length, buffered bbox). No static ferry list - derived
entirely from a computed route's own GraphHopper path details."
```

---

### Task 6: `GraphHopperRoutingProvider._build_custom_model` — ferry avoidance wiring

**Files:**

- Modify: `src/tripplanner/routing/providers.py:14-22,205-238` (imports, `berechne_route`, new helper functions)
- Test: `tests/routing/test_providers.py`

**Interfaces:**

- Consumes: `TripRequest.alle_faehren_vermeiden`/`vermiedene_faehren`, `FaehrAusschluss` (Task 2).
- Produces: `GraphHopperRoutingProvider._build_custom_model(anfrage: TripRequest) -> dict[str, object] | None`; `berechne_route()` now calls it and passes the result to `client.route(custom_model=...)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/routing/test_providers.py`, as a new class after `TestNormalizeMaxSpeed`:

```python
class TestBuildCustomModel:
    """Tests for GraphHopperRoutingProvider._build_custom_model()."""

    def test_no_avoidance_and_no_speed_profile_returns_none(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Without ferry avoidance and without use_custom_model, no custom_model is built."""
        assert gh_provider._build_custom_model(trip_request) is None

    def test_alle_faehren_vermeiden_adds_ferry_priority_rule(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """alle_faehren_vermeiden=True adds a road_environment==FERRY priority rule."""
        anfrage = trip_request.model_copy(update={"alle_faehren_vermeiden": True})

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is not None
        assert {"if": "road_environment == FERRY", "multiply_by": 0.0} in custom_model["priority"]
        assert "areas" not in custom_model

    def test_vermiedene_faehren_adds_area_and_priority_rule(
        self, gh_provider: GraphHopperRoutingProvider, trip_request: TripRequest
    ) -> None:
        """Each avoided ferry creates a GeoJSON Area and an in_<id> priority rule."""
        ausschluss = FaehrAusschluss(
            name="Rødby (DK) - Puttgarden (D)",
            bbox_sw=(54.50, 11.22),
            bbox_no=(54.66, 11.36),
        )
        anfrage = trip_request.model_copy(update={"vermiedene_faehren": [ausschluss]})

        custom_model = gh_provider._build_custom_model(anfrage)

        assert custom_model is not None
        assert {"if": "in_faehre_0", "multiply_by": 0.0} in custom_model["priority"]
        area = custom_model["areas"]["faehre_0"]
        assert area["type"] == "Feature"
        assert area["geometry"]["type"] == "Polygon"
        ring = area["geometry"]["coordinates"][0]
        assert ring[0] == [11.22, 54.50]  # [lon, lat] Reihenfolge (GeoJSON)
        assert ring[0] == ring[-1]  # geschlossener Ring

    def test_use_custom_model_and_ferry_avoidance_combined(self, trip_request: TripRequest) -> None:
        """use_custom_model=True and ferry avoidance work together on the same priority list."""
        provider = GraphHopperRoutingProvider(client=None, use_custom_model=True)  # type: ignore[arg-type]
        anfrage = trip_request.model_copy(update={"alle_faehren_vermeiden": True})

        custom_model = provider._build_custom_model(anfrage)

        assert custom_model is not None
        assert custom_model["distance_influence"] == 0.0
        assert {"if": "road_class == MOTORWAY", "multiply_by": 1.0} in custom_model["priority"]
        assert {"if": "road_environment == FERRY", "multiply_by": 0.0} in custom_model["priority"]
```

Update the imports at the top of `tests/routing/test_providers.py`:

```python
from tripplanner.routing.models import GraphHopperResponse
from tripplanner.routing.providers import GraphHopperRoutingProvider
from tripplanner.trip_input.models import FaehrAusschluss, TripRequest
```

(The `trip_request` fixture is already available from `tests/routing/conftest.py`, Task 2's tests do not touch it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/routing/test_providers.py::TestBuildCustomModel -v`
Expected: FAIL — `AttributeError: 'GraphHopperRoutingProvider' object has no attribute '_build_custom_model'`

- [ ] **Step 3: Implement**

In `src/tripplanner/routing/providers.py`, update the import block (around line 16-22) to add `FaehrAusschluss`:

```python
from tripplanner.trip_input.models import FaehrAusschluss, TripRequest
```

Add a module-level helper function just before `class GraphHopperRoutingProvider:` (i.e. right after `FakeRoutingProvider` ends, before line 155):

```python
def _faehr_ausschluss_zu_geojson_feature(ausschluss: FaehrAusschluss) -> dict[str, object]:
    """Baut ein rechteckiges GeoJSON `Polygon`-Feature aus einer gepufferten Bounding Box.

    GeoJSON-Koordinaten sind `[lon, lat]` (Umwandlung von der projektweiten
    `(lat, lon)`-Konvention an dieser externen Serialisierungsgrenze - eine der
    drei dokumentierten GeoJSON-Konversionsstellen des Projekts).
    """
    sw_lat, sw_lon = ausschluss.bbox_sw
    no_lat, no_lon = ausschluss.bbox_no
    ring = [
        [sw_lon, sw_lat],
        [no_lon, sw_lat],
        [no_lon, no_lat],
        [sw_lon, no_lat],
        [sw_lon, sw_lat],
    ]
    return {
        "type": "Feature",
        "properties": {"name": ausschluss.name},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }
```

Replace the `berechne_route` body's custom_model construction (the block currently reading `custom_model = None` / `if self.use_custom_model: custom_model = {...}`) with a call to a new method, and add that method. Full replacement of `async def berechne_route(self, anfrage: TripRequest) -> Route:` (lines 205-238):

```python
async def berechne_route(self, anfrage: TripRequest) -> Route:
    """Calculates a route for a TripRequest (including waypoints)."""
    # TripRequest -> GraphHopper parameter conversion
    points = [anfrage.start] + [wp.koordinate for wp in anfrage.zwischenstopps] + [anfrage.ziel]

    details_list = [
        *await self._ermittele_verfuegbare_path_details(),
        *self._IMMER_VERFUEGBARE_DETAILS,
    ]

    custom_model = self._build_custom_model(anfrage)

    response = await self.client.route(
        points=points,
        profile="car",
        # elevation=False: the `polyline` decoder only supports 2D
        # (lat, lon) - a 3D-encoded polyline (with elevation) would
        # `polyline.decode()` misalign and cause a crash.
        # `RouteSegment.geometry` is only (lat, lon) anyway; slope
        # is calculated separately by the `elevation` module from DEM tiles.
        elevation=False,
        details=details_list,
        custom_model=custom_model,
    )

    # Mapping GraphHopperResponse → Route
    return self._map_path_to_route(response.paths[0])


def _build_custom_model(self, anfrage: TripRequest) -> dict[str, object] | None:
    """Builds the optional GraphHopper `custom_model` from speed-limit and ferry preferences. 

    Returns `None` when neither `use_custom_model` (speed-limit profile) nor 
    ferry avoidance (`anfrage.alle_faehren_vermeiden`/`anfrage.vermiedene_faehren`)
    is requested - identical to previous behavior without a custom 
    model (no custom_model field in the GraphHopper request). 
    """
    priority: list[dict[str, object]] = []
    speed: list[dict[str, object]] | None = None
    distance_influence: float | None = None

    if self.use_custom_model:
        speed = [
            {"if": "road_class == MOTORWAY", "limit_to": 130},
            {"if": "true", "limit_to": 100},
        ]
        priority.append({"if": "road_class == MOTORWAY", "multiply_by": 1.0})
        distance_influence = 0.0

    if anfrage.alle_faehren_vermeiden:
        priority.append({"if": "road_environment == FERRY", "multiply_by": 0.0})

    areas: dict[str, object] = {}
    for index, ausschluss in enumerate(anfrage.vermiedene_faehren):
        area_id = f"faehre_{index}"
        areas[area_id] = _faehr_ausschluss_zu_geojson_feature(ausschluss)
        priority.append({"if": f"in_{area_id}", "multiply_by": 0.0})

    if not priority and speed is None:
        return None

    custom_model: dict[str, object] = {}
    if speed is not None:
        custom_model["speed"] = speed
    if priority:
        custom_model["priority"] = priority
    if areas:
        custom_model["areas"] = areas
    if distance_influence is not None:
        custom_model["distance_influence"] = distance_influence

    return custom_model
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/routing/ -v`
Expected: PASS

- [ ] **Step 5: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/routing/providers.py tests/routing/test_providers.py
git commit -m "feat(routing): wire ferry avoidance into GraphHopper custom_model

_build_custom_model() merges the (currently dormant in production)
speed-profile custom_model with ferry-avoidance priority rules:
global road_environment==FERRY exclusion, and per-excluded-ferry
GeoJSON area exclusion (in_<id> priority rule). Returns None (no
custom_model sent) when neither is requested - no change to today's
production behavior."
```

---

### Task 7: Integration test against the live GraphHopper server

**Files:**

- Create: `tests/routing/test_faehren_integration.py`

**Interfaces:**

- Consumes: `GraphHopperClient`, `GraphHopperRoutingProvider`, `erkenne_faehren`, `TripRequest`, `FaehrAusschluss` (all prior tasks).
- Produces: nothing new — this is a verification-only task reproducing the manually-validated scenarios from the design spec against the real `docker-compose` GraphHopper server.

- [ ] **Step 1: Confirm the local GraphHopper server is reachable**

Run: `curl -s http://localhost:8989/info | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])"`
Expected: `11.0` (if not running, start it: `docker compose up -d graphhopper` and wait for readiness before continuing — do NOT skip this task, it is required verification of the whole feature).

- [ ] **Step 2: Write the test**

Create `tests/routing/test_faehren_integration.py`:

```python
"""Integration tests: ferry avoidance against a real GraphHopper server. 

Reproduces the scenarios validated live against the project GraphHopper (see docker-compose.yml, 
DE+DK+SE extract) validated from
`docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md`.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.faehren import erkenne_faehren
from tripplanner.routing.providers import GraphHopperRoutingProvider
from tripplanner.trip_input.models import FaehrAusschluss, TripRequest, VehicleProfile

# Rødby (DK) <-> Puttgarden (D): direct ferry crossing of the Fehmarnbelt, 
# approx. 22 km / 69 min per ferry (verified live). 
_PUTTGARDEN = (54.5033, 11.2270)
_RODBY = (54.6558, 11.3453)


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    "Example vehicle profile for integration tests."
    return VehicleProfile(
        masse_kg=1706.0,
        cw_wert=0.23,
        stirnflaeche_m2=2.22,
        rollwiderstandsbeiwert=0.011,
        batteriekapazitaet_kwh=62.5,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_baseline_route_uses_the_ferry(vehicle_profile: VehicleProfile) -> None:
    """Without avoidance, the direct route uses the Rødby-Puttgarden ferry."""
    client = GraphHopperClient(base_url="http://localhost:8989")
    provider = GraphHopperRoutingProvider(client)
    anfrage = TripRequest(
        start=_PUTTGARDEN,
        ziel=_RODBY,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
    )
    try:
        route = await provider.berechne_route(anfrage)
        faehren = erkenne_faehren(route)
        assert len(faehren) == 1
        assert route.gesamtlaenge_m < 30_000
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_alle_faehren_vermeiden_forces_long_detour(vehicle_profile: VehicleProfile) -> None:
    """alle_faehren_vermeiden=True forces a much longer land route without ferries."""
    client = GraphHopperClient(base_url="http://localhost:8989")
    provider = GraphHopperRoutingProvider(client)
    anfrage = TripRequest(
        start=_PUTTGARDEN,
        ziel=_RODBY,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
        alle_faehren_vermeiden=True,
    )
    try:
        route = await provider.berechne_route(anfrage)
        assert erkenne_faehren(route) == []
        assert route.gesamtlaenge_m > 400_000
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_specific_ferry_exclusion_reroutes_around_detected_segment(
    vehicle_profile: VehicleProfile,
) -> None:
    """When the previously detected ferry is specifically excluded, it is not used again."""
    client = GraphHopperClient(base_url="http://localhost:8989")
    provider = GraphHopperRoutingProvider(client)
    baseline_anfrage = TripRequest(
        start=_PUTTGARDEN,
        ziel=_RODBY,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
    )
    try:
        baseline_route = await provider.berechne_route(baseline_anfrage)
        erkannt = erkenne_faehren(baseline_route)
        assert len(erkannt) == 1

        ausschluss_anfrage = baseline_anfrage.model_copy(
            update={
                "vermiedene_faehren": [
                    FaehrAusschluss(
                        name=erkannt[0].name,
                        bbox_sw=erkannt[0].bbox_sw,
                        bbox_no=erkannt[0].bbox_no,
                    )
                ]
            }
        )
        rerouted = await provider.berechne_route(ausschluss_anfrage)

        assert erkenne_faehren(rerouted) == []
        assert rerouted.gesamtlaenge_m > baseline_route.gesamtlaenge_m
    finally:
        await client.close()
```

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest tests/routing/test_faehren_integration.py -v -m integration`
Expected: PASS (all 3 tests) — if it fails, inspect whether the local GraphHopper container is up to date and re-run Step 1 before debugging the implementation.

- [ ] **Step 4: Run the full non-integration suite to confirm no regression**

Run: `uv run pytest -m "not integration"`
Expected: PASS, coverage ≥ 85%.

- [ ] **Step 5: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add tests/routing/test_faehren_integration.py
git commit -m "test(routing): add live GraphHopper integration tests for ferry avoidance

Reproduces the three scenarios manually validated during design
(baseline uses ferry, global avoidance detours ~500km, specific
exclusion reroutes) against the real docker-compose GraphHopper
server."
```

---

### Task 8: `create_trip_simulation` route observer + production routing entry point switch

**Files:**

- Modify: `src/tripplanner/trip_input/api.py:8-16,77-98,444-479` (imports, `_step_1_route_berechnen`, `create_trip_simulation`)
- Test: `tests/trip_input/test_api.py`

**Interfaces:**

- Produces: `create_trip_simulation(..., route_observer: Callable[[Route], None] | None = None)` — purely additive, optional parameter, default `None` preserves exact current behavior for all 9 existing call sites. `_step_1_route_berechnen` now calls `provider.berechne_route(anfrage)` instead of `provider.berechne_route_mit_waypoints(...)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/trip_input/test_api.py`, near the other `create_trip_simulation` tests (after the imports, find a location following the existing test style — use `grep -n "async def test_create_trip_simulation" tests/trip_input/test_api.py` to locate a good insertion point):

```python
async def test_create_trip_simulation_calls_route_observer_with_computed_route(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """route_observer wird nach Schritt 1 mit der berechneten Route aufgerufen."""
    captured: list[Route] = []

    await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
        route_observer=captured.append,
    )

    assert len(captured) == 1
    assert isinstance(captured[0], Route)
    assert captured[0].gesamtlaenge_m > 0


async def test_create_trip_simulation_without_route_observer_unaffected(
    valid_trip_request: dict,
    fake_routing_provider: FakeRoutingProvider,
    fake_weather_provider: FakeWeatherProvider,
    fake_charging_provider_berlin_munich: FakeChargingStationProvider,
) -> None:
    """Without route_observer (default None), the function behaves unchanged."""
    result = await create_trip_simulation(
        valid_trip_request,
        routing_provider=fake_routing_provider,
        weather_provider=fake_weather_provider,
        charging_provider=fake_charging_provider_berlin_munich,
    )

    assert result.gesamt_distanz_km > 0
```

Add `Route` to the imports at the top of `tests/trip_input/test_api.py` if not already present:

```python
from tripplanner.routing.models import Route, RouteSegment
```

(`RouteSegment` is already imported per the existing file — check before duplicating; only add `Route` to that existing import line.)

Also add a test proving the switch to `berechne_route` actually threads ferry preferences through end-to-end at the provider level (this is the load-bearing regression check for Task 8):

```python
async def test_step_1_route_berechnen_uses_berechne_route_not_waypoints(
    valid_trip_request: dict,
) -> None:
    """_step_1_route_berechnen() calls berechne_route() (not berechne_route_mit_waypoints()), 
    so that TripRequest preferences (e.g. ferry avoidance) reach the provider."""

    class _RecordingProvider(FakeRoutingProvider):
        def __init__(self) -> None:
            self.berechne_route_called_with: TripRequest | None = None

        async def berechne_route(self, anfrage: TripRequest) -> Route:
            self.berechne_route_called_with = anfrage
            return await super().berechne_route(anfrage)

    provider = _RecordingProvider()
    anfrage = TripRequest.model_validate(valid_trip_request)

    await trip_api._step_1_route_berechnen(anfrage, provider)

    assert provider.berechne_route_called_with is anfrage
```

Add `from tripplanner.trip_input.models import TripRequest, VehicleProfile, Waypoint` — check the existing import line at the top of the file (already imports `VehicleProfile, Waypoint` from `tripplanner.trip_input.models`); add `TripRequest` to it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/trip_input/test_api.py -k "route_observer or berechne_route_not_waypoints" -v`
Expected: FAIL — `route_observer` is an unexpected keyword argument; `_RecordingProvider.berechne_route_called_with` stays `None` (current code calls `berechne_route_mit_waypoints`).

- [ ] **Step 3: Implement**

In `src/tripplanner/trip_input/api.py`, update the `collections.abc` import (line 13):

```python
from collections.abc import AsyncIterator, Callable
```

Replace `_step_1_route_berechnen` (lines 77-98):

```python
async def _step_1_route_berechnen(
    anfrage: TripRequest,
    routing_provider: RoutingProvider | None = None,
) -> Route:
    """Step 1: Calculate OSM route (including waypoints as mandatory waypoints). 

    `FakeRoutingProvider` is used as the default provider so the pipeline 
    runs without a real GraphHopper server. For production, a real provider
    such as `GraphHopperRoutingProvider` can be passed. Calls `berechne_route()`
    (not `berechne_route_mit_waypoints()`) so that preferences from `anfrage`
    (e.g. ferry avoidance) reach the provider. 
    """
    provider = routing_provider or FakeRoutingProvider()
    return await provider.berechne_route(anfrage)
```

In `create_trip_simulation`'s signature (lines 444-453), add the new parameter as the last one:

```python
async def create_trip_simulation(  # noqa: PLR0913, PLR0917
    anfrage_dict: dict[str, object],
    routing_provider: RoutingProvider | None = None,
    elevation_provider: ElevationProvider | None = None,
    weather_provider: FakeWeatherProvider | None = None,
    construction_provider: FakeConstructionProvider | None = None,
    charging_provider: ChargingStationProvider | None = None,
    start_soc_pct: float = 80.0,
    ziel_soc_pct: float = 20.0,
    route_observer: Callable[[Route], None] | None = None,
) -> TripSimulationResult:
```

Add to its docstring `Args:` block (after `ziel_soc_pct`):

```
        route_observer: Optionaler Callback, der unmittelbar nach Schritt 1 (Routing)
            called with the computed route - e.g. to extract detected 
            ferry connections without calling GraphHopper a second time
(see `create_trip_endpoint`). 
```

In the function body, replace:

```python
    # 2. Step 1: Route berechnen
    route = await _step_1_route_berechnen(anfrage, routing_provider)
```

with:

```python
    # 2. Step 1: Route berechnen
    route = await _step_1_route_berechnen(anfrage, routing_provider)
    if route_observer is not None:
        route_observer(route)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/trip_input/test_api.py -v`
Expected: PASS (all tests in the file, including the ~9 pre-existing `create_trip_simulation` call sites — this confirms the switch from `berechne_route_mit_waypoints` to `berechne_route` is behaviorally transparent to them)

- [ ] **Step 5: Run the routing suite too (FakeRoutingProvider.berechne_route is exercised more now)**

Run: `uv run pytest tests/routing/ tests/trip_input/ -v`
Expected: PASS

- [ ] **Step 6: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/trip_input/api.py tests/trip_input/test_api.py
git commit -m "feat(trip_input): route production routing through berechne_route()

_step_1_route_berechnen now calls berechne_route(anfrage) instead of
berechne_route_mit_waypoints(...), so TripRequest preferences (ferry
avoidance) reach GraphHopperRoutingProvider. Behaviorally equivalent
point-building; berechne_route_mit_waypoints keeps its existing
signature/tests, just no longer the production entry point.

Also adds an optional route_observer callback to create_trip_simulation
so callers (the /trips endpoint) can inspect the computed Route without
a second GraphHopper call - purely additive, default None, zero
behavior change for existing callers."
```

---

### Task 9: `/trips` API — ferry request/response fields

**Files:**

- Modify: `src/tripplanner/trip_input/api.py:43-56,767-921` (imports, new API models, `TripRequestAPI`, `TripSimulationResultAPI`, `create_trip_endpoint`)
- Test: `tests/trip_input/test_api.py`

**Interfaces:**

- Produces: `TripRequestAPI.alle_faehren_vermeiden: bool`, `TripRequestAPI.vermiedene_faehren: list[FaehrAusschlussAPI]`, `TripSimulationResultAPI.erkannte_faehren: list[FaehrSegmentAPI]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/trip_input/test_api.py`, near `test_fastapi_endpoint_creates_trip`:

```python
def test_fastapi_endpoint_response_includes_erkannte_faehren_key(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Response contains the erkannte_faehren key (empty, because FakeRoutingProvider 
    does not supply road_environment data)."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
    data = response.json()
    assert data["erkannte_faehren"] == []


def test_fastapi_endpoint_accepts_ferry_avoidance_fields(
    client: TestClient, valid_trip_request: dict
) -> None:
    """Endpunkt akzeptiert alle_faehren_vermeiden und vermiedene_faehren fehlerfrei."""
    api_request = {
        "start": valid_trip_request["start"],
        "ziel": valid_trip_request["ziel"],
        "zwischenstopps": [],
        "abfahrtszeit": valid_trip_request["abfahrtszeit"].isoformat(),
        "fahrzeugprofil": valid_trip_request["fahrzeugprofil"].model_dump(),
        "praeferenzen": {},
        "alle_faehren_vermeiden": True,
        "vermiedene_faehren": [
            {"name": "Testfähre", "bbox_sw": [54.0, 11.0], "bbox_no": [55.0, 12.0]}
        ],
    }

    response = client.post("/trips", json=api_request)

    assert response.status_code == 201
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/trip_input/test_api.py -k "erkannte_faehren or ferry_avoidance_fields" -v`
Expected: FAIL — `KeyError: 'erkannte_faehren'` / `422 Unprocessable Entity` (extra fields rejected... actually Pydantic ignores unknown fields by default unless `extra="forbid"`; check current model config — if it currently accepts unknown fields silently, this second test would already return 201 even before the change, but `alle_faehren_vermeiden` would be silently dropped and have no effect. The FIRST test (`erkannte_faehren` key) is the one that reliably fails pre-implementation.)

- [ ] **Step 3: Implement**

In `src/tripplanner/trip_input/api.py`, update imports:

- Line 43-48, add `erkenne_faehren`:

```python
from tripplanner.routing import (
    FakeRoutingProvider,
    GraphHopperClient,
    GraphHopperRoutingProvider,
    RoutingProvider,
    erkenne_faehren,
)
```

- Line 49, add `FaehrSegment`:

```python
from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment
```

- Line 52, add `FaehrAusschluss`:

```python
from tripplanner.trip_input.models import FaehrAusschluss, TripRequest, VehicleProfile, Waypoint
```

Add two new API models just before `class TripRequestAPI` (before line 781):

```python
class FaehrAusschlussAPI(BaseModel):
    """API request for a previously detected ferry connection to be avoided."""

    name: str = Field(..., description="Display name of the ferry connection")
    bbox_sw: tuple[float, float] = Field(..., description="Southwest corner of the bounding box")
    bbox_no: tuple[float, float] = Field(..., description="Nordost-Ecke der Bounding Box")
```

Update `TripRequestAPI` (lines 781-795), adding two fields at the end:

```python
class TripRequestAPI(BaseModel):
    """API request for /trips endpoint."""

    start: tuple[float, float] = Field(..., description="(lat, lon) start coordinate")
    ziel: tuple[float, float] = Field(..., description="(lat, lon) target coordinate")
    zwischenstopps: list[WaypointAPI] = Field(
        default_factory=list, description="List of waypoints"
    )
    abfahrtszeit: str = Field(
        ..., description="ISO-8601 Abfahrtszeit (z. B. '2026-08-15T08:30:00')"
    )
    fahrzeugprofil: VehicleProfile = Field(..., description="Physikalisches Fahrzeugprofil")
    start_soc_pct: float = Field(80.0, ge=0.0, le=100.0, description="Start SoC in percent")
    ziel_soc_pct: float = Field(20.0, ge=0.0, le=100.0, description="Target SoC in percent")
    praeferenzen: dict[str, object] = Field(default_factory=dict, description="User preferences")
    alle_faehren_vermeiden: bool = Field(
        default=False, description="When True, all ferry connections are excluded"
    )
    vermiedene_faehren: list[FaehrAusschlussAPI] = Field(
        default_factory=list,
        description=(
            "List of specific, previously detected ferry connections to be excluded"
        ),
    )
```

Add a new API model just before `class TripSimulationResultAPI` (before line 821):

```python
class FaehrSegmentAPI(BaseModel):
    """API response for a ferry connection detected in the computed route."""

    name: str = Field(..., description="Ferry name (from GraphHopper street_name or fallback)")
    laenge_m: float = Field(..., ge=0, description="Length of the ferry connection in meters")
    bbox_sw: tuple[float, float] = Field(
        ..., description="Southwest corner of the buffered bounding box"
    )
    bbox_no: tuple[float, float] = Field(
        ..., description="Northeast corner of the buffered bounding box"
    )
```

Update `TripSimulationResultAPI` (lines 821-832), adding a field at the end:

```python
class TripSimulationResultAPI(BaseModel):
    """API response for /trips endpoint."""

    gesamt_distanz_km: float = Field(..., description="Total distance in km")
    gesamt_fahrzeit_min: float = Field(..., description="Total driving time in minutes")
    gesamt_ladezeit_min: float = Field(..., description="Total charging time in minutes")
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Target SoC in %")
    frames: list[FrameAPI] = Field(..., description="List of simulation frames")
    charging_stops: list[ChargingStopAPI] = Field(
        default_factory=list, description="One entry per charging stop, for map display"
    )
    erkannte_faehren: list[FaehrSegmentAPI] = Field(
        default_factory=list,
        description="Ferry connections detected in the computed route (empty if none)", ,
    )
```

In `create_trip_endpoint`, update `anfrage_dict` (lines 850-868) to add the two request fields:

```python
anfrage_dict: dict[str, object] = {
    "start": request.start,
    "ziel": request.ziel,
    "zwischenstopps": [
        {
            "koordinate": wp.koordinate,
            "aufenthaltsdauer": timedelta(seconds=wp.aufenthaltsdauer_s)
            if wp.aufenthaltsdauer_s
            else None,
            "geplante_abfahrt": datetime.fromisoformat(wp.geplante_abfahrt)
            if wp.geplante_abfahrt
            else None,
        }
        for wp in request.zwischenstopps
    ],
    "abfahrtszeit": request.abfahrtszeit,
    "fahrzeugprofil": request.fahrzeugprofil.model_dump(),
    "praeferenzen": request.praeferenzen,
    "alle_faehren_vermeiden": request.alle_faehren_vermeiden,
    "vermiedene_faehren": [
        {"name": f.name, "bbox_sw": f.bbox_sw, "bbox_no": f.bbox_no}
        for f in request.vermiedene_faehren
    ],
}

erkannte_route: Route | None = None


def _route_erfassen(route: Route) -> None:
    nonlocal erkannte_route
    erkannte_route = route
```

Update the `try` block (lines 870-906): add `route_observer=_route_erfassen` to the `create_trip_simulation(...)` call, compute `erkannte_faehren` right after, and add `erkannte_faehren=[...]` to the returned `TripSimulationResultAPI`:

```python
try:
    ergebnis = await create_trip_simulation(
        anfrage_dict,
        routing_provider=routing_provider,
        charging_provider=charging_provider,
        start_soc_pct=request.start_soc_pct,
        ziel_soc_pct=request.ziel_soc_pct,
        route_observer=_route_erfassen,
    )

    erkannte_faehren = erkenne_faehren(erkannte_route) if erkannte_route is not None else []

    return TripSimulationResultAPI(
        gesamt_distanz_km=ergebnis.gesamt_distanz_km,
        gesamt_fahrzeit_min=ergebnis.gesamt_fahrzeit_min,
        gesamt_ladezeit_min=ergebnis.gesamt_ladezeit_min,
        start_soc_pct=ergebnis.start_soc_pct,
        ziel_soc_pct=ergebnis.ziel_soc_pct,
        frames=[
            FrameAPI(
                zeitpunkt=f.zeitpunkt.isoformat(),
                position=f.position,
                soc_pct=f.soc_pct,
                zustand=f.zustand.value,
                geschwindigkeit_kmh=f.geschwindigkeit_kmh,
            )
            for f in ergebnis.frames
        ],
        charging_stops=[
            ChargingStopAPI(
                name=stop.name,
                position=stop.position,
                ankunfts_soc_pct=stop.ankunfts_soc_pct,
                ziel_soc_pct=stop.ziel_soc_pct,
                ladedauer_s=stop.ladedauer_s,
                energie_geladen_kwh=stop.energie_geladen_kwh,
            )
            for stop in ergebnis.charging_stops
        ],
        erkannte_faehren=[
            FaehrSegmentAPI(name=f.name, laenge_m=f.laenge_m, bbox_sw=f.bbox_sw, bbox_no=f.bbox_no)
            for f in erkannte_faehren
        ],
    )
except ValueError as e:
    raise HTTPException(
        status_code=422,
        detail=f"Route not feasible: {e!s}",
    ) from e
except httpx.HTTPError as e:
    raise HTTPException(
        status_code=502,
        detail=f"Routing-Server (GraphHopper) nicht erreichbar oder lieferte einen Fehler: {e}",
    ) from e
except Exception as e:
    logger.exception(
        "Fehler bei der Routensimulation: %s", e, extra={"traceback": traceback.format_exc()}
    )
    raise HTTPException(status_code=500, detail=f"Simulation failed: {e!s}") from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/trip_input/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Run the full non-integration suite**

Run: `uv run pytest -m "not integration"`
Expected: PASS, coverage ≥ 85%.

- [ ] **Step 6: Lint/type-check and commit**

Run: `uv run hk check --all`

```bash
git add src/tripplanner/trip_input/api.py tests/trip_input/test_api.py
git commit -m "feat(trip_input): expose ferry avoidance on the /trips API

TripRequestAPI gains alle_faehren_vermeiden/vermiedene_faehren;
TripSimulationResultAPI gains erkannte_faehren (populated via the
route_observer callback from Task 8 + erkenne_faehren(), no duplicate
GraphHopper call)."
```

---

### Task 10: Frontend types

**Files:**

- Modify: `frontend/src/types.ts` (add `FaehrSegment`, extend `TripSimulationResult`)
- Modify: `frontend/src/types/trip-request.ts` (add `FaehrAusschluss`, extend `TripRequestPayload` and `buildTripRequestPayload`)
- Test: `frontend/tests/unit/trip-request.test.ts` (new file)

**Interfaces:**

- Produces: `FaehrSegment`, `FaehrAusschluss` TS interfaces; `TripRequestPayload.alle_faehren_vermeiden`/`vermiedene_faehren`; `TripSimulationResult.erkannte_faehren`; `buildTripRequestPayload({..., alleFaehrenVermeiden?, vermiedeneFaehren?})`.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/trip-request.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { buildTripRequestPayload, createEmptyStop } from "@/types/trip-request";
import type { Stop, VehicleProfileInput } from "@/types/trip-request";

const vehicleProfile: VehicleProfileInput = {
  masse_kg: 1706,
  cw_wert: 0.23,
  stirnflaeche_m2: 2.22,
  rollwiderstandsbeiwert: 0.011,
  batteriekapazitaet_kwh: 62.5,
  nebenverbraucher_baseline_kw: 0.34,
  reifentyp: "standard",
  dachbox: false,
};

function makeStops(): Stop[] {
  return [
    { ...createEmptyStop(), address: "Berlin", position: [52.52, 13.405] },
    { ...createEmptyStop(), address: "Hamburg", position: [53.551, 9.993] },
  ];
}

describe("buildTripRequestPayload ferry fields", () => {
  it("defaults alle_faehren_vermeiden to false and vermiedene_faehren to empty", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
    });

    expect(payload.alle_faehren_vermeiden).toBe(false);
    expect(payload.vermiedene_faehren).toEqual([]);
  });

  it("passes through explicit ferry avoidance values", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
      alleFaehrenVermeiden: true,
      vermiedeneFaehren: [
        { name: "Rødby (DK) - Puttgarden (D)", bbox_sw: [54.5, 11.22], bbox_no: [54.66, 11.36] },
      ],
    });

    expect(payload.alle_faehren_vermeiden).toBe(true);
    expect(payload.vermiedene_faehren).toHaveLength(1);
    expect(payload.vermiedene_faehren[0].name).toBe("Rødby (DK) - Puttgarden (D)");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/trip-request.test.ts`
Expected: FAIL — TypeScript error, `alleFaehrenVermeiden`/`vermiedeneFaehren` do not exist on the args type; `payload.alle_faehren_vermeiden` is `undefined`.

- [ ] **Step 3: Implement — `types/trip-request.ts`**

Add a new interface after `WaypointInput` (before `TripRequestPayload`, around line 82):

```typescript
/** A (buffered) bounding box around a detected ferry connection, for exclusion 
 *  in a subsequent route calculation (`FaehrAusschlussAPI`). */
export interface FaehrAusschluss {
  name: string;
  bbox_sw: [number, number];
  bbox_no: [number, number];
}
```

Update `TripRequestPayload` (around line 85-95) to add two fields:

```typescript
export interface TripRequestPayload {
  start: [number, number];
  ziel: [number, number];
  zwischenstopps: WaypointInput[];
  /** ISO-8601, z. B. "2026-08-15T08:30:00". */
  abfahrtszeit: string;
  fahrzeugprofil: VehicleProfileInput;
  praeferenzen: Record<string, unknown>;
  start_soc_pct: number;
  ziel_soc_pct: number;
  alle_faehren_vermeiden: boolean;
  vermiedene_faehren: FaehrAusschluss[];
}
```

Update `buildTripRequestPayload`'s args type and return object:

```typescript
export function buildTripRequestPayload(args: {
  stops: Stop[];
  fahrzeugprofil: VehicleProfileInput;
  startSocPct: number;
  zielSocPct: number;
  praeferenzen?: Record<string, unknown>;
  alleFaehrenVermeiden?: boolean;
  vermiedeneFaehren?: FaehrAusschluss[];
}): TripRequestPayload {
```

(keep the existing validation logic in the function body unchanged) and update the returned object literal to add:

```typescript
    alle_faehren_vermeiden: args.alleFaehrenVermeiden ?? false,
    vermiedene_faehren: args.vermiedeneFaehren ?? [],
```

(as the last two entries of the returned object, after `ziel_soc_pct: args.zielSocPct,`).

- [ ] **Step 4: Implement — `types.ts`**

Add a new interface after `Waypoint` (end of file):

```typescript

/** A ferry connection detected in the computed route (`FaehrSegmentAPI`). */
export interface FaehrSegment {
  name: string;
  laenge_m: number;
  bbox_sw: [number, number];
  bbox_no: [number, number];
}
```

Update `TripSimulationResult` to add the field after `charging_stops`:

```typescript
export interface TripSimulationResult {
  /** Liste der Simulationsframes */
  frames: SimulationFrame[];
  /** Gesamtdistanz in km */
  gesamt_distanz_km: number;
  /** Gesamtfahrzeit in Minuten */
  gesamt_fahrzeit_min: number;
  /** Gesamtladezeit in Minuten */
  gesamt_ladezeit_min: number;
  /** Start-SoC in % */
  start_soc_pct: number;
  /** Ziel-SoC in % */
  ziel_soc_pct: number;
  /** Charging stops (one entry per actual stop, not per frame) */
  charging_stops: ChargingStop[];
  /** Ferry connections detected in the computed route */
  erkannte_faehren: FaehrSegment[];
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/trip-request.test.ts`
Expected: PASS

- [ ] **Step 6: Run the full frontend test suite, lint, typecheck**

Run: `cd frontend && npx vitest run && npm run lint && npm run typecheck`
Expected: PASS (existing tests unaffected — `TripSimulationResult.erkannte_faehren` is a new required field; check whether any existing test constructs a `TripSimulationResult` object literal that would now fail typecheck, e.g. in `frontend/tests/unit/timing-utils.test.ts` or similar — if so, add `erkannte_faehren: []` to those literals as part of this step)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/types/trip-request.ts frontend/tests/unit/trip-request.test.ts
git commit -m "feat(frontend): add ferry avoidance types

FaehrAusschluss (request), FaehrSegment (response) - snake_case wire
fields mirroring the backend API 1:1, matching the existing
convention (no camelCase translation layer)."
```

---

### Task 11: `TripPlannerForm.tsx` — ferry UI

**Files:**

- Modify: `frontend/src/components/TripPlannerForm.tsx`
- Test: `frontend/tests/unit/trip-planner-form-utils.test.ts`

**Interfaces:**

- Consumes: `FaehrAusschluss` (Task 10, from `../types/trip-request`), `FaehrSegment` (Task 10, from `../types`).
- Produces: `TripPlannerFormProps.erkannteFaehren?: FaehrSegment[]` (new optional prop); exported pure helpers `sameFaehrAusschluss`, `toggleFaehrAusschluss` (for testing, matching the file's existing convention of exporting pure helpers alongside the component).

- [ ] **Step 1: Write the failing tests**

Add to `frontend/tests/unit/trip-planner-form-utils.test.ts`, inside the outer `describe("TripPlannerForm pure helpers", () => { ... })` block, after the `validateForm` describe block:

```typescript
  // =========================================================================
  // sameFaehrAusschluss / toggleFaehrAusschluss
  // =========================================================================

  describe("toggleFaehrAusschluss", () => {
    const faehre = {
      name: "Rødby (DK) - Puttgarden (D)",
      laenge_m: 22000,
      bbox_sw: [54.5, 11.22] as [number, number],
      bbox_no: [54.66, 11.36] as [number, number],
    };

    it("adds the ferry when toggled on and not already present", () => {
      const result = toggleFaehrAusschluss([], faehre, true);
      expect(result).toHaveLength(1);
      expect(result[0].name).toBe(faehre.name);
    });

    it("does not duplicate the ferry when toggled on twice", () => {
      const once = toggleFaehrAusschluss([], faehre, true);
      const twice = toggleFaehrAusschluss(once, faehre, true);
      expect(twice).toHaveLength(1);
    });

    it("removes the ferry when toggled off", () => {
      const withFaehre = toggleFaehrAusschluss([], faehre, true);
      const result = toggleFaehrAusschluss(withFaehre, faehre, false);
      expect(result).toHaveLength(0);
    });

    it("toggling off an absent ferry is a no-op", () => {
      const result = toggleFaehrAusschluss([], faehre, false);
      expect(result).toHaveLength(0);
    });
  });
```

Update the imports at the top of the file:

```typescript
import { describe, it, expect } from "vitest";
import {
  getStopRole,
  isRawCoordinateLabel,
  isUnresolvedAddress,
  swapStops,
  validateForm,
  toggleFaehrAusschluss,
} from "@/components/TripPlannerForm";
import type { Stop } from "@/types/trip-request";
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/unit/trip-planner-form-utils.test.ts`
Expected: FAIL — `toggleFaehrAusschluss` is not exported.

- [ ] **Step 3: Implement**

In `frontend/src/components/TripPlannerForm.tsx`, add imports (near the top, with the other type imports):

```typescript
import type {
  Stop,
  VehicleProfileInput,
  TripRequestPayload,
  FaehrAusschluss,
} from "../types/trip-request";
import type { FaehrSegment } from "../types";
```

Add `erkannteFaehren` to `TripPlannerFormProps`:

```typescript
export interface TripPlannerFormProps {
  stops: Stop[];
  onStopsChange: (stops: Stop[]) => void;
  pickingStopId: string | null;
  onRequestPick: (stopId: string | null) => void;
  onSubmit: (payload: TripRequestPayload) => void;
  isSubmitting: boolean;
  submitError?: string | null;
  /** Ferry connections that were detected in the most recently computed route
   *  (from `TripSimulationResult.erkannte_faehren`), displayed as 
   *  "avoid" checkboxes. `undefined`/empty until a route has been computed. */
   *  berechnet wurde. */
  erkannteFaehren?: FaehrSegment[];
}
```

Add two exported pure helper functions after `validateForm` (before the `// Component` section header):

```typescript
/** Compares two FaehrAusschluss entries for content equality. */
export function sameFaehrAusschluss(a: FaehrAusschluss, b: FaehrAusschluss): boolean {
  return (
    a.name === b.name &&
    a.bbox_sw[0] === b.bbox_sw[0] &&
    a.bbox_sw[1] === b.bbox_sw[1] &&
    a.bbox_no[0] === b.bbox_no[0] &&
    a.bbox_no[1] === b.bbox_no[1]
  );
}

/** Adds or removes a detected ferry connection from the exclusion list. */
export function toggleFaehrAusschluss(
  liste: FaehrAusschluss[],
  faehre: FaehrSegment,
  vermeiden: boolean,
): FaehrAusschluss[] {
  const eintrag: FaehrAusschluss = {
    name: faehre.name,
    bbox_sw: faehre.bbox_sw,
    bbox_no: faehre.bbox_no,
  };
  const bereitsVorhanden = liste.some((f) => sameFaehrAusschluss(f, eintrag));
  if (vermeiden) {
    return bereitsVorhanden ? liste : [...liste, eintrag];
  }
  return liste.filter((f) => !sameFaehrAusschluss(f, eintrag));
}
```

Update the component's destructured props to include `erkannteFaehren`:

```typescript
export function TripPlannerForm({
  stops,
  onStopsChange,
  pickingStopId,
  onRequestPick,
  onSubmit,
  isSubmitting,
  submitError,
  erkannteFaehren,
}: TripPlannerFormProps) {
```

Add local state near `startSoc`/`zielSoc`:

```typescript
  const [alleFaehrenVermeiden, setAlleFaehrenVermeiden] = useState(false);
  const [vermiedeneFaehren, setVermiedeneFaehren] = useState<FaehrAusschluss[]>([]);
```

Replace the `handleSubmit` function body with a reusable `buildAndSubmit`, so both the submit button and the per-ferry checkboxes can trigger a request with an up-to-date `vermiedeneFaehren` value (avoiding a stale-closure read of React state):

```typescript
  const buildAndSubmit = (alleFaehren: boolean, vermiedene: FaehrAusschluss[]) => {
    const errors = validateForm({ stops, startSoc, zielSoc });

    if (errors.length > 0) {
      return;
    }

    try {
      const payload = buildTripRequestPayload({
        stops,
        fahrzeugprofil: vehicleProfile,
        startSocPct: startSoc,
        zielSocPct: zielSoc,
        praeferenzen: {},
        alleFaehrenVermeiden: alleFaehren,
        vermiedeneFaehren: vermiedene,
      });
      onSubmit(payload);
    } catch (error) {
      if (error instanceof TripRequestBuildError) {
        // Passed as submitError to the parent component
        // We catch it here as an additional inline error message
        // (the props integration can also handle this via submitError)
      }
    }
  };

  const handleSubmit = () => buildAndSubmit(alleFaehrenVermeiden, vermiedeneFaehren);
```

Insert a new "Ferries" `<fieldset>` between the "Ladestand" fieldset and the submit `<button>` (i.e. right after the closing `</fieldset>` of the "Ladestand" section, before the `{/* 4. Submit */}` comment):

```typescript
      {/* 3.5 Ferries */}
      <fieldset
        style={{
          marginBottom: "1.5rem",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          padding: "1rem",
        }}
      >
        <legend style={{ fontWeight: 600, padding: "0 0.5rem" }}>Ferries</legend>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <input
            type="checkbox"
            checked={alleFaehrenVermeiden}
            onChange={(e) => setAlleFaehrenVermeiden(e.target.checked)}
            id="alle-faehren-vermeiden-checkbox"
            disabled={isSubmitting}
          />
          <label htmlFor="alle-faehren-vermeiden-checkbox">Avoid all ferries</label>
        </div>
        {erkannteFaehren && erkannteFaehren.length > 0 && (
          <div style={{ marginTop: "0.75rem", display: "grid", gap: "0.4rem" }}>
            <p style={{ margin: 0, fontSize: "0.8rem", fontWeight: 500 }}>
              Ferries used in the last route:
            </p>
            {erkannteFaehren.map((faehre, idx) => {
              const checkboxId = `faehre-vermeiden-${idx}`;
              const vermieden = vermiedeneFaehren.some((f) =>
                sameFaehrAusschluss(f, {
                  name: faehre.name,
                  bbox_sw: faehre.bbox_sw,
                  bbox_no: faehre.bbox_no,
                }),
              );
              return (
                <div
                  key={checkboxId}
                  style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}
                >
                  <input
                    type="checkbox"
                    checked={vermieden}
                    onChange={(e) => {
                      const next = toggleFaehrAusschluss(
                        vermiedeneFaehren,
                        faehre,
                        e.target.checked,
                      );
                      setVermiedeneFaehren(next);
                      buildAndSubmit(alleFaehrenVermeiden, next);
                    }}
                    id={checkboxId}
                    disabled={isSubmitting}
                  />
                  <label htmlFor={checkboxId}>
                    {faehre.name} vermeiden ({(faehre.laenge_m / 1000).toFixed(1)} km)
                  </label>
                </div>
              );
            })}
          </div>
        )}
      </fieldset>

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/unit/trip-planner-form-utils.test.ts`
Expected: PASS

- [ ] **Step 5: Run full frontend verification**

Run: `cd frontend && npx vitest run && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/TripPlannerForm.tsx frontend/tests/unit/trip-planner-form-utils.test.ts
git commit -m "feat(frontend): add ferry avoidance UI to TripPlannerForm

Global 'Avoid all ferries' checkbox (always available) plus
per-detected-ferry checkboxes populated from erkannteFaehren (the
previous simulation result), auto-resubmitting on toggle."
```

---

### Task 12: `App.tsx` + `TripSummary.tsx` wiring

**Files:**

- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/TripSummary.tsx`

**Interfaces:**

- Consumes: `TripPlannerFormProps.erkannteFaehren` (Task 11), `TripSimulationResult.erkannte_faehren` (Task 10).

- [ ] **Step 1: Wire `App.tsx`**

In `frontend/src/App.tsx`, add the `erkannteFaehren` prop to the `<TripPlannerForm>` element:

```tsx
      <TripPlannerForm
        stops={stops}
        onStopsChange={setStops}
        pickingStopId={pickingStopId}
        onRequestPick={setPickingStopId}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
        submitError={submitError}
        erkannteFaehren={simulationResult?.erkannte_faehren}
      />
```

- [ ] **Step 2: Surface detected ferries in `TripSummary.tsx`**

In `frontend/src/components/TripSummary.tsx`, add a conditional row to the summary `<table>`'s `<tbody>`, after the "Ziel-SoC" row:

```tsx
          {result.erkannte_faehren.length > 0 && (
            <tr>
              <td style={labelCellStyle}>Ferries</td>
              <td style={valueCellStyle}>
                {result.erkannte_faehren.map((f) => f.name).join(", ")}
              </td>
            </tr>
          )}
```

- [ ] **Step 3: Run full frontend verification**

Run: `cd frontend && npx vitest run && npm run lint && npm run typecheck && npm run build`
Expected: PASS (the `build` step catches any remaining type errors in files not covered by unit tests, e.g. `App.tsx`/`TripSummary.tsx`)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/TripSummary.tsx
git commit -m "feat(frontend): surface detected/avoidable ferries in App and TripSummary

App passes the last result's erkannte_faehren down to TripPlannerForm
for the avoid-ferry checkboxes; TripSummary shows ferry names used by
the current result."
```

---

### Task 13: Docs update + full-stack verification

**Files:**

- Modify: `docs/03-module-specifications.md` (routing/trip_input module bullet points)

**Interfaces:** none (documentation + end-to-end verification only).

- [ ] **Step 1: Update the module spec doc**

In `docs/03-module-specifications.md`, locate the `routing` module's "Ausgaben" bullet (around line 13) and append a sentence noting ferry detection; locate the `trip_input` module's description of `TripRequest`/`praeferenzen` and note the two typed ferry-avoidance fields. Read the file first to get exact line numbers before editing (structure may differ slightly from the routing-section excerpt seen during design), then make a minimal one-or-two-sentence addition per module — do not restructure the document.

- [ ] **Step 2: Full backend verification**

Run: `uv run hk check --all`
Expected: PASS

Run: `uv run pytest -m "not integration"`
Expected: PASS, coverage ≥ 85%.

Run: `uv run pytest -m integration`
Expected: PASS (requires the local GraphHopper server up — `docker compose up -d graphhopper` if not already running).

- [ ] **Step 3: Full frontend verification**

Run: `cd frontend && npx vitest run && npm run lint && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 4: End-to-end smoke test via `./run.sh`**

Per `AGENTS.md`, start both services through the project script (not `uvicorn`/`npm run dev` directly):

```bash
./run.sh restart
./run.sh status
```

Then, with the backend up, reproduce the live-validated ferry scenario through the actual `/trips` endpoint:

```bash
curl -s -X POST http://localhost:8000/trips -H "Content-Type: application/json" -d '{
  "start": [54.5033, 11.2270],
  "ziel": [54.6558, 11.3453],
  "zwischenstopps": [],
  "abfahrtszeit": "2026-08-15T08:00:00",
  "fahrzeugprofil": {
    "masse_kg": 1706.0, "cw_wert": 0.23, "stirnflaeche_m2": 2.22,
    "rollwiderstandsbeiwert": 0.011, "batteriekapazitaet_kwh": 62.5
  },
  "praeferenzen": {}
}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('erkannte_faehren:', d['erkannte_faehren'])"
```

Expected: `erkannte_faehren` contains one entry with `"name"` mentioning `Rødby`/`Puttgarden`.

Then verify avoidance actually reroutes:

```bash
curl -s -X POST http://localhost:8000/trips -H "Content-Type: application/json" -d '{
  "start": [54.5033, 11.2270],
  "ziel": [54.6558, 11.3453],
  "zwischenstopps": [],
  "abfahrtszeit": "2026-08-15T08:00:00",
  "fahrzeugprofil": {
    "masse_kg": 1706.0, "cw_wert": 0.23, "stirnflaeche_m2": 2.22,
    "rollwiderstandsbeiwert": 0.011, "batteriekapazitaet_kwh": 62.5
  },
  "praeferenzen": {},
  "alle_faehren_vermeiden": true
}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('gesamt_distanz_km:', d['gesamt_distanz_km'], 'erkannte_faehren:', d['erkannte_faehren'])"
```

Expected: `gesamt_distanz_km` far exceeds the direct ~22 km crossing (several hundred km), `erkannte_faehren` is `[]`.

Then verify the frontend renders and drives it, using the `browser` tool against `http://localhost:5173` (or whatever port `./run.sh` reports): set a trip whose direct route uses this ferry crossing (e.g. Hamburg → Copenhagen or the two coordinates above), submit, confirm the "Ferries" section shows the detected crossing and the "avoid" checkbox triggers a visibly different (longer) route on the map.

Once verified, stop the services:

```bash
./run.sh stop
```

- [ ] **Step 5: Commit the docs update**

```bash
git add docs/03-module-specifications.md
git commit -m "docs: mention ferry detection/avoidance in module spec"
```
