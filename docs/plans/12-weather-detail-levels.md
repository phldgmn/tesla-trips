# Weather Detail Levels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the binary "Wetter" route-details toggle (off/on) with a four-level weather
granularity control (`off`, `low`, `medium`, `high`) that lets the user trade weather
resolution for calculation speed. `off` = today's disabled state, `high` = today's enabled
state, `low`/`medium` = new coarser levels.

**Architecture:** The weather cost lives in `trip_input`'s pipeline (`_step_5_fetch_weather`
inside the iterative ETA/weather convergence loop of `create_trip_simulation`): it builds one
weather query per route segment and re-fetches up to `max_iterations` (3) times as ETAs
converge, each fetch fanning out to the coordinate groups across all weather providers. The
plan adds a `wetter_detailgrad` (level) field that travels from the frontend through
`TripRequestAPI` into the pipeline, and moves the "how many weather queries, how often do we
refetch" decision into a new pure, unit-testable function in the `weather` module. `off` and
`high` must behave byte-for-byte like the current boolean states; `low`/`medium` shrink the
query set and skip refetching.

**Tech Stack:** Python (FastAPI, Pydantic v2, Typer), React + TypeScript (Vite).

**Spec:** Direct user request (2026-08-23): four-level weather toggle, `off` = current
disabled state, `high` = current enabled state, `low`/`medium` = less granular weather lookup
that still provides useful information.

## Global Constraints (from `AGENTS.md`)

- `uv run hk check --all` must pass; `uv run pytest -m "not integration"` must pass.
- Coverage threshold 85% for `src/tripplanner/` is not undercut.
- No new cross-module data structures except through the module's `models.py`;
  `tripplanner.geo` is the only exception dependency.
- Every public function gets full type annotations (`mypy --strict`) and a Google-style
  docstring; new code, comments and docstrings in English (translate touched German text).
- Coordinate convention: `(lat, lon)`.
- No live external-API calls from unit tests — use the existing fakes
  (`FakeWeatherProvider`, etc.).
- Services only via `./run.sh` (start/stop); agents stop services when done.
- Commit in small, self-contained steps; changes are always committed.
- Frontend build/lint via the existing `frontend/` tooling (npm scripts in
  `frontend/package.json`); frontend unit tests live in `frontend/tests/unit/`.

---

## 1. Background: where the cost is today

Read before implementing. The slowness the user experiences comes from the weather path, and
it has four multiplicative factors (all in the code today, none of them changed by `low`/`medium`
unless explicitly designed to be):

1. **One query per route segment** — `_step_5_fetch_weather`
   (`src/tripplanner/trip_input/api.py`) builds a `WeatherQuery` (midpoint coordinate + ETA
   time) for *every* segment. A long DE→DK→SE route can have hundreds of segments → hundreds
   of distinct coordinates.
2. **Coordinate-fanout per fetch** — `LoadBalancedWeatherProvider`
   (`src/tripplanner/weather/providers.py`) resolves every distinct coordinate as a group
   (bounded concurrency 10), each group issuing one HTTP request per candidate provider
   (Open-Meteo, MET Norway, OpenWeather [rate-limited to 60 rpm], SMHI, DMI). One distinct
   coordinate ≈ one round-trip per provider.
3. **Repeated refetching** — `create_trip_simulation` loops `max_iterations=3` times,
   re-running `_step_5_fetch_weather` each iteration with updated ETA timestamps; only the
   provider-internal `(koordinate, zeitpunkt)` cache deduplicates. Timestamps usually shift
   each iteration → cache misses → near-full refetch.
4. **Per-iteration construction refetch** — `_step_6_construction_sites` also runs inside the
   loop. (Context only; see Out of scope.)

The two levers this plan pulls: **shrink the query set** (spatial granularity) and **stop
re-fetching / stop re-iterating** (temporal granularity). Both must make `low`/`medium`
strictly cheaper than `high` in total weather HTTP requests.

## 2. Level semantics (the design contract)

| Level    | Weather queries                | HTTP fetches | Refetch in convergence loop | Convergence loop (`max_iterations`) |
|----------|--------------------------------|--------------|-----------------------------|-------------------------------------|
| `off`    | none — placeholder samples     | 0            | n/a                         | keep as today (no weather work)     |
| `low`    | 1 representative point (trip midpoint, at departure time or mid-trip time) | 1 total | no                        | capped at 1 iteration               |
| `medium` | one midpoint per **N-th** segment (implementer picks N, suggested 4–6, exposed as a module constant) | 1 total | no                        | capped at 1 iteration               |
| `high`   | exactly today's behavior: one query per segment | up to 3 × segments (via cache-deduplicated refetch) | yes (as today) | 3 (as today) |

Rules:

- `off` and `high` are **exact** behavior-preserving renames of today's
  `wetter_beruecksichtigen=False` / `=True` paths. Do not "improve" them.
- `low`/`medium` fetch exactly **once** (before/inside the first iteration) and never
  refetch: the placeholder samples stay fixed while the loop runs. Because the weather no
  longer moves, capping the loop to 1 iteration is sufficient — charging-plan changes do not
  need to be re-optimized against new weather.
- Weather samples are consumed per segment downstream (`energy`, `wind`), so `low`/`medium`
  must **fan the reduced samples back out to one `WeatherSample` per segment** (broadcast the
  nearest/representative sample). Sample counts and order must keep matching the segment
  count exactly — the length contract is asserted in `wind.py`
  (`compute_wind_components_for_route`).
- `baustellen_beruecksichtigen` stays a separate boolean; this plan does not touch it.
- The per-provider rate-limit/timeout/cooldown machinery (`OpenWeatherProvider` limiter,
  `LoadBalancedWeatherProvider` cooldowns) is out of scope. (A possible follow-up, not part of
  this plan: cap provider concurrency or prefer keyless providers at `low`/`medium`. Do not
  implement here unless the chosen N for `medium` makes request volume still look excessive.)

## 3. Backend changes

### 3.1 API request model — `src/tripplanner/trip_input/api.py`

- Replace `wetter_beruecksichtigen: bool` with
  `wetter_detailgrad: Literal["off", "low", "medium", "high"] = "high"` on `TripRequestAPI`.
- **Backward compatibility:** add a Pydantic validator/`model_validator` that also accepts a
  legacy boolean: `true` → `"high"`, `false` → `"off"` (old clients and any saved payloads
  still send the boolean). Both the new field name and the old boolean should be understood —
  decide and document whether the old *field name* `wetter_beruecksichtigen` is also accepted
  (recommend: yes, via `model_config`/alias or a pre-validator that renames it; old
  frontends exist in the wild).
- Endpoint `create_trip_endpoint`: replace the
  `weather_provider if request.wetter_beruecksichtigen else None` ternary with logic driven
  by `request.wetter_detailgrad`, and pass the level into `create_trip_simulation` (new
  keyword argument, e.g. `weather_detail: WeatherDetailLevel`).
- The domain model `TripRequest` (`src/tripplanner/trip_input/models.py`) does **not** need
  the field: the detail level is an orchestration parameter, not part of the trip request
  that routing/energy consume. Keep it out of `models.py` unless it becomes needed.

### 3.2 Pipeline — `create_trip_simulation` / `_step_5_fetch_weather` in the same file

- Add a `weather_detail` parameter to `create_trip_simulation` (default `"high"`) and
  thread it to `_step_5_fetch_weather`.
- `off`: keep the current `provider is None → FakeWeatherProvider()` placeholder behavior
  exactly (the endpoint will pass `None` for `off`, so the step needs no new branch for it).
- `low`/`medium`: build queries via the new weather-module function (3.3) instead of the
  per-segment loop; fetch once; fan samples back out to per-segment `WeatherSample`s; do not
  pass `previous_queries` on later iterations (there are no later iterations — see cap below).
- Cap the convergence loop: `max_iterations` is effectively 1 when the level is `low`/`medium`
  (implement as `loop_max = 1 if weather_detail in ("low", "medium") else max_iterations`, or
  an early `break` after the first iteration). Document the reasoning in the docstring.
- Do **not** change `high`'s code path in any observable way.

### 3.3 New weather-module contract — `src/tripplanner/weather/`

- Add `WeatherDetailLevel` (a `Literal` type alias or `str` enum with values
  `"off" | "low" | "medium" | "high"`) to `weather/models.py` and re-export it from
  `weather/__init__.py`.
- Add a pure function, suggested shape:

  ```python
  async def fetch_weather_by_detail(
      provider: WeatherProvider,
      route: Route,
      segment_eta_list: Sequence[tuple[RouteSegment, timedelta]],
      abfahrtszeit: datetime,
      detail: WeatherDetailLevel,
  ) -> list[WeatherSample]:
      """...returns exactly one WeatherSample per segment (length == len(segment_eta_list))."""
  ```

  (Name/signature are a suggestion; the **contract** is: takes route + per-segment ETAs +
  departure time + level; returns per-segment samples; performs the right number of HTTP
  fetches per the table in §2; is deterministic and unit-testable with
  `FakeWeatherProvider`/`LoadBalancedWeatherProvider`.)
  - `high`: identical query set to today's per-segment midpoints (move the existing loop,
    don't duplicate it).
  - `low`: single `WeatherQuery` — suggested: midpoint of the middle segment, timestamp at
    departure time + half the total trip duration (so mid-trip weather, not just
    departure-time weather). One `fetch_weather` call; broadcast the sample to all segments
    (set each fan-out sample's `koordinate`/`zeitpunkt` to the segment's own values so
    downstream per-sample bookkeeping stays honest).
  - `medium`: every N-th segment's midpoint query (N as a module constant, suggested 5;
    first and last segment always included); one `fetch_weather` call; fan out each query's
    sample to its neighboring segments (nearest-neighbor assignment, e.g. each segment gets
    the sample of the closest sampled midpoint).
- Keep the `previous_queries`/`refetch_weather` mechanics intact for `high` — do not remove
  them.
- The fan-out helper (nearest-neighbor assignment for `medium`) is small and pure — make it a
  separate testable function.

### 3.4 CLI — `src/tripplanner/trip_input/cli.py`

- Add `--wetter-detailgrad` option to the `trips` command
  (`typer.Option` with the four choices, default `"high"`), pass it through the request
  dict / `create_trip_simulation` call in both the offline and production branches.
  (Optional but cheap; keeps the CLI honest with the API. If skipped, document the choice.)

## 4. Frontend changes

### 4.1 Types — `frontend/src/types/trip-request.ts`

- Add `export type WeatherDetailLevel = "off" | "low" | "medium" | "high";`
- `TripRequestPayload`: replace `wetter_beruecksichtigen: boolean` with
  `wetter_detailgrad: WeatherDetailLevel`.
- `buildTripRequestPayload`: replace `wetterBeruecksichtigen?: boolean` with
  `wetterDetailgrad?: WeatherDetailLevel`, default `"high"`.
- Keep `baustellenBeruecksichtigen` untouched.

### 4.2 Form state + UI — `frontend/src/components/TripPlannerForm.tsx`

- Replace `usePersistentState("wetter-beruecksichtigen", true)` with
  `usePersistentState<WeatherDetailLevel>("wetter-detailgrad", "high")` (new key).
- **localStorage migration:** the old key stored a boolean. On read, if the old key
  (`tesla-trips:v1:wetter-beruecksichtigen`) exists and the new key does not, map
  `true` → `"high"`, `false` → `"off"`, then remove the old key. Small helper function —
  unit-testable and worth one dedicated test (existing users should not silently jump to
  `high` after disabling weather).
- UI: in the "Routendetails:" row (currently a boolean pill at ~line 1260), replace the
  binary "Wetter" button with a **4-segment control** (four small connected pill buttons
  labeled `Aus` / `Niedrig` / `Mittel` / `Hoch` with the `CloudSun` icon) or a single
  cycling pill showing the active label — implementer's choice, but:
  - disabled while `isSubmitting` (as today);
  - `title` tooltip per level in English, e.g. `Hoch`: "Weather per route segment (most accurate, slowest calculation)",
    `Mittel`: "Weather at selected points (faster, coarse spatial resolution)",
    `Niedrig`: "Single weather point for the entire trip (fastest weather calculation)",
    `Aus`: "Weather data ignored (placeholder values)";
  - active state highlighted in the existing blue palette;
  - `aria-pressed` / `role="radiogroup"` semantics appropriate for a multi-state control.
- Pass `wetterDetailgrad` through the submit handler where `wetterBeruecksichtigen` is
  passed today.

## 5. Tests

### Backend (existing tests that must be updated)

- `tests/trip_input/test_api.py`:
  - `test_fastapi_endpoint_wetter_beruecksichtigen_false_skips_weather_provider` →
    `wetter_detailgrad: "off"`; assert the injected `FakeWeatherProvider` is never called.
  - `test_fastapi_endpoint_wetter_beruecksichtigen_default_true_calls_weather_provider` →
    default request (no field) → provider called, level behaves as `high`.
  - Add: `wetter_detailgrad` legacy-boolean compat test (`"wetter_beruecksichtigen":
    false` in the JSON → treated as `off`; `true` → `high`).
  - Add: `off` → 0 provider calls; `high` → per-iteration fetches as today; `low` → exactly
    **one** `fetch_weather` call total (assert via `FakeWeatherProvider.fetch_weather_calls`)
    and all segments still receive samples; `medium` → one call whose query count is
    `≈ ceil(n_segments / N)` (assert the bound, not the exact value, so the constant can be
    tuned).
- CLI test (`tests/trip_input/test_cli.py`) if the CLI option is added: option parses all
  four values and rejects unknown ones.

### Backend (new)

- New test module for the weather-detail function, e.g.
  `tests/weather/test_weather_detail.py`:
  - `high`: query set equals the per-segment midpoints (compare against the existing
    `_step_5` behavior on a small fixture route).
  - `low`: exactly one query; returned sample list length == segment count; single
    provider call.
  - `medium`: query count ≤ ceil(n/N)+1, first/last segment sampled; fan-out length ==
    segment count; nearest-neighbor assignment correct on a hand-computed fixture.
  - Empty/single-segment edge cases.

### Frontend

- `frontend/tests/unit/trip-request.test.ts`: `wetter_detailgrad` present in payload, default
  `"high"`, passes through when set.
- `frontend/tests/unit/` (existing `persistent-state.test.ts` or a new module): legacy
  boolean → level migration (true→high, false→off, missing key→default, corrupted old
  value→default).

## 6. Verification (do all before declaring done)

1. `uv run hk check --all` — clean.
2. `uv run pytest -m "not integration"` — green, coverage ≥ 85% for `src/tripplanner/`.
3. Frontend: `npm run lint` (and build/type-check per `frontend/package.json` scripts) +
   `npm test` in `frontend/` — green.
4. Manual smoke test via `./run.sh start`:
   - all four levels submit successfully (use `--offline`-equivalent fakes if no external
     APIs are reachable; otherwise keep the route short to respect rate limits);
   - watch `.run/backend.log`: `high` logs/provider-spies many coordinates; `low` one;
     `medium` a handful; `off` zero weather fetches.
   - localStorage: disable weather (old key `true`/`false`), reload, confirm the new control
     shows `Hoch`/`Aus` after migration.
   - Stop services with `./run.sh stop` afterward.
5. Docs: if `docs/tripplanner.trip_input.md` / `docs/tripplanner.weather.md` document the
   endpoint fields or provider surface by hand, update the affected sections; new
   docstrings in English.

## 7. Acceptance criteria

- [ ] `POST /trips` accepts `wetter_detailgrad` (`"off"|"low"|"medium"|"high"`, default
      `"high"`) and still accepts the legacy `wetter_beruecksichtigen` boolean.
- [ ] `off` is behaviorally identical to today's disabled state (placeholder samples, zero
      weather HTTP traffic).
- [ ] `high` is behaviorally identical to today's enabled state.
- [ ] `low`/`medium` complete with strictly fewer weather HTTP requests than `high`
      (one fetch total each) and still produce a valid full simulation (per-segment samples,
      wind components, energy results).
- [ ] UI shows a 4-state weather control in the "Routendetails:" row with tooltips, disabled
      while submitting, persisted across reloads with legacy-boolean migration.
- [ ] All tests from §5 added/updated and passing; `hk check --all` clean.

## 8. Out of scope (explicitly do NOT do)

- Fixing the underlying performance issues (rate-limiting, provider timeouts, cache
  invalidation, construction refetch inside the loop) — the user only wants the control.
- Changing `baustellen_beruecksicherten` or any other provider toggle.
- New external dependencies or API keys.
- Persisting the per-level result into the response (no new response fields).
- Refactoring `LoadBalancedWeatherProvider`, OpenMeteo batching, or the
  `refetch_weather` protocol for `high`.
