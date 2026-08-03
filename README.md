# Tesla-Tripplaner

Hochgradig personalisierter Reiseplaner für ein Tesla Model 3: physikalisch fundiertes Verbrauchsmodell, iterative Wetter-/ETA-Auflösung und optimierte Ladeplanung ausschließlich über Tesla Supercharger.

## Setup

```bash
uv sync
uv run hk check --all
uv run pytest -m "not integration"
```

## Dokumentation

- Fachspezifikation: `docs/01-projektspezifikation.md` bis `docs/06-offene-punkte-widersprueche.md`
- Implementierungsplan: `docs/07-implementierungsplan.md` und `docs/plans/`
- Agenten-Leitlinien: `AGENTS.md`

Frontend (`frontend/`): TypeScript + MapLibre GL JS, siehe `docs/plans/08-simulation-visualization-api.md`.
