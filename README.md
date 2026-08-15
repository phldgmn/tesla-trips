# Tesla-Tripplaner

Hochgradig personalisierter Reiseplaner für ein Tesla Model 3: physikalisch fundiertes Verbrauchsmodell, iterative Wetter-/ETA-Auflösung und optimierte Ladeplanung ausschließlich über Tesla Supercharger.

## Setup

```bash
mise install   # installiert Python/uv/node/hk/Linter in gepinnten Versionen (siehe mise.toml)
uv sync
uv run hk check --all
uv run pytest -m "not integration"
```

## Projekt ausführen

Für die tägliche Entwicklung gibt es ein zentrales Skript, das beide Services managed:

```bash
# Alle starten (Default, stoppt ggf. laufende Instanzen)
./run.sh start

# Nur Backend / Frontend / GraphHopper / Basemap-Tiles
./run.sh start backend
./run.sh start frontend
./run.sh start graphhopper
./run.sh start tiles

# Status prüfen
./run.sh status

# Alle stoppen
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
./run.sh start graphhopper   # oder: ./run.sh start backend / start (=all)
```

`./run.sh start graphhopper` (und damit auch `start backend`/`start` ohne Argument)
startet bei Bedarf zuerst OrbStack, führt dann bei fehlendem Extrakt
automatisch `scripts/prepare_osm_extract.sh` aus und wartet anschließend, bis
der GraphHopper-Container healthy ist. Manuell äquivalent:

```bash
./scripts/prepare_osm_extract.sh   # lädt DE+DK+SE-Extrakte, merged sie
docker compose up -d
```

Die `docker-compose.yml`-Datei im Repo-Root konfiguriert den GraphHopper-Container mit dem offiziellen Image `israelhikingmap/graphhopper:11.0` (dieselbe Version wie in der CI-Pipeline, `.github/workflows/ci.yml`, Zeile 33). Das Image selbst enthält **keinen** vorgebauten Graphen – `docker-compose.yml` liest daher `-i /data/de-dk-se.osm.pbf`, eine lokale Datei, die `scripts/prepare_osm_extract.sh` erzeugt: Sie lädt die vollständigen Geofabrik-Länderextrakte für **Deutschland**, **Dänemark** und **Schweden** herunter (zusammen mehrere GB) und führt sie mit `osmium merge` (`brew install osmium-tool`) zu einer einzigen Datei zusammen, da GraphHopper nur eine einzelne lokale Eingabedatei akzeptiert. Download, Merge und der anschließende GraphHopper-Import können je nach Verbindung/Hardware deutlich länger dauern als bei einem kleinen Demo-Extrakt (typischerweise mehrere zehn Minuten bis über eine Stunde beim ersten Lauf). Der importierte Graph wird in `./data` zwischengespeichert (bereits via `.gitignore` ausgeschlossen), sodass spätere Starts ihn wiederverwenden statt neu zu importieren.

Damit deckt die lokale Routing-Instanz standardmäßig ganz Deutschland, Dänemark und Schweden ab (grenzüberschreitendes Routing funktioniert, da alle drei Länder in einem zusammenhängenden Graphen liegen statt in getrennten Extrakten).

Um die Quelldaten neu herunterzuladen/zusammenzuführen (z. B. nach einem Geofabrik-Update): `./scripts/prepare_osm_extract.sh --force`, danach `rm -rf data/default-gh` (Graph-Cache) und `./run.sh restart graphhopper`, damit GraphHopper den neuen Extrakt tatsächlich neu importiert.

**Alternativer GraphHopper-Endpunkt (GRAPHHOPPER_URL):**

Wenn Sie einen entfernten oder gemeinsamen GraphHopper-Server nutzen möchten, können Sie die Umgebungsvariable `GRAPHHOPPER_URL` setzen (Standardwert: `http://localhost:8989`). Diese Variable wird vom Backend beim Start der API-Lifetime-Phase gelesen und in der `GraphHopperClient`-Instanz verwendet. In der CI-Pipeline wird dieselbe Variable für den Integrationstest-Job gesetzt (.github/workflows/ci.yml, Zeile 59).

**Selbst gehostete Vektor-Basemap-Tiles (Port 8081):**

Die Karte im Frontend nutzt einen selbst gehosteten Vektor-Tile-Server statt
einer externen CDN, gebaut aus demselben DE+DK+SE-OSM-Extrakt, den auch
GraphHopper fürs Routing nutzt (`data/de-dk-se.osm.pbf`). Der Build läuft
per [Planetiler](https://github.com/onthegomap/planetiler)
(OpenMapTiles-Schema, kompatibel zum verwendeten "liberty"-Style) und
erzeugt ein einzelnes [PMTiles](https://docs.protomaps.com/pmtiles/)-Archiv
(`data/tiles/basemap.pmtiles`), ausgeliefert per `pmtiles serve`
(`docker-compose.yml`, Service "tiles").

```bash
./run.sh start tiles   # oder: ./run.sh start frontend / start (=all)
```

`./run.sh start tiles` (und damit auch `start frontend`/`start` ohne
Argument) baut bei fehlendem Tileset automatisch
`scripts/build_basemap_tiles.sh` (kann bei einem vollständigen DE+DK+SE-Build
15-60+ Minuten dauern und benötigt ca. 30GB freien Diskspace) und wartet
anschließend, bis der Tile-Server erreichbar ist. Manuell äquivalent:

```bash
./scripts/build_basemap_tiles.sh   # baut data/tiles/basemap.pmtiles
docker compose up -d tiles
```

Sprite und Schriftarten (Glyphs) bleiben bewusst bei der öffentlichen
openfreemap.org-CDN (kleine, unkritische Assets) – nur die eigentlichen
Kartendaten (Straßen, Gebäude, Landnutzung etc.) werden selbst gehostet;
siehe `frontend/src/components/Map.tsx` (`buildBasemapStyle`) und die
vendorte Style-Definition `frontend/src/assets/liberty-style.json`.

Um das Tileset neu zu bauen (z. B. nach einem OSM-Extrakt-Update):
`./scripts/build_basemap_tiles.sh --force`, danach `./run.sh restart tiles`.

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
