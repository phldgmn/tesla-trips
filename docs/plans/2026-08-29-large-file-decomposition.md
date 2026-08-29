# Large-File Decomposition Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the repository's oversized, multi-responsibility files (backend Python modules and frontend React/TS components) into focused, single-responsibility files/packages, with **zero behavior change** and **zero public-symbol renames** — every existing `from tripplanner.x import Y` and `import { Y } from "@/components/X"` continues to resolve.

**Architecture:** For every target file, a module (Python) or file-group (TS) becomes a package/folder whose `__init__.py` (Python) or `index.ts`/barrel-file (TS) re-exports exactly the same public names as before. Symbols are grouped into new files by natural responsibility cluster (parsing vs. HTTP transport vs. persistence vs. orchestration vs. presentation), following the module boundaries already documented in `docs/02-architecture.md` and `docs/03-module-specifications.md`. No cross-module dependency is introduced beyond what already exists; module-to-module contracts still flow exclusively through `models.py` (backend) or `types.ts`/`types/trip-request.ts` (frontend), per `AGENTS.md`.

**Tech Stack:** Python 3.14 (uv, pydantic, FastAPI, NetworkX), React + TypeScript (Vite), pytest, ruff/mypy via `hk check --all`, Vitest for frontend.

**Spec:** This plan is self-contained; it supersedes no other plan. It complements `docs/02-architecture.md` and `docs/03-module-specifications.md`, which define the module boundaries this refactor must not violate.

## Global Constraints

- Every task is **behavior-preserving**: no logic changes, no signature changes, no renamed public symbols. Pure move-and-re-export.
- Module boundaries from `docs/03-module-specifications.md` are inviolable: a symbol never moves to a different top-level package (`routing`, `elevation`, `weather`, `wind`, `construction`, `energy`, `battery`, `charging_infrastructure`, `optimization`, `simulation`, `trip_input`). Only *intra-package* file layout changes.
- Every backend package converted from a flat module (`x.py`) to a package (`x/`) MUST keep `tripplanner.<pkg>.<x>.<Symbol>` resolvable — i.e. convert `x.py` → `x/__init__.py` + sibling files, not `x.py` alongside a same-named folder.
- Every frontend split MUST keep the existing `@/components/X` / `@/utils/X` import path resolvable — via a folder + `index.ts`/`index.tsx` barrel, or by keeping the original filename as a thin re-export.
- Private (`_`-prefixed) symbols directly imported by existing tests MUST remain importable from their current dotted path (add a re-export if moved) — see per-task "Preserve" lists.
- After each task: run the scoped test command listed in that task, then `uv run hk check --all` (Python tasks) or the frontend lint/typecheck/test commands (frontend tasks) before committing. Full-suite `uv run pytest -m "not integration"` runs once at the end of each phase, not after every single task, to keep iteration fast — but each task's own scoped tests MUST pass before moving on.
- Commit after every task (AGENTS.md rule 6/7): small, self-contained commits, one per task.
- Do not touch `docs/`, `scripts/`, `.superpowers/`, or CI config as part of this refactor except where a task explicitly says so.
- New files use the same docstring/type-annotation standards as the code being moved (mypy --strict clean, Google-style docstrings) — copy them verbatim, do not rewrite prose.

---

## Phase 0: Baseline Safety Net

### Task 0.1: Confirm green baseline

**Files:** none (verification only)

- [ ] Run `uv run pytest -m "not integration"` and confirm it passes (record pass count).
- [ ] Run `uv run hk check --all` and confirm it passes.
- [ ] Run `cd frontend && npm run lint && npm run typecheck && npm test -- --run` (or the equivalent scripts in `frontend/package.json`) and confirm green.
- [ ] Commit nothing; this is the reference point. If anything is already red, stop and report — do not refactor on top of a broken baseline.

---

## Phase 1: `charging_infrastructure` Package

Current: `providers.py` (1448 lines), `client.py` (1021 lines) are god-objects; `database.py` (705), `pricing.py` (284), `models.py` (260), `charging_infrastructure.py` (115), `__init__.py` (75) are already cohesive.

### Task 1.1: Split `charging_infrastructure/client.py` → `clients/` package

**Files:**
- Create: `src/tripplanner/charging_infrastructure/clients/__init__.py`
- Create: `src/tripplanner/charging_infrastructure/clients/common.py`
- Create: `src/tripplanner/charging_infrastructure/clients/supercharge_info.py`
- Create: `src/tripplanner/charging_infrastructure/clients/tesla_curl.py`
- Create: `src/tripplanner/charging_infrastructure/clients/nodriver.py`
- Remove: `src/tripplanner/charging_infrastructure/client.py`
- Modify: `src/tripplanner/charging_infrastructure/__init__.py` (import path only, symbol names unchanged)
- Test: existing `tests/charging_infrastructure/test_client.py`, `tests/charging_infrastructure/test_nodriver_integration.py` (unchanged, must still pass — they import `tripplanner.charging_infrastructure.client`)

**Interfaces:**
- Public symbols preserved exactly: `CurlError`, `TeslaClient` (Protocol), `create_tesla_client`, `SuperchargeInfoClient`, `TeslaLocationsClient`, `NodriverTeslaClient`, `NodriverBrowserFetcher`.
- `tripplanner.charging_infrastructure.client` MUST remain a valid dotted path after the split (tests and `providers.py`/`trip_input/api.py`/`trip_input/cli.py` import from it directly) — achieved by keeping `client.py` as a **package** named `client/` (not `clients/`), OR by keeping a thin `client.py` shim. Use the shim approach for lowest risk: keep `src/tripplanner/charging_infrastructure/client.py` as a **13-line re-export shim** `from .clients.common import CurlError, TeslaClient; from .clients.supercharge_info import SuperchargeInfoClient; from .clients.tesla_curl import TeslaLocationsClient; from .clients.nodriver import NodriverBrowserFetcher, NodriverTeslaClient; from .clients.common import create_tesla_client` re-exported via `__all__`. Do NOT remove `client.py`; revise the "Files" list accordingly: **Modify** `client.py` into a shim rather than delete it.

- [ ] **Step 1: Create `clients/common.py`** — move `_debug_log`, `CurlError`, `TeslaClient` Protocol, and `create_tesla_client` (the factory, which imports `NodriverTeslaClient`/`TeslaLocationsClient` — use local imports inside the function body to avoid import cycles with `tesla_curl.py`/`nodriver.py`).
- [ ] **Step 2: Create `clients/supercharge_info.py`** — move `SuperchargeInfoClient` verbatim; import `_debug_log` from `.common` if used.
- [ ] **Step 3: Create `clients/tesla_curl.py`** — move `TeslaLocationsClient` verbatim; import `CurlError`, `TeslaClient`, `_debug_log` from `.common`.
- [ ] **Step 4: Create `clients/nodriver.py`** — move `NodriverBrowserFetcher` and `NodriverTeslaClient` verbatim; import `CurlError`, `TeslaClient`, `_debug_log` from `.common`.
- [ ] **Step 5: Create `clients/__init__.py`** re-exporting `CurlError, TeslaClient, create_tesla_client, SuperchargeInfoClient, TeslaLocationsClient, NodriverTeslaClient, NodriverBrowserFetcher` with `__all__`.
- [ ] **Step 6: Replace `client.py` body** with the 13-line re-export shim described above (keep module docstring).
- [ ] **Step 7: Run** `uv run pytest tests/charging_infrastructure/test_client.py tests/charging_infrastructure/test_nodriver_integration.py -v` — expect all pass, zero collection errors.
- [ ] **Step 8: Run** `uv run hk check --all` — fix any import-order/lint findings in the new files (do not change logic).
- [ ] **Step 9: Commit** `git add src/tripplanner/charging_infrastructure/client.py src/tripplanner/charging_infrastructure/clients/ && git commit -m "refactor(charging_infrastructure): split client.py into clients/ package by transport"`.

### Task 1.2: Split `charging_infrastructure/providers.py` → `providers/` package

**Files:**
- Create: `src/tripplanner/charging_infrastructure/providers/__init__.py`
- Create: `src/tripplanner/charging_infrastructure/providers/spatial.py`
- Create: `src/tripplanner/charging_infrastructure/providers/local_file.py`
- Create: `src/tripplanner/charging_infrastructure/providers/fake.py`
- Create: `src/tripplanner/charging_infrastructure/providers/record_mapping.py`
- Create: `src/tripplanner/charging_infrastructure/providers/pricing_queue.py`
- Create: `src/tripplanner/charging_infrastructure/providers/tesla.py`
- Modify: `src/tripplanner/charging_infrastructure/providers.py` → replace with re-export shim (same pattern as Task 1.1)
- Test: `tests/charging_infrastructure/test_providers.py`, `tests/charging_infrastructure/conftest.py` (unchanged)

**Interfaces:**
- Public symbols preserved: `CachedPricing`, `PricingQueueDrainResult`, `LocalFileChargingStationProvider`, `FakeChargingStationProvider`, `TeslaChargingStationProvider`.
- `record_mapping.py` exposes module-level constants (hoisted from `TeslaChargingStationProvider._POWER_V2_MAX=150`, `_POWER_V3_MAX=250`, `_POWER_V3_ULTRA_MAX=350`) as `POWER_V2_MAX`, `POWER_V3_MAX`, `POWER_V3_ULTRA_MAX` (module-private naming `_POWER_*` is fine too — keep the underscore prefix since these were private class attributes, not public API) and free functions `site_to_db_record`, `tesla_location_to_db_record`, `tesla_detail_to_db_record`, `tesla_coords`, `parse_int`, `db_record_to_charging_station` (drop leading underscore since they become module-level implementation details of a new module — still not part of package `__all__`, so no public-API change).
- Fix the duplicated stall-power thresholds: `LocalFileChargingStationProvider` in `local_file.py` MUST import the same constants from `record_mapping.py` instead of re-hardcoding `150/250/325/325`.

- [ ] **Step 1: Create `providers/spatial.py`** — move `_build_lat_bands`, `_stations_in_radius`, `_LAT_BAND_KM`, `_KM_PER_LAT_DEG` verbatim (keep names as-is, module-private).
- [ ] **Step 2: Create `providers/record_mapping.py`** — move the six mapping staticmethods out of `TeslaChargingStationProvider` as module-level functions (`site_to_db_record`, `tesla_location_to_db_record`, `tesla_detail_to_db_record`, `tesla_coords`, `parse_int`, `db_record_to_charging_station`), taking the same parameters they took as `self`-bound staticmethods (drop `self`/`cls`). Hoist `_POWER_V2_MAX`, `_POWER_V3_MAX`, `_POWER_V3_ULTRA_MAX`, connector_map/status_map builders as module constants.
- [ ] **Step 3: Create `providers/local_file.py`** — move `LocalFileChargingStationProvider` verbatim; replace its hardcoded `150/250/325/325` stall-power literals with imports from `record_mapping` (behavior-preserving: verify the literal values match exactly before substituting — if `LocalFileChargingStationProvider` used `325` where `record_mapping` has `_POWER_V3_ULTRA_MAX=350`, do NOT unify; keep the existing literal value and only dedupe where values are identical bit-for-bit). Import `spatial` helpers it uses.
- [ ] **Step 4: Create `providers/fake.py`** — move `FakeChargingStationProvider` verbatim.
- [ ] **Step 5: Create `providers/pricing_queue.py`** — move `CachedPricing`, `PricingQueueDrainResult`, `PRICING_MAX_AGE`, and the five pricing-queue methods (`refresh_pricing`, `get_cached_pricing`, `enqueue_stations_for_pricing_refresh`, `list_pricing_queue`, `drain_pricing_queue`, `_resolve_supercharge_info_id`, `_resolve_numeric_slug`) as a mixin class `PricingQueueMixin` that `TeslaChargingStationProvider` inherits from (these methods use `self._db`, `self._tesla_client` etc. — preserve as instance methods via mixin, not free functions, to avoid threading many parameters).
- [ ] **Step 6: Create `providers/tesla.py`** — move remaining `TeslaChargingStationProvider` body (`__init__`, `close`, `refresh`, `refresh_from_tesla_api`, `_fetch_tesla_locations`, `_enrich_stations`, `_load_stations_from_db`, `get_stations_in_radius`, `get_stations_along_route`, `refresh_single_station`, `get_all_stations`, `_db_record_to_charging_station` call sites); declare `class TeslaChargingStationProvider(PricingQueueMixin):`; delegate record-mapping calls to `record_mapping.*` functions; delegate spatial calls to `spatial.*`.
- [ ] **Step 7: Create `providers/__init__.py`** re-exporting `CachedPricing, PricingQueueDrainResult, FakeChargingStationProvider, LocalFileChargingStationProvider, TeslaChargingStationProvider` with `__all__`.
- [ ] **Step 8: Replace `providers.py` body** with a re-export shim identical in spirit to Task 1.1 Step 6.
- [ ] **Step 9: Run** `uv run pytest tests/charging_infrastructure/ -v` — expect all pass.
- [ ] **Step 10: Run** `uv run hk check --all`.
- [ ] **Step 11: Commit** `git add -A src/tripplanner/charging_infrastructure && git commit -m "refactor(charging_infrastructure): split providers.py into providers/ package by responsibility"`.

### Task 1.3: Fix `__init__.py` docstring/export drift

**Files:**
- Modify: `src/tripplanner/charging_infrastructure/__init__.py`

- [ ] **Step 1:** Read the module docstring; it claims `SQLiteDatabase` is exported but `__all__` omits it. Either add `SQLiteDatabase` to the explicit imports/`__all__` (if any caller would benefit — check via `grep -rn "charging_infrastructure import.*SQLiteDatabase"`) or correct the docstring to stop claiming it's exported. Since no external caller imports `SQLiteDatabase` from the package root (they use `tripplanner.charging_infrastructure.database.SQLiteDatabase`), correct the docstring — do not add a new export (avoid widening public API unasked).
- [ ] **Step 2:** Run `uv run pytest tests/charging_infrastructure/ -v`.
- [ ] **Step 3:** Commit `git commit -am "docs(charging_infrastructure): fix __init__ docstring vs __all__ mismatch"`.

---

## Phase 2: `optimization` Package

### Task 2.1: Extract charging-math and detour-cost pure functions from `optimizer.py`

**Files:**
- Create: `src/tripplanner/optimization/charging_math.py`
- Create: `src/tripplanner/optimization/detour_costs.py`
- Modify: `src/tripplanner/optimization/optimizer.py`
- Test: `tests/optimization/test_optimization.py`

**Interfaces:**
- `charging_math.py` exposes free functions taking explicit parameters instead of `self`: `calc_soc_verbrauch_pct(energie_kwh, batteriekapazitaet_kwh)`, `calc_ladezeit_s(start_soc_pct, end_soc_pct, ladekurve, ladeleistung_kw, soc_step_pct)`, `mittlere_ladeleistung_kw(...)`, `soc_nach_fester_ladezeit(...)`, `lade_ziel_kandidaten(...)`, `kandidaten_mit_mindestladedauer(...)`. Signatures mirror the current method signatures minus `self`; inspect each method body first to enumerate its exact `self.<attr>` reads and add them as parameters.
- `detour_costs.py` exposes `detour_kosten(station, segment_index, avg_verbrauch_kwh_pro_m, detour_kosten_map, ...)` mirroring `_detour_kosten`, plus module constants `DETOUR_ROUTENFAKTOR = 1.6`, `DETOUR_GESCHWINDIGKEIT_KMH = 70.0`.
- `NetworkXOptimizer` keeps its full public method surface (`optimize`) unchanged; its private methods become thin one-line delegations to the new free functions, preserving `self._method(...)` call sites that other private methods in `optimizer.py` still use (do not update every call site's name — keep `self._calc_ladezeit_s(...)` etc. as thin wrappers calling `charging_math.calc_ladezeit_s(...)`, so the rest of `optimizer.py` needs zero further edits in this task).

- [ ] **Step 1:** Read `src/tripplanner/optimization/optimizer.py:1474-1593` (cluster A: `_calc_soc_verbrauch_pct` through `_lade_ziel_kandidaten`/`_kandidaten_mit_mindestladedauer`) to get exact current signatures and `self.*` reads.
- [ ] **Step 2:** Create `charging_math.py` with free-function equivalents; copy docstrings verbatim.
- [ ] **Step 3:** In `optimizer.py`, replace each of the six method **bodies** with a one-line call into `charging_math.<fn>(...)` passing the needed `self.*` attributes; keep the method signatures (still bound methods) so every existing internal call site (`self._calc_ladezeit_s(...)`) keeps working unchanged.
- [ ] **Step 4:** Read `optimizer.py:1195-1229` (`_detour_kosten`) and constants at file top (`DETOUR_ROUTENFAKTOR`, `DETOUR_GESCHWINDIGKEIT_KMH`).
- [ ] **Step 5:** Create `detour_costs.py` with `detour_kosten(...)` free function + the two constants; `optimizer.py` re-imports the constants from `detour_costs` (delete the local constant definitions, single source of truth) and `_detour_kosten` becomes a one-line delegate.
- [ ] **Step 6:** Delete `NetworkXOptimizer._haversine_distance` (dead duplicate of `tripplanner.geo.haversine_distance_m`); replace its one call site (`_map_waypoints_to_segments`/`_waypoint_to_segment`, wherever it's called) with a direct import of `haversine_distance_m` from `tripplanner.geo`.
- [ ] **Step 7:** Run `uv run pytest tests/optimization/ -v` — expect all pass unchanged (this task changes zero observable behavior).
- [ ] **Step 8:** Run `uv run hk check --all`.
- [ ] **Step 9:** Commit `git commit -am "refactor(optimization): extract charging-math and detour-cost pure functions from optimizer.py"`.

### Task 2.2: Extract result-extraction functions from `optimizer.py`

**Files:**
- Create: `src/tripplanner/optimization/result_extraction.py`
- Modify: `src/tripplanner/optimization/optimizer.py`
- Test: `tests/optimization/test_optimization.py`

**Interfaces:**
- `result_extraction.py` exposes `extract_charging_stops(graph, path, ...)`, `extract_waypoint_aufenthalte(graph, path, ...)`, `compute_waypoint_times(graph, path, ...)` mirroring `_extract_charging_stops`, `_extract_waypoint_aufenthalte`, `_compute_waypoint_times` (read exact current signatures/self-reads from `optimizer.py:1595-1752` before writing).

- [ ] **Step 1:** Read `optimizer.py:1595-1752` for exact signatures.
- [ ] **Step 2:** Create `result_extraction.py` with the three functions as free functions (they operate on `G`/`path` plus a few `self.*` scalars — pass those as explicit parameters).
- [ ] **Step 3:** In `optimizer.py`, replace the three method bodies with one-line delegations, preserving method names/signatures for callers within `optimize()`.
- [ ] **Step 4:** Run `uv run pytest tests/optimization/ -v`.
- [ ] **Step 5:** Run `uv run hk check --all`.
- [ ] **Step 6:** Commit `git commit -am "refactor(optimization): extract result-extraction functions from optimizer.py"`.

### Task 2.3: Extract segment/waypoint mapping into `station_mapping.py`

**Files:**
- Modify: `src/tripplanner/optimization/station_mapping.py`
- Modify: `src/tripplanner/optimization/optimizer.py`
- Test: `tests/optimization/test_station_mapping.py`, `tests/optimization/test_optimization.py`

- [ ] **Step 1:** Read `optimizer.py:314-393` (`_map_waypoints_to_segments`, `_waypoint_to_segment`, `_map_stations_to_segments`).
- [ ] **Step 2:** Add `map_waypoints_to_segments(...)` and `waypoint_to_segment(...)` as free functions to `station_mapping.py` (append after existing `map_station_to_segment`/`map_stations_to_segments`, matching their existing style — they already correctly use `tripplanner.geo.haversine_distance_m`).
- [ ] **Step 3:** In `optimizer.py`, replace `_map_waypoints_to_segments`/`_waypoint_to_segment` bodies with delegations to `station_mapping.map_waypoints_to_segments`/`waypoint_to_segment`; `_map_stations_to_segments` already delegates to `station_mapping.map_stations_to_segments` — leave it.
- [ ] **Step 4:** Run `uv run pytest tests/optimization/ -v`.
- [ ] **Step 5:** Run `uv run hk check --all`.
- [ ] **Step 6:** Commit `git commit -am "refactor(optimization): move waypoint-segment mapping helpers into station_mapping.py"`.

### Task 2.4: Extract state-graph construction into `graph_builder.py`

**Files:**
- Create: `src/tripplanner/optimization/graph_builder.py`
- Modify: `src/tripplanner/optimization/optimizer.py`
- Test: `tests/optimization/test_optimization.py`

**Interfaces:**
- `graph_builder.py` defines an internal class `StateGraphBuilder` (not exported from `optimization/__init__.py` — it is a private implementation detail of `NetworkXOptimizer`, so name it without leading underscore in its own module but do not add it to the package's public re-exports) carrying `soc_step_pct`, `time_step_min`, `base_time`, and a `push_seq` counter, with methods `generate_graph`, `schedule`, `add_drive_edge`, `add_ferry_edge`, `add_charging_edges`, `fuege_ladekante_hinzu`, `add_waypoint_wait_edge`, `required_departure` — same bodies as the current `_generate_graph` etc., ported to take the previously-`self`-only optimizer state via the builder's own `__init__` parameters (`soc_step_pct`, `time_step_min`, `battery/vehicle params`, `charging_math`/`detour_costs` module references already available via plain import).
- `NetworkXOptimizer.optimize()` constructs one `StateGraphBuilder(...)` per call and calls `builder.generate_graph(...)`, replacing the eight `self._generate_graph`/`self._schedule`/etc. call sites with `builder.generate_graph`/`builder.schedule`/etc.

- [ ] **Step 1:** Read `optimizer.py:498-1472` in full (the eight cluster-C methods) to enumerate every `self.*` attribute they read (`self.soc_step_pct`, `self.time_step_min`, `self._base_time`, `self._cum_time_s`, `self._avg_verbrauch_kwh_pro_m`, `self._push_seq`, plus any vehicle/battery/constraint objects passed through `optimize()`).
- [ ] **Step 2:** Create `graph_builder.py` with `class StateGraphBuilder` whose `__init__` takes exactly those attributes as constructor parameters, and whose methods are the eight cluster-C methods verbatim (renamed without leading underscore, `self` now refers to the builder instance instead of the optimizer).
- [ ] **Step 3:** In `optimizer.py`, inside `optimize()`, construct `builder = StateGraphBuilder(soc_step_pct=..., time_step_min=..., base_time=..., cum_time_s=..., avg_verbrauch_kwh_pro_m=...)` right before the call that used to be `self._generate_graph(...)`, and change that call (and any other cluster-C self-call inside `optimize()`) to `builder.generate_graph(...)`. Delete the eight now-unused private methods from `NetworkXOptimizer`.
- [ ] **Step 4:** Verify no other method of `NetworkXOptimizer` outside `optimize()` calls any cluster-C method (per the dependency map: only `optimize()` calls `_generate_graph`; `_generate_graph` calls the rest internally, which now live inside `StateGraphBuilder` calling its own sibling methods) — grep `self\._(generate_graph|schedule|add_drive_edge|add_ferry_edge|add_charging_edges|fuege_ladekante_hinzu|add_waypoint_wait_edge|required_departure)\b` in `optimizer.py` to confirm zero remaining references before deleting.
- [ ] **Step 5:** Run `uv run pytest tests/optimization/ -v` — this is the highest-risk task in Phase 2 (biggest code motion); if any test fails, diff the moved method bodies byte-for-byte against the original before debugging logic.
- [ ] **Step 6:** Run `uv run hk check --all`.
- [ ] **Step 7:** Commit `git commit -am "refactor(optimization): extract state-graph construction into graph_builder.StateGraphBuilder"`.

### Task 2.5: Verify optimizer.py final shape and run full optimization suite

**Files:** none new; verification task.

- [ ] **Step 1:** Read `src/tripplanner/optimization/optimizer.py` top to bottom; confirm it now contains only: module constants (`COST_INF`, `MAX_SOC_PCT`), `NetworkXOptimizer` (with `optimize`, `_estimate_max_time_buckets`, `_heuristik`, and thin delegating wrappers where other files' call sites require the method to still exist on the class), `ORToolsOptimizer`, `create_networkx_optimizer`, `create_ortools_optimizer`.
- [ ] **Step 2:** Run `uv run pytest tests/optimization/ tests/simulation/ tests/trip_input/ -v` (simulation and trip_input consume `ChargingPlan`/`create_networkx_optimizer` transitively).
- [ ] **Step 3:** Run `uv run hk check --all`.
- [ ] **Step 4:** Commit if any lint-only fixups were needed: `git commit -am "chore(optimization): lint cleanup after optimizer.py split"` (skip if nothing to commit).

---

## Phase 3: `weather` and `elevation` Packages

### Task 3.1: Delete dead-duplicate `weather/client.py`

**Files:**
- Remove content of: `src/tripplanner/weather/client.py` (file itself may stay empty-shim or be deleted — see below)
- Modify: `tests/weather/test_client.py`
- Test: `tests/weather/test_client.py`

**Interfaces:** `weather/client.py`'s `OpenMeteoClient`/`_extract_sample_from_response` are confirmed dead in production (only `tests/weather/test_client.py` imports them; `providers.py` has its own drifted copy). Since this is dead code, not a "split", deleting it is a cleanup, not a rename — but it does delete a currently-importable symbol, which the "no public symbol renamed" constraint does not protect (dead code removal is explicitly in scope per AGENTS.md "remove obsolete code"). Confirm zero non-test importers before deleting.

- [ ] **Step 1:** Run `grep -rn "weather\.client\|weather/client" src/ tests/ --include=*.py` (via the `grep` tool) to confirm the only importer is `tests/weather/test_client.py`.
- [ ] **Step 2:** Read `tests/weather/test_client.py` in full; it tests `OpenMeteoClient`'s exact-coordinate grouping behavior, which the production `providers.OpenMeteoProvider`/`OpenMeteoClient` implements differently (rounded coords + hour-snap). Confirm with the user's stated intent (AGENTS.md "ask before deleting... code you didn't write" only applies to *unrelated* code; this dead code is squarely in scope) — proceed: this test exercises dead code and provides no production regression coverage, so delete both the test file and `weather/client.py`.
- [ ] **Step 3:** `git rm src/tripplanner/weather/client.py tests/weather/test_client.py`.
- [ ] **Step 4:** Confirm `weather/__init__.py` does not import from `.client` (grep first) — if it does, remove that import line.
- [ ] **Step 5:** Run `uv run pytest tests/weather/ -v`.
- [ ] **Step 6:** Run `uv run hk check --all`.
- [ ] **Step 7:** Commit `git commit -m "refactor(weather): delete dead-duplicate weather/client.py and its test (superseded by providers.OpenMeteoProvider)"`.

### Task 3.2: Split `weather/providers.py` → per-provider modules

**Files:**
- Create: `src/tripplanner/weather/providers/__init__.py`
- Create: `src/tripplanner/weather/providers/_shared.py`
- Create: `src/tripplanner/weather/providers/caching.py`
- Create: `src/tripplanner/weather/providers/protocols.py`
- Create: `src/tripplanner/weather/providers/openmeteo.py`
- Create: `src/tripplanner/weather/providers/metno.py`
- Create: `src/tripplanner/weather/providers/openweather.py`
- Create: `src/tripplanner/weather/providers/smhi.py`
- Create: `src/tripplanner/weather/providers/dmi.py`
- Create: `src/tripplanner/weather/providers/composite.py`
- Modify: `src/tripplanner/weather/providers.py` → re-export shim
- Test: `tests/weather/test_providers.py`, `test_dmi.py`, `test_metno.py`, `test_openweather.py`, `test_smhi.py`, `test_load_balanced_provider.py`, `test_weather.py`, `test_weather_cache.py`, `test_weather_detail.py`, `tests/trip_input/test_api.py`, `test_cli.py`, `test_providers_factory.py`

**Interfaces:**
- Preserve exactly: `WeatherProvider` (Protocol), `FakeWeatherProvider`, `OpenMeteoClient`, `OpenMeteoProvider`, `MetNorwayProvider`, `SlidingWindowRateLimiter`, `OpenWeatherProvider`, `SmhiProvider`, `DmiProvider`, `WeatherProviderEntry`, `LoadBalancedWeatherProvider`, plus **private** symbols directly imported by tests: `_cache_key` (test_load_balanced_provider.py) and `_extract_sample_from_response` (test_weather.py) — these MUST resolve at `tripplanner.weather.providers._cache_key` / `._extract_sample_from_response` after the split (re-export them from the shim, even though they are private, because tests already depend on the exact dotted path).
- `_shared.py`: `_group_queries_by_coordinate`, `_snap_to_hour_z`, `_clamp` (leaf, no internal deps, imported by every provider module).
- `caching.py`: `_cache_key`, `_cache_str_key`, `_cache_deserialize`.
- `protocols.py`: `WeatherProvider`, `FakeWeatherProvider`.
- `openmeteo.py`: `HOURLY_PARAMS`, `_KMH_TO_MPS`, `OpenMeteoClient`, `_extract_sample_from_response`, `OpenMeteoProvider` (imports `_shared`, `caching`).
- `metno.py`: `MetNorwayProvider`, `_metno_precipitation`, `_extract_metno_sample` (imports `_shared`).
- `openweather.py`: `SlidingWindowRateLimiter`, `OpenWeatherProvider`, `_openweather_entries_by_time`, `_extract_openweather_sample` (imports `_shared`).
- `smhi.py`: `SmhiProvider`, `_extract_smhi_sample` (imports `_shared`).
- `dmi.py`: `DmiProvider`, `_extract_dmi_sample` (imports `_shared`).
- `composite.py`: `WeatherProviderEntry`, `LoadBalancedWeatherProvider`, `_neutral_weather_sample` (imports `_shared`, `caching`, `weather.coverage.detect_country`, and all five per-provider modules).

- [ ] **Step 1:** Read `weather/providers.py` in full (already partially read by prior scout — re-confirm exact line ranges via `grep '^(class |def |[A-Z_]+ = )' src/tripplanner/weather/providers.py`).
- [ ] **Step 2:** Create `providers/_shared.py` with the three shared helpers.
- [ ] **Step 3:** Create `providers/caching.py` with the three cache helpers (import `WeatherSample`, `TTLCache` as currently used).
- [ ] **Step 4:** Create `providers/protocols.py` with `WeatherProvider` Protocol and `FakeWeatherProvider`.
- [ ] **Step 5:** Create `providers/openmeteo.py` (`HOURLY_PARAMS`, `_KMH_TO_MPS`, `OpenMeteoClient`, `_extract_sample_from_response`, `OpenMeteoProvider`), importing `_shared`/`caching`.
- [ ] **Step 6:** Create `providers/metno.py`, `providers/openweather.py`, `providers/smhi.py`, `providers/dmi.py` — one per country provider family, each importing `_shared` only (no cross-provider deps).
- [ ] **Step 7:** Create `providers/composite.py` (`WeatherProviderEntry`, `LoadBalancedWeatherProvider`, `_neutral_weather_sample`), importing all five provider modules + `_shared`/`caching`/`weather.coverage`.
- [ ] **Step 8:** Create `providers/__init__.py` re-exporting every public name listed above plus `_cache_key` and `_extract_sample_from_response` explicitly (`from .caching import _cache_key as _cache_key` style re-export, or plain `from .caching import _cache_key`) so `tripplanner.weather.providers._cache_key` still resolves through the shim in Step 9.
- [ ] **Step 9:** Replace `weather/providers.py` body with a re-export shim: `from .providers import *` won't carry underscored names, so explicitly: `from .providers.caching import _cache_key, _cache_str_key, _cache_deserialize` / `from .providers.openmeteo import HOURLY_PARAMS, _KMH_TO_MPS, OpenMeteoClient, _extract_sample_from_response, OpenMeteoProvider` / etc. for every module, plus `__all__` listing every public name.
- [ ] **Step 10:** Run `uv run pytest tests/weather/ tests/trip_input/test_api.py tests/trip_input/test_cli.py tests/trip_input/test_providers_factory.py -v`.
- [ ] **Step 11:** Run `uv run hk check --all`.
- [ ] **Step 12:** Commit `git commit -m "refactor(weather): split providers.py into providers/ package by provider family"`.

### Task 3.3: Split `elevation/providers.py` — extract `TileCache` from `CopernicusDEMDataSource`

**Files:**
- Create: `src/tripplanner/elevation/tile_cache.py`
- Modify: `src/tripplanner/elevation/providers.py`
- Test: `tests/elevation/test_providers.py`

**Interfaces:**
- `tile_cache.py` defines `class TileCache` encapsulating the LRU dataset cache, per-tile locking, disk-cache read/write, and eviction logic currently inlined in `CopernicusDEMDataSource` (`_find_cached_tiles`, `_tile_name`, `_tile_uri`, `_schedule_cache_write`, `_write_band_to_cache`, `_get_tile_lock`, `_evict_over_cap`, `_dataset_for`, `_dataset_for_tile`) — read the exact method bodies first (`elevation/providers.py:222-761`) to determine the precise constructor parameters (cache dir, cap, etc.).
- `CopernicusDEMDataSource` becomes a facade: `__init__` constructs a `TileCache(...)`, and `get_elevation`/`get_elevations_batch`/`get_tile_at`/`get_tiles_in_bbox`/`_read_tile_bulk` delegate tile acquisition to `self._tile_cache.dataset_for(...)` etc. `DEMDataSourceProtocol` and `FakeDataSource` stay in `providers.py` unchanged (they're small and don't need to move); `copernicus_tile_name` stays in `providers.py` too, imported by `tile_cache.py`.

- [ ] **Step 1:** Read `elevation/providers.py:222-761` in full to enumerate every private helper method and its exact signature/self-reads.
- [ ] **Step 2:** Create `tile_cache.py` with `class TileCache` hosting the nine cache-management methods (renamed without leading underscore since they're now the class's own methods: `find_cached_tiles`, `tile_uri`, `schedule_cache_write`, `write_band_to_cache`, `get_tile_lock`, `evict_over_cap`, `dataset_for`, `dataset_for_tile`; keep `_tile_name` as a private helper since it's a thin wrapper over `copernicus_tile_name`), importing `copernicus_tile_name` and `models.{DEMTile, DEMTileKey}` from `.providers`/`.models`.
- [ ] **Step 3:** In `providers.py`, change `CopernicusDEMDataSource.__init__` to construct `self._tile_cache = TileCache(...)` with the same cache-dir/cap parameters it received; rewrite `_read_tile_bulk`, `get_elevation`, `get_elevations_batch`, `get_tile_at`, `get_tiles_in_bbox` to call `self._tile_cache.dataset_for(...)` / `.dataset_for_tile(...)` instead of the old private methods; delete the nine moved methods from `CopernicusDEMDataSource`.
- [ ] **Step 4:** Run `uv run pytest tests/elevation/ -v` — highest-risk step (concurrency/locking code); if failures occur, verify `threading.Lock`/`asyncio` primitives were moved with correct `self` binding (locks must live on the `TileCache` instance, not be accidentally shared as class-level state).
- [ ] **Step 5:** Run `uv run hk check --all`.
- [ ] **Step 6:** Commit `git commit -am "refactor(elevation): extract TileCache from CopernicusDEMDataSource into tile_cache.py"`.

---

## Phase 4: `construction` and `routing` Packages

### Task 4.1: Consolidate the triplicated 80 km/h default-speed constant

**Files:**
- Modify: `src/tripplanner/construction/models.py`
- Modify: `src/tripplanner/construction/providers.py`
- Modify: `src/tripplanner/construction/providers_de_autobahn.py`
- Modify: `src/tripplanner/construction/providers_de_datexii.py`
- Test: `tests/construction/`

- [ ] **Step 1:** Read all three definitions (`_DK_SE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH` in `providers.py`, `_DE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH` in `providers_de_autobahn.py`, `_DE_DATEXII_DEFAULT_SPEED_LIMIT_KMH` in `providers_de_datexii.py`) and confirm all three equal `80` (do not merge if any value differs — re-verify by reading, not from memory).
- [ ] **Step 2:** Add one module constant `DEFAULT_ROADWORKS_SPEED_LIMIT_KMH = 80` to `construction/models.py` (already the shared data-model module for this package).
- [ ] **Step 3:** In each of the three provider files, replace the local constant definition with `from .models import DEFAULT_ROADWORKS_SPEED_LIMIT_KMH` and update the (now-removed) local name's usages to the imported name — or keep each file's original local name as an alias (`_DE_ROADWORKS_DEFAULT_SPEED_LIMIT_KMH = DEFAULT_ROADWORKS_SPEED_LIMIT_KMH`) to avoid touching every usage site; alias approach is lower-risk, use it.
- [ ] **Step 4:** Run `uv run pytest tests/construction/ -v`.
- [ ] **Step 5:** Run `uv run hk check --all`.
- [ ] **Step 6:** Commit `git commit -am "refactor(construction): consolidate triplicated 80 km/h default speed-limit constant"`.

### Task 4.2: Remove dead `_parse_with_xmlschema` passthrough in `construction/parser.py`

**Files:**
- Modify: `src/tripplanner/construction/parser.py`
- Test: `tests/construction/test_parser.py`

- [ ] **Step 1:** Read `parser.py:41-70` (`parse_datexii_xml`, `_parse_with_xmlschema`, `_parse_with_elementtree`) to confirm `_parse_with_xmlschema` is a pure passthrough to `_parse_with_elementtree` with no distinct behavior.
- [ ] **Step 2:** If confirmed dead: inline `_parse_with_elementtree`'s call directly in `parse_datexii_xml` and delete `_parse_with_xmlschema`. If `parse_datexii_xml` branches on some condition to choose between them, keep both and skip this task's deletion (report the actual branching condition instead — do not delete live logic).
- [ ] **Step 3:** Run `uv run pytest tests/construction/test_parser.py -v`.
- [ ] **Step 4:** Run `uv run hk check --all`.
- [ ] **Step 5:** Commit `git commit -am "refactor(construction): remove dead _parse_with_xmlschema passthrough"` (skip commit if Step 2 determined it's not dead).

### Task 4.3: Split `construction/providers.py` by responsibility

**Files:**
- Create: `src/tripplanner/construction/providers/__init__.py`
- Create: `src/tripplanner/construction/providers/config.py`
- Create: `src/tripplanner/construction/providers/se_parser.py`
- Create: `src/tripplanner/construction/providers/wkt.py`
- Create: `src/tripplanner/construction/providers/fake.py`
- Create: `src/tripplanner/construction/providers/impl.py`
- Modify: `src/tripplanner/construction/providers.py` → re-export shim
- Modify: `tests/construction/test_providers_impl.py`, `test_strtree_matching.py`, `test_direction_matching.py` (update private-method call sites — see Interfaces)
- Test: `tests/construction/`, `tests/trip_input/test_api.py`, `test_cli.py`, `tests/integration/test_trip_end_to_end.py`

**Interfaces:**
- Preserve exactly: `ConstructionProviderConfig`, `ConstructionProviderImpl`, `FakeConstructionProvider`.
- `wkt.py`: `_parse_wkt_point`, `_parse_wkt_line`.
- `se_parser.py`: `_parse_trafikverket_situations` (imports `parser.DATEXIIConstructionZoneInternal`/`ROADWORKS_TYPES` as it does today).
- `config.py`: `ConstructionProviderConfig`.
- `fake.py`: `FakeConstructionProvider`.
- `impl.py`: `ConstructionProviderImpl` — during the move, delete the six thin one-line `matching`-wrapper methods (`_build_strtree`, `_match_geometry_to_segments`, `_match_zones_to_segment_ids`, `_zone_to_geometry`, `_map_closure_type`, `_filter_opposite_direction`) from the class and **update the three test files that call them directly** to call `tripplanner.construction.matching.<fn>` instead (per-test mechanical rename, e.g. `provider._build_strtree(...)` → `matching.build_strtree(...)`) — this is the one place this phase touches test files' call sites, because the scout report flagged these wrappers as dead/test-only.
- `__init__.py` re-exports `ConstructionProviderConfig`, `ConstructionProviderImpl`, `FakeConstructionProvider`.

- [ ] **Step 1:** Read `construction/providers.py` in full with exact line numbers via `grep '^(class |def |[A-Z_]+ = )' src/tripplanner/construction/providers.py`.
- [ ] **Step 2:** Create `providers/wkt.py` with `_parse_wkt_point`, `_parse_wkt_line`.
- [ ] **Step 3:** Create `providers/se_parser.py` with `_parse_trafikverket_situations`.
- [ ] **Step 4:** Create `providers/config.py` with `ConstructionProviderConfig`.
- [ ] **Step 5:** Create `providers/fake.py` with `FakeConstructionProvider`.
- [ ] **Step 6:** Create `providers/impl.py` with `ConstructionProviderImpl` (import `wkt`, `se_parser`, `config`, `matching`, `parser` as needed); delete the six thin wrapper methods from the class body.
- [ ] **Step 7:** Grep `tests/construction/*.py` for `._build_strtree(`, `._match_geometry_to_segments(`, `._match_zones_to_segment_ids(`, `._zone_to_geometry(`, `._map_closure_type(`, `._filter_opposite_direction(`; for each hit, rewrite to call the equivalent `matching.<fn>(...)` free function with the same arguments (drop the leading `provider`/`self` receiver arg since `matching.*` functions already take explicit params — confirm exact param lists by reading `matching.py`).
- [ ] **Step 8:** Create `providers/__init__.py` re-exporting the three public classes.
- [ ] **Step 9:** Replace `providers.py` body with a re-export shim.
- [ ] **Step 10:** Run `uv run pytest tests/construction/ -v`.
- [ ] **Step 11:** Run `uv run pytest tests/trip_input/test_api.py tests/trip_input/test_cli.py -v`.
- [ ] **Step 12:** Run `uv run hk check --all`.
- [ ] **Step 13:** Commit `git commit -m "refactor(construction): split providers.py into providers/ package; test callers use matching.* directly"`.

### Task 4.4: Split `routing/providers.py` by responsibility

**Files:**
- Create: `src/tripplanner/routing/providers/__init__.py`
- Create: `src/tripplanner/routing/providers/fake.py`
- Create: `src/tripplanner/routing/providers/custom_model.py`
- Create: `src/tripplanner/routing/providers/graphhopper.py`
- Modify: `src/tripplanner/routing/providers.py` → re-export shim
- Test: `tests/routing/test_providers.py`, `test_faehren_integration.py`, `tests/trip_input/test_cli.py`

**Interfaces:**
- Preserve exactly: `RoutingProvider` (Protocol), `FakeRoutingProvider`, `GraphHopperRoutingProvider`, `ferry_exclusion_to_geojson_feature`, `VIA_POINT_REACHED_SIGN`.
- `fake.py`: `FakeRoutingProvider` (+ its private `_diskretisiere_teilstrecke` helper).
- `custom_model.py`: `ferry_exclusion_to_geojson_feature` + the `_build_custom_model` logic factored as a free function `build_custom_model(...)` that `GraphHopperRoutingProvider._build_custom_model` delegates to (keep the method as a one-line wrapper, since it's called internally by `berechne_route`).
- `graphhopper.py`: `RoutingProvider` Protocol + `GraphHopperRoutingProvider` (response-mapping methods `_map_path_to_route`, `_normalize_max_speed`, `_wert_fuer_edge`, `VIA_POINT_REACHED_SIGN` stay inside this file per the scout's lower-priority note — do not further split response mapping out in this task, to limit risk).

- [ ] **Step 1:** Read `routing/providers.py` in full with exact line numbers.
- [ ] **Step 2:** Create `providers/fake.py` with `FakeRoutingProvider` + `_diskretisiere_teilstrecke`.
- [ ] **Step 3:** Create `providers/custom_model.py` with `ferry_exclusion_to_geojson_feature` and a `build_custom_model(...)` free function extracted from `_build_custom_model`'s body (same parameters minus `self`, plus whatever `self.*` it reads).
- [ ] **Step 4:** Create `providers/graphhopper.py` with `RoutingProvider` Protocol, `VIA_POINT_REACHED_SIGN`, and `GraphHopperRoutingProvider` (its `_build_custom_model` method becomes a one-line delegate to `custom_model.build_custom_model(...)`).
- [ ] **Step 5:** Create `providers/__init__.py` re-exporting `RoutingProvider`, `FakeRoutingProvider`, `GraphHopperRoutingProvider`, `ferry_exclusion_to_geojson_feature`, `VIA_POINT_REACHED_SIGN`.
- [ ] **Step 6:** Replace `providers.py` body with a re-export shim.
- [ ] **Step 7:** Run `uv run pytest tests/routing/ -v`.
- [ ] **Step 8:** Run `uv run pytest tests/trip_input/test_cli.py tests/optimization/test_detour_routing.py -v`.
- [ ] **Step 9:** Run `uv run hk check --all`.
- [ ] **Step 10:** Commit `git commit -m "refactor(routing): split providers.py into providers/ package (fake, custom_model, graphhopper)"`.

### Task 4.5: De-duplicate segment-construction logic between `FakeRoutingProvider` and `GraphHopperRoutingProvider`

**Files:**
- Modify: `src/tripplanner/routing/models.py` (only if a shared constructor helper is warranted)
- Modify: `src/tripplanner/routing/providers/fake.py`
- Modify: `src/tripplanner/routing/providers/graphhopper.py`
- Test: `tests/routing/`

- [ ] **Step 1:** Read both `RouteSegment(...)` construction call sites (`FakeRoutingProvider.berechne_route_mit_waypoints` and `GraphHopperRoutingProvider._map_path_to_route`) side by side; enumerate every field set and every divergent default (Fake hardcodes PRIMARY/asphalt/100/1.5).
- [ ] **Step 2:** Decide if a shared constructor helper is worth it: since the two builders intentionally diverge (fake uses synthetic defaults, real provider uses GraphHopper response data), do NOT force a shared helper if it would require passing through the same divergent-default parameters anyway — this task is a no-op if extraction would just move the duplication rather than remove it. Read carefully; if the only shared part is `bearing_deg`/`haversine_distance_m` computation (already imported from `tripplanner.geo` by both), confirm both already call the shared `geo` functions (per the earlier report, they do) and close this task with no code change, documenting the finding.
- [ ] **Step 3:** If Step 2 finds a genuine extractable helper (e.g. a shared "compute bearing + length for adjacent coordinate pairs" utility not yet using `tripplanner.geo`), extract it into `tripplanner/geo/geo.py` (already the shared leaf module per module boundaries) and use it from both. Otherwise skip to Step 4.
- [ ] **Step 4:** Run `uv run pytest tests/routing/ -v`.
- [ ] **Step 5:** Commit only if a change was made: `git commit -am "refactor(routing): de-duplicate segment bearing/length computation via geo helpers"` (skip if Step 2 concluded no-op).

---

## Phase 5: `simulation`, `battery`, `energy` Packages

### Task 5.1: Split `simulation/simulate.py` internal helpers

**Files:**
- Modify: `src/tripplanner/simulation/simulate.py`
- Test: `tests/simulation/test_simulate.py`

- [ ] **Step 1:** Read `simulation/simulate.py` in full (510 lines: `_interpolate_position_along_segment`, `_find_segment_for_time`, `simulate_trip`).
- [ ] **Step 2:** This file is a single 370-line `simulate_trip` function plus two small helpers — genuinely one cohesive algorithm (time-series reconstruction), per the scout's finding. Do not force an artificial split of one function into a new file; instead, within `simulate_trip`, identify the distinct phases already present (e.g. driving-segment walk vs. charging-stop walk vs. frame emission) and extract 2-4 well-named **private helper functions** at module level (not a new file) if and only if each extracted piece is independently meaningful and testable (e.g. `_emit_driving_frames(...)`, `_emit_charging_frames(...)`). If the function's phases are too intertwined with shared mutable local state (running SoC, running clock) to cleanly separate, leave `simulate_trip` as one function — do not force decomposition that would require threading 8+ parameters through helper calls for no readability gain.
- [ ] **Step 3:** Whichever outcome Step 2 reaches, run `uv run pytest tests/simulation/test_simulate.py -v`.
- [ ] **Step 4:** Run `uv run hk check --all`.
- [ ] **Step 5:** Commit `git commit -am "refactor(simulation): extract driving/charging frame-emission helpers from simulate_trip"` (or skip commit if Step 2 concluded no split warranted — report the finding instead).

### Task 5.2: Split `battery/models.py` — separate reference-curve data from hot-path machinery

**Files:**
- Create: `src/tripplanner/battery/reference_curves.py`
- Modify: `src/tripplanner/battery/models.py`
- Test: `tests/battery/`

**Interfaces:**
- Preserve exactly: `SoCState`, `ChargingCurvePoint`, `InterpolationMethod`, `ChargingCurve`, `VehicleBatteryParameters`, `ChargingStop`, `LadekurveReferenz`.
- `reference_curves.py` receives `LadekurveReferenz` (community-measurement reference curves for Tesla models) — pure data, no hot-path dependency. `models.py` keeps `_ChargingCurveFastPath`, `_evaluate_fast_path`, `SoCState`, `ChargingCurvePoint`, `InterpolationMethod`, `ChargingCurve`, `VehicleBatteryParameters`, `ChargingStop` and imports `LadekurveReferenz` from `.reference_curves` for backward-compat re-export in `__init__.py`/`models.py` itself (keep `from .reference_curves import LadekurveReferenz` inside `models.py` so `tripplanner.battery.models.LadekurveReferenz` still resolves).

- [ ] **Step 1:** Read `battery/models.py:317-427` (`LadekurveReferenz`) in full.
- [ ] **Step 2:** Create `reference_curves.py` with `LadekurveReferenz` verbatim (check its imports — likely just `ChargingCurve`/`ChargingCurvePoint` from `.models`, which is fine as a one-way dependency: `reference_curves.py` imports from `models.py`, never the reverse, avoiding a cycle).
- [ ] **Step 3:** In `models.py`, delete the `LadekurveReferenz` class body and add `from .reference_curves import LadekurveReferenz` at the bottom (after `ChargingCurve`/`ChargingCurvePoint` are defined, to avoid the circular import — since `reference_curves.py` imports from `models.py`, `models.py` must import `reference_curves` only after its own classes exist; a bottom-of-file import satisfies this, or move the import to `battery/__init__.py` instead if a bottom-of-file import is stylistically wrong for this codebase — check `battery/__init__.py`'s current re-export pattern first and match it).
- [ ] **Step 4:** Run `uv run pytest tests/battery/ -v`.
- [ ] **Step 5:** Run `uv run hk check --all`.
- [ ] **Step 6:** Commit `git commit -am "refactor(battery): move LadekurveReferenz reference-curve data to reference_curves.py"`.

### Task 5.3: Investigate and resolve `battery/battery.py` duplication with `optimizer.py`

**Files:**
- Modify: `src/tripplanner/optimization/charging_math.py` (created in Task 2.1)
- Test: `tests/battery/`, `tests/optimization/`

- [ ] **Step 1:** Read `battery/battery.py` (`interpolate_charging_power`, `compute_charge_duration`) side-by-side with `optimization/charging_math.py`'s `calc_ladezeit_s`/`mittlere_ladeleistung_kw` (created in Task 2.1) to determine if they are the *same* algorithm duplicated across modules, or *different* algorithms that happen to look similar (per the scout report, `battery/battery.py`'s charge-duration logic is "effectively test-only in production" — confirm this by grepping production (non-test) call sites of `compute_charge_duration`).
- [ ] **Step 2:** If `compute_charge_duration` has zero production call sites (test-only), this is a module-boundary question, not a file-split question: `optimization` cannot import "the real algorithm" from `battery` if `battery`'s version is unused/stale — do not merge across the module boundary silently. Report the finding; if the two implementations compute different results for the same inputs (verify with a quick side-by-side reading, not by running code), leave both in place (they are not true duplicates, just similarly-named) and close this task with no change.
- [ ] **Step 3:** If they are genuinely the same algorithm and `compute_charge_duration` truly has no production caller, this is a dead-code question outside this refactor's behavior-preserving scope (removing a public, tested function is a behavior change to the public API) — do not delete it under this plan. Document the finding for a future cleanup task instead.
- [ ] **Step 4:** No commit expected for this task (investigation-only, per constraints "no logic changes"); report the finding in the phase summary.

### Task 5.4: Extract constants/helpers from `energy/energy.py`

**Files:**
- Modify: `src/tripplanner/energy/energy.py` (in-place cleanup only, per scout: "cohesive physics module with extractable constants/helpers" — low priority)
- Test: `tests/energy/`

- [ ] **Step 1:** Read `energy/energy.py` in full (329 lines: `f_oberflaeche`, `berechne_luftdichte`, `f_strassenzustand`, `calculate_segment_consumption`, `calculate_total_consumption`).
- [ ] **Step 2:** This module is already cohesive (single physical model, one clear public entry pair). No file split — confirm no action needed beyond checking for any magic-number literals inside `calculate_segment_consumption` (329-131=198 lines) that duplicate named constants already defined elsewhere in the file; if found, replace the literal with the existing constant (pure readability fix, zero behavior change). If none found, close with no change.
- [ ] **Step 3:** Run `uv run pytest tests/energy/ -v` only if Step 2 made a change.
- [ ] **Step 4:** Commit only if Step 2 made a change: `git commit -am "refactor(energy): replace duplicated magic-number literals with named constants"`.

---

## Phase 6: `trip_input` Package (`api.py`, `cli.py`)

This is the highest-value, highest-risk phase: `api.py` (1979 lines) mixes five responsibilities and is imported by 22+ files.

### Task 6.1: Extract the pipeline-step functions into `pipeline.py`

**Files:**
- Create: `src/tripplanner/trip_input/pipeline.py`
- Modify: `src/tripplanner/trip_input/api.py`
- Test: `tests/trip_input/test_api.py`

**Interfaces:**
- Preserve exactly: `create_trip_simulation` (the orchestration entry point) stays importable from `tripplanner.trip_input.api` (it is the module's most-imported symbol).
- `pipeline.py` receives the eleven `_step_*` functions verbatim (`_step_1_route_calculate` through `_step_route_charging_detours`, i.e. lines 97-626 per the grep above), plus their shared helpers `_bbox_center`, `_match_ferry_time_window`, `_log_step`, `_logger` (the pipeline-step logger instance, distinct from the module-level `logger` used by the FastAPI app — read both definitions at lines 627 and 1125 to confirm they are indeed two separate logger instances before deciding whether to keep them separate or unify; if the codebase intentionally uses two names for the same underlying `logging.getLogger(__name__)` call, keep both as-is to avoid behavior change).
- `create_trip_simulation` itself (lines 754-1030) moves to `pipeline.py` too, since it directly orchestrates the eleven steps and is the natural sibling.
- `api.py` re-imports `create_trip_simulation` from `.pipeline` (`from .pipeline import create_trip_simulation`) so every existing `from tripplanner.trip_input.api import create_trip_simulation` continues to work.

- [ ] **Step 1:** Read `api.py:1-96` (imports, module docstring) and `api.py:97-1030` in full (already partially covered by the grep above; read the un-grepped bodies via `read src/tripplanner/trip_input/api.py:97-1030`).
- [ ] **Step 2:** Create `pipeline.py` with the module docstring adapted (pipeline-specific), all eleven `_step_*` functions, `_bbox_center`, `_match_ferry_time_window`, `_logger`, `_log_step`, and `create_trip_simulation`, verbatim — copy every import each of these needs (routing, elevation, weather, construction, energy, charging_infrastructure, optimization, battery, simulation, geo models as currently imported at the top of `api.py`).
- [ ] **Step 3:** In `api.py`, delete the moved code; add `from .pipeline import create_trip_simulation` (and re-export it, e.g. keep it in `api.py`'s own `__all__` if one exists, or simply leave the bare import so `from tripplanner.trip_input.api import create_trip_simulation` still works — bare imports are resolvable as module attributes in Python, no explicit re-export syntax needed).
- [ ] **Step 4:** Grep `tests/trip_input/test_api.py` and any other test for direct imports of the eleven `_step_*` functions or `_bbox_center`/`_match_ferry_time_window` from `tripplanner.trip_input.api` — if found, update those test imports to `tripplanner.trip_input.pipeline` (private symbols moving modules is acceptable per this plan's constraints only for *test-only* private imports, which must be updated to the new location rather than shimmed, since shimming every private helper would defeat the split's purpose).
- [ ] **Step 5:** Run `uv run pytest tests/trip_input/test_api.py -v`.
- [ ] **Step 6:** Run `uv run hk check --all`.
- [ ] **Step 7:** Commit `git commit -am "refactor(trip_input): extract 11-step pipeline + create_trip_simulation into pipeline.py"`.

### Task 6.2: Extract FastAPI app shell into `app.py`

**Files:**
- Create: `src/tripplanner/trip_input/app.py`
- Modify: `src/tripplanner/trip_input/api.py`
- Test: `tests/trip_input/test_api.py`

**Interfaces:**
- Preserve exactly: `app` (the `FastAPI` instance — `run.sh`/uvicorn target it via `tripplanner.trip_input.api:app`, so this exact dotted path MUST keep working after the split — verify by reading `run.sh` for the uvicorn invocation string before proceeding).
- `app.py` receives: `_configure_logging`, `logger` (module-level FastAPI logger), `_lifespan`, `app = FastAPI(...)`, `get_routing_provider`, `get_charging_provider`, `get_elevation_provider`, `get_weather_provider`, `get_construction_provider`, `health_check`.
- `api.py` re-imports `app` from `.app` (`from .app import app`) so `uvicorn tripplanner.trip_input.api:app` keeps resolving.
- The `/trips`, `/superchargers*` endpoint functions (`create_trip_endpoint`, `list_superchargers`, `get_supercharger_detail`, `refresh_supercharger`) stay in `api.py` for this task (they need the schema models still in `api.py` until Task 6.3) — but they use the `app` instance and the `get_*_provider` dependency getters from `.app`, so update their imports accordingly.

- [ ] **Step 1:** Read `run.sh` to find the exact uvicorn target string (e.g. `tripplanner.trip_input.api:app`) — confirm it must be preserved.
- [ ] **Step 2:** Read `api.py:1125-1246` in full (logger, `_configure_logging`, `_lifespan`, `app =`, the five dependency getters, `health_check`).
- [ ] **Step 3:** Create `app.py` with all of the above, verbatim, plus its own required imports (FastAPI, the four provider factory imports from `providers_factory.py`, etc.).
- [ ] **Step 4:** In `api.py`, delete the moved code; add `from .app import app, get_routing_provider, get_charging_provider, get_elevation_provider, get_weather_provider, get_construction_provider` (the four getters are used as `Depends(get_x_provider)` defaults in the remaining endpoint functions, so they must stay importable in `api.py`'s namespace).
- [ ] **Step 5:** Run `uv run pytest tests/trip_input/test_api.py -v`.
- [ ] **Step 6:** Smoke-test the FastAPI app still boots: run `uv run uvicorn tripplanner.trip_input.api:app --port 8099 &` briefly, curl `/health`, then stop it (or use the project's `./run.sh start backend` / `./run.sh stop` per AGENTS.md — prefer `./run.sh` here as required).
- [ ] **Step 7:** Run `uv run hk check --all`.
- [ ] **Step 8:** Commit `git commit -am "refactor(trip_input): extract FastAPI app shell (lifespan, dependency getters, health check) into app.py"`.

### Task 6.3: Extract API schema models into `schemas/` submodules

**Files:**
- Create: `src/tripplanner/trip_input/schemas/__init__.py`
- Create: `src/tripplanner/trip_input/schemas/superchargers.py`
- Create: `src/tripplanner/trip_input/schemas/request.py`
- Create: `src/tripplanner/trip_input/schemas/response.py`
- Modify: `src/tripplanner/trip_input/api.py`
- Test: `tests/trip_input/test_api.py`

**Interfaces:**
- Preserve exactly (all currently defined at module scope in `api.py`, must remain importable from `tripplanner.trip_input.api`): `SuperchargerStationAPI`, `SuperchargerStationDetailAPI`, `WaypointAPI`, `FaehrAusschlussAPI`, `FaehrZeitfensterAPI`, `LadedauerVorgabeAPI`, `TripRequestAPI`, `FrameAPI`, `ChargingStopAPI`, `FaehrSegmentAPI`, `ChargingCostByCurrencyAPI`, `ConstructionZoneEventAPI`, `ConstructionZoneAPI`, `WaypointStopAPI`, `TripSimulationResultAPI`.
- `schemas/superchargers.py`: `SuperchargerStationAPI`, `SuperchargerStationDetailAPI`, `_station_to_api`.
- `schemas/request.py`: `WaypointAPI`, `FaehrAusschlussAPI`, `FaehrZeitfensterAPI`, `LadedauerVorgabeAPI`, `TripRequestAPI`.
- `schemas/response.py`: `FrameAPI`, `ChargingStopAPI`, `FaehrSegmentAPI`, `ChargingCostByCurrencyAPI`, `ConstructionZoneEventAPI`, `ConstructionZoneAPI`, `WaypointStopAPI`, `TripSimulationResultAPI`, `_build_construction_zones_api`, `_attach_charging_pricing`.
- `schemas/__init__.py` re-exports all fourteen model classes.
- `api.py` imports `from .schemas import *`-equivalent explicit imports so all fourteen names remain module attributes of `tripplanner.trip_input.api`.

- [ ] **Step 1:** Read `api.py:1247-1780` in full (all fourteen Pydantic models + the two remaining helper functions `_station_to_api`, `_build_construction_zones_api`, `_attach_charging_pricing` at lines 680, 1031, 1275).
- [ ] **Step 2:** Create `schemas/superchargers.py` with `SuperchargerStationAPI`, `SuperchargerStationDetailAPI`, `_station_to_api`.
- [ ] **Step 3:** Create `schemas/request.py` with the five request models.
- [ ] **Step 4:** Create `schemas/response.py` with the eight response models plus `_build_construction_zones_api` and `_attach_charging_pricing` (these two functions build response-model instances, so they belong with the response schemas, not the pipeline).
- [ ] **Step 5:** Create `schemas/__init__.py` re-exporting all fourteen model classes (not the private helper functions — those stay accessible via their submodule paths only, matching their current "module-private" status).
- [ ] **Step 6:** In `api.py`, delete the moved code; add `from .schemas import (SuperchargerStationAPI, SuperchargerStationDetailAPI, WaypointAPI, FaehrAusschlussAPI, FaehrZeitfensterAPI, LadedauerVorgabeAPI, TripRequestAPI, FrameAPI, ChargingStopAPI, FaehrSegmentAPI, ChargingCostByCurrencyAPI, ConstructionZoneEventAPI, ConstructionZoneAPI, WaypointStopAPI, TripSimulationResultAPI)` plus `from .schemas.superchargers import _station_to_api` and `from .schemas.response import _build_construction_zones_api, _attach_charging_pricing` (endpoint functions in `api.py` still call these two directly).
- [ ] **Step 7:** Run `uv run pytest tests/trip_input/test_api.py tests/trip_input/test_charging_detours.py -v`.
- [ ] **Step 8:** Run `uv run hk check --all`.
- [ ] **Step 9:** Commit `git commit -am "refactor(trip_input): extract API schema models (superchargers/request/response) into schemas/ package"`.

### Task 6.4: Verify `api.py` final shape

**Files:** none new; verification.

- [ ] **Step 1:** Read `api.py` top to bottom; confirm it now contains only: imports, `app`-shell re-imports, `create_trip_simulation`/pipeline re-import, schema re-imports, and the four `/trips`/`/superchargers*` endpoint functions (`create_trip_endpoint`, `list_superchargers`, `get_supercharger_detail`, `refresh_supercharger`) plus `_station_to_api` call sites. Expect roughly 250-350 lines.
- [ ] **Step 2:** Run `uv run pytest tests/trip_input/ tests/integration/test_trip_end_to_end.py -v` (the latter is `@pytest.mark.integration` — run explicitly since Phase-level `pytest -m "not integration"` skips it, but this task's blast radius touches its imports; if a local GraphHopper instance isn't running, this step may be skipped with a note).
- [ ] **Step 3:** Run `uv run hk check --all`.
- [ ] **Step 4:** Restart backend via `./run.sh restart backend`, curl `POST /trips` with a minimal payload (or reuse `trips-response.local` fixture request shape) and `GET /health`, confirm 200 responses, then `./run.sh stop`.
- [ ] **Step 5:** Commit any final lint fixups: `git commit -am "chore(trip_input): lint cleanup after api.py split"` (skip if nothing to commit).

### Task 6.5: Split `cli.py` — separate trip-planning CLI from charger-data-management CLI

**Files:**
- Create: `src/tripplanner/trip_input/cli_charger.py`
- Modify: `src/tripplanner/trip_input/cli.py`
- Test: `tests/trip_input/test_cli.py`

**Interfaces:**
- Preserve exactly: `app` (the Typer app, `trips` command), `charger_app` (the Typer sub-app, `refresh`/`pricing-queue`/`scrape-pricing` commands) — both are registered via Typer's `add_typer` mechanism; confirm the exact registration call in `cli.py` before moving anything, since Typer sub-app wiring is order-sensitive.
- `cli.py` keeps: `app`, `parse_coord`, `parse_waypoint`, `trips` command (lines 44-243).
- `cli_charger.py` receives: `charger_app`, `refresh` (line 245), `pricing_queue` (line 364), `scrape_pricing` (line 403), plus their shared helpers/constants found by reading lines 244-486 in full.
- `cli.py` imports `charger_app` from `.cli_charger` and re-registers it on `app` at the same point in the file the registration currently occurs (`app.add_typer(charger_app, ...)` — read the exact call before moving).

- [ ] **Step 1:** Read `cli.py:1-43` (imports, module setup) and `cli.py:244-486` in full (the three `charger_app` commands + any shared constants/helpers).
- [ ] **Step 2:** Find the exact `app.add_typer(charger_app, ...)` call site (likely near the top, right after `app`/`charger_app` are constructed) — read `cli.py:1-43` again if it's not in the already-read ranges.
- [ ] **Step 3:** Create `cli_charger.py` with `charger_app = typer.Typer(...)` (same construction args as currently used) and the three commands + their helpers, verbatim, with the same imports (`TeslaLocationsClient`, `TeslaChargingStationProvider`, etc.).
- [ ] **Step 4:** In `cli.py`, delete the moved code; add `from .cli_charger import charger_app`; keep the `app.add_typer(charger_app, ...)` registration call in `cli.py` at its original position (do not move the registration itself, only the sub-app's command definitions).
- [ ] **Step 5:** Run `uv run pytest tests/trip_input/test_cli.py -v`.
- [ ] **Step 6:** Smoke-test the CLI: `uv run tripplanner --help` (or the project's actual CLI entry point per `pyproject.toml` `[project.scripts]`) and confirm both `trips` and `charger` subcommands still list correctly.
- [ ] **Step 7:** Run `uv run hk check --all`.
- [ ] **Step 8:** Commit `git commit -am "refactor(trip_input): split cli.py into trip-planning CLI and cli_charger.py charger-management CLI"`.

### Task 6.6: `providers_factory.py` and `models.py` — confirm no split needed

**Files:** none; verification/documentation task.

- [ ] **Step 1:** Read `trip_input/providers_factory.py` (234 lines) and `trip_input/models.py` (188 lines) in full.
- [ ] **Step 2:** Per the scout's finding these are "small/cohesive"; confirm by checking each has a single clear responsibility (factory: wiring concrete providers for prod/dev; models: `TripRequest`/`VehicleProfile`/related request-side data). If confirmed, no change — document in the phase summary. If a genuine mixed concern is found on this closer read, add a follow-up task before closing Phase 6 (do not silently skip if a real issue surfaces).

---

## Phase 7: Frontend — `Map.tsx` and `route-line.ts`

### Task 7.1: Split `route-line.ts` into `route-splice.ts` + `route-legs.ts`, keep `route-line.ts` as barrel

**Files:**
- Create: `frontend/src/utils/route-splice.ts`
- Create: `frontend/src/utils/route-legs.ts`
- Modify: `frontend/src/utils/route-line.ts` (becomes barrel + owns `RouteSample`, `projectDistanceAlongLineM`, `findNearestRouteSample`, `METERS_PER_DEGREE_LAT`)
- Test: `frontend/tests/unit/route-line.test.ts` (unchanged import paths), `frontend/tests/unit/map-utils.test.ts` (unchanged)

**Interfaces:**
- Preserve exactly, all still importable from `@/utils/route-line`: `CHARGE_JUMP_EPSILON_M`, `ChargingDetourInput`, `RouteSample`, `SplicedRoute`, `ResolvedDetour`, `buildSplicedRoute`, `RouteLeg`, `splitRouteIntoLegs`, `METERS_PER_DEGREE_LAT`, `projectDistanceAlongLineM`, `findNearestRouteSample`.
- `route-splice.ts` exports: `CHARGE_JUMP_EPSILON_M`, `ChargingDetourInput`, `SplicedRoute`, `ResolvedDetour`, `buildSplicedRoute` (imports `RouteSample` type from `./route-line` — this creates `route-splice.ts` → `route-line.ts` → re-exports `route-splice.ts`; to avoid a circular import, `RouteSample` interface itself stays defined in `route-line.ts` directly, not re-exported from a submodule — `route-line.ts` is a genuine mixed barrel-plus-owner, not a pure re-export shim).
- `route-legs.ts` exports: `RouteLeg`, `splitRouteIntoLegs` (imports `SplicedRoute`, `RouteSample` types from `./route-line`).

- [ ] **Step 1:** Read `frontend/src/utils/route-line.ts` in full with exact line numbers (already have from the scout report: lines 30-241 splicing core, 531-608 leg splitting, 612-715 geometry/projection).
- [ ] **Step 2:** Create `route-splice.ts` with `CHARGE_JUMP_EPSILON_M`, `ChargingDetourInput`, `SplicedRoute`, `ResolvedDetour`, `cumulativeDistancesM`, `nearestIndexByDistance`, `resolveDetours`, `buildSplicedRoute`, importing `RouteSample` as a type-only import from `./route-line` (`import type { RouteSample } from "./route-line"`) and `toLngLat`/`haversineDistanceM` from `./geo-utils`.
- [ ] **Step 3:** Create `route-legs.ts` with `RouteLeg`, `splitRouteIntoLegs`, importing `SplicedRoute`, `RouteSample` types from `./route-line`.
- [ ] **Step 4:** Rewrite `route-line.ts` to: (a) keep `RouteSample` interface definition in place (do not move it — it's the shared anchor type), (b) keep `METERS_PER_DEGREE_LAT`, `projectDistanceAlongLineM`, `findNearestRouteSample` in place, (c) delete the moved splicing/leg code, (d) add `export * from "./route-splice"; export * from "./route-legs"` at the top or bottom (TypeScript allows `export *` re-exports; verify no name collisions between the two — there are none per the symbol lists above).
- [ ] **Step 5:** Run `cd frontend && npm test -- --run tests/unit/route-line.test.ts tests/unit/map-utils.test.ts`.
- [ ] **Step 6:** Run `cd frontend && npm run typecheck && npm run lint`.
- [ ] **Step 7:** Commit `git commit -am "refactor(frontend): split route-line.ts into route-splice.ts + route-legs.ts, keep route-line.ts as barrel"`.

### Task 7.2: Split `Map.tsx` into `components/Map/` folder with barrel

**Files:**
- Create: `frontend/src/components/Map/index.ts`
- Create: `frontend/src/components/Map/basemap.ts`
- Create: `frontend/src/components/Map/soc-gradient.ts`
- Create: `frontend/src/components/Map/markers.ts`
- Create: `frontend/src/components/Map/popups.ts`
- Create: `frontend/src/components/Map/superchargers.ts`
- Create: `frontend/src/components/Map/view-state.ts`
- Create: `frontend/src/components/Map/MapVisualization.tsx`
- Remove: `frontend/src/components/Map.tsx`
- Test: `frontend/tests/unit/map-utils.test.ts`, `frontend/tests/unit/construction-zone-popup.test.ts`

**Interfaces:**
- Preserve exactly, all still importable from `@/components/Map` (the barrel): `MapVisualization` (default export in current file — check whether `App.tsx` imports it as `{ MapVisualization }` named or default; per the scout report it's a named import `import { MapVisualization } from ...` — the barrel must export it as a named export, matching current usage), `buildBasemapStyle`, `TILES_BASE_URL`, `basemapStyle`, `SOC_COLOR_STOPS`, `socToColor`, `buildSocGradientExpression`, `StopRole`, `stopRole`, `roleToMarkerColor`, `roleToLabel`, `roleToMarkerGlyph`, `buildMarkerElement`, `buildSuperchargerPopoverElement`, `SUPERCHARGER_LAYER_IDS`, `buildSuperchargerGeoJson`, `buildPopupText`, `buildChargingStopMarkerElement`, `formatChargingDuration`, `buildChargingStopPopupHtml`, `buildStopPopupHtml`, `WAYPOINT_STOP_MATCH_TOLERANCE_M`, `findWaypointStopAt`, `SPERRUNGSTYP_LABELS`, `buildConstructionZoneMarkerElement`, `buildConstructionZonePopupHtml`, `buildRouteHoverText`, `MapViewState`, `DEFAULT_MAP_VIEW`, `isValidMapViewState`, `MapProps`.
- Since `App.tsx` currently imports from `"@/components/Map"` (resolving to `Map.tsx`), after this split the same specifier must resolve to `Map/index.ts` — this is automatic Node/Vite/TS module resolution behavior (a directory with `index.ts` resolves the same bare specifier), so **no import statement anywhere needs to change** as long as `Map.tsx` is deleted (not left alongside `Map/`, which would create an ambiguous/incorrect resolution — verify `tsconfig.json`/`vite.config.ts` moduleResolution settings resolve directory-with-index correctly before deleting the flat file, by testing the build after Step 8 below).
- `basemap.ts`: `buildBasemapStyle`, `TILES_BASE_URL`, `basemapStyle`, plus the `setWorkerUrl(...)` call and `liberty-style.json` import (module-load side effects move here).
- `soc-gradient.ts`: `SOC_COLOR_STOPS`, `toHex` (not exported, keep private), `socToColor`, `buildSocGradientExpression`.
- `markers.ts`: `StopRole`, `stopRole`, `roleToMarkerColor`, `roleToLabel`, `roleToMarkerGlyph`, `buildMarkerElement`, `buildChargingStopMarkerElement`, `buildConstructionZoneMarkerElement`.
- `popups.ts`: `buildPopupText`, `formatChargingDuration`, `formatLaenge` (not exported), `buildChargingStopPopupHtml`, `buildStopPopupHtml`, `buildConstructionZonePopupHtml`, `SPERRUNGSTYP_LABELS`, `findWaypointStopAt`, `WAYPOINT_STOP_MATCH_TOLERANCE_M`, `buildRouteHoverText` (imports role/marker helpers from `./markers`).
- `superchargers.ts`: `buildSuperchargerPopoverElement`, `buildSuperchargerGeoJson`, `SUPERCHARGER_LAYER_IDS`.
- `view-state.ts`: `MapViewState`, `DEFAULT_MAP_VIEW`, `isValidMapViewState`.
- `MapVisualization.tsx`: `MapProps`, `MapVisualization` component, importing from all the above plus `../../utils/route-line` (post-Task-7.1 barrel), `../../utils/geo-utils`, `../../utils/datetime-utils`, `../../utils/currency-utils`, `../../utils/persistent-state`, `../../types`, `../../api/chargingApi`.
- `index.ts`: `export * from "./basemap"; export * from "./soc-gradient"; export * from "./markers"; export * from "./popups"; export * from "./superchargers"; export * from "./view-state"; export * from "./MapVisualization";` — verify zero name collisions across these six modules (checked against the symbol lists above: none).

- [ ] **Step 1:** Read `frontend/src/components/Map.tsx` in full (already partially covered by the scout report's line ranges) to get exact code bodies.
- [ ] **Step 2:** Create `Map/basemap.ts` with the module-init side effects, `buildBasemapStyle`, `TILES_BASE_URL`, `basemapStyle`.
- [ ] **Step 3:** Create `Map/soc-gradient.ts` with `SOC_COLOR_STOPS`, `toHex`, `socToColor`, `buildSocGradientExpression` (import `RouteSample` type from `../../utils/route-line`).
- [ ] **Step 4:** Create `Map/markers.ts` with the role/marker cluster.
- [ ] **Step 5:** Create `Map/popups.ts` with the popup/format cluster, importing role helpers from `./markers`.
- [ ] **Step 6:** Create `Map/superchargers.ts` with the supercharger overlay cluster.
- [ ] **Step 7:** Create `Map/view-state.ts` with the map-view-state cluster.
- [ ] **Step 8:** Create `Map/MapVisualization.tsx` with `MapProps` + the `MapVisualization` component body, updating its internal references to import from the six new sibling files instead of using same-file symbols; keep every external import path the same except relative-depth adjustments (one more `../` level, since the file moved from `components/` to `components/Map/`).
- [ ] **Step 9:** Create `Map/index.ts` with the six `export *` statements.
- [ ] **Step 10:** Delete `frontend/src/components/Map.tsx`.
- [ ] **Step 11:** Run `cd frontend && npm run typecheck` — this will surface any missed import-path adjustment (extra `../`) immediately as a module-not-found error; fix each one found.
- [ ] **Step 12:** Run `cd frontend && npm test -- --run tests/unit/map-utils.test.ts tests/unit/construction-zone-popup.test.ts`.
- [ ] **Step 13:** Run `cd frontend && npm run lint`.
- [ ] **Step 14:** Run `cd frontend && npm run build` (production build catches resolution issues Vitest's jsdom environment might not) — confirm it succeeds.
- [ ] **Step 15:** Commit `git add -A frontend/src/components/Map* && git commit -m "refactor(frontend): split Map.tsx into components/Map/ folder by responsibility, barrel-exported"`.

---

## Phase 8: Frontend — `TripPlannerForm.tsx`

### Task 8.1: Split `TripPlannerForm.tsx` into a folder with barrel

**Files:**
- Create: `frontend/src/components/TripPlannerForm/index.ts`
- Create: `frontend/src/components/TripPlannerForm/form-helpers.ts`
- Create: `frontend/src/components/TripPlannerForm/ferry-helpers.ts`
- Create: `frontend/src/components/TripPlannerForm/timeline-rows.tsx`
- Create: `frontend/src/components/TripPlannerForm/TripPlannerForm.tsx`
- Remove: `frontend/src/components/TripPlannerForm.tsx`
- Test: `frontend/tests/unit/trip-planner-form-utils.test.ts`

**Interfaces:**
- Preserve exactly, all still importable from `@/components/TripPlannerForm`: `TripPlannerFormProps`, `GeocodingState`, `GeocodeSuggestionDisplay`, `getStopRole`, `isRawCoordinateLabel`, `isUnresolvedAddress`, `swapStops`, `validateForm`, `sameFaehrAusschluss`, `toggleFaehrAusschluss`, `setFaehrZeitfensterFuer`, `setLadedauerVorgabeFuer`, `faehrKey`, `getStopTimelineIcon`, `unterscheidetSichAlsUhrzeit`, `formatLadestationName`, `formatFahrsegmentStrecke`, `formatFahrsegmentDauer`, `TripPlannerForm` (the component itself — confirm export style, named per grep at line 562: `export function TripPlannerForm({...`).
- `form-helpers.ts`: `getStopRole`, `isRawCoordinateLabel`, `isUnresolvedAddress`, `swapStops`, `validateForm`, `getStopTimelineIcon`, `unterscheidetSichAlsUhrzeit`, `formatLadestationName`, `formatFahrsegmentStrecke`, `formatFahrsegmentDauer` (pure helpers, no ferry-specific logic).
- `ferry-helpers.ts`: `sameFaehrAusschluss`, `toggleFaehrAusschluss`, `setFaehrZeitfensterFuer`, `setLadedauerVorgabeFuer`, `faehrKey`.
- `timeline-rows.tsx`: `TimelineRow`, `Zeitbadge`, `Tagestrenner`, `FahrsegmentZeile` (the four small presentational sub-components used by the route timeline, lines 343-557 per the grep).
- `TripPlannerForm.tsx`: `TripPlannerFormProps`, `GeocodingState`, `GeocodeSuggestionDisplay`, `TripPlannerForm` component itself, importing helpers from the three sibling files above plus `ChargingStationPicker`, `Modal`, `buildRouteEintraege` (unchanged relative paths adjusted by one `../` level), geocoding API, currency/timing/datetime utils as currently used.
- `index.ts`: re-exports everything.

- [ ] **Step 1:** Read `frontend/src/components/TripPlannerForm.tsx:1-343` (imports + helper cluster, already covered by the grep above) and the remaining ranges already read by the FrontendForm scout (`300-2474` in chunks) — re-read any range not already captured verbatim in this session if writing code requires exact bodies.
- [ ] **Step 2:** Create `form-helpers.ts` with the ten pure form/validation/formatting helpers.
- [ ] **Step 3:** Create `ferry-helpers.ts` with the five ferry-exclusion helpers.
- [ ] **Step 4:** Create `timeline-rows.tsx` with the four presentational sub-components (`TimelineRow`, `Zeitbadge`, `Tagestrenner`, `FahrsegmentZeile`), importing `LucideIcon` types and any shared formatting helpers from `./form-helpers` as needed (e.g. `FahrsegmentZeile` likely uses `formatFahrsegmentDauer`/`formatFahrsegmentStrecke`).
- [ ] **Step 5:** Create `TripPlannerForm.tsx` (inside the new folder) with `TripPlannerFormProps`, `GeocodingState`, `GeocodeSuggestionDisplay`, the `GEOCODING_DEBOUNCE_MS`/`GEOCODING_MIN_QUERY_LENGTH` constants, and the full `TripPlannerForm` component body (lines 562-2474), importing from `./form-helpers`, `./ferry-helpers`, `./timeline-rows`, and adjusting all other relative imports by one extra `../` level (e.g. `../ChargingStationPicker` → `../../ChargingStationPicker`, `../../types/trip-request` → `../../../types/trip-request`, etc. — verify each one against the original file's import block).
- [ ] **Step 6:** Create `index.ts` re-exporting everything from the four sibling files.
- [ ] **Step 7:** Delete `frontend/src/components/TripPlannerForm.tsx` (the original flat file).
- [ ] **Step 8:** Run `cd frontend && npm run typecheck` — fix any relative-import-depth errors surfaced.
- [ ] **Step 9:** Run `cd frontend && npm test -- --run tests/unit/trip-planner-form-utils.test.ts`.
- [ ] **Step 10:** Run `cd frontend && npm run lint`.
- [ ] **Step 11:** Run `cd frontend && npm run build`.
- [ ] **Step 12:** Commit `git add -A frontend/src/components/TripPlannerForm* && git commit -m "refactor(frontend): split TripPlannerForm.tsx into components/TripPlannerForm/ folder by responsibility, barrel-exported"`.

### Task 8.2: Separate `trip-request.ts` types from payload-builder/validation logic

**Files:**
- Create: `frontend/src/types/trip-request-builder.ts`
- Modify: `frontend/src/types/trip-request.ts`
- Test: any test importing `buildTripRequestPayload`/`validateStops`/`createEmptyStop`

**Interfaces:**
- Preserve exactly, all still importable from `@/types/trip-request`: every existing exported type/interface plus `buildTripRequestPayload`, `validateStops`, `createEmptyStop`.
- `trip-request-builder.ts` receives `buildTripRequestPayload`, `validateStops`, `createEmptyStop` (the three logic functions), importing the request-contract types from `./trip-request`.
- `trip-request.ts` keeps all pure types/interfaces and adds `export * from "./trip-request-builder"` at the bottom.

- [ ] **Step 1:** Read `frontend/src/types/trip-request.ts` in full.
- [ ] **Step 2:** Create `trip-request-builder.ts` with the three functions, importing types from `./trip-request`.
- [ ] **Step 3:** In `trip-request.ts`, delete the three function bodies; add `export * from "./trip-request-builder"`.
- [ ] **Step 4:** Grep `frontend/src` and `frontend/tests` for `buildTripRequestPayload|validateStops|createEmptyStop` to find every consumer/test; confirm all import from `@/types/trip-request` (unchanged) — no consumer edits expected.
- [ ] **Step 5:** Run `cd frontend && npm run typecheck && npm test -- --run && npm run lint`.
- [ ] **Step 6:** Commit `git commit -am "refactor(frontend): separate trip-request payload builder/validation from type definitions"`.

---

## Phase 9: Frontend — Remaining Medium Files

### Task 9.1: Split `TripSummary.tsx` — extract pure time-plan builder

**Files:**
- Create: `frontend/src/utils/time-plan.ts`
- Modify: `frontend/src/components/TripSummary.tsx`
- Modify: `frontend/tests/unit/trip-summary-zeitplan.test.ts` (update import path — see Interfaces)
- Test: `frontend/tests/unit/trip-summary-zeitplan.test.ts`

**Interfaces:**
- `time-plan.ts` receives `buildTimePlan`, `TimePlanEntry`, `shortAddress`, `stopLabel` verbatim.
- `TripSummary.tsx` imports these from `../utils/time-plan` and keeps the React component + styles.
- Since `buildTimePlan`/`TimePlanEntry` are pure and test-covered from a **different** current import path (directly from `TripSummary.tsx` per the scout's note "Re-export buildTimePlan or update trip-summary-zeitplan.test.ts"), and this move changes their canonical location, update `trip-summary-zeitplan.test.ts`'s import statement to point at `../../src/utils/time-plan` (mechanical one-line change) rather than adding a re-export from the component file — cleaner long-term home for a pure-logic test.

- [ ] **Step 1:** Read `frontend/src/components/TripSummary.tsx` in full.
- [ ] **Step 2:** Create `time-plan.ts` with `buildTimePlan`, `TimePlanEntry`, `shortAddress`, `stopLabel`.
- [ ] **Step 3:** In `TripSummary.tsx`, delete the moved code, import the four names from `../utils/time-plan`.
- [ ] **Step 4:** Update `trip-summary-zeitplan.test.ts`'s import statement to the new path.
- [ ] **Step 5:** While reading, also check the flagged duplication ("child-timeplan distance/duration loop duplicates berechneFahrsegment") — if `time-plan.ts`'s logic can call `berechneFahrsegment` from `timing-utils.ts` instead of re-implementing the loop, do so (behavior-preserving only if the two implementations are provably equivalent — verify by reading both loops side by side before substituting; if the substitution is risky, skip and leave a comment noting the duplication for a future task rather than risk a silent behavior change).
- [ ] **Step 6:** Run `cd frontend && npm test -- --run tests/unit/trip-summary-zeitplan.test.ts`.
- [ ] **Step 7:** Run `cd frontend && npm run typecheck && npm run lint`.
- [ ] **Step 8:** Commit `git commit -am "refactor(frontend): extract buildTimePlan/TimePlanEntry from TripSummary.tsx into utils/time-plan.ts"`.

### Task 9.2: Split `geocoding.ts` — separate parsing from HTTP transport

**Files:**
- Create: `frontend/src/api/geocoding-parser.ts`
- Modify: `frontend/src/api/geocoding.ts`
- Test: any existing geocoding test (grep for `geocoding` test files first)

**Interfaces:**
- Preserve exactly, importable from `@/api/geocoding`: every currently-exported function/type, including `GeocodeSuggestion`.
- `geocoding-parser.ts` receives `readStringField`, `parseSuggestion`, `formatDisplayAddress`, `GeocodeSuggestion`.
- `geocoding.ts` keeps the Nominatim HTTP client function(s), importing parsing helpers from `./geocoding-parser`, and re-exports `GeocodeSuggestion` (`export type { GeocodeSuggestion } from "./geocoding-parser"`).

- [ ] **Step 1:** Read `frontend/src/api/geocoding.ts` in full.
- [ ] **Step 2:** Create `geocoding-parser.ts` with the four symbols.
- [ ] **Step 3:** In `geocoding.ts`, delete the moved code, import from `./geocoding-parser`, re-export `GeocodeSuggestion`.
- [ ] **Step 4:** Grep `frontend/src frontend/tests` for imports from `@/api/geocoding` or `./geocoding` to confirm no consumer needs edits.
- [ ] **Step 5:** Run `cd frontend && npm run typecheck && npm test -- --run && npm run lint`.
- [ ] **Step 6:** Commit `git commit -am "refactor(frontend): separate geocoding HTTP transport from address parsing/formatting"`.

### Task 9.3: Split `currency-conversion.ts` — separate Frankfurter API client from cache/conversion logic

**Files:**
- Create: `frontend/src/utils/frankfurter.ts`
- Modify: `frontend/src/utils/currency-conversion.ts`
- Test: `frontend/tests/unit/currency-conversion.test.ts` (per scout: imports all three of `convertToEUR`/`convertAllToEUR`/`clearRateCache` — confirm exact test import list before editing)

**Interfaces:**
- Preserve exactly, importable from `@/utils/currency-conversion`: `convertToEUR`, `convertAllToEUR`, `clearRateCache` (per the test's import list — confirm the complete public surface by reading the file, not assuming only these three).
- `frankfurter.ts` receives `fetchLatestRates`, `getRates`, and the localStorage-cache constants/logic.
- `currency-conversion.ts` keeps `convertToEUR`, `convertAllToEUR`, `clearRateCache` as the public facade, delegating rate lookups to `./frankfurter`.

- [ ] **Step 1:** Read `frontend/src/utils/currency-conversion.ts` in full and confirm its complete exported surface.
- [ ] **Step 2:** Create `frankfurter.ts` with `fetchLatestRates`, `getRates`, cache constants/localStorage logic.
- [ ] **Step 3:** In `currency-conversion.ts`, delete the moved code, import from `./frankfurter`, keep the three public facade functions delegating to it.
- [ ] **Step 4:** Run `cd frontend && npm test -- --run tests/unit/currency-conversion.test.ts`.
- [ ] **Step 5:** Run `cd frontend && npm run typecheck && npm run lint`.
- [ ] **Step 6:** Commit `git commit -am "refactor(frontend): separate Frankfurter API client/cache from currency-conversion facade"`.

### Task 9.4: De-duplicate `App.tsx` identical `setStops` handlers

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: none new (behavior-preserving, no test file targets this specifically — rely on typecheck + existing App-level test coverage if any, or manual smoke test)

- [ ] **Step 1:** Read `frontend/src/App.tsx` in full; locate `handlePickPosition`/`handleStopMove` (per the scout: byte-identical `setStops` map bodies).
- [ ] **Step 2:** Confirm byte-for-byte identity by reading both bodies side by side.
- [ ] **Step 3:** If identical, extract the shared `setStops` map body into one private helper function (e.g. `applyStopPositionUpdate`) called by both handlers, preserving each handler's own signature/name (do not rename the two exported/used handlers themselves — only their shared internals).
- [ ] **Step 4:** Run `cd frontend && npm run typecheck && npm run lint`.
- [ ] **Step 5:** Manually smoke-test in the browser: `./run.sh start`, open the app, drag a stop marker on the map (exercises `handleStopMove`) and use "pick on map" for a stop (exercises `handlePickPosition`), confirm both still update stop positions correctly, then `./run.sh stop`.
- [ ] **Step 6:** Commit `git commit -am "refactor(frontend): de-duplicate identical setStops handlers in App.tsx"`.

---

## Phase 10: Final Verification

### Task 10.1: Full backend verification

**Files:** none; verification only.

- [ ] **Step 1:** Run `uv run pytest -m "not integration"` — confirm the same or greater pass count as the Phase 0 baseline, zero new failures.
- [ ] **Step 2:** Run `uv run pytest --cov=src/tripplanner --cov-report=term-missing -m "not integration"` and confirm the 85% coverage threshold (per AGENTS.md / `docs/04-repo-tooling-setup.md`) still holds — moved code carries its existing test coverage with it, but re-verify because splitting a module can occasionally leave a private helper's tests behind if an import path update was missed.
- [ ] **Step 3:** Run `uv run hk check --all` — zero errors.
- [ ] **Step 4:** If a local GraphHopper instance is available, run `uv run pytest -m integration` for the integration suite too.

### Task 10.2: Full frontend verification

**Files:** none; verification only.

- [ ] **Step 1:** Run `cd frontend && npm run lint && npm run typecheck && npm test -- --run && npm run build`.
- [ ] **Step 2:** Start the full stack via `./run.sh start`, open the app in a browser, run through one complete trip-planning flow end to end (enter origin/destination, submit, view map + summary, hover a construction zone popup, open the vehicle/SoC modal), confirm no console errors and no visual regressions versus pre-refactor behavior.
- [ ] **Step 3:** `./run.sh stop`.

### Task 10.3: Report findings from investigation-only tasks

**Files:** none; reporting only.

- [ ] **Step 1:** Summarize the outcome of Task 5.3 (`battery/battery.py` vs `optimizer.py` duplication) and Task 4.5 (routing segment-construction duplication) for the user, since both were scoped as investigate-and-report-if-no-safe-action rather than guaranteed code changes.
- [ ] **Step 2:** If either investigation surfaced a genuine dead-code or behavior-changing cleanup opportunity that this plan explicitly deferred (per its behavior-preservation constraint), list it as a follow-up recommendation, not as unfinished work under this plan.
