# Design: Ferry Avoidance (Global + Specific)

**Status:** Approved
**Date:** 2026-08-15

## 1. Problem

Users planning a trip across Denmark/Germany/Sweden may cross the Baltic/Fehmarnbelt via
car ferry (e.g. GraphHopper resolves `Rødby (DK) - Puttgarden (D)` today). Some users want to:

1. Avoid ferries entirely (cost, schedule dependency, seasickness, etc.).
2. Avoid one specific ferry crossing while still allowing others.

Today `TripRequest.praeferenzen: dict[str, object]` exists but is never read by `routing`
(`_step_1_route_berechnen` in `trip_input/api.py` calls
`RoutingProvider.berechne_route_mit_waypoints(...)`, which only takes bare coordinates —
no preferences reach GraphHopper at all). No ferry data is requested from GraphHopper
(`RouteSegment` has no `road_environment` field), so ferries used by a route are invisible
to both backend and frontend.

## 2. Approach

No hardcoded ferry registry. GraphHopper already returns everything needed to identify a
ferry crossing on a *computed* route: the `road_environment` path detail (encoded value,
values `ROAD`/`FERRY`/`TUNNEL`/`BRIDGE`/`FORD`/`OTHER`) and the `street_name` path detail
(always available, not gated by `graph.encoded_values` — reads the OSM `name` tag/relation
directly). Verified live against the project's GraphHopper 11.0 instance
(`docker-compose.yml`, DE+DK+SE extract):

```
POST /route {"points":[[11.2270,54.5033],[11.3453,54.6558]], "details":["road_environment","street_name"]}
→ details.road_environment: [[0,15,"road"],[15,29,"ferry"],[29,67,"road"],[67,68,"bridge"],[68,101,"road"]]
→ details.street_name:      [[0,15,null],[15,29,"Rødby (DK) - Puttgarden (D)"],[29,52,"Sydmotorvejen"],...]
```

So: compute the route once, detect which ferry segments it actually used (with real
names + exact geometry), show them to the user, let them tick "avoid this ferry", and
recompute with that specific crossing excluded. Self-correcting: if avoiding one ferry
forces the route onto a different one, that one shows up on the next response too, no
maintenance of a coordinate list required, and it works for any ferry present in whatever
OSM extract is loaded (not just a curated DE/DK/SE list).

### 2.1 GraphHopper mechanics (validated live)

- **Global avoidance:** `custom_model.priority: [{"if": "road_environment == FERRY", "multiply_by": 0.0}]`.
  Confirmed: direct Puttgarden→Rødby query drops from 22 km/69 min (ferry) to ~520 km/~5.5 h
  (forced land detour via bridges) when this rule is applied — ferry successfully excluded.
- **Specific-ferry avoidance:** GraphHopper custom areas. `custom_model.areas.<id>` is a
  GeoJSON `Feature`/`Polygon` (coordinates in `[lon, lat]` order, closed ring); reference it
  in a priority rule via `{"if": "in_<id> && road_environment == FERRY", "multiply_by": 0.0}`.
  The area rule now conjoins the area check with `road_environment == FERRY`, so only ferry
  edges inside the buffered box are excluded, not every road in the box. Verified request
  shape is accepted by the live server (only genuinely-unroutable cases, like excluding a
  ferry with no ferry-free path at all, still return "Connection between locations not
  found" — cases where the endpoints fall inside an excluded ferry's bbox but use no ferry
  now reroute successfully instead of HTTP 400/502).
- **`ch.disable: true` is required** whenever `custom_model` is sent — the server runs
  profile `car` in CH ("speed mode"), which rejects `custom_model` outright with
  `"The 'custom_model' parameter is currently not supported for speed mode..."` otherwise.
  Confirmed live. This is a GraphHopper server quirk, not specific to ferries — it must be
  set for *any* future custom_model use too.
- `street_name` is **not** in `/info`'s `encoded_values` (unlike `road_environment`, which
  is) — it must bypass the existing `_ermittele_verfuegbare_path_details()` availability
  filter and always be requested, since that filter only knows about `graph.encoded_values`
  and would otherwise silently drop it.
- Omitting `distance_influence` from `custom_model` entirely is valid (confirmed live) —
  used for the ferry-only case so we don't perturb route selection style when no speed
  profile is otherwise in effect.

## 3. Data model changes

### 3.1 `routing/models.py` (`RouteSegment`)

Add two optional fields, following the existing pattern of `strassenklasse`/`oberflaeche`
(populated from GraphHopper path details, `None` when unavailable):

- `road_environment: str | None` — normalized uppercase (`FERRY`, `ROAD`, `BRIDGE`,
  `TUNNEL`, `FORD`, `OTHER`); GraphHopper returns lowercase (`"ferry"`), so
  `GraphHopperRoutingProvider._map_path_to_route` uppercases it, consistent with how
  `road_class` values are already uppercase constants elsewhere in the model.
- `strassenname: str | None` — raw `street_name` value, empty string treated as `None`.

### 3.2 New file `routing/faehren.py`

```python
class FaehrSegment(BaseModel):
    """Eine im berechneten Route erkannte, zusammenhängende Fährverbindung."""
    name: str  # z. B. "Rødby (DK) - Puttgarden (D)"; Fallback "Unbenannte Fähre" wenn kein street_name
    laenge_m: float
    bbox_sw: Coordinate  # gepufferte Bounding Box, Südwest-Ecke
    bbox_no: Coordinate  # gepufferte Bounding Box, Nordost-Ecke

FAEHR_PUFFER_GRAD: float = 0.005  # ~500 m Puffer um die exakte Segmentgeometrie

def erkenne_faehren(route: Route) -> list[FaehrSegment]:
    """Gruppiert zusammenhängende `road_environment == "FERRY"`-Segmente der Route zu
    FaehrSegment-Einträgen (Name aus `strassenname`, gepufferte Bounding Box aus
    `geometrie`). Segmente ohne road_environment (z. B. FakeRoutingProvider) liefern []."""
```

### 3.3 `trip_input/models.py` (`TripRequest`)

Add two typed sibling fields (matching the existing convention of `start_soc_pct`/
`ziel_soc_pct` living outside `praeferenzen`, not inside it):

```python
class FaehrAusschluss(BaseModel):
    """Eine vom Nutzer zu vermeidende, zuvor per `FaehrSegment` erkannte Fährverbindung."""
    name: str
    bbox_sw: Coordinate
    bbox_no: Coordinate

# on TripRequest:
alle_faehren_vermeiden: bool = Field(default=False, ...)
vermiedene_faehren: list[FaehrAusschluss] = Field(default_factory=list, ...)
```

`FaehrAusschluss` and `FaehrSegment` are intentionally separate, structurally-identical
types (no import from `routing` into `trip_input`, preserving the existing one-directional
module dependency: `routing` depends on `trip_input.models`, not vice versa). The API layer
(`trip_input/api.py`, which already imports both modules) converts between them.

### 3.4 `trip_input/api.py` (`TripRequestAPI`, `TripSimulationResultAPI`)

- `TripRequestAPI`: add `alle_faehren_vermeiden: bool = False` and
  `vermiedene_faehren: list[FaehrAusschlussAPI] = []`.
- `TripSimulationResultAPI`: add `erkannte_faehren: list[FaehrSegmentAPI]`, populated by
  calling `erkenne_faehren(route)` right after Step 1 in `create_trip_simulation`/
  `create_trip_endpoint`.

## 4. Routing provider changes

### 4.1 `GraphHopperClient.route()`

When `custom_model` is truthy, always set `payload["ch.disable"] = True`. Documented via
inline comment referencing the live-confirmed server error. No signature change.

### 4.2 `GraphHopperRoutingProvider`

- `_ALLE_PATH_DETAILS` gains `"road_environment"` (goes through the existing
  availability filter — it's a real encoded value). `street_name` is requested
  unconditionally, concatenated onto the filtered list, not subject to the filter.
- Production entry point switches from `berechne_route_mit_waypoints(...)` to
  `berechne_route(anfrage)` in `trip_input/api.py`'s `_step_1_route_berechnen` — the two
  are behaviorally equivalent for point-building (both currently discard waypoint
  `aufenthaltsdauer` when calling GraphHopper), but `berechne_route` receives the full
  `TripRequest`, which now carries the avoidance fields. `berechne_route_mit_waypoints`
  was later removed from `GraphHopperRoutingProvider` and from the `RoutingProvider`
  Protocol entirely (final whole-branch review: it never called the ferry-avoidance
  helper below and would have silently dropped preferences if anything had still called
  it) — `FakeRoutingProvider` keeps its own copy as a plain method, unaffected.
- New private helper builds the ferry portion of `custom_model` from
  `anfrage.alle_faehren_vermeiden` / `anfrage.vermiedene_faehren`:
  - Global: appends `{"if": "road_environment == FERRY", "multiply_by": 0.0}` to
    `priority`.
  - Specific: for each `FaehrAusschluss`, generates a stable-per-request area id
    (`f"faehre_{i}"`), builds a GeoJSON `Polygon` Feature from `bbox_sw`/`bbox_no`
    (converting `(lat, lon)` → `[lon, lat]` at this external serialization boundary, per
    the project's documented three GeoJSON conversion points), and appends
    `{"if": "in_faehre_{i} && road_environment == FERRY", "multiply_by": 0.0}` to
    `priority` (the `road_environment == FERRY` conjunct was added in the final
    whole-branch review fix wave — without it the area rule blocked every road inside
    the buffered box, not just the ferry).
  - Merges with the existing (currently dormant in production) `use_custom_model` speed
    profile: if that flag is set, ferry priority rules are appended to the same
    `priority` array (GraphHopper applies independent `if` rules multiplicatively, not as
    an `else_if` chain — confirmed from GraphHopper docs).
  - `distance_influence` is only set (to `0.0`) when `use_custom_model` is also active;
    omitted otherwise (confirmed valid to omit).
  - Returns `None` (no `custom_model` sent at all) when no avoidance is requested and
    `use_custom_model` is `False` — **exact today's behavior preserved**, no regression.
- `FakeRoutingProvider`: accepts the new `TripRequest` fields (already does, via
  `**anfrage` pass-through) but performs no ferry logic — synthetic routes never contain
  `road_environment`, so `erkenne_faehren` naturally returns `[]` for fake routes.

## 5. Frontend changes

### 5.1 Types (`frontend/src/types/trip-request.ts`, `frontend/src/types/*`)

- `TripRequestPayload` (which mirrors `TripRequestAPI` field-for-field, snake_case
  included — see e.g. existing `start_soc_pct`/`zwischenstopps`/`aufenthaltsdauer_s`,
  no camelCase translation layer for backend-bound fields) gains
  `alle_faehren_vermeiden: boolean` and `vermiedene_faehren: FaehrAusschluss[]`, where
  `FaehrAusschluss = { name: string; bbox_sw: [number, number]; bbox_no: [number, number] }`.
  `buildTripRequestPayload` passes these straight through like `start_soc_pct` today —
  no key renaming.
- `TripSimulationResult` gains `erkannte_faehren: FaehrSegment[]`, same shape as
  `FaehrAusschluss` plus `laenge_m: number` (mirrors `FaehrSegmentAPI`/`FaehrSegment`).

### 5.2 `TripPlannerForm.tsx`

- New "Fähren" section (near the SoC/vehicle sections):
  - Checkbox "Alle Fähren vermeiden" — always available, independent of any prior
    computation, wired directly to local `alleFaehrenVermeiden` component state
    (internal state var name is free-form camelCase per usual React/TS convention; only
    the wire payload field is snake_case), serialized as `alle_faehren_vermeiden`.
  - When `simulationResult.erkannte_faehren` is non-empty (passed down from `App.tsx`),
    render one checkbox per detected ferry: `"{name} vermeiden ({laenge_km} km)"`.
    Checking it adds the corresponding `FaehrAusschluss` to local `vermiedeneFaehren`
    state and triggers `onSubmit` again (recompute), serialized as `vermiedene_faehren`.
- `App.tsx`: `vermiedeneFaehren` state persists across further recomputation (e.g. user
  also edits SoC afterward) — accumulated exclusions are not reset until the user
  explicitly unchecks them or clears the trip.

### 5.3 `TripSummary.tsx`

Optionally surface `erkannte_faehren` (ferries actually used in the *current* result)
even when the user hasn't opened the planner form section, so it's visible in the result
view too. Exact placement decided during implementation (not a load-bearing design point).

## 6. Error handling

Unchanged pattern. If ferry exclusion makes a route impossible, GraphHopper returns 400
with `message: "Connection between locations not found"`; `GraphHopperClient.route()`
already extracts and appends this `message` to the raised `HTTPStatusError`, which
`create_trip_endpoint` already maps to HTTP 502 with the GraphHopper detail text. No new
error path needed.

## 7. Testing

- **Unit — `routing/faehren.py`:** `erkenne_faehren` against synthetic `Route`/
  `RouteSegment` fixtures — single contiguous ferry run, multiple disjoint ferry runs,
  no ferry present, missing `strassenname` (fallback name), missing `road_environment`
  (empty result, covers `FakeRoutingProvider`-shaped routes).
- **Unit — `GraphHopperRoutingProvider`:** mock `GraphHopperClient`, assert the
  `custom_model`/`ch.disable` payload shape for: no avoidance (custom_model stays
  `None`), global avoidance only, specific-ferry avoidance only, both combined, combined
  with `use_custom_model=True`.
- **Integration (`@pytest.mark.integration`, real GraphHopper via `docker-compose`):**
  reproduce the manually-validated Puttgarden↔Rødby case — baseline route uses the ferry
  (~22 km), global-avoid reroutes overland (~500+ km, no `FERRY` in `road_environment`
  details), specific-avoid using the detected segment's own bbox reroutes identically.
- **Frontend:** `validateForm`/payload-builder unit tests for the new fields; component
  test asserting the ferry checkboxes render from `erkannteFaehren` and update
  `vermiedeneFaehren` on submit.

## 8. Non-goals

- No persisted/curated ferry database — everything is derived per-request from the live
  GraphHopper response.
- No cost modeling for ferries beyond what already exists (`docs/Tesla-Supercharger-...
  -Scraping.md` mentions a "Fähre" cost line item elsewhere in the project; out of scope
  here — this feature only changes *route selection*, not cost calculation).
- No UI for manually typing/pasting arbitrary ferry coordinates — avoidance is only
  offered for ferries actually detected in a computed route.
