# Refactoring Report: Robustness, Stability, Maintainability, Security

Date: 2026-09-22 · Scope: `src/tripplanner/` (27.6k LOC Python), `frontend/src/`, `docker-compose.yml`, `run.sh`, CI.

Items are grouped by theme and scored with **Priority = (Impact + Risk) × (6 − Effort)**, each scored 1–5.
Before editing any symbol, run GitNexus impact analysis as `CLAUDE.md` requires
(`node .gitnexus/run.cjs impact "<symbol>" --direction upstream --repo .`).

| # | Item | Category | I | R | E | Priority |
|---|------|----------|---|---|---|----------|
| 1 | Popup HTML injection (XSS) from third-party data | Security | 3 | 5 | 1 | **40** |
| 2 | Internal error details returned in HTTP responses | Security | 2 | 4 | 1 | **30** |
| 3 | Unbounded / unvalidated request input on `/trips` | Security / Robustness | 3 | 4 | 2 | **28** |
| 4 | Services exposed on all network interfaces | Security | 2 | 4 | 1 | **30** |
| 5 | CPU-bound optimizer and sync SQLite block the event loop | Stability | 4 | 4 | 2 | **32** |
| 6 | Module-global charging provider duplicates the DI lifecycle | Maintainability / Stability | 4 | 3 | 3 | **21** |
| 7 | Unauthenticated endpoints that start scraping | Security / Stability | 3 | 4 | 2 | **28** |
| 8 | Unreachable shim modules (`providers.py` next to `providers/`) | Maintainability | 3 | 2 | 1 | **25** |
| 9 | 32 broad `except Exception` handlers | Robustness | 3 | 3 | 3 | **18** |
| 10 | `assert` used for runtime checks (22×) | Robustness | 2 | 3 | 1 | **25** |
| 11 | Shared SQLite connection without a thread-safety contract | Stability | 3 | 3 | 2 | **24** |
| 12 | No retries or circuit breaker for GraphHopper | Robustness | 3 | 3 | 2 | **24** |
| 13 | Dependency & supply-chain hygiene | Security | 2 | 3 | 1 | **25** |
| 14 | Committed debug log and leftover artifacts | Security / Hygiene | 1 | 3 | 1 | **20** |
| 15 | O(n) station lookup per request | Performance | 2 | 2 | 1 | **20** |
| 16 | Oversized frontend components | Maintainability | 4 | 2 | 4 | **12** |
| 17 | Mixed German/English identifiers and docstrings | Maintainability | 3 | 1 | 4 | **8** |

---

## 1. Popup HTML injection (XSS) — Priority 40

**Problem.** Map popups are built as HTML strings and passed to MapLibre's `Popup.setHTML()`, which does not sanitize its input. Values from external sources are interpolated without escaping:

- `frontend/src/components/Map/popups.ts:79`: `stop.name` (a station name from Tesla or supercharge.info)
- `frontend/src/components/Map/popups.ts:208`: `event.detourNotice` (free text from DATEX II, Autobahn, or Trafikverket construction feeds)
- `popups.ts:229`: `event.closureType` when no label is found (`?? event.closureType`)

Any third-party feed that returns `<img src=x onerror=…>` can run script in the app's origin.

**How to fix.**
1. Add `frontend/src/utils/html-escape.ts`:
   ```ts
   const MAP: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
   export const escapeHtml = (s: unknown): string => String(s ?? "").replace(/[&<>"']/g, (c) => MAP[c]);
   ```
2. In `popups.ts`, wrap every value that isn't a constant with `escapeHtml(...)`: `stop.name`, `title`, `heading`, `value` in the row mapper, and `detourNotice`. Escape in the shared row renderer (`<td>${escapeHtml(value)}</td>`) so later callers stay covered.
3. Better long term: build popups as DOM nodes and use `Popup.setDOMContent()`, since `popups.ts:100` already does this for one case. Set text through `textContent` only.
4. Add a Vitest case to `tests/unit/construction-zone-popup.test.ts` that feeds `detourNotice: "<img src=x onerror=alert(1)>"` and asserts the output contains `&lt;img`.
5. Optional defense in depth: add a CSP `<meta>` to `frontend/index.html` (`script-src 'self'`).

## 2. Internal error details leak to clients — Priority 30

**Problem.** `src/tripplanner/trip_input/api.py:156-162, 233-242, 437-456` put `str(e)` directly in `HTTPException.detail`. That can expose file paths, SQL, upstream URLs, and parser internals. The catch-all at `:452` returns the message of any exception.

**How to fix.**
1. Keep the logging (`logger.exception(...)`), but return a generic message plus a correlation id:
   ```python
   error_id = uuid.uuid4().hex[:12]
   logger.exception("Simulation failed [%s]", error_id)
   raise HTTPException(500, detail=f"Simulation fehlgeschlagen (Fehler-ID {error_id})") from e
   ```
2. Only `ValueError` from the domain layer ("route not feasible") carries a user-facing message. Create a dedicated `class TripInfeasibleError(ValueError)` in `trip_input/models.py`, raise it where the message is intended for users, and pass through only that type's text. Treat other `ValueError`s as 500 errors.
3. Delete `extra={"traceback": traceback.format_exc()}` (`api.py:454`). `logger.exception` already records the traceback.
4. Update the `tests/trip_input/test_api.py` assertions that match on the old `detail` text.

## 3. Unbounded / unvalidated `/trips` input — Priority 28

**Problem.** `trip_input/schemas/request.py`:
- Coordinates (`:15`, `:58-59`, bbox fields) are `tuple[float, float]` with no range check. NaN and ±inf are accepted.
- `zwischenstopps` (`:60`) has no length limit. A 10k-waypoint request fans out into routing, weather, and elevation calls, and the optimizer graph grows accordingly, which makes a cheap DoS.
- Time fields (`abfahrtszeit`, `geplante_abfahrt`, `abfahrt`, `ankunft`) are `str` and get parsed later in the pipeline, so format errors surface deep inside it.
- `praeferenzen: dict[str, object]` (`:98`) is an untyped bag.

**How to fix.**
1. Define reusable annotated types:
   ```python
   Lat = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
   Lon = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
   LatLon = tuple[Lat, Lon]
   ```
   Replace every `tuple[float, float]` in the request schemas with `LatLon`.
2. `zwischenstopps: list[WaypointAPI] = Field(default_factory=list, max_length=25)`. Also bound `ladedauer_vorgaben`, `faehr_*` lists, and `ladeleistung_kw` (`le=350`).
3. Change the time fields to `AwareDatetime` and remove the manual parsing downstream (search for `fromisoformat` in `trip_input/`).
4. Replace `praeferenzen` with a typed `PreferencesAPI(BaseModel, extra="forbid")`, or remove it if nothing reads it (`grep -rn praeferenzen src/`).
5. Add `model_config = ConfigDict(extra="forbid")` to all request models so misspelled fields fail loudly.
6. The frontend request builder (`frontend/src/types/trip-request-builder.ts`) must send ISO strings with an offset. Check it with `tests/unit/trip-request.test.ts`.

## 4. Services bound to all interfaces — Priority 30

**Problem.** `docker-compose.yml` publishes `"8989:8989"` and `"8081:8080"`, which are reachable from the LAN. The tile server also runs `--cors=*`. The `pmtiles` image uses the `:latest` tag.

**How to fix.**
1. Bind to loopback: `"127.0.0.1:8989:8989"` and `"127.0.0.1:8081:8080"`.
2. Change `--cors=*` to `--cors=http://localhost:3000`.
3. Pin `protomaps/go-pmtiles` to an explicit version, and ideally to a digest (`@sha256:…`).
4. Add a compose `healthcheck` for GraphHopper. `run.sh`'s `graphhopper_health` reads `.State.Health.Status`, which returns nothing when no healthcheck is defined:
   ```yaml
   healthcheck:
     test: ["CMD", "wget", "-qO-", "http://localhost:8989/health"]
     interval: 15s
     retries: 240
   ```
5. Make sure `run.sh` starts uvicorn with `--host 127.0.0.1` (the default; add it explicitly).

## 5. Event-loop blocking — Priority 32

**Problem.**
- `trip_input/pipeline.py:377`: `optimizer.optimize(...)` (NetworkX graph build and search, CPU-bound, called up to N times inside the convergence loop) runs synchronously inside `async def _step_8_optimize_charging_plan`. While it runs, every other request, including `/health`, stalls.
- `weather/providers/composite.py:176, 261`: `TTLCache.get`/`set` open a new SQLite connection **per sample** synchronously inside async code. Each call runs connect, a WAL pragma, a query, and close.

**How to fix.**
1. Offload the optimizer: `return await asyncio.to_thread(optimizer.optimize, ...)`. Check first that the optimizer holds no shared mutable state (run `context` on `NetworkXOptimizer`). If profiling shows GIL contention, move it to a module-level `ProcessPoolExecutor` created in `_lifespan` and shut down there. Inputs must be picklable Pydantic models.
2. For `TTLCache`:
   - Add bulk methods `get_many(keys) -> dict` and `set_many(items)` that use one connection and `executemany`.
   - In `composite.py`, gather all keys for a batch and call `await asyncio.to_thread(self._persistent_cache.get_many, keys)` once per batch.
   - Run `PRAGMA journal_mode=WAL` once in `_init_db` only (it persists), and use `sqlite3.connect(..., timeout=5)`.
3. Add a regression test: with a fake provider that sleeps, start `/trips` and assert that `/health` responds in under 100 ms concurrently (`httpx.AsyncClient` + `asyncio.gather`).

## 6. Global charging provider alongside DI — Priority 21

**Problem.** `/trips` gets its provider from `app.state.providers` (built in `_lifespan`). The `/superchargers*` endpoints call `get_all_charging_stations()` and similar functions, which use a separate lazily created module global `_DEFAULT_PROVIDER` (`charging_infrastructure/charging_infrastructure.py:24, 41, 98-100`). As a result:
- There are two SQLite connections and two browser/HTTP clients, and the global one is never closed on shutdown.
- `assert isinstance(...)` (`:100`) is the only type guard.
- Tests have to monkeypatch the global instead of using `dependency_overrides`.

**How to fix.**
1. Rewrite the supercharger endpoints in `api.py` to take `charging: ChargingStationProvider = Depends(get_charging_provider)`.
2. Move `refresh_supercharger_station` and the pricing-refresh functions onto `TeslaChargingStationProvider` as methods, or make them take the provider as their first argument.
3. Keep the `init_charging_infrastructure` / `get_all_charging_stations` functions only for the CLI (`cli_charger.py`). Have them build a provider inside a context manager (`with provider_session() as p:`) so the CLI also closes resources.
4. Delete `_DEFAULT_PROVIDER` and the `global` statement once nothing references them (confirm with GitNexus `impact` plus a text search, since dynamic access returns `UNKNOWN`).

## 7. Unauthenticated scrape triggers — Priority 28

**Problem.** `POST /superchargers/{slug}/refresh` and `/refresh-pricing` start a headless Chromium (nodriver) or curl_cffi session against tesla.com for each call. The endpoints have no authentication, no rate limit, and no concurrency cap. Repeated calls can exhaust memory, start many browser processes, and get the host IP blocked by Akamai (see the HTTP 429 entries in `charger_debug.log`).

**How to fix.**
1. Put an `asyncio.Semaphore(1)` on the provider's refresh path so only one browser fetch runs at a time.
2. Add per-slug cooldowns: before scraping, check `get_pricing_recency`/`last_refresh`. If the data is fresher than N minutes, return the cached value with an `X-Cache: HIT` header.
3. Validate `slug` with `Path(pattern=r"^[a-z0-9-]{1,100}$")`.
4. If the API is ever reachable outside localhost, require a shared-secret header (`X-Admin-Token`, compared with `secrets.compare_digest` against an env var) for the refresh routes.

## 8. Unreachable shim modules — Priority 25

**Problem.** Each of these directories contains both `x.py` and an `x/` package:
- `construction/providers.py` + `construction/providers/`
- `charging_infrastructure/providers.py` + `charging_infrastructure/providers/`

Python always resolves the **package**, so both `providers.py` files are dead code. Their docstrings claim to preserve imports, which misleads readers, and editing them has no effect. (`charging_infrastructure/client.py` → `clients/` has a different name and does work.)

**How to fix.**
1. Confirm that the package `__init__.py` already re-exports every name listed in the shim's `__all__`, including `_parse_wkt_line`, `_parse_wkt_point`, and `_parse_trafikverket_situations`, which tests import.
2. `git rm src/tripplanner/construction/providers.py src/tripplanner/charging_infrastructure/providers.py`.
3. Run `uv run pytest -m "not integration"` and `uv run hk check --all`.
4. Add a check to CI (or `hk.pkl`) that fails when both `x.py` and `x/` exist:
   `find src -name '*.py' | while read f; do [ -d "${f%.py}" ] && echo "shadowed: $f" && exit 1; done`.

## 9. Broad exception handlers — Priority 18

**Problem.** There are 32 `except Exception` sites. Most are in the scraping clients (`clients/nodriver.py` ×10, `human_flow.py` ×3), `elevation/providers.py` ×4, `elevation/tile_cache.py` ×3, and `database.py` ×2. Many swallow the error silently, which hides bugs such as `AttributeError`/`TypeError` behind "no data" results.

**How to fix, per site:**
1. Narrow the clause to the real failure types: `httpx.HTTPError`, `sqlite3.Error`, `rasterio.errors.RasterioError`, `OSError`, `asyncio.TimeoutError`, `json.JSONDecodeError`, `pydantic.ValidationError`.
2. When a broad catch really is needed (for example, best-effort browser cleanup in `nodriver.py:274-316`), keep it but log `logger.debug("…", exc_info=True)` and add a `# noqa: BLE001 — reason` comment.
3. `database.py:286, 392` roll back a transaction. Replace them with `with self._conn:` (the sqlite3 context manager commits or rolls back automatically) and let the exception propagate.
4. `pipeline.py:617` catches `BaseException`. That's acceptable because it re-raises, but narrow it to `Exception` so `CancelledError` and `KeyboardInterrupt` don't log as misleading "FAILED" steps.
5. Enable ruff rule `BLE` (blind-except) in `pyproject.toml` to prevent regressions.

## 10. `assert` as runtime guard — Priority 25

**Problem.** There are 22 `assert` statements in `src/`, for example `database.py:74` and `charging_infrastructure.py:100`. Running with `python -O` strips them, and when they fail they raise an uninformative `AssertionError`.

**How to fix.** Replace each with an explicit check:
```python
if self._conn is None:
    raise RuntimeError("SuperchargerDatabase not initialized; call initialize() first")
```
If an assert exists only to narrow a type for mypy, use a private helper `def _require_conn(self) -> sqlite3.Connection`. Enable ruff `S101` for `src/` only; `tests/*` is already excluded from `D`, so add `S101` there too.

## 11. SQLite connection thread safety — Priority 24

**Problem.** `charging_infrastructure/database.py:73` opens one long-lived `sqlite3.Connection` with the default `check_same_thread=True`. The pricing and refresh code paths already use `asyncio.to_thread`. If any DB call ends up on a worker thread, it raises `ProgrammingError`. If `check_same_thread` is disabled without a lock, concurrent writes can corrupt transaction state.

**How to fix.**
1. Pick one model and document it in the class docstring:
   - **(Recommended)** Open a connection per operation, as `cache/store.py` already does. Add a `@contextmanager def _connect(self)` that sets pragmas and `row_factory`.
   - Or use `check_same_thread=False` plus a `threading.Lock` around every public method.
2. Wrap all writes in `with conn:` so transactions are atomic.
3. Add a unit test that calls `update_station` from `asyncio.to_thread` concurrently with `load_stations`.

## 12. GraphHopper resilience — Priority 24

**Problem.** `routing/client.py` makes single-shot calls with no retry, so a transient 503 or a connection reset while GraphHopper warms up fails the whole trip. Each iteration of the convergence loop re-routes, which multiplies the exposure.

**How to fix.**
1. Configure `httpx.AsyncHTTPTransport(retries=2)` in the client constructor. This retries connection errors only.
2. Add a small `_with_retry` helper for idempotent 502/503/504 responses (3 attempts, exponential backoff with jitter). Don't retry 4xx.
3. Set explicit `httpx.Timeout(connect=3, read=60)` values. Long routes with `ch.disable=True` can be slow.
4. Expose a readiness check: call `await client.info()` in `_lifespan`, log a clear warning if it fails, and add `/ready` next to `/health`.

## 13. Dependency & supply-chain hygiene — Priority 25

**Problems.** `pyproject.toml` has only lower bounds. `uv.lock` mitigates this locally, but CI runs `uv sync` without `--locked`. GitHub Actions use mutable tags (`@v4`, `@v2`). There is no vulnerability scanning. `nodriver` and `curl-cffi` are heavy runtime dependencies even for the API-only path.

**How to fix.**
1. In CI, use `uv sync --locked` so a stale lockfile fails the build.
2. Pin actions to commit SHAs (`actions/checkout@<sha> # v4`) and add Dependabot (`.github/dependabot.yml`) for `pip`, `npm`, `github-actions`, and `docker`.
3. Add jobs for `uvx pip-audit` (or `uv run pip-audit`) and `npm audit --omit=dev --audit-level=high`.
4. Move scraping dependencies into an optional extra: `[project.optional-dependencies] scraping = ["nodriver", "curl-cffi"]`. Keep the existing lazy import in `nodriver.py:97`, and raise a clear error when the extra is missing.
5. Add `permissions: contents: read` at the top of `ci.yml`.
6. The pytest `addopts` already pass `--cov...`, and CI repeats them. Remove the duplicate flags from `ci.yml`.

## 14. Committed debug log and artifacts — Priority 20

**Problem.** `charger_debug.log` is tracked. It holds raw WAF challenge tokens, timestamps, and request URLs, and grows with every scrape run. `.gitignore` has no `*.log` rule.

**How to fix.**
1. `git rm --cached charger_debug.log`, then add `*.log` to `.gitignore`.
2. Change `_debug_log` in `charging_infrastructure/clients/common.py` to write under `.run/` or `$TRIPPLANNER_CACHE_DIR/logs/` (both ignored) by default, and truncate response bodies to about 200 bytes.
3. Add `check-added-large-files` and a `*.log` block to `hk.pkl` pre-commit.

## 15. O(n) station lookup — Priority 20

**Problem.** `api.py:115-119` loads **all** stations and runs `_station_to_api()` on each one to compare slugs, on every detail request. `/superchargers?country=` also filters in Python after loading everything.

**How to fix.** Use the existing `SuperchargerDatabase.find_station_by_slug` (`database.py:423`) through the provider, exposed as a `get_station_by_slug(slug)` method, and push the country filter down into `load_stations(country_filter={country})` (`database.py:164`). Make sure an index exists on `tesla_location_id` and `country_code` (`CREATE INDEX IF NOT EXISTS` in `initialize`).

## 16. Oversized frontend components — Priority 12

**Problem.** `TripPlannerForm.tsx` is 2,107 lines and `MapVisualization.tsx` is 1,014. `docs/plans/2026-08-29-large-file-decomposition.md` already covers the backend side.

**How to fix.** Extend that plan with a frontend phase:
1. Move form state into a `useTripPlannerState` hook (reducer plus `persistent-state.ts`).
2. Split the form into sections (`RouteSection`, `VehicleSection`, `FerrySection`, `ChargingOverridesSection`) under `components/TripPlannerForm/sections/`, re-exported from `index.ts`.
3. In `MapVisualization.tsx`, extract one hook per layer (`useRouteLayer`, `useChargingStopMarkers`, `useConstructionZoneMarkers`, `useSuperchargerLayer`), each owning its `useEffect` and cleanup.
4. Make the tile URL (`basemap.ts:29`, hard-coded `http://localhost:8081`) configurable through `import.meta.env.VITE_TILES_URL` with the current value as fallback.

## 17. Mixed-language identifiers — Priority 8

**Problem.** German and English are mixed in the API schema (`zwischenstopps`, `ziel_soc_pct`), module names (`routing/faehren.py`, `utils/route-eintraege.ts`), function names (`_faehren_erfassen`), and docstrings. The recent translation commits (`487a2bb`, `9e7913f`) show this is already in progress.

**How to fix.** Continue incrementally, **one bounded context per PR**, using GitNexus `rename` rather than find-and-replace. For the public API schema, use Pydantic `Field(validation_alias=AliasChoices("waypoints", "zwischenstopps"))` so the frontend and backend can migrate independently. After that, update `trip-request-builder.ts` and remove the aliases.

---

## Phased remediation plan

| Phase | Items | Rationale |
|-------|-------|-----------|
| **1: Quick security wins (≈1 day)** | 1, 2, 4, 14, 8, 10 | Mostly one-line changes, each covered by an existing or small new test. |
| **2: Input and runtime hardening (≈2–3 days)** | 3, 5, 7, 13 | Changes request contracts and concurrency, so the frontend request builder and API tests need updating. |
| **3: Structural cleanup (≈1 week, alongside feature work)** | 6, 11, 12, 15, 9 | Touches provider lifecycles; do it behind the existing test suite, one commit per item. |
| **4: Ongoing** | 16, 17 | Large but low-risk; follow the decomposition plan's move-and-re-export approach. |

For every item: run impact analysis → make the change → `uv run pytest -m "not integration"` and `uv run hk check --all` (plus `npm run lint && npm run typecheck && npm test` for frontend items) → `detect-changes --scope all` → commit.
