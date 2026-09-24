# Elevation Profile Performance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cut `extract_elevation_profile` pipeline-step wall-clock (measured **118.4 s** in a
production log, vs. **7.2 s** for the entire GraphHopper route calculation) to a small
multiple of the network round-trip count that is actually unavoidable — target **low single-digit
seconds** for a DE/DK/SE-scale trip. No change to the elevation/energy contract (segment count,
gradient semantics, coordinate convention) — this is a pure I/O-efficiency fix.

**Evidence (from user-supplied backend log, 2026-08-23):**

```
20:51:03,366 Pipeline step 'extract_elevation_profile' — start
20:53:01,786 Pipeline step 'extract_elevation_profile' — done in 118420.4 ms
```

Every other step in the same pipeline run (`route_calculate` 7.2 s, `optimize_charging_plan`
~1.2 s ×2, everything else <110 ms) is fast. `extract_elevation_profile` alone is >90% of total
pipeline time (128.7 s). This matches the pattern already on record from an earlier "why is
route calculation slow" investigation (prior session, `docs/plans/` context / agent memory):
*"Elevation … currently `[get_elevation(c) for c in coords]` sequential, one S3 byte-range fetch
per coordinate, no tile caching."* That investigation flagged elevation as secondary to weather
at the time; weather has since been parallelized (`LoadBalancedWeatherProvider`), which leaves
elevation as the dominant remaining cost — consistent with this log.

**Tech Stack:** Python (FastAPI, rasterio/GDAL, asyncio).

**Spec:** Direct user request (2026-08-23): investigate why `extract_elevation_profile` takes
118 s and produce a plan to do it in a fraction of the time.

## Global Constraints (from `AGENTS.md`)

- `uv run hk check --all` must pass; `uv run pytest -m "not integration"` must pass.
- Coverage threshold 85% for `src/tripplanner/` is not undercut.
- No new cross-module data structures except through the module's `models.py`.
- Every public function keeps full type annotations (`mypy --strict`) and a Google-style
  docstring; new/touched code and docstrings in English.
- No live external-API calls from unit tests — use local fixture tiles / mocks (as the existing
  `tests/elevation/test_providers.py::copernicus_tile_dir` fixture and
  `tests/elevation/test_elevation.py::_MockDataset` already do).
- Coordinate convention: `(lat, lon)`.
- Commit in small, self-contained steps.

---

## 1. Root cause analysis

### 1.1 The segment contract (do not change)

`ElevationProvider.get_elevation_profile()` (`src/tripplanner/elevation/elevation.py:46-90`)
builds exactly **one coordinate per route-segment boundary** (`len(route.segments) + 1`
points) — every raw GraphHopper polyline edge is one `RouteSegment`
(`src/tripplanner/routing/providers.py:328-364`, one segment per consecutive point pair, no
distance-based merging). `sampling_distance_m` is accepted but **never used** — it's dead,
misleading parameter (docstring implies coarser sampling is possible; the body ignores it).

`ElevationProvider.calculate_segment_gradients()` (`elevation.py:92-143`) then **requires**
`len(elevation_points) == len(route.segments) + 1` and raises `ValueError` otherwise
(`elevation.py:110-114`) — every downstream per-segment slope/energy calculation depends on
this 1:1 mapping.

**Consequence for this plan:** we cannot cut the number of sampled points (that's the
"coarser segments" structural option the prior investigation explicitly deferred as
higher-risk/out of scope). A DE/DK/SE-scale trip realistically produces **low thousands** of
segment-boundary coordinates from GraphHopper's polyline. The fix must make fetching *that many
points* cheap, not reduce the count.

### 1.2 The actual bottleneck: per-point blocking network reads

`CopernicusDEMDataSource.get_elevations_batch()` (`src/tripplanner/elevation/providers.py:330-409`)
does the following, per call:

1. Groups coordinates by 1°×1° tile (cheap, in-memory).
2. Opens each **distinct tile** dataset concurrently via `asyncio.to_thread` (good — this part
   already parallelizes).
3. **Then, for every individual coordinate** (not tile), runs, synchronously, on the event-loop
   thread (`providers.py:370-379`):

   ```python
   row, col = dataset.index(lon, lat)
   value = dataset.read(1, window=Window(col, row, 1, 1))[0, 0]
   ```

Step 3 is the 118 s. Two compounding problems:

- **One HTTP range request per point, not per tile.** Each `dataset.read(..., window=Window(col,
  row, 1, 1))` against a `/vsicurl/` GDAL dataset over the public Copernicus S3 bucket is a
  potential network round-trip (a GDAL-internal block cache absorbs repeat reads *within the
  same raster block*, but every time a route crosses into a new block — Copernicus GLO-30 COGs
  use small internal blocks, so a route re-enters a new block every few hundred meters to a few
  km — a fresh HTTP GET fires). With low thousands of points along a long route, this is easily
  **hundreds of cold block fetches**, each paying full S3 request latency (DNS/TLS/HTTP,
  typically 100–400 ms from a residential/dev network with no persistent connection reuse
  tuning — see 1.3).
- **It's fully synchronous and un-batched across tiles.** Nothing in step 3 is wrapped in
  `asyncio.to_thread` or `asyncio.gather` — every single `dataset.read()` call blocks the event
  loop and every point (even points in *different, already-open* tiles) is fetched **one at a
  time, strictly sequentially**. This matches the observed symptom exactly: near-zero CPU
  (network-wait, not compute), multi-minute wall clock. Compare to step 2, which already proves
  the concurrent pattern works (tile opens *are* parallelized) — step 3 just never got the same
  treatment.

### 1.3 Secondary: no GDAL/vsicurl tuning, no persistent tile cache

- No `GDAL_DISABLE_READDIR_ON_OPEN`, `VSI_CACHE`, `GDAL_HTTP_VERSION`, or `CPL_VSIL_CURL_*`
  environment/`rasterio.Env` configuration anywhere in the codebase (confirmed: no matches for
  `VSICURL|GDAL_|VSI_CACHE|CPL_` outside this analysis). Defaults leave several avoidable
  round-trips on the table (e.g. GDAL may probe for sibling files — `.aux.xml`, `.ovr` — on
  every `rasterio.open()`, each an extra failed HTTP request against S3).
- `docs/plans/02-elevation.md` (the original elevation-module plan) and
  `docs/plans/10-provider-integration-wiring.md` both describe wiring
  `CopernicusDEMDataSource(cache_dir=...)` — a **local on-disk tile cache** was part of the
  original design intent. The current constructor
  (`src/tripplanner/elevation/providers.py:217-234`) has no `cache_dir` parameter at all; every
  process start re-fetches every tile from S3 from scratch, for every trip, forever. Germany /
  Denmark / Sweden trips repeatedly hit the same handful of 1° cells (home region, popular
  corridors) — this cache was never actually built.

---

## 2. Fix, ranked by leverage / risk

### Fix 1 (primary, highest leverage): batch pixel reads per tile, off the event loop

Replace the per-coordinate `dataset.read(window=Window(col,row,1,1))` loop with **one bulk read
per tile**, executed in a worker thread, with all tiles' reads running concurrently:

- For each tile, once the dataset is open, read the **entire band** once
  (`dataset.read(1)` → a single 2-D numpy array) **inside `asyncio.to_thread`**, not per-point.
  A GLO-30 tile is ~3600×3600 int16 pixels; as a compressed COG this is a handful of MB — one
  bulk transfer over S3 is dramatically cheaper than hundreds of discrete range requests to the
  same object, and turns all *n* points in that tile into pure in-memory numpy indexing
  (`array[row, col]`) afterward — zero further I/O.
  - If memory footprint of caching full arrays for up to `max_open_tiles` (16) tiles is a
    concern, cap it (e.g. read only the pixel *rows* spanned by the tile's sampled points via a
    single bounding-box `Window`, still one read call per tile instead of one per point — either
    approach removes the O(points) HTTP-request scaling; prefer whole-band read for simplicity
    unless memory profiling says otherwise).
- Run the per-tile bulk-read+index step for **all tiles concurrently** via
  `asyncio.gather(*[asyncio.to_thread(_read_tile_and_index, uri, points) for uri, points in
  tile_coords.items()])` — mirrors the existing (working) pattern from step 2 of the same
  method.
- Preserve existing behavior exactly for: nodata → `0.0`, missing tile → `0.0`, exceptions during
  open/read → `0.0` with the existing `logger.warning`, and the eviction self-heal (re-open a
  tile closed mid-batch by the LRU) — same semantics, just operating on a bulk array instead of
  a single pixel.
- `get_elevation()` (single-point path, `providers.py:301-328`, used by `get_tile_at`/tests/
  any non-batch caller) is untouched — it's not on the hot path for route profiles.

This is the fix that turns "hundreds of sequential blocking round-trips" into "a handful of
concurrent bulk transfers" — expected to cut the 118 s to low single digits, bounded by
`(number of distinct 1° tiles the route crosses) × (time to fetch one tile) / concurrency`,
not by point count at all.

### Fix 2 (cheap, compounding): GDAL/vsicurl tuning

Wrap tile opens in an `rasterio.Env(...)` context (or set once at process startup, e.g. in
`providers_factory.py` / app lifespan) with:

- `GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"` — skip sibling-file probing on every open.
- `CPL_VSIL_CURL_USE_HEAD="NO"` — skip the extra HEAD request GDAL issues before the first
  range GET.
- `VSI_CACHE="TRUE"` + a bounded `VSI_CACHE_SIZE` (e.g. 64 MB) — caches raw HTTP byte ranges,
  helping if the same tile is reopened later in the same process (LRU eviction + reopen case).
- `GDAL_HTTP_VERSION="2"` — enables HTTP/2 multiplexing against S3, reducing per-request
  connection overhead when Fix 1 still issues more than one request per tile (e.g. large tiles
  read as multiple windowed chunks).
- `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"` — extra guard against stray sibling-file requests.

Low risk (pure GDAL config, no code-path change), should be a quick win on top of Fix 1,
particularly for the "cold start" (first tile open) latency.

### Fix 3 (optional, addresses repeat-trip cost): persistent on-disk tile cache

Add the `cache_dir` parameter to `CopernicusDEMDataSource.__init__` that the original elevation
plan (`docs/plans/02-elevation.md`, `docs/plans/10-provider-integration-wiring.md`) already
called for but was never implemented:

- `cache_dir: Path | None = None` constructor arg.
- On tile open: if `cache_dir / f"{tile_name}.tif"` exists locally, open that (local file,
  `rasterio.open(local_path)`, no `/vsicurl/`) instead of the remote URI.
- On a cache miss: after a successful remote open + full-band read (Fix 1 already reads the
  whole band), write the bytes to `cache_dir` (e.g. via `rasterio`'s copy/translate to a local
  GeoTIFF, or a raw `requests`/`httpx` download of the same object — implementer's choice,
  document it) so subsequent requests (same process *or* a new process) hit disk.
- Wire `providers_factory.build_production_providers()`
  (`src/tripplanner/trip_input/providers_factory.py:91-94`) to pass a configured `cache_dir`
  (e.g. an XDG-style cache path or an env var, consistent with any existing cache-dir
  conventions in the repo — check for one before inventing a new env var name).
- Bound the cache (simple: no eviction needed for DE/DK/SE-scale usage — a handful of 1° tiles
  at a few MB each; document the expected footprint rather than building LRU eviction on disk
  unless it turns out to matter).

This doesn't help the *first* trip through a new region, but makes every subsequent trip through
previously-visited tiles (very likely for a personal trip planner used repeatedly in DE/DK/SE)
close to instant for the elevation step. Do this **after** Fix 1 — Fix 1 alone already gets a
cold trip from 118 s to low single digits; Fix 3 is the icing for repeat trips.

---

## 3. Implementation steps

### 3.1 `src/tripplanner/elevation/providers.py`

- Rewrite `get_elevations_batch()` step 3 (`providers.py:365-409`): replace the per-coordinate
  `for idx, lat, lon in tile_coords[uri]: ... dataset.read(window=Window(col,row,1,1))` loop
  with a per-tile bulk read function run via `asyncio.to_thread`, gathered concurrently across
  tiles (see Fix 1). Keep the existing eviction self-heal branch, adapted to operate on the bulk
  array (re-open + bulk-read again if the dataset was closed mid-batch).
- Add `rasterio.Env(...)` (Fix 2 settings) around the tile-open/read calls — either as a
  context manager wrapping the whole `get_elevations_batch` body, or set once via
  `rasterio.Env(...).__enter__()` at provider construction / app startup (prefer scoping it to
  the call so it doesn't leak global GDAL state into unrelated code — confirm with a quick
  rasterio-Env-nesting check, since `rasterio.Env` supports nesting).
- (Fix 3, if included) add `cache_dir` param + local-file-first tile resolution in `_tile_uri`/
  `_dataset_for_tile`.

### 3.2 `src/tripplanner/trip_input/providers_factory.py`

- (Fix 3 only) pass `cache_dir=...` when constructing `CopernicusDEMDataSource()`
  (`providers_factory.py:93`).

### 3.3 No changes needed

- `src/tripplanner/elevation/elevation.py` — `get_elevation_profile` / `calculate_segment_gradients`
  contract stays exactly as-is (see §1.1 — deliberately out of scope).
- `src/tripplanner/routing/providers.py` — segment-building stays as-is.
- `src/tripplanner/trip_input/api.py` — `_step_2_extract_elevation_profile` is a thin wrapper;
  no change needed.

---

## 4. Tests

### Update

(Existing tests exercise the per-point loop directly — behavior must be preserved,
internals will change.)

- `tests/elevation/test_elevation.py::TestElevationTileEviction`
  (`test_batched_elevation_survives_tile_eviction`, `test_batched_elevation_all_fresh_datasets`,
  `providers.py:370-451` in the test file) — currently mock `dataset.read(band, window=None)`
  returning a scalar-filling `_MockReadResult`. Update `_MockDataset`/`_MockReadResult` to
  support a bulk `read(1)` call returning a 2-D-indexable array-like (or update the mocks'
  `read()` signature to match whatever the rewritten implementation calls), keeping the same
  assertions: results correct despite eviction, some tiles closed/evicted during a batch that
  exceeds `_max_open_tiles`.
- `tests/elevation/test_providers.py` (`test_get_elevations_batch_mixes_tile_hit_and_miss` and
  neighbors, `test_providers.py:151-155` area) — these use a real local `copernicus_tile_dir`
  fixture (no network); confirm they still pass unmodified (they test the *public contract*,
  not the internal read strategy) — if they fail, the failure itself is the regression signal
  for the rewrite.

### Add

- A test proving the new implementation does **not** call `dataset.read()` once per coordinate:
  e.g. instrument a mock dataset counting `read()` invocations, batch 50 coordinates that fall
  into 2 tiles, assert `read()` was called a small constant number of times (≤ tiles count, or
  ≤ tiles × small-window-chunks if the bounding-window variant is chosen) — not 50.
- A test proving per-tile reads run concurrently (e.g. mock `read()` with an `asyncio.sleep`,
  assert wall time ≈ one tile's delay, not `n_tiles × delay`) — mirrors the concurrency the
  method's docstring already claims for step 2 (tile *opening*), now also true for step 3
  (pixel *reading*).
- (Fix 3) `cache_dir` tests: first call to a tile writes a local file; second call (same or new
  `CopernicusDEMDataSource` instance, same `cache_dir`) reads from disk without touching the
  remote base URL (assert via a `base_url` that would error/404 if hit).
- (Fix 2) no dedicated unit test needed for GDAL env tuning (it's config, not logic) — smoke-test
  it in the integration test below.

### Integration

(Existing, `pytest.mark.integration`, real network — not run in CI per `AGENTS.md`, run
manually.)

- `tests/integration/test_trip_end_to_end.py::test_elevation_real_data` already exercises a
  real Munich→Garmisch route against the live Copernicus bucket
  (`_make_elevation_provider`, `test_trip_end_to_end.py:121-122,205-209`). Add a wall-clock
  assertion or at least manual timing note (e.g. `time.perf_counter()` around the call, logged
  or asserted `< N seconds` with generous slack for CI network variance) so a future regression
  here is caught by the one test that talks to the real bucket. Keep it `@pytest.mark.integration`
  (excluded from the default `pytest -m "not integration"` run per `AGENTS.md`).

---

## 5. Verification (do all before declaring done)

1. `uv run hk check --all` — clean.
2. `uv run pytest -m "not integration"` — green, coverage ≥ 85% for `src/tripplanner/`.
3. Manually run `uv run pytest -m integration tests/integration/test_trip_end_to_end.py -k elevation`
   against the real Copernicus bucket and confirm the elevation step's wall-clock dropped from
   the 118 s baseline to low single-digit seconds for a comparable multi-hundred-km route.
4. `./run.sh start`, submit a real long DE/DK/SE trip through the running app, and confirm via
   `.run/backend.log`'s `Pipeline step 'extract_elevation_profile'` timing line that it now
   completes in low single-digit seconds (the exact same log line format the user reported the
   118 s from). Stop services with `./run.sh stop` afterward.
5. Confirm gradient/energy output is unchanged for a fixed test route (no regression in
   `steigung_prozent`/`hoehendifferenz_m` values) — the existing
   `test_calculate_segment_gradients_basic` and integration Zugspitze-regression-value tests
   (mentioned in `docs/plans/02-elevation.md:435`) cover this; rerun and confirm unchanged
   values, since Fix 1 changes *how* pixels are read but must not change *which* pixel value is
   returned for a given coordinate.

## 6. Acceptance criteria

- [ ] `get_elevations_batch()` issues at most one bulk read per distinct tile (not one per
      coordinate), verified by a test asserting the read-call count is bounded by tile count.
- [ ] Per-tile reads run concurrently (`asyncio.gather`/`to_thread`), verified by a
      concurrency-timing test.
- [ ] `extract_elevation_profile` pipeline step drops from the 118 s baseline to low
      single-digit seconds on a real multi-hundred-km DE/DK/SE route (manual verification per
      §5.3/§5.4 — record the before/after numbers in the commit message).
- [ ] Elevation values and computed gradients are byte-identical to before the change for a
      fixed test route (no silent precision/semantics change).
- [ ] All existing elevation tests pass (updated where they mocked the old per-point `read()`
      call shape); new tests from §4 added and passing; `hk check --all` clean.
- [ ] (If Fix 3 implemented) repeat trips through the same 1° tiles skip the network entirely on
      the second run, verified by a cache-dir test.

## 7. Out of scope (explicitly do NOT do)

- Changing `sampling_distance_m` semantics, coarsening route segments, or otherwise reducing the
  number of elevation sample points — that's the "structural" option the prior performance
  investigation explicitly deferred as higher-risk (touches energy/gradient semantics); this
  plan is a pure I/O-layer fix.
- Touching the weather or construction pipeline steps (already addressed/tracked separately —
  see `docs/plans/12-weather-detail-levels.md` and prior session notes on
  `LoadBalancedWeatherProvider`).
- Rate-limit/provider-selection changes to `CopernicusDEMDataSource` (there is no rate limit on
  the public Copernicus bucket to manage, unlike the weather providers).
- Adding a new external dependency — `rasterio`/GDAL and its `vsicurl`/`Env` facilities already
  provide everything needed.
