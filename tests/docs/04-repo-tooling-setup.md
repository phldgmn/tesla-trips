# Repository Structure and Tooling Setup

The goal of this document is to ensure that AI coding agents produce clean, consistent code from the outset. This is not achieved by asking for it in prompts, but through **automated, mandatory gates**: linting/formatting with auto-fix, type checking, and tests that run before every commit and in CI and block commits/merges when violations are detected.

## Repository Structure

```text
tesla-tripplanner/
├── mise.toml                  # Tooling provider: pins Python/uv/node/hk/linter versions
├── hk.pkl                     # Git hook and lint configuration
├── pyproject.toml             # uv/Python project definition
├── uv.lock
├── AGENTS.md                  # see 05-agent-guidelines.md
├── docs/
│   ├── 01-project-specifications.md
│   ├── 02-architecture.md
│   └── 03-module-specifications.md
├── src/
│   └── tripplanner/
│       ├── routing/
│       ├── elevation/
│       ├── weather/
│       ├── wind/
│       ├── construction/
│       ├── energy/
│       ├── battery/
│       ├── charging_infrastructure/
│       ├── optimization/
│       ├── simulation/
│       └── api/                # CLI/FastAPI entry point
├── tests/
│   ├── routing/
│   ├── elevation/
│   ├── weather/
│   ├── ...                     # Mirrors src/ structure 1:1
│   └── fixtures/               # Recorded API responses, example DEM tiles, DATEX II examples
├── frontend/
│   ├── package.json
│   ├── src/
│   └── ...                     # TypeScript/MapLibre GL JS
└── .github/workflows/ci.yml
```

Each module from `03-module-specifications.md` corresponds exactly to a subpackage under `src/tripplanner/`, with a mirrored test directory. Cross-module imports are made exclusively through the respective module's `models.py` interfaces — no access to another module's internal implementation details.

## Tooling Provider (mise)

`mise.toml` in the repository root is the single source of truth for tool versions: the Python interpreter, `uv`, `node`, `hk`, and the external linters/formatters invoked by `hk.pkl` (`shellcheck`, `shfmt`, `yamllint`, `markdownlint`). This means a single command (`mise install`) installs and pins everything required locally and in CI (`jdx/mise-action`, see below) for consistent tool versions, instead of scattered `brew install …` instructions or multiple GitHub Actions setup steps.

```toml
# mise.toml (excerpt)
[tools]
uv = "0.11"
python = "3.12"
node = "20"
hk = "1.53.0"
shellcheck = "latest"
shfmt = "latest"
yamllint = "latest"
"npm:markdownlint-cli" = "latest"
```

* `mise install` installs/pins all tools listed in `mise.toml` at the specified versions.
* `uv` remains the Python package/virtual-environment manager (`uv sync`, `uv run …`, `uv.lock`) — mise only provides the binaries. `UV_PYTHON_DOWNLOADS = "never"` (see `[env]` in `mise.toml`) forces `uv` to use the Python interpreter pinned by mise instead of downloading its own.
* `mise run lint` / `mise run test` / `mise run install` are shorthand forms of the standard commands (`uv run hk check --all`, `uv run pytest -m 'not integration'`, `uv sync && npm --prefix frontend ci`) — optional, but they do not replace the commands mandated by `AGENTS.md`.
* `hk.pkl` also checks `mise.toml` itself using the `mise` builtin (`mise fmt --check`) as part of `hk check --all`.
* **Intentional exception:** Backend/frontend server processes continue to be started/stopped exclusively through `./run.sh` (see `AGENTS.md`) — `mise.toml` defines no tasks for this purpose, so as not to undermine that rule.

## Python Setup (uv)

```toml
# pyproject.toml (excerpt)
[project]
name = "tripplanner"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2",
    "httpx",
    "rasterio",
    "ortools",
    "networkx",
    "fastapi",
]

[dependency-groups]
dev = [
    "pytest",
    "pytest-cov",
    "ruff",
    "mypy",
]
```

* `uv sync` installs all dependencies, including the dev group.
* `uv run pytest`, `uv run ruff check`, and `uv run mypy src` are the standard commands — including when invoked from `hk.pkl`.

## Git Hooks with hk

`hk.pkl` in the repository root is mandatory for every commit (locally) and is additionally enforced in CI (see below):

```pkl
amends "package://github.com/jdx/hk/releases/download/v1.53.0/hk@1.53.0#/Config.pkl"
import "package://github.com/jdx/hk/releases/download/v1.53.0/hk@1.53.0#/Builtins.pkl"

local linters = new Mapping<String, Step> {
    ["ruff-check"] {
        glob = List("*.py")
        check = "uv run ruff check {{files}}"
        fix = "uv run ruff check --fix {{files}}"
    }
    ["ruff-format"] {
        glob = List("*.py")
        check = "uv run ruff format --check {{files}}"
        fix = "uv run ruff format {{files}}"
    }
    ["mypy"] {
        glob = List("*.py")
        check = "uv run mypy {{files}}"
        // no fix — type errors are not fixed automatically,
        // but deliberately block the commit
    }
    ["prettier"] = (Builtins.prettier) {
        glob = List("frontend/**/*.ts", "frontend/**/*.tsx", "frontend/**/*.json")
    }
    ["eslint"] {
        glob = List("frontend/**/*.ts", "frontend/**/*.tsx")
        check = "npm --prefix frontend run lint"
        fix = "npm --prefix frontend run lint:fix"
    }
    ["shellcheck"] = Builtins.shellcheck
    ["shfmt"] = (Builtins.shfmt) { /* -i 2, see hk.pkl: repository convention is 2-space indentation */ }
    ["yamllint"] = Builtins.yamllint
    ["markdown-lint"] = Builtins.markdown_lint
    ["mise"] = Builtins.mise
}

hooks {
    ["pre-commit"] {
        fix = true       // Auto-fix runs directly during the commit
        stash = "git"    // unstaged changes are stashed while this runs
        steps = linters
    }
    ["pre-push"] {
        steps {
            ["pytest"] {
                check = "uv run pytest -m 'not integration'"
            }
        }
    }
}
```

**Important for AI agents:** `hk check --all` or `hk run pre-commit --all` must complete successfully before every commit. A commit that succeeds only because a hook was bypassed (`--no-verify`) is not considered complete (see `05-agent-guidelines.md`).

`shellcheck`, `shfmt`, `yamllint`, `markdownlint`, and `hk` itself are **not** installed manually (no `brew install …`); they are pinned through `mise.toml` and provided via `mise install` (see the "Tooling Provider (mise)" section above). Rule exceptions for `markdownlint`/`yamllint` are defined in `.markdownlint.jsonc`/`.yamllint.yml` in the repository root, each with an explanatory comment.

## Linting/Formatting Configuration (Python)

```toml
# pyproject.toml (excerpt)
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "PL", "RUF"]

[tool.mypy]
strict = true
disallow_untyped_defs = true
warn_return_any = true
```

`strict = true` for mypy is intentional: AI agents tend to use `Any` or omit type annotations as shortcuts — strict mode prevents this automatically rather than relying on review discipline.

## Test Strategy

* **Unit tests** for each module, with no real network/filesystem access outside test fixtures (see `03-module-specifications.md`, "Testability" section for each module).
* **Integration tests** (`@pytest.mark.integration`) against real local services (GraphHopper container, real DEM tile) — these do not run on every `pre-push`, but separately in CI or on demand.
* **Regression tests** for the optimization layer: small, manually verifiable scenarios with a known optimal charging plan.
* **Coverage threshold** (`pytest-cov`) as a CI gate, e.g. a minimum of 85% for `src/tripplanner/` — prevents agents from committing new code without accompanying tests.

## CI Pipeline (GitHub Actions, Example)

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jdx/mise-action@v2   # installs Python/uv/hk/… from mise.toml
      - run: uv sync
      - run: uv run hk check --all
      - run: uv run pytest -m "not integration" --cov=src/tripplanner --cov-fail-under=85
  integration-tests:
    runs-on: ubuntu-latest
    services:
      graphhopper:
        image: israelhikingmap/graphhopper   # placeholder; define the concrete image in the project
    steps:
      - uses: actions/checkout@v4
      - uses: jdx/mise-action@v2
      - run: uv sync
      - run: uv run pytest -m integration
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jdx/mise-action@v2   # installs Node from mise.toml
      - run: npm --prefix frontend ci
      - run: npm --prefix frontend run lint
      - run: npm --prefix frontend run typecheck
      - run: npm --prefix frontend run test
```

CI deliberately repeats the same checks as the local `hk` hooks — local hooks can be bypassed (`--no-verify`, missing installation), whereas CI is the authoritative final gate before a merge.

## Documentation

For generated API/module documentation from docstrings, it is recommended to use a consistent docstring format (Google or NumPy style, established project-wide) and enforce it via `ruff` (`D` rules, compatible with pydocstyle), so that later documentation generation (e.g. with mkdocs and mkdocstrings) works without additional cleanup.
