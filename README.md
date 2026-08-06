# Tesla-Tripplaner

Hochgradig personalisierter Reiseplaner für ein Tesla Model 3: physikalisch fundiertes Verbrauchsmodell, iterative Wetter-/ETA-Auflösung und optimierte Ladeplanung ausschließlich über Tesla Supercharger.

## Setup

```bash
uv sync
uv run hk check --all
uv run pytest -m "not integration"
```

## Projekt ausführen

### Backend (FastAPI)

```bash
uv sync
uv run uvicorn tripplanner.trip_input.api:app --reload
```

Die API läuft danach auf `http://localhost:8000` (interaktive Docs unter `/docs`). Zentraler Endpunkt: `POST /trips` (siehe `src/tripplanner/trip_input/api.py`).

Alternativ steht ein CLI-Entry-Point für Einzelabfragen ohne laufenden Server zur Verfügung:

```bash
uv run python -m tripplanner.trip_input.cli trips --help
```

Für Routing-Anfragen wird ein lokaler GraphHopper-Server auf Port `8989` erwartet (siehe `docs/plans/01-routing.md` für das Docker-Compose-Setup mit OSM-Kartendaten für DE/DK/SE). Ohne diesen Server schlagen nur Requests fehl, die tatsächlich Routing benötigen.

### Frontend (Vite + React + MapLibre GL JS)

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

Der Dev-Server läuft auf `http://localhost:3000`. Das Frontend besitzt aktuell keine feste Backend-Basis-URL/Proxy-Konfiguration — Backend muss ggf. separat erreichbar gemacht werden (CORS ist im Backend standardmäßig nicht aktiviert, siehe `docs/plans/08-simulation-visualization-api.md`).

Weitere Frontend-Kommandos: `npm --prefix frontend run build`, `run lint`, `run typecheck`, `run test`.

## Dokumentation

- Fachspezifikation: `docs/01-projektspezifikation.md` bis `docs/06-offene-punkte-widersprueche.md`
- Implementierungsplan: `docs/07-implementierungsplan.md` und `docs/plans/`
- Agenten-Leitlinien: `AGENTS.md`

Frontend (`frontend/`): TypeScript + MapLibre GL JS, siehe `docs/plans/08-simulation-visualization-api.md`.
