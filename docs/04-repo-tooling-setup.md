# Repo-Struktur und Tooling-Setup

Ziel dieses Dokuments: KI-Coding-Agenten sollen von Anfang an sauberen, konsistenten Code erzeugen. Das wird nicht durch Bitten im Prompt erreicht, sondern durch **automatisierte, verpflichtende Gates**: Linting/Formatting mit Auto-Fix, Type-Checking und Tests, die vor jedem Commit und in der CI laufen und einen Commit/Merge bei Verstößen blockieren.

## Repo-Struktur

```
tesla-tripplanner/
├── hk.pkl                     # Git-Hook- und Lint-Konfiguration
├── pyproject.toml             # uv/Python-Projektdefinition
├── uv.lock
├── AGENTS.md                  # siehe 05-agent-guidelines.md
├── docs/
│   ├── 01-projektspezifikation.md
│   ├── 02-architektur.md
│   └── 03-modulspezifikationen.md
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
│       └── api/                # CLI/FastAPI-Einstiegspunkt
├── tests/
│   ├── routing/
│   ├── elevation/
│   ├── weather/
│   ├── ...                     # Spiegelt src/-Struktur 1:1
│   └── fixtures/                # aufgezeichnete API-Antworten, Beispiel-DEM-Kacheln, DATEX-II-Beispiele
├── frontend/
│   ├── package.json
│   ├── src/
│   └── ...                      # TypeScript/MapLibre GL JS
└── .github/workflows/ci.yml
```

Jedes Modul aus `03-modulspezifikationen.md` entspricht genau einem Unterpaket unter `src/tripplanner/` mit gespiegeltem Testordner. Modul-übergreifende Importe erfolgen ausschließlich über die `models.py`-Schnittstellen der jeweiligen Module — kein Zugriff auf interne Implementierungsdetails eines fremden Moduls.

## Python-Setup (uv)

```toml
# pyproject.toml (Auszug)
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

- `uv sync` installiert alle Abhängigkeiten inkl. Dev-Gruppe.
- `uv run pytest`, `uv run ruff check`, `uv run mypy src` als Standard-Aufrufe — auch aus `hk.pkl` heraus.

## Git-Hooks mit hk

`hk.pkl` im Repo-Root, verpflichtend für jeden Commit (lokal) und zusätzlich in CI erzwungen (siehe unten):

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
        // kein fix — Type-Fehler werden nicht automatisch behoben,
        // sondern blockieren den Commit bewusst
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
    ["shfmt"] = (Builtins.shfmt) { /* -i 2, siehe hk.pkl: Repo-Konvention 2-Space-Einrückung */ }
    ["yamllint"] = Builtins.yamllint
    ["markdown-lint"] = Builtins.markdown_lint
}

hooks {
    ["pre-commit"] {
        fix = true       // Auto-Fix läuft direkt beim Commit
        stash = "git"    // unstaged Änderungen werden währenddessen weggesichert
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

**Wichtig für KI-Agenten:** `hk check --all` bzw. `hk run pre-commit --all` muss vor jedem Commit fehlerfrei durchlaufen. Ein Commit, der nur zustande kommt, weil ein Hook umgangen wurde (`--no-verify`), gilt als nicht abgeschlossen (siehe `05-agent-guidelines.md`).

`shellcheck`, `shfmt`, `yamllint` und `markdownlint` sind externe Binaries (nicht über `uv`/`npm` verwaltet) und müssen lokal installiert sein, z. B. via `brew install shellcheck shfmt yamllint markdownlint-cli`. Regel-Ausnahmen für `markdownlint`/`yamllint` stehen in `.markdownlint.jsonc`/`.yamllint.yml` im Repo-Root, jeweils mit Begründungskommentar.

## Linting/Formatting-Konfiguration (Python)

```toml
# pyproject.toml (Auszug)
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

`strict = true` bei mypy ist bewusst gewählt: KI-Agenten neigen dazu, `Any` oder fehlende Typannotationen als Abkürzung zu nutzen — strict mode verhindert das automatisiert, statt auf Review-Disziplin zu vertrauen.

## Teststrategie

- **Unit-Tests** je Modul, keine echten Netzwerk-/Dateisystemzugriffe außerhalb von Test-Fixtures (siehe `03-modulspezifikationen.md`, Abschnitt „Testbarkeit" je Modul).
- **Integrationstests** (`@pytest.mark.integration`) gegen echte lokale Dienste (GraphHopper-Container, echte DEM-Kachel) — laufen nicht bei jedem `pre-push`, sondern separat in CI bzw. auf Anforderung.
- **Regressionstests** für die Optimierungsschicht: kleine, von Hand nachvollziehbare Szenarien mit bekanntem optimalem Ladeplan.
- **Coverage-Schwelle** (`pytest-cov`) als CI-Gate, z. B. minimal 85 % für `src/tripplanner/` — verhindert, dass Agenten neuen Code ohne begleitende Tests einchecken.

## CI-Pipeline (GitHub Actions, Beispielskizze)

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run hk check --all
      - run: uv run pytest -m "not integration" --cov=src/tripplanner --cov-fail-under=85
  integration-tests:
    runs-on: ubuntu-latest
    services:
      graphhopper:
        image: israelhikingmap/graphhopper   # Platzhalter, konkretes Image im Projekt festlegen
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run pytest -m integration
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm --prefix frontend ci
      - run: npm --prefix frontend run lint
      - run: npm --prefix frontend run typecheck
      - run: npm --prefix frontend run test
```

CI wiederholt bewusst dieselben Checks wie die lokalen hk-Hooks — lokale Hooks können umgangen werden (`--no-verify`, fehlende Installation), CI ist die verbindliche letzte Instanz vor einem Merge.

## Dokumentation

Für generierte API-/Modul-Dokumentation aus Docstrings wird empfohlen, ein einheitliches Docstring-Format (Google- oder NumPy-Style, projektweit festgelegt) zu verwenden und per `ruff` (`D`-Regeln, pydocstyle-kompatibel) zu erzwingen, damit spätere Doku-Generierung (z. B. mkdocs mit mkdocstrings) ohne Nacharbeit funktioniert.
