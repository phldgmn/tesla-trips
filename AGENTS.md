# AGENTS.md — Guidelines for AI Coding Agents

This file is located in the repository root and is read by AI coding agents before starting every task.

## Core Principle

Code is considered complete only when **all** of the following requirements are met — these are not a checklist to tick off after writing the code, but the definition of done:

1. `uv run hk check --all` completes without errors (linting, formatting, type checking).
2. Tests exist for every new piece of functionality that fail before the change and pass afterward.
3. `uv run pytest -m "not integration"` passes completely.
4. The coverage threshold defined in `docs/04-repo-tooling-setup.md` is not undercut (85% for `src/tripplanner/`).
5. No new dependencies are introduced between modules except through the interfaces defined in `models.py` (see `docs/03-module-specifications.md`).
6. **Changes are ALWAYS committed.** Every completed task (bugfix, feature, refactor) ends with a `git commit` containing the changes — regardless of whether this was explicitly requested. Unfinished/experimental work is explicitly excluded (e.g. following an explicit user instruction to "not commit yet"). A task is not considered complete as long as changes exist only in the working tree.
7. **Commit frequently.** Instead of producing a single large commit at the end of a task, commit in small, self-contained steps (e.g. after each working intermediate state). This makes it easier to selectively roll back to an earlier working state if something goes wrong, rather than discarding the entire task.

## Starting and Stopping Services

For integration tests and manual API/frontend testing, **only** `./run.sh` is used — not `uvicorn` directly and not `npm run dev` directly. The script provides:

1. **Stopping running instances** before restarting — including instances left over from a previous agent run.
2. **Color-coded, identifiable output** (`[BACKEND]` / `[FRONTEND]`) in a shared terminal.
3. **PID tracking in `.run/`**, so it remains clear what is running even after switching sessions.
4. **Consistent log files** (`.run/backend.log`, `.run/frontend.log`) for debugging.

```bash
# Start both services (default; stops them first if something is already running)
./run.sh start

# Backend only
./run.sh start backend

# Frontend only
./run.sh start frontend

# Check status
./run.sh status

# Stop
./run.sh stop

# Restart (stop + start)
./run.sh restart
```

**Rule:** Agents that require the backend or frontend for a task (integration tests, visual inspection, API tests against a running server) must start the services via `./run.sh` and stop them via `./run.sh stop` after completing the task. Directly invoking `uvicorn` or `npm run dev` to start services is not permitted.

## Forbidden Shortcuts

Agents must **not**:

* Create commits with `--no-verify` or comparable mechanisms that bypass the hooks.
* Suppress linting or type errors using `# noqa`, `# type: ignore`, or by lowering `mypy`/`ruff` rules in `pyproject.toml` without an explanation of the substantive reason being documented in the commit/PR. Rule exceptions are permitted at the individual-line level with an explanatory comment, but not as a global configuration change without consultation.
* Delete or disable tests (`skip`) to make a failing CI pipeline pass.
* Make live requests to external data sources (GraphHopper, Open-Meteo, DATEX II, Tesla charging-station data) from unit tests — fixtures exist for this purpose (see `docs/03-module-specifications.md`).
* Perform system-wide searches such as `find / …`, `find ~ …`, or comparable scans of the entire filesystem/home directory. Searches must be restricted to the repository directory (or explicitly named, narrow paths) — e.g. use `glob`/`grep` tools with repository-relative paths rather than an unrestricted `find /`.

## Procedure for Each Task

1. Identify the responsible module from `docs/03-module-specifications.md` or the detailed plans under `docs/plans/`; do not begin work across module boundaries if the task can be confined to a single module.
2. Read the existing interfaces (`models.py` of the module) before introducing new data structures — avoid duplicating data models.
3. Write the test first, or at minimum determine before implementation which test cases will be used to verify the change.
4. Implement the change.
5. Run `uv run hk check --all` and the relevant tests locally before proposing a commit.
6. The commit message describes **what** and **why**, not merely **what** (e.g. not just "add wind module", but briefly state the calculation assumption).

## Sub-Agents

Where appropriate, use sub-agents to parallelize independent subtasks — e.g. research across multiple modules, independent bugfixes in separate files, or gathering context in parallel while the main agent continues with the actual implementation. The prerequisite is that the subtasks are genuinely independent (no shared files, no sequential dependency), and their results are reviewed before being incorporated rather than merged blindly.

## Module Boundaries

* No module may access the internal implementation details of another module — only its `models.py` data structures and public functions/classes.
* Exception: `tripplanner.geo` is a dependency-free geo primitive (not a business module) and may be imported by any module (see `docs/07-implementation-plan.md`, section 6.2).
* External data sources (HTTP clients, filesystem access) are encapsulated behind a provider interface (see, for example, `WeatherProvider` and `ChargingStationProvider` in `docs/03-module-specifications.md`) so that they can be replaced in tests and the data source can be exchanged when necessary.
* New external dependencies (libraries, APIs) must not be introduced without being related to one of the architectural decisions defined in `docs/01-project-specifications.md`.

## Type Annotations and Docstrings

* Every public function/method has complete type annotations (enforced by `mypy --strict`) and a docstring following the project-wide standard (Google style, see `docs/04-repo-tooling-setup.md`).
* Pydantic models are the only permitted form of data structures crossing module boundaries.

## Language in Code, Comments, and Documentation

* New code, new comments, and new documentation (docstrings, README sections, `docs/` files, commit messages for code-related content) are **always written in English** — regardless of the language of this AGENTS.md file or existing legacy content in the repository.
* When modifying existing code, an existing comment, or an existing documentation section that is still in German, translate the affected part into English where reasonably possible.

## Coordinate Convention

All `Coordinate` tuples in the project are `(lat, lon)`. Exceptions are permitted only at the three external boundaries documented in `docs/07-implementation-plan.md`, section 3 (GraphHopper request, rasterio pixel lookup, MapLibre/GeoJSON rendering).

## When in Doubt

If a requirement is ambiguous, the agent makes a justified, documented assumption (a comment in the code + a mention in the PR description) rather than leaving the task undone — unless the ambiguity concerns one of the open questions in `docs/06-open-points-contradictions.md`; these have already been conclusively resolved in `docs/07-implementation-plan.md`, section 7.
