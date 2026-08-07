# Tesla-Tripplaner

Hochgradig personalisierter Reiseplaner für ein Tesla Model 3: physikalisch fundiertes Verbrauchsmodell, iterative Wetter-/ETA-Auflösung und optimierte Ladeplanung ausschließlich über Tesla Supercharger.

## Setup

```bash
uv sync
uv run hk check --all
uv run pytest -m "not integration"
```

## Projekt ausführen

Für die tägliche Entwicklung gibt es ein zentrales Skript, das beide Services managed:

```bash
# Beide starten (Default, stoppt ggf. laufende Instanzen)
./run.sh start

# Nur Backend / nur Frontend
./run.sh start backend
./run.sh start frontend

# Status prüfen
./run.sh status

# Beide stoppen
./run.sh stop

# Neu starten (stop + start)
./run.sh restart
```

**Details:**

- Backend (uvicorn) läuft auf `http://localhost:8000`, interaktive API-Docs unter `/docs`.
- Frontend (Vite) läuft auf `http://localhost:3000` mit Proxy-Regel `/api` → `localhost:8000` (CORS wird lokal über den Vite-Proxy umgangen, siehe `frontend/vite.config.ts`).
- Jeder Service schreibt sein Log in `.run/<name>.log`; die PID liegt in `.run/<name>.pid`.
- Das Skript killt bei `start` immer zuerst existierende Prozesse auf dem jeweiligen Port, unabhängig davon, ob sie vom Skirt stammen. Wiederholtes `./run.sh start` ist also sicher.
- **Jeder Agent in diesem Repo** verwendet `./run.sh` zum Starten/Stoppen von Backend und Frontend (siehe `AGENTS.md`).

### Manuelle Alternativen

#### Backend (FastAPI)

```bash
uv sync
uv run uvicorn tripplanner.trip_input.api:app --reload
```

Die API läuft danach auf `http://localhost:8000`. Zentraler Endpunkt: `POST /trips`.

Alternativ steht ein CLI-Entry-Point für Einzelabfragen ohne laufenden Server zur Verfügung:

```bash
uv run python -m tripplanner.trip_input.cli trips --help
```

Für Routing-Anfragen wird ein lokaler GraphHopper-Server auf Port `8989` erwartet. Ohne diesen Server schlagen nur Requests fehl, die tatsächlich Routing benötigen (die `/trips` API-Route liefert dann HTTP 502).

**Schnellstart mit Docker Compose:**

```bash
docker compose up -d
```

Die `docker-compose.yml`-Datei im Repo-Root konfiguriert den GraphHopper-Container mit dem offiziellen Image `israelhikingmap/graphhopper:11.0` (dieselbe Version wie in der CI-Pipeline, `.github/workflows/ci.yml`, Zeile 33). Das Image selbst enthält **keinen** vorgebauten Graphen – `docker-compose.yml` übergibt daher `--url https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf`, sodass der Container beim ersten Start automatisch einen kleinen, echten OSM-Extrakt (Berlin-Umgebung, ~95 MB) herunterlädt und importiert (dauert ca. 1–2 Minuten). Der importierte Graph wird in `./data` zwischengespeichert (bereits via `.gitignore` ausgeschlossen), sodass spätere `docker compose up -d`-Aufrufe ihn wiederverwenden statt neu zu importieren.

Der Berlin-Extrakt deckt nur das Berliner Umland ab, nicht ganz Deutschland.

Für die volle Abdeckung von Deutschland, Dänemark und Schweden (DE/DK/SE):

1. `./data` leeren (sonst wird der zwischengespeicherte Berlin-Graph weiterverwendet): `rm -rf data/*`
2. In `docker-compose.yml` den `command`-Abschnitt anpassen, z. B. auf einen bereits heruntergeladenen lokalen Extrakt verweisen (Datei zuvor nach `./data` legen) oder eine andere `--url` angeben. Geofabrik-Quellen:
   - Deutschland: `https://download.geofabrik.de/europe/germany-latest.osm.pbf` (~4.5 GB)
   - Dänemark: `https://download.geofabrik.de/europe/denmark-latest.osm.pbf`
   - Schweden: `https://download.geofabrik.de/europe/sweden-latest.osm.pbf` (~772 MB)
3. `docker compose up -d` (Download + Import der großen Extrakte kann deutlich länger dauern als beim Berlin-Demo-Extrakt).

Cross-Border-Routing über mehrere Länder hinweg erfordert einen zusammenhängenden Extrakt (z. B. den Europe-Gesamtextrakt mit `osmconvert` auf eine DE/DK/SE-Bounding-Box zugeschnitten) statt dreier getrennter Länder-Extrakte.

**Alternativer GraphHopper-Endpunkt (GRAPHHOPPER_URL):**

Wenn Sie einen entfernten oder gemeinsamen GraphHopper-Server nutzen möchten, können Sie die Umgebungsvariable `GRAPHHOPPER_URL` setzen (Standardwert: `http://localhost:8989`). Diese Variable wird vom Backend beim Start der API-Lifetime-Phase gelesen und in der `GraphHopperClient`-Instanz verwendet. In der CI-Pipeline wird dieselbe Variable für den Integrationstest-Job gesetzt (.github/workflows/ci.yml, Zeile 59).

#### Frontend (Vite + React + MapLibre GL JS)

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

Weitere Frontend-Kommandos: `npm --prefix frontend run build`, `npm --prefix frontend run lint`, `npm --prefix frontend run typecheck`, `npm --prefix frontend run test`.

## Dokumentation

- Fachspezifikation: `docs/01-projektspezifikation.md` bis `docs/06-offene-punkte-widersprueche.md`
- Implementierungsplan: `docs/07-implementierungsplan.md` und `docs/plans/`
- Agenten-Leitlinien: `AGENTS.md`

Frontend (`frontend/`): TypeScript + MapLibre GL JS, siehe `docs/plans/08-simulation-visualization-api.md`.
