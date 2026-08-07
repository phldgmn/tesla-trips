# Plan Phase 0 — Repo-Fundament & Tooling

## 1. Zweck & Scope

Phase 0 legt das technische Fundament für das gesamte Projekt. Es geht nicht um Funktionalität, sondern um die Einrichtung einer strukturierten, testbaren, lint- und type-safe Codebasis, die KI-Agenten und Entwickler:innen anleitet und zwingt, qualitativ hochwertigen Code zu liefern.

**Was dieses Modul leistet:**
- Exakter Verzeichnisbaum für alle 12 Module (`routing`, `elevation`, `weather`, `wind`, `construction`, `energy`, `battery`, `charging_infrastructure`, `optimization`, `simulation`, `visualization`, `trip_input`)
- Python-Projektdefinition mittels `pyproject.toml` mit allen Dependencies und Dev-Gruppen
- Git-Hook-Konfiguration mittels `hk.pkl` für pre-commit (Linting/Auto-Fix) und pre-push (Unit-Tests)
- CI-Pipeline (`ci.yml`) mit Lint-, Test- und Frontend-Jobs inkl. konkretem GraphHopper-Docker-Image
- AGENTS.md als Leitlinien-Datei für alle KI-Coding-Agenten (ausformuliert aus `05-agent-guidelines.md`)
- Vorbereitung für mkdocs/mkdocstrings-Dokumentation

**Abgrenzung:**
- Keine Implementierung von Logik (Routing, Energie, Optimierung etc.)
- Keine Einrichtung von Datenquellen (GraphHopper-Container starten ist Aufgabe der CI, nicht dieses Plans)
- Keine Erstellung von Test- oder Quellcode-Dateien außer der Struktur-Vorgabe

**NICHT-Scope (spätere Erweiterung):**
- Aktiver GraphHopper-Container im dev-mode (nur CI-intern)
- Echt-Zeit-Wetter-Crawler
- Live-Baustellen-Feed
- Datenbank-Migrationen

---

## 2. Abhängigkeiten & Phasenzuordnung

**Phase:** Phase 0 (Fundament & Tooling)

**Konsumierte Typen aus anderen Modulen:** Keine — Phase 0 precediert alle anderen Module.

**Zukünftige Abhängigkeiten (für Integration in Phase 1+):**
- Alle Module (`tripplanner.routing`, `tripplanner.elevation`, …) werden `uv` als Paketmanager nutzen
- Externe Dependencies (GraphHopper, Open-Meteo, DATEX II) werden via `httpx` angesprochen (in `pyproject.toml` definiert)
- Test-Framework: `pytest`, `pytest-cov` (Coverage-Gate 85%)

---

## 3. Verzeichnisbaum (exakt wie vorgegeben + Modul-Skeleton)

```
tesla-tripplanner/
├── hk.pkl                     # Git-Hook- und Lint-Konfiguration (s. Abschnitt 4)
├── pyproject.toml             # uv/Python-Projektdefinition (s. Abschnitt 4)
├── uv.lock
├── AGENTS.md                  # siehe 05-agent-guidelines.md (s. Abschnitt 5)
├── docs/
│   ├── 01-projektspezifikation.md
│   ├── 02-architektur.md
│   └── 03-modulspezifikationen.md
├── src/
│   └── tripplanner/
│       ├── __init__.py        # Package init (leer, aber erforderlich)
│       ├── geo/                # Phase 0 — geteilte geografische Primitive (kein Business-Modul)
│       │   ├── __init__.py    # exportiert Coordinate, bearing_deg(), haversine_distance_m()
│       │   └── geo.py         # Coordinate = tuple[float, float] (lat, lon); Bearing-/Distanzformeln
│       ├── routing/           # Phase 1
│       │   ├── __init__.py    # re-exportiert öffentliche API
│       │   ├── models.py      # Pydantic-Modelle: Route, RouteSegment
│       │   ├── routing.py     # Kernlogik (graphhopper_client, route berechnen)
│       │   ├── providers.py   # GraphHopperProvider + FakeProvider für Tests
│       │   └── client.py      # HTTP-Client für GraphHopper-API
│       ├── elevation/         # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: ElevationPoint, SegmentGradient
│       │   ├── elevation.py   # DEM-Lookup, Steigung berechnen
│       │   ├── providers.py   # ElevationProvider (rasterio-basiert)
│       │   └── client.py      # (optional, falls file-system-access gekapselt)
│       ├── weather/           # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: WeatherQuery, WeatherSample
│       │   ├── weather.py     # Wetterabfrage, Iterationslogik
│       │   ├── providers.py   # WeatherProvider (Open-Meteo) + FakeProvider
│       │   └── client.py      # HTTP-Client für Open-Meteo API
│       ├── wind/              # Phase 2 (abhängig von weather.models)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: WindComponents
│       │   └── wind.py        # Windkomponenten berechnen (Bearing, Projektion)
│       ├── construction/      # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: ConstructionZone, Sperrungstyp-Enum
│       │   ├── construction.py # DATEX II Parser, Baustellen-Extraktion
│       │   ├── providers.py   # ConstructionProvider + FakeProvider
│       │   └── client.py      # (optional, falls DATEX II Feed über HTTP)
│       ├── energy/            # Phase 3 (abhängig von routing, elevation, weather, wind, construction)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: VehicleEnergyParameters, SegmentEnergyResult
│       │   ├── energy.py      # Physik-Modell (Rollwiderstand, Luftwiderstand, etc.)
│       │   ├── rolling_resistance.py  # (optional: modularisiert)
│       │   ├── aerodynamics.py        # (optional: modularisiert)
│       │   └── providers.py   # (optional, falls Wetter/Ladekurven gekapselt)
│       ├── battery/           # Phase 4 (abhängig von energy models + charging_infrastructure)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: SoCState, ChargingCurvePoint, ChargingCurve
│       │   ├── battery.py     # SoC-Verlauf, Ladekurve, Entladung
│       │   └── providers.py   # (optional)
│       ├── charging_infrastructure/  # Phase 1
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: ChargingStation, ChargingStationProvider
│       │   ├── providers.py   # ChargingStationProvider (Tesla Supercharger, lokal) + FakeProvider
│       │   └── client.py      # (optional, falls JSON/SQLite-Daten über API)
│       ├── optimization/      # Phase 5 (abhängig von routing, energy, battery, charging_infrastructure)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: ChargingPlan, ChargingStop, OptimizationConstraints
│       │   ├── optimization.py # Optimierungs-Interface, Zustandsraum-Suche
│       │   └── optimizer_ortools.py  # OR-Tools-Implementierung
│       ├── simulation/        # Phase 6 (abhängig von optimization output)
│       │   ├── __init__.py
│       │   ├── models.py      # Pydantic-Modelle: SimulationFrame, TripSimulationResult
│       │   └── simulation.py  # Simulation aus ChargingPlan + Route
│       ├── visualization/     # Phase 7 (abhängig von simulation output-Contract)
│       │   ├── __init__.py
│       │   └── models.py      # (optional, falls JSON-Schema generiert wird)
│       └── trip_input/        # Phase 7 (CLI/API, abhängig von simulation)
│           ├── __init__.py
│           ├── models.py      # Pydantic-Modelle: TripRequest, Waypoint, VehicleProfile
│           ├── api.py         # FastAPI-Endpunkte
│           └── cli.py         # CLI-Schnittstelle (typer/argparse)
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
│   │       └── synthetic_dem_tile.tif  # kleine Test-Kachel
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
│   │       └── small_scenarios.json  # von Hand nachvollziehbare Szenarien
│   ├── simulation/
│   │   ├── test_simulation.py
│   │   └── conftest.py
│   ├── visualization/
│   │   ├── test_visualization.py
│   │   └── conftest.py
│   ├── trip_input/
│   │   ├── test_trip_input.py
│   │   └── conftest.py
│   └── fixtures/  # aufgezeichnete API-Antworten, Beispiel-DEM-Kacheln, DATEX-II-Beispiele
│       ├── common_fixtures/
│       │   └── test_coords.json  # Koordinaten-Samples (Start/Ziel/Waypoints)
│       └── integration_fixtures/
│           └── graphhopper_demo_route.json
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── main.ts            # Entry-Point, MapLibre GL JS init
│   │   ├── components/
│   │   │   ├── Map.tsx
│   │   │   ├── RouteOverlay.tsx
│   │   │   ├── SoCChart.tsx
│   │   │   └── ChargingStopsMarker.tsx
│   │   ├── types.ts           # TypeScript-Types (aus Pydantic-Modellen generiert)
│   │   └── utils.ts
│   └── tests/
│       ├── unit/
│       └── e2e/
├── .github/workflows/ci.yml   # CI-Pipeline (s. Abschnitt 4)
└── mkdocs.yml                 # MkDocs-Konfiguration (s. Abschnitt 7)
```

**Modul-Skeleton-Konvention (pro Modul verbindlich):**
```
src/tripplanner/<modul>/
├── __init__.py        # re-exportiert öffentliche API (z. B. from .models import Route)
├── models.py           # Pydantic-Modelle — einzige Cross-Modul-Schnittstelle
├── <modul>.py           # Kernlogik / öffentliche Funktionen
├── providers.py          # NUR falls externe Datenquelle: Protocol-Interface + konkrete Implementierung + Fake
└── client.py               # HTTP/IO-Client, vom Provider genutzt (falls vorhanden)
```
**Regel:** Kein Modul importiert interne Implementierungsdetails eines anderen Moduls — ausschließlich `tripplanner.<anderes_modul>.models`. **Ausnahme:** `tripplanner.geo` ist kein Business-Modul, sondern ein minimales, abhängigkeitsfreies Geo-Primitiv (`Coordinate`-Typalias, `bearing_deg()`, `haversine_distance_m()`). Es darf von jedem Modul importiert werden, da es keine Geschäftslogik und keinen veränderlichen Zustand enthält — analog zu einer externen Bibliothek. Alle `Coordinate`-Tupel im Projekt sind `(lat, lon)`; siehe `docs/plans/01-routing.md`, Abschnitt 3, „Koordinaten-Konvention".

---

## 4. Konfigurationsdateien vollständig ausformuliert

### 4.1 `pyproject.toml`

```toml
[project]
name = "tripplanner"
version = "0.1.0"
description = "Hochgradig personalisierter Reiseplaner für Tesla Model 3"
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
    "integration: integration tests (slow, gegen echte Dienste)",
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
        // kein fix — Type-Fehler werden nicht automatisch behoben,
        // sondern blockieren den Commit bewusst
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
        fix = true       // Auto-Fix läuft direkt beim Commit
        stash = "git"    // unstaged Änderungen werden währenddessen weggesichert
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
        image: israelhikingmap/graphhopper:11.0  # offizielles Community-Image, aktuellstable Tag
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

## 5. `AGENTS.md` — vollständige Ausformulierung

```markdown
# AGENTS.md — Leitlinien für KI-Coding-Agenten

Diese Datei liegt im Repo-Root und wird von KI-Coding-Agenten vor Beginn jeder Aufgabe gelesen.

## Grundprinzip

Code gilt nur dann als fertig, wenn **alle** der folgenden Punkte erfüllt sind — nicht als Checkliste zum Abhaken nach dem Schreiben, sondern als Definition of Done:

1. `uv run hk check --all` läuft ohne Fehler durch (Linting, Formatting, Type-Checking).
2. Für jede neue Funktionalität existieren Tests, die vor der Änderung fehlschlagen und danach erfolgreich sind.
3. `uv run pytest -m "not integration"` läuft vollständig grün.
4. Die Coverage-Schwelle aus `04-repo-tooling-setup.md` wird nicht unterschritten (85 % für `src/tripplanner/`).
5. Keine neue Abhängigkeit zwischen Modulen außer über die in `models.py` definierten Schnittstellen (siehe `03-modulspezifikationen.md`).

## Verbotene Abkürzungen

Agenten dürfen **nicht**:

- Commits mit `--no-verify` oder vergleichbaren Mechanismen an den Hooks vorbei erzeugen.
- Lint- oder Type-Fehler durch `# noqa`, `# type: ignore` oder das Absenken von `mypy`/`ruff`-Regeln in `pyproject.toml` "beheben", ohne dass eine inhaltliche Begründung im Commit/PR dokumentiert ist. Regel-Ausnahmen sind auf Zeilenebene mit Begründungskommentar zulässig, nicht als globale Config-Änderung ohne Rücksprache.
- Tests löschen oder deaktivieren (`skip`), um eine rote CI grün zu bekommen.
- Externe Datenquellen (GraphHopper, Open-Meteo, DATEX II, Tesla-Ladepunktdaten) in Unit-Tests live ansprechen — dafür existieren Fixtures (siehe `03-modulspezifikationen.md`).

## Vorgehen pro Aufgabe

1. Zuständiges Modul aus `03-modulspezifikationen.md` identifizieren; Aufgabe nicht modulübergreifend beginnen, wenn sie sich auf ein Modul eingrenzen lässt.
2. Bestehende Schnittstellen (`models.py` des Moduls) lesen, bevor neue Datenstrukturen eingeführt werden — Duplikate von Datenmodellen vermeiden.
3. Test zuerst schreiben oder zumindest vor der Implementierung festlegen, anhand welcher Testfälle die Änderung verifiziert wird.
4. Implementierung.
5. `uv run hk check --all` und relevante Tests lokal ausführen, bevor ein Commit vorgeschlagen wird.
6. Commit-Nachricht beschreibt **was** und **warum**, nicht nur **was** (z. B. nicht nur "add wind module", sondern kurz die Berechnungsannahme benennen).

## Modulgrenzen

- Kein Modul greift auf interne Implementierungsdetails eines anderen Moduls zu — nur auf dessen `models.py`-Datenstrukturen und öffentliche Funktionen/Klassen.
- Externe Datenquellen (HTTP-Clients, Dateisystemzugriffe) werden hinter einem Provider-Interface gekapselt (siehe z. B. `WeatherProvider`, `ChargingStationProvider` in `03-modulspezifikationen.md`), damit sie in Tests ersetzbar sind und die Datenquelle bei Bedarf austauschbar bleibt.
- Neue externe Abhängigkeiten (Bibliotheken, APIs) werden nicht ohne Bezug zu einem der in `01-projektspezifikation.md` festgelegten Architekturentscheidungen eingeführt.

## Typannotationen und Docstrings

- Jede öffentliche Funktion/Methode hat vollständige Typannotationen (durch `mypy --strict` erzwungen) und einen Docstring im projektweit einheitlichen Stil (Google-Style, siehe `04-repo-tooling-setup.md`).
- Pydantic-Modelle sind die einzige zulässige Form für Datenstrukturen, die Modulgrenzen überqueren.

## Bei Unsicherheit

Wenn eine Anforderung mehrdeutig ist (z. B. konkreter Schwellwert für die ETA-Neuiteration, konkrete Tesla-Ladepunkt-Datenquelle — siehe `06-offene-punkte-widersprueche.md`), trifft der Agent eine begründete, dokumentierte Annahme (Kommentar im Code + Erwähnung im PR-Text) statt die Aufgabe unbearbeitet zu lassen — außer die Mehrdeutigkeit betrifft eine der offenen Fragen in `06-offene-punkte-widersprueche.md`; diese werden vor Beginn der jeweiligen Modul-Implementierung mit dem Projektverantwortlichen geklärt.
```

---

## 6. Aufgaben-Checkliste

| Nr | Task | Betroffene Dateien | Beschreibung | Akzeptanzkriterium |
|----|------|-------------------|--------------|--------------------|
| 1 | Repo-Verzeichnisbaum erstellen | `src/tripplanner/*/`, `tests/*/` | Alle 12 Modul-Verzeichnisse plus `tripplanner/geo/` mit `__init__.py`, `models.py`, `<modul>.py`, `providers.py`, `client.py` (wo relevant) anlegen; `docs/plans/` und `frontend/` mit Basisstruktur | `find src/tripplanner -type f -name "*.py" \| wc -l` ergibt mindestens 48, `find tests -type d \| wc -l` ergibt mindestens 13 |
| 2 | `pyproject.toml` anlegen | `pyproject.toml` | Vollständige TOML-Datei gemäß Abschnitt 4.1 erstellen | `uv sync` läuft fehlerfrei, `uv pip list \| grep pydantic` zeigt Version ≥2 |
| 3 | `hk.pkl` anlegen | `hk.pkl` | Vollständige Konfiguration gemäß Abschnitt 4.2 | `hk check --all` läuft ohne Fehler auf leerem Repo |
| 4 | `.github/workflows/ci.yml` anlegen | `.github/workflows/ci.yml` | Vollständiger Workflow gemäß Abschnitt 4.3 | CI-Check simuliert (`act -W .github/workflows/ci.yml --container-architecture="linux/amd64"` oder GitHub UI) zeigt keine Syntaxfehler |
| 5 | `AGENTS.md` anlegen | `AGENTS.md` | Vollständiger Inhalt gemäß Abschnitt 5 | Datei existiert im Repo-Root, enthält alle 5 Abschnitte |
| 6 | `mkdocs.yml` und Dokumentation konfigurieren | `mkdocs.yml`, `docs/index.md` | MkDocs mit mkdocstrings konfigurieren (Python-Modul-Doku) | `mkdocs serve` startet local server und zeigt API-Doku |
| 7 | `uv.lock` generieren | `uv.lock` | `uv sync` ausführen und lock file commiten | `git status` zeigt nur neue/modified Dateien, keine untracked Dependencies |
| 8 | Test-Setup verifizieren (Unit-Tests ohne Integration) | `tests/conftest.py`, `tests/.../test_*.py` | Mindestens 2 Dummy-Tests pro Modul anlegen (z. B. `tests/routing/test_routing.py`) | `uv run pytest -m "not integration"` läuft grün (erwartet rote Tests am Anfang) |
| 9 | Ruff/Linter-Konfiguration testen | `pyproject.toml`, `ruff check` | `uv run ruff check src` und `uv run ruff format --check src` auf leerem Code | Keine Errors, keine Warnings |
| 10 | MyPy-Setup testen | `mypy` | `uv run mypy src` auf leerem Code | Keine Errors (erwartet rote Tests, da noch keine Typannotationen) |
| 11 | Frontend-Setup (Vorbereitung) | `frontend/package.json` | `package.json` mit TypeScript, MapLibre GL JS, React/TypeScript-Setup anlegen | `npm ci` und `npm run typecheck` laufen fehlerfrei |
| 12 | Git-Hooks testen (pre-commit) | `hk pre-commit` | Dummy-Python-Datei mit Lint-Fehler erstellen und commiten | Hook bricht Commit ab, Auto-Fix läuft (wenn `--fix` möglich) |
| 13 | `tripplanner.geo`-Primitiv implementieren | `src/tripplanner/geo/geo.py`, `tests/geo/test_geo.py` | `Coordinate = tuple[float, float]`, `bearing_deg(a, b) -> float` (Vorwärtsazimut), `haversine_distance_m(a, b) -> float`; keine Abhängigkeit zu anderen `tripplanner`-Modulen | Unit-Tests mit bekannten Referenzpunkten (z. B. Berlin→Hamburg-Bearing ≈ 312°) grün |

---

## 7. Risiken & offene technische Fragen

- **Risiko:** GraphHopper-Docker-Image (`israelhikingmap/graphhopper:11.0`) ist ein Community-Image, keine offizielle Release von GraphHopper GmbH. **Abwehrmaßnahme:** Image-Versionspinning in CI (`:11.0` statt `:latest`), Monitoring auf Image-Updates.
- **Risiko:** `hk` ist ein relativ neuer Hook-Runner; falls Team-Mitglieder (`--no-verify` nutzen) oder CI-Umgebungen (fehlende `hk` Installation) Probleme verursachen. **Abwehrmaßnahme:** CI wiederholt alle Checks explizit (s. Abschnitt 4.3), `hk` ist in `dev`-Group enthalten.
- **Offene Frage:** Soll `frontend/` tatsächlich TypeScript + MapLibre GL JS sein (wie in `02-architektur.md` empfohlen) oder auf Leaflet setzen (wie in `04-repo-tooling-setup.md` als Alternative genannt)? **Entscheidung:** MapLibre GL JS wird als Standard genommen (besseres Vektor-Styling, bereits im Team-Stack verbreitet, siehe Tech-Stack in `02-architektur.md`).
- **Offene Frage:** Soll die `hk.pkl` zusätzlich `black` als Alternative zu `ruff format` unterstützen? **Entscheidung:** Nein — `ruff format` ist schneller, konsistent mit `ruff check`, und in `pyproject.toml` bereits als einziger Formatter konfiguriert.
- **Offene Frage:** Soll die CI-Pipeline `pre-commit`-Hook-Check (lokal) als separater Job haben? **Entscheidung:** Nein — CI testet nur die synthetischen Checks (ruff, mypy, pytest), da lokale Hooks nicht reproduzierbar sind (umgangen via `--no-verify`).

---

## 8. mkdocs/mkdocstrings-Setup-Empfehlung

**Zweck:** Generierung von API-Dokumentation aus Docstrings im Google-Style (wie in `pyproject.toml`/`ruff.lint.pydocstyle.convention = "google"` definiert).

**Empfohlene Konfiguration (`mkdocs.yml`):**

```yaml
site_name: Tesla-Tripplaner API
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

**Regel für Entwickler:** Alle öffentlichen Funktionen/Methode müssen Docstrings im Google-Style haben, damit `mkdocs serve` vollständige Dokumentation generiert. Beispiel:

```python
def calculate_energy(
    segment: RouteSegment, weather: WeatherSample, vehicle: VehicleEnergyParameters
) -> SegmentEnergyResult:
    """Berechnet den Energieverbrauch für ein Segment unter Berücksichtigung von Wetter und Fahrzeug.

    Args:
        segment: Das zu berechnende RouteSegment mit Geometrie und Steigung.
        weather: Das aktuelle Wetter an diesem Segment ( temperatur, wind etc.).
        vehicle: Fahrzeugparameter ( Masse, cW, Stirnfläche, etc.).

    Returns:
        SegmentEnergyResult mit energiebedarf_kwh und rekuperation_kwh.

    Raises:
        ValueError: Wenn segment.laenge_m <= 0 oder temperatur < -50°C.
    """
    # Implementation
```

**Build- und Deploy-Befehle:**
- `mkdocs build` — erstellt statische HTML-Seiten in `site/`
- `mkdocs serve` — lokal live-Preview
- Deploy: `mkdocs gh-deploy` — direkt auf GitHub Pages (falls gewünscht)

---

**Handover from previous session (2026-08-02):**
- **Was war geplant:** Phase 0 — Repo-Fundament & Tooling (kein Code, nur Konfiguration und Struktur).
- **Was ist als nächstes zu tun:** Implementierung der 12 Tasks aus der Checkliste, beginnend mit Verzeichnisbaum (Task 1) und `pyproject.toml` (Task 2).
- **Wichtigste Dateien:** `docs/plans/00-foundation-tooling.md` (dieser Plan), `docs/04-repo-tooling-setup.md`, `docs/05-agent-guidelines.md`, `docs/01-projektspezifikation.md`, `docs/02-architektur.md`, `docs/03-modulspezifikationen.md`.