# Tesla Trip Planner

Highly personalized trip planner for a Tesla Model 3: physics-based energy
consumption model, iterative weather/ETA resolution, and optimized charging
planning exclusively via Tesla Superchargers.

## Status & Disclaimer

Personal, non-commercial side project. No license is granted — the code is
shared for reference/transparency only; there is no permission to reuse,
redistribute, or build on it. Not affiliated with, endorsed by, or
sponsored by Tesla, Inc.

Some data sources (see `docs/Tesla-Supercharger-API.md`,
`docs/Tesla-Supercharger-Data-Sources.md`,
`docs/Tesla-Supercharger-Detail-Scraping.md`) are undocumented, public
Tesla endpoints accessed without an official API agreement; using them
likely violates Tesla's Terms of Service. Run this at your own risk and
own IP address.

## Setup

```bash
mise install   # installs Python/uv/node/hk/linters in pinned versions (see mise.toml)
uv sync
uv run hk check --all
uv run pytest -m "not integration"
```

## Running the Project

Daily development uses a central script that manages both services:

```bash
# Start all (default; stops running instances first)
./run.sh start

# Backend / Frontend / GraphHopper / Basemap tiles only
./run.sh start backend
./run.sh start frontend
./run.sh start graphhopper
./run.sh start tiles

# Check status
./run.sh status

# Stop all
./run.sh stop

# Restart (stop + start)
./run.sh restart
```

**Details:**

- Backend (uvicorn) runs on `http://localhost:8000`, interactive API docs at `/docs`.
- Frontend (Vite) runs on `http://localhost:3000` with a proxy rule `/api` →
  `localhost:8000` (CORS is bypassed locally via the Vite proxy, see
  `frontend/vite.config.ts`).
- Every service writes its log to `.run/<name>.log`; the PID lives in
  `.run/<name>.pid`.
- On `start` the script always kills existing processes on the respective port
  first, regardless of whether they came from the script. Repeated
  `./run.sh start` is therefore safe.
- **Every agent in this repo** uses `./run.sh` to start/stop backend and
  frontend (see `AGENTS.md`).

### Manual Alternatives

#### Backend (FastAPI)

```bash
uv sync
uv run uvicorn tripplanner.trip_input.api:app --reload
```

The API then runs on `http://localhost:8000`. Central endpoint: `POST /trips`.

Alternatively, a CLI entry point is available for one-off queries without a
running server:

```bash
uv run python -m tripplanner.trip_input.cli trips --help
```

Routing requests expect a local GraphHopper server on port `8989`. Without
that server only requests that actually need routing fail (the `/trips` API
route then returns HTTP 502).

**Quick start with Docker Compose:**

```bash
./run.sh start graphhopper   # or: ./run.sh start backend / start (=all)
```

`./run.sh start graphhopper` (and therefore also `start backend`/`start`
without arguments) starts OrbStack if needed, automatically runs
`scripts/prepare_osm_extract.sh` when the extract is missing, and then waits
until the GraphHopper container is healthy. Manually equivalent:

```bash
./scripts/prepare_osm_extract.sh   # downloads DE+DK+SE extracts, merges them
docker compose up -d
```

The `docker-compose.yml` file in the repo root configures the GraphHopper
container with the official image `israelhikingmap/graphhopper:11.0` (the same
version as in the CI pipeline, `.github/workflows/ci.yml`, line 33). The image
itself contains **no** prebuilt graph — `docker-compose.yml` therefore reads
`-i /data/de-dk-se.osm.pbf`, a local file produced by
`scripts/prepare_osm_extract.sh`: it downloads the complete Geofabrik country
extracts for **Germany**, **Denmark**, and **Sweden** (several GB together) and
merges them with `osmium merge` (`brew install osmium-tool`) into a single
file, since GraphHopper only accepts a single local input file. Download,
merge, and the subsequent GraphHopper import can take several minutes each.

The local routing instance therefore covers all of Germany, Denmark, and
Sweden by default (cross-border routing works, since all three countries are
in one connected graph instead of separate extracts).

To re-download/re-merge the source data (e.g. after a Geofabrik update):
`./scripts/prepare_osm_extract.sh --force`, then `rm -rf data/default-gh`
(graph cache) and `./run.sh restart graphhopper`, so GraphHopper actually
re-imports the new extract.

**Alternative GraphHopper endpoint (GRAPHHOPPER_URL):**

If you want to use a remote or shared GraphHopper server, you can set the
environment variable `GRAPHHOPPER_URL` (default: `http://localhost:8989`).
This variable is read by the backend at API-lifetime startup and used in the
`GraphHopperClient` instance. The CI pipeline sets the same variable for the
integration test job (`.github/workflows/ci.yml`, line 59).

**Self-hosted vector basemap tiles (port 8081):**

The frontend map uses a self-hosted vector tile server instead of an external
CDN, built from the same DE+DK+SE OSM extract that GraphHopper uses for
routing (`data/de-dk-se.osm.pbf`). The build runs via
[Planetiler](https://github.com/onthegomap/planetiler) (OpenMapTiles schema,
compatible with the used "liberty" style) and produces a single
[PMTiles](https://docs.protomaps.com/pmtiles/) archive
(`data/tiles/basemap.pmtiles`), served via `pmtiles serve`
(`docker-compose.yml`, service "tiles").

```bash
./run.sh start tiles   # or: ./run.sh start frontend / start (=all)
```

`./run.sh start tiles` (and therefore also `start frontend`/`start` without
arguments) automatically builds `scripts/build_basemap_tiles.sh` when the
tileset is missing (can take 15–60+ minutes for a full DE+DK+SE build and
needs about 30GB of free disk space) and then waits until the tile server is
reachable. Manually equivalent:

```bash
./scripts/build_basemap_tiles.sh   # builds data/tiles/basemap.pmtiles
docker compose up -d tiles
```

Sprites and fonts (glyphs) deliberately stay on the public openfreemap.org
CDN (small, non-critical assets) — only the actual map data (roads, buildings,
land use, etc.) is self-hosted; see `frontend/src/components/Map.tsx`
(`buildBasemapStyle`) and the vendored style definition
`frontend/src/assets/liberty-style.json`.

To rebuild the tileset (e.g. after an OSM extract update):
`./scripts/build_basemap_tiles.sh --force`, then `./run.sh restart tiles`.

#### Frontend (Vite + React + MapLibre GL JS)

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

Further frontend commands: `npm --prefix frontend run build`,
`npm --prefix frontend run lint`, `npm --prefix frontend run typecheck`,
`npm --prefix frontend run test`.

## Documentation

- Functional specification: `docs/01-project-specifications.md` through
  `docs/06-open-points-contradictions.md`
- Implementation plan: `docs/07-implementation-plan.md` and `docs/plans/`
- Agent guidelines: `AGENTS.md`

Frontend (`frontend/`): TypeScript + MapLibre GL JS.
