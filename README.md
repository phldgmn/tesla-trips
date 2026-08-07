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

Die `docker-compose.yml`-Datei im Repo-Root konfiguriert den GraphHopper-Container mit dem offiziellen Image `israelhikingmap/graphhopper:11.0`. Dieses Image ist aktuell (Stand: Oktober 2025) die stable Release (`11.0`, veröffentlicht am 14. Oktober 2025) und stammt aus demselben Container-Image, das auch in der CI-Pipeline (.github/workflows/ci.yml, Zeile 33) verwendet wird.

Das vorkompilierte Demo-Image enthält einen kleinen OSM-Ausschnitt (Berlin-Umgebung), sodass die Health-Check-Abfrage der CI (`point=52.52,13.40&point=52.53,13.41`, zentrale Berlin-Koordinaten) sofort funktioniert – ein vollständiges Routing-Setup ist also ohne weitere Datenbereitstellung schon nach dem `docker compose up -d` gegeben. Der Demo-Ausschnitt deckt aber ausschließlich das Berliner Umland ab, nicht ganz Deutschland.

Für die volle Abdeckung von Deutschland, Dänemark und Schweden (DE/DK/SE) müssen Sie selbst Geofabrik-OSM-Extrakte in das Verzeichnis `./data` herunterladen (dieses Verzeichnis ist bereits via `.gitignore` ausgeschlossen):

- Deutschland: `germany-latest.osm.pbf` (~4.5 GB)
- Dänemark: `denmark-latest.osm.pbf` (aus `europe/denmark.html`)
- Schweden: `sweden-latest.osm.pbf` (~772 MB)

Download-Befehle (einmalig ausführen):

```bash
wget https://download.geofabrik.de/europe/germany-latest.osm.pbf -O data/germany-latest.osm.pbf
wget https://download.geofabrik.de/europe/denmark-latest.osm.pbf -O data/denmark-latest.osm.pbf
wget https://download.geofabrik.de/europe/sweden-latest.osm.pbf -O data/sweden-latest.osm.pbf
```

Anschließend müssen Sie den GraphHopper-Container neu starten, damit er den Graphen neu baut:

```bash
docker compose down
docker compose up -d
```

Der erste Start mit großen OSM-Dateien kann mehrere Minuten dauern (Graph-Processing). Erst danach ist die Route-Endpunkt in vollem Umfang nutzbar.

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
