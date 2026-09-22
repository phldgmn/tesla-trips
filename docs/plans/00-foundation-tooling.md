# Plan Phase 0 — Repo Foundation & Tooling

## 1. Purpose & Scope

Phase 0 lays the technical foundation for the entire project. This is not about functionality, but about establishing a structured, testable, lint- and type-safe codebase that guides and compels AI agents and developers to deliver high-quality code.

**What this module delivers:**

- Exact directory tree for all 12 modules (`routing`, `elevation`, `weather`, `wind`, `construction`, `energy`, `battery`, `charging_infrastructure`, `optimization`, `simulation`, `visualization`, `trip_input`)
- Python project definition via `pyproject.toml` with all dependencies and dev groups
- Git hook configuration via `hk.pkl` for pre-commit (linting/auto-fix) and pre-push (unit tests)
- CI pipeline (`ci.yml`) with lint, test, and frontend jobs including a specific GraphHopper Docker image
- AGENTS.md as a guidelines file for all AI coding agents (expanded from `05-agent-guidelines.md`)
- Preparation for mkdocs/mkdocstrings documentation

**Out of scope:**

- No implementation of business logic (routing, energy, optimization, etc.)
- No data source setup (starting the GraphHopper container is a CI task, not part of this plan)
- No creation of test or source code files beyond the structural scaffold

**NOT in scope (future expansion):**

- Active GraphHopper container in dev-mode (CI-internal only)
- Real-time weather crawler
- Live construction-site feed
- Database migrations

---

## 2. Dependencies & Phase Assignment

**Phase:** Phase 0 (Foundation & Tooling)

**Types consumed from other modules:** None — Phase 0 precedes all other modules.

**Future dependencies (for integration in Phase 1+):**

- All modules (`tripplanner.routing`, `tripplanner.elevation`, …) will use `uv` as the package manager
- External dependencies (GraphHopper, Open-Meteo, DATEX II) are accessed via `httpx` (defined in `pyproject.toml`)
- Test framework: `pytest`, `pytest-cov` (coverage gate at 85%)

---

## 3. Directory Tree (exactly as specified + Module Skeleton)

```
tesla-tripplanner/
├── hk.pkl                     # Git hook and lint configuration (see section 4)
├── pyproject.toml             # uv/Python project definition (see section 4)
├── uv.lock
├── AGENTS.md                  # see 05-agent-guidelines.md (see section 5)
├── docs/
│   ├── 01-project-specifications.md
│   ├── 02-architecture.md
│   └── 03-module-specifications.md
├── src/
│   └── tripplanner/
│       ├── __init__.py        # Package init (empty but required)
│       ├── geo/                # Phase 0 — shared geographic primitives (not a business module)
│       │   ├── __init__.py    # exports Coordinate, bearing_deg(), haversine_distance_m()
│       │   └── geo.py         # Coordinate = tuple[float, float] (lat, lon); bearing/distance formulas
│       ├── routing/           # Phase 1
│       │   ├── __init__.py    # re-exports public API
│       │   ├── models.py      # Pydantic models: Route, RouteSegment
│       │   ├── routing.py     # Core logic (graphhopper_client, route calculation)
│       │   ├── providers.py   # GraphHopperProvider + FakeProvider for tests
│       │   └── client.py      # HTTP client for GraphHopper API
│       ├── elevation/         # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: ElevationPoint, SegmentGradient
│       │   ├── elevation.py   # DEM lookup, gradient calculation
│       │   ├── providers.py   # ElevationProvider (rasterio-based)
│       │   └── client.py      # (optional, if file-system access is encapsulated)
│       ├── weather/           # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: WeatherQuery, WeatherSample
│       │   ├── weather.py     # Weather query, iteration logic
│       │   ├── providers.py   # WeatherProvider (Open-Meteo) + FakeProvider
│       │   └── client.py      # HTTP client for Open-Meteo API
│       ├── wind/              # Phase 2 (depends on weather.models)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: WindComponents
│       │   └── wind.py        # Calculate wind components (bearing, projection)
│       ├── construction/      # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: ConstructionZone, RestrictionType enum
│       │   ├── construction.py # DATEX II parser, construction site extraction
│       │   ├── providers.py   # ConstructionProvider + FakeProvider
│       │   └── client.py      # (optional, if DATEX II feed is over HTTP)
│       ├── energy/            # Phase 3 (depends on routing, elevation, weather, wind, construction)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: VehicleEnergyParameters, SegmentEnergyResult
│       │   ├── energy.py      # Physics model (rolling resistance, aerodynamic drag, etc.)
│       │   ├── rolling_resistance.py  # (optional: modularized)
│       │   ├── aerodynamics.py        # (optional: modularized)
│       │   └── providers.py   # (optional, if weather/charging curves are encapsulated)
│       ├── battery/           # Phase 4 (depends on energy models + charging_infrastructure)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: SoCState, ChargingCurvePoint, ChargingCurve
│       │   ├── battery.py     # SoC history, charging curve, discharge
│       │   └── providers.py   # (optional)
│       ├── charging_infrastructure/  # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: ChargingStation, ChargingStationProvider
│       │   ├── providers.py   # ChargingStationProvider (Tesla Supercharger, local) + FakeProvider
│       │   └── client.py      # (optional, if JSON/SQLite data via API)
│       ├── optimization/      # Phase 5 (depends on routing, energy, battery, charging_infrastructure)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: ChargingPlan, ChargingStop, OptimizationConstraints
│       │   ├── optimization.py # Optimization interface, state-space search
│       │   └── optimizer_ortools.py  # OR-Tools implementation
│       ├── simulation/        # Phase 6 (depends on optimization output)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic models: SimulationFrame, TripSimulationResult
│       │   └── simulation.py  # Simulation from ChargingPlan + Route
│       ├── visualization/     # Phase 7 (depends on simulation output contract)
│       │   ├── __init__.py
│       │   └── models.py      # (optional, if JSON schema is generated)
│       └── trip_input/        # Phase 7 (CLI/API, depends on simulation)
│           ├── __init__.py
│           ├── models.py      # Pydantic models: TripRequest, Waypoint, VehicleProfile
│           ├── api.py         # FastAPI endpoints
│           └── cli.py         # CLI interface (typer/argparse)
├── tests/
│   ├── routing/
│   │   ├── test_routing.py
│   │   ├── test_providers.py
│   │   └── conftest.py
│   │   └── fixtures/routing/
│   │       └── graphhopper_response_example.json
│   ├── elevation/
│   │   ├── test_elevation.py
│   │   ├── test_providers.py
│   │   └── conftest.py
│   │   └── fixtures/elevation/
│   │       └── synthetic_dem_tile.tif  # small test tile
│   ├── weather/
│   │   ├── test_weather.py
│   │   ├── test_providers.py
│   │   └── conftest.py
│   │   └── fixtures/weather/
│   │       └── open_meteo_response_example.json
│   ├── wind/
│   │   ├── test_wind.py
│   │   └── conftest.py
│   ├── construction/
│   │   ├── test_construction.py
│   │   ├── test_providers.py
│   │   └── conftest.py
│   │   └── fixtures/construction/
│   │       └── datexii_example.xml
│   ├── energy/
│   │   ├── test_energy.py
│   │   └── conftest.py
│   ├── battery/
│   │   ├── test_battery.py
│   │   └── conftest.py
│   │   └── fixtures/battery/
│   │       └── charging_curve_data.csv
│   ├── charging_infrastructure/
│   │   ├── test_charging_infrastructure.py
│   │   ├── test_providers.py
│   │   └── conftest.py
│   │   └── fixtures/charging_infrastructure/
│   │       └── tesla_supercharger_snapshot.json
│   ├── optimization/
│   │   ├── test_optimization.py
│   │   └── conftest.py
│   │   └── fixtures/optimization/
│   │       └── small_scenarios.json  # manually verifiable scenarios
│   ├── simulation/
│   │   ├── test_simulation.py
│   │   └── conftest.py
│   ├── visualization/
│   │   ├── test_visualization.py
│   │   └── conftest.py
│   ├── trip_input/
│   │   ├── test_trip_input.py
│   │   └── conftest.py
│   └── fixtures/  # Recorded API responses, example DEM tiles, DATEX-II examples
│       ├── common_fixtures/
│       │   └── test_coords.json  # Coordinate samples (start/target/waypoints)
│       └── integration_fixtures/
│           └── graphhopper_demo_route.json
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── main.ts            # Entry point, MapLibre GL JS init
│   │   ├── components/
│   │   │   ├── Map.tsx
│   │   │   ├── RouteOverlay.tsx
│   │   │   ├── SoCChart.tsx
│   │   │   └── ChargingStopsMarker.tsx
│   │   ├── types.ts           # TypeScript types (generated from Pydantic models)
│   │   └── utils.ts
│   └── tests/
│       ├── unit/
│       └── e2e/
├── .github/workflows/ci.yml   # CI pipeline (see section 4)
└── mkdocs.yml                 # MkDocs configuration (see section 7)
```

**Module Skeleton Convention (binding per module):**

```
src/tripplanner/<modul>/
├── __init__.py        # re-exports public API (e.g. from .models import Route)
├── models.py           # Pydantic models — the only cross-module interface
├── <modul>.py           # Core logic / public functions
├── providers.py          # ONLY if external data source: protocol interface + concrete implementation + fake
└── client.py               # HTTP/IO client, used by the provider (if present)
```

**Rule:** No module imports internal implementation details of another module — exclusively `tripplanner.<other_module>.models`. **Exception:** `tripplanner.geo` is not a business module, but a minimal, dependency-free geo primitive (`Coordinate` type alias, `bearing_deg()`, `haversine_distance_m()`). It may be imported by any module, as it contains no business logic and no mutable state — analogous to an external library. All `Coordinate` tuples in the project are `(lat, lon)`; see `docs/plans/01-routing.md`, section 3, "coordinate convention".

---

## 4. Configuration Files Fully Specified

### 4.1 `pyproject.toml`

```toml
[project]
name = "tripplanner"
version = "0.1.0"
description = "Highly personalized trip planner for Tesla Model 3"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0.0",
    "httpx>=0.26.0",
    "rasterio>=1.3.10",
    "ortools>=9.10.0",
    "networkx>=3.3.0",
    "fastapi>=0.115.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.6.0",
    "mypy>=1.11.0",
    "hk>=1.53.0",
    "mkdocs>=1.6.0",
    "mkdocstrings[python]>=0.26.0",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "PL", "RUF"]
extend-select = ["D"]
ignore = []

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false

[tool.mypy]
strict = true
disallow_untyped_defs = true
warn_return_any = true
warn_unused_configs = true
disallow_incomplete_defs = true
disallow_untyped_calls = true
disallow_untyped_decorators = false
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_no_return = true
check_untyped_defs = true
follow_imports = "silent"
ignore_missing_imports = true
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: integration tests (slow, against real services)",
]
addopts = "-v --tb=short --cov=src/tripplanner --cov-report=term-missing --cov-fail-under=85"

[tool.coverage.run]
source = ["src/tripplanner"]
branch = true

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
omit = [
    "*/__pycache__/*",
    "*/tests/*",
]
```

### 4.2 `hk.pkl`

```pkl
amends "package://github.com/jdx/hk/releases/download/v1.53.0/hk@1.53.0#/Config.pkl"
import "package://github.com/jdx/hk/releases/download/v1.53.0/hk@1.53.0#/Builtins.pkl"

local python_files = List("*.py")
local frontend_ts_files = List("frontend/**/*.ts", "frontend/**/*.tsx")
local frontend_json_files = List("frontend/**/*.json")

local linters = new Mapping<String, Step> {
    ["ruff-check"] {
        glob = python_files
        check = "uv run ruff check {{files}}"
        fix = "uv run ruff check --fix {{files}}"
    }
    ["ruff-format"] {
        glob = python_files
        check = "uv run ruff format --check {{files}}"
        fix = "uv run ruff format {{files}}"
    }
    ["mypy"] {
        glob = python_files
        check = "uv run mypy src"
        // no fix — type errors are not automatically fixed,
        // but intentionally block the commit
    }
    ["prettier"] = (Builtins.prettier) {
        glob = List("frontend/**/*.ts", "frontend/**/*.tsx", "frontend/**/*.json")
    }
    ["eslint"] {
        glob = frontend_ts_files
        check = "npm --prefix frontend run lint"
        fix = "npm --prefix frontend run lint:fix"
    }
}

local tests = new Mapping<String, Step> {
    ["pytest-unit"] {
        check = "uv run pytest -m 'not integration'"
    }
    ["pytest-cov"] {
        check = "uv run pytest -m 'not integration' --cov=src/tripplanner --cov-report=term-missing --cov-fail-under=85"
    }
}

hooks {
    ["pre-commit"] {
        fix = true       # Auto-Fix runs directly at commit
        stash = "git"    # unstaged changes are backed up during this
        steps = linters
    }
    ["pre-push"] {
        steps {
            ["pytest-unit"] {
                check = "uv run pytest -m 'not integration'"
            }
            ["pytest-cov"] {
                check = "uv run pytest -m 'not integration' --cov=src/tripplanner --cov-report=term-missing --cov-fail-under=85"
            }
        }
    }
}
```

### 4.3 `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: uv sync

      - name: Run linters (ruff check, ruff format, mypy)
        run: uv run hk check --all

      - name: Run unit tests with coverage
        run: uv run pytest -m "not integration" --cov=src/tripplanner --cov-report=term-missing --cov-fail-under=85

  integration-tests:
    runs-on: ubuntu-latest
    services:
      graphhopper:
        image: israelhikingmap/graphhopper:11.0  # official community image, current stable tag
        ports:
          - 8989:8989
        env:
          JETTY_BASE: /var/lib/graphhopper
          GH_LOADED_GRAPH_CACHE_DIR: /var/lib/graphhopper/cache
        options: >-
          --health-cmd "curl -f http://localhost:8989/route?point=52.52,13.40&point=52.53,13.41 || exit 1"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
          --health-start-period 30s
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: uv sync

      - name: Run integration tests
        run: uv run pytest -m integration -v
        env:
          GRAPHHOPPER_URL: http://localhost:8989

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        working-directory: frontend
        run: npm ci

      - name: Run lint
        working-directory: frontend
        run: npm run lint

      - name: Run typecheck
        working-directory: frontend
        run: npm run typecheck

      - name: Run tests
        working-directory: frontend
        run: npm test
```

---

## 5. `AGENTS.md` — Full Specification

```markdown
# AGENTS.md — Guidelines for AI Coding Agents

This file is located in the repo root and is read by AI coding agents before starting any task.

## Core Principle

Code is only considered complete when **all** of the following criteria are met — not as a checklist to tick off after writing, but as a definition of done:

1. `uv run hk check --all` passes without errors (linting, formatting, type-checking).
2. For every new feature, tests exist that fail before the change and pass after.
3. `uv run pytest -m "not integration"` passes entirely.
4. The coverage threshold from `04-repo-tooling-setup.md` is not fallen below (85% for `src/tripplanner/`).
5. No new dependencies between modules except through those defined in `models.py` (see `03-module-specifications.md`).

## Forbidden Shortcuts

Agents must **not**:

- Create commits with `--no-verify` or similar mechanisms to bypass hooks.
- "Fix" lint or type errors through `# noqa`, `# type: ignore`, or by lowering `mypy`/`ruff` rules in `pyproject.toml` without a substantive justification documented in the commit/PR. Rule exceptions at the line level with explanation comments are allowed, not as global config changes without consultation.
- Delete or disable tests (`skip`) to make a red CI green.
- Access external data sources (GraphHopper, Open-Meteo, DATEX II, Tesla charging station data) live in unit tests — fixtures exist for this (see `03-module-specifications.md`).

## Approach per task

1. Identify the responsible module from `03-module-specifications.md`; do not start the task cross-module if it can be scoped to a single module.
2. Read existing interfaces (`models.py` of the module) before introducing new data structures — avoid duplicating data models.
3. Write tests first or at least define before implementation which test cases will verify the change.
4. Implementation.
5. Run `uv run hk check --all` and relevant tests locally before proposing a commit.
6. Commit message describes **what** and **why**, not just **what** (e.g. not just "add wind module", but briefly name the calculation assumption).

## Module boundaries

- No module accesses internal implementation details of another module — only its `models.py` data structures and public functions/classes.
- External data sources (HTTP clients, filesystem access) are encapsulated behind a provider interface (see e.g. `WeatherProvider`, `ChargingStationProvider` in `03-module-specifications.md`), so they are replaceable in tests and the data source remains interchangeable when needed.
- New external dependencies (libraries, APIs) are not introduced without reference to one of the architectural decisions defined in `01-project-specifications.md`.

## Type annotations and docstrings

- Every public function/method has full type annotations (enforced by `mypy --strict`) and a docstring in the project-wide uniform style (Google-Style, see `04-repo-tooling-setup.md`).
- Pydantic models are the only permissible form for data structures crossing module boundaries.

## When in doubt

When a requirement is ambiguous (e.g. specific threshold for ETA re-iteration, specific Tesla charging station data source — see `06-open-points-contradictions.md`), the agent makes a justified, documented assumption (comment in code + mention in PR text) rather than leaving the task unaddressed — unless the ambiguity affects one of the open questions in `06-open-points-contradictions.md`; these are clarified with the project lead before starting the respective module implementation.
```

---

## 6. Task Checklist

| Nr | Task | Affected Files | Description | Acceptance criterion |
| ---- | ------ | ------------------- | -------------- | -------------------- |
| 1 | Create repo directory tree | `src/tripplanner/*/`, `tests/*/` | Create all 12 module directories plus `tripplanner/geo/` with `__init__.py`, `models.py`, `<modul>.py`, `providers.py`, `client.py` (where relevant); `docs/plans/` and `frontend/` with base structure | `find src/tripplanner -type f -name "*.py" \| wc -l` yields at least 48, `find tests -type d \| wc -l` yields at least 13 |
| 2 | Create `pyproject.toml` | `pyproject.toml` | Create complete TOML file per section 4.1 | `uv sync` runs without errors, `uv pip list \| grep pydantic` shows version ≥2 |
| 3 | Create `hk.pkl` | `hk.pkl` | Complete configuration per section 4.2 | `hk check --all` runs without errors on an empty repo |
| 4 | Create `.github/workflows/ci.yml` | `.github/workflows/ci.yml` | Complete workflow per section 4.3 | CI check simulates (`act -W .github/workflows/ci.yml --container-architecture="linux/amd64"` or GitHub UI) shows no syntax errors |
| 5 | Create `AGENTS.md` | `AGENTS.md` | Complete content per section 5 | File exists in repo root, contains all 5 sections |
| 6 | Configure `mkdocs.yml` and documentation | `mkdocs.yml`, `docs/index.md` | Configure MkDocs with mkdocstrings (Python module documentation) | `mkdocs serve` starts local server and shows API documentation |
| 7 | Generate `uv.lock` | `uv.lock` | Run `uv sync` and commit lock file | `git status` shows only new/modified files, no untracked dependencies |
| 8 | Verify test setup (unit tests without integration) | `tests/conftest.py`, `tests/.../test_*.py` | Create at least 2 dummy tests per module (e.g. `tests/routing/test_routing.py`) | `uv run pytest -m "not integration"` passes green (red tests expected at start) |
| 9 | Test Ruff/linter configuration | `pyproject.toml`, `ruff check` | `uv run ruff check src` and `uv run ruff format --check src` on empty code | No errors, no warnings |
| 10 | Test MyPy setup | `mypy` | `uv run mypy src` on empty code | No errors (red tests expected, as no type annotations yet) |
| 11 | Frontend setup (preparation) | `frontend/package.json` | Create `package.json` with TypeScript, MapLibre GL JS, React/TypeScript setup | `npm ci` and `npm run typecheck` run without errors |
| 12 | Test git hooks (pre-commit) | `hk pre-commit` | Create dummy Python file with lint error and commit | Hook aborts commit, auto-fix runs (if `--fix` possible) |
| 13 | Implement `tripplanner.geo` primitive | `src/tripplanner/geo/geo.py`, `tests/geo/test_geo.py` | `Coordinate = tuple[float, float]`, `bearing_deg(a, b) -> float` (forward azimuth), `haversine_distance_m(a, b) -> float`; no dependency to other `tripplanner` modules | Unit tests with known reference points (e.g. Berlin→Hamburg bearing ≈ 312°) green |

---

## 7. Risks & Open Technical Questions

- **Risk:** GraphHopper Docker image (`israelhikingmap/graphhopper:11.0`) is a community image, not an official release from GraphHopper GmbH. **Mitigation:** Image version pinning in CI (`:11.0` instead of `:latest`), monitoring for image updates.
- **Risk:** `hk` is a relatively new hook runner; in case team members use (`--no-verify`) or CI environments (missing `hk` installation) cause problems. **Mitigation:** CI explicitly repeats all checks (see section 4.3), `hk` is included in the `dev` group.
- **Open question:** Should `frontend/` actually use TypeScript + MapLibre GL JS (as recommended in `02-architecture.md`) or use Leaflet (called as an alternative in `04-repo-tooling-setup.md`)? **Decision:** MapLibre GL JS is taken as standard (better vector styling, already prevalent in the team stack, see tech stack in `02-architecture.md`).
- **Open question:** Should the `hk.pkl` additionally support `black` as an alternative to `ruff format`? **Decision:** No — `ruff format` is faster, consistent with `ruff check`, and already configured as the only formatter in `pyproject.toml`.
- **Open question:** Should the CI pipeline have a separate job for the `pre-commit` hook check (local)? **Decision:** No — CI only tests the synthetic checks (ruff, mypy, pytest), as local hooks are not reproducible (bypassed via `--no-verify`).

---

## 8. mkdocs/mkdocstrings Setup Recommendation

**Purpose:** Generation of API documentation from docstrings in Google style (as defined in `pyproject.toml`/`ruff.lint.pydocstyle.convention = "google"`).

**Recommended configuration (`mkdocs.yml`):**

```yaml
site_name: Tesla-Tripplanner API
theme:
  name: "material"
  palette:
    - media: "(prefers-color-scheme: light)"
      primary: "indigo"
    - media: "(prefers-color-scheme: dark)"
      scheme: "default"
      primary: "indigo"

plugins:
  - search
  - mkdocstrings:
      handlers:
        python:
          options:
            docstring_style: "google"
            show_root_heading: true
            show_source: true
            separate_signature: true
            merge_init_into_class: true
            heading_level: 2

nav:
  - Home: index.md
  - API:
      - tripplanner: tripplanner/
  - Modules:
      - routing: tripplanner.routing/
      - elevation: tripplanner.elevation/
      - weather: tripplanner.weather/
      - wind: tripplanner.wind/
      - construction: tripplanner.construction/
      - energy: tripplanner.energy/
      - battery: tripplanner.battery/
      - charging_infrastructure: tripplanner.charging_infrastructure/
      - optimization: tripplanner.optimization/
      - simulation: tripplanner.simulation/
      - visualization: tripplanner.visualization/
      - trip_input: tripplanner.trip_input/

watch:
  - src/tripplanner
```

**Rule for developers:** All public functions/methods must have Google-style docstrings so that `mkdocs serve` generates complete documentation. Example:

```python
def calculate_energy(
    segment: RouteSegment, weather: WeatherSample, vehicle: VehicleEnergyParameters
) -> SegmentEnergyResult:
    """Calculates energy consumption for a segment considering weather and vehicle.

    Args:
        segment: The RouteSegment to calculate with geometry and gradient.
        weather: The current weather at this segment (temperature, wind, etc.).
        vehicle: Vehicle parameters (mass, drag coefficient, frontal area, etc.).

    Returns:
        SegmentEnergyResult with energy_consumption_kwh and recuperation_kwh.

    Raises:
        ValueError: If segment.laenge_m <= 0 or temperature < -50°C.
    """
    # Implementation
```

**Build and deploy commands:**

- `mkdocs build` — generates static HTML pages in `site/`
- `mkdocs serve` — local live preview
- Deploy: `mkdocs gh-deploy` — directly to GitHub Pages (if desired)

---

**Handover from previous session (2026-08-02):**

- **What was planned:** Phase 0 — Repo Foundation & Tooling (no code, only configuration and structure).
- **What's next:** Implement the 12 tasks from the checklist, starting with directory tree (task 1) and `pyproject.toml` (task 2).
- **Key files:** `docs/plans/00-foundation-tooling.md` (this plan), `docs/04-repo-tooling-setup.md`, `docs/05-agent-guidelines.md`, `docs/01-project-specifications.md`, `docs/02-architecture.md`, `docs/03-module-specifications.md`.
