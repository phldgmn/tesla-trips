#!/usr/bin/env bash
# run.sh — Centralized service runner for Tesla-Tripplaner
#
# Usage:
#   ./run.sh start   [backend|frontend|graphhopper|tiles|all]  (default: all)
#   ./run.sh stop    [backend|frontend|graphhopper|tiles|all]
#   ./run.sh restart [backend|frontend|graphhopper|tiles|all]
#   ./run.sh status
#
# Manages backend (uvicorn on :8000), frontend (Vite on :3000), the local
# GraphHopper routing container (docker-compose.yml, :8989) and the local
# vector-tile basemap server (docker-compose.yml "tiles" service, :8081,
# see scripts/build_basemap_tiles.sh). `start backend`/`start all` first
# ensure OrbStack's Docker daemon is running (auto-starting it if needed)
# and then bring up/health-check the GraphHopper container before the
# backend starts, since the backend depends on GraphHopper for real
# routing. `start frontend`/`start all` likewise bring up the tiles
# server first, since the map needs it to render the basemap. Stops
# already-running services before starting, so it's safe to call
# repeatedly. All output is prefixed with [BACKEND] / [FRONTEND] for
# clear identification in a shared terminal session.

set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || dirname "$0")"

RUN_DIR=".run"
PID_BACKEND="$RUN_DIR/backend.pid"
PID_FRONTEND="$RUN_DIR/frontend.pid"
LOG_BACKEND="$RUN_DIR/backend.log"
LOG_FRONTEND="$RUN_DIR/frontend.log"

PORT_BACKEND=8000
PORT_FRONTEND=3000
PORT_GRAPHHOPPER=8989
PORT_TILES=8081
OSM_EXTRACT="data/de-dk-se.osm.pbf"
GRAPHHOPPER_CONTAINER="tesla-trips-graphhopper"
TILES_OUTPUT="data/tiles/basemap.pmtiles"

# ── Colors ────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  NC='\033[0m' # No Colour
else
  RED=''
  GREEN=''
  YELLOW=''
  NC=''
fi

# ── Helpers ───────────────────────────────────────────────────────────────

info() { printf "${GREEN}%s${NC}\n" "$*"; }
warn() { printf "${YELLOW}%s${NC}\n" "$*"; }
err() { printf "${RED}%s${NC}\n" "$*" >&2; }

run_dir_init() { mkdir -p "$RUN_DIR"; }

pid_alive() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null)" || return 1
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

port_pid() {
  local port="$1"
  lsof -ti ":$port" -P -n 2>/dev/null || true
}

kill_by_pidfile() {
  local pid_file="$1" label="$2"
  if pid_alive "$pid_file"; then
    local pid
    pid="$(cat "$pid_file")"
    warn "${label}: stopping PID $pid …"
    kill "$pid" 2>/dev/null || true
    # Grace period, then force-kill
    for _ in $(seq 1 5); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pid_file"
  fi
}

kill_by_port() {
  local port="$1" label="$2"
  local pids
  pids="$(port_pid "$port")"
  if [[ -n "$pids" ]]; then
    warn "${label}: killing process(es) on port $port (PIDs: $(echo "$pids" | tr '\n' ' '))…"
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 0.5
    # Force-kill survivors
    local survivors
    survivors="$(port_pid "$port")"
    [[ -n "$survivors" ]] && echo "$survivors" | xargs kill -9 2>/dev/null || true
  fi
}

# ── Docker / OrbStack / GraphHopper ──────────────────────────────────────

ensure_orbstack() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi
  info "ORBSTACK: Docker-Daemon nicht erreichbar — starte OrbStack …"
  if ! open -a OrbStack 2>/dev/null; then
    err "ORBSTACK: konnte OrbStack nicht starten (ist es installiert?)"
    return 1
  fi
  local waited=0
  local max_wait=60
  while ! docker info >/dev/null 2>&1; do
    if ((waited >= max_wait)); then
      err "ORBSTACK: Docker-Daemon nach ${max_wait}s nicht erreichbar"
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
  info "ORBSTACK: Docker-Daemon bereit"
}

graphhopper_health() {
  docker inspect -f '{{.State.Health.Status}}' "$GRAPHHOPPER_CONTAINER" 2>/dev/null || echo "missing"
}

start_graphhopper() {
  ensure_orbstack || return 1
  if [[ ! -f "$OSM_EXTRACT" ]]; then
    info "GRAPHHOPPER: OSM-Extrakt fehlt ($OSM_EXTRACT) — bereite DE+DK+SE-Datensatz vor …"
    info "GRAPHHOPPER: Download (~mehrere GB) + Merge kann je nach Verbindung länger dauern."
    if ! ./scripts/prepare_osm_extract.sh; then
      err "GRAPHHOPPER: Vorbereitung des OSM-Extrakts fehlgeschlagen."
      return 1
    fi
  fi
  info "GRAPHHOPPER: starting via docker compose …"
  docker compose up -d graphhopper
  info "GRAPHHOPPER: waiting for healthy container (Import des DE+DK+SE-Graphen kann beim ersten Start deutlich länger als bei kleinen Extrakten dauern) …"
  local waited=0
  local max_wait="${GRAPHHOPPER_HEALTH_TIMEOUT:-3600}"
  local health
  while true; do
    health="$(graphhopper_health)"
    if [[ "$health" == "healthy" ]]; then
      info "GRAPHHOPPER: running and healthy (http://localhost:$PORT_GRAPHHOPPER)"
      return 0
    fi
    if [[ "$health" == "unhealthy" ]]; then
      err "GRAPHHOPPER: Container meldet unhealthy — siehe 'docker compose logs graphhopper'"
      return 1
    fi
    if ((waited >= max_wait)); then
      err "GRAPHHOPPER: nach ${max_wait}s nicht healthy — siehe 'docker compose logs graphhopper'"
      return 1
    fi
    sleep 5
    waited=$((waited + 5))
  done
}

stop_graphhopper() {
  if docker compose ps -q graphhopper 2>/dev/null | grep -q .; then
    warn "GRAPHHOPPER: stopping container …"
    docker compose stop graphhopper
    info "GRAPHHOPPER: stopped"
  fi
}

tiles_health() {
  # go-pmtiles hat keinen eingebauten Docker-HEALTHCHECK — Bereitschaft
  # daher per HTTP-Poll der TileJSON-Antwort prüfen (siehe
  # docker-compose.yml, Service "tiles").
  if curl -sf -o /dev/null "http://localhost:$PORT_TILES/basemap.json"; then
    echo "healthy"
  elif docker compose ps -q tiles 2>/dev/null | grep -q .; then
    echo "starting"
  else
    echo "missing"
  fi
}

start_tiles() {
  ensure_orbstack || return 1
  if [[ ! -f "$TILES_OUTPUT" ]]; then
    info "TILES: Basemap-Tileset fehlt ($TILES_OUTPUT) — baue es aus $OSM_EXTRACT …"
    info "TILES: Build (Planetiler, ganz DE+DK+SE) kann 15-60+ Minuten dauern."
    if ! ./scripts/build_basemap_tiles.sh; then
      err "TILES: Build des Basemap-Tilesets fehlgeschlagen."
      return 1
    fi
  fi
  info "TILES: starting via docker compose …"
  docker compose up -d tiles
  info "TILES: waiting for healthy container …"
  local waited=0
  local max_wait=60
  while true; do
    if [[ "$(tiles_health)" == "healthy" ]]; then
      info "TILES: running and healthy (http://localhost:$PORT_TILES)"
      return 0
    fi
    if ((waited >= max_wait)); then
      err "TILES: nach ${max_wait}s nicht healthy — siehe 'docker compose logs tiles'"
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

stop_tiles() {
  if docker compose ps -q tiles 2>/dev/null | grep -q .; then
    warn "TILES: stopping container …"
    docker compose stop tiles
    info "TILES: stopped"
  fi
}

# ── Actions ───────────────────────────────────────────────────────────────

stop_backend() {
  if pid_alive "$PID_BACKEND"; then
    kill_by_pidfile "$PID_BACKEND" "BACKEND"
    info "BACKEND: stopped"
  else
    # Fallback: kill orphans on the backend port
    kill_by_port "$PORT_BACKEND" "BACKEND (orphan)"
    rm -f "$PID_BACKEND"
  fi
}

stop_frontend() {
  if pid_alive "$PID_FRONTEND"; then
    kill_by_pidfile "$PID_FRONTEND" "FRONTEND"
    info "FRONTEND: stopped"
  else
    kill_by_port "$PORT_FRONTEND" "FRONTEND (orphan)"
    rm -f "$PID_FRONTEND"
  fi
}

start_backend() {
  stop_backend
  run_dir_init
  start_graphhopper || {
    err "BACKEND: GraphHopper nicht bereit — Backend-Start abgebrochen."
    return 1
  }
  info "BACKEND: starting uvicorn on :$PORT_BACKEND …"
  # Use exec -a so the process has a recognisable name
  uv run uvicorn tripplanner.trip_input.api:app \
    --host 0.0.0.0 --port "$PORT_BACKEND" \
    --reload \
    >"$LOG_BACKEND" 2>&1 &
  echo $! >"$PID_BACKEND"
  # Wait briefly for the port or a crash
  sleep 1
  if pid_alive "$PID_BACKEND"; then
    info "BACKEND: running (PID $(cat "$PID_BACKEND"), log: $LOG_BACKEND)"
  else
    err "BACKEND: failed to start — check $LOG_BACKEND"
    return 1
  fi
}

start_frontend() {
  stop_frontend
  run_dir_init
  start_tiles || {
    err "FRONTEND: Basemap-Tiles nicht bereit — Frontend-Start abgebrochen."
    return 1
  }
  info "FRONTEND: starting Vite dev server on :$PORT_FRONTEND …"
  npm --prefix frontend run dev \
    >"$LOG_FRONTEND" 2>&1 &
  echo $! >"$PID_FRONTEND"
  sleep 1
  if pid_alive "$PID_FRONTEND"; then
    info "FRONTEND: running (PID $(cat "$PID_FRONTEND"), log: $LOG_FRONTEND)"
  else
    err "FRONTEND: failed to start — check $LOG_FRONTEND"
    return 1
  fi
}

status() {
  local rc=0
  echo "─── Service Status ───"
  local gh_health
  gh_health="$(graphhopper_health)"
  if [[ "$gh_health" == "healthy" ]]; then
    printf '%s● GRAPHHOPPER%s  healthy  http://localhost:%s\n' "$GREEN" "$NC" "$PORT_GRAPHHOPPER"
  elif [[ "$gh_health" == "missing" ]]; then
    printf '%s○ GRAPHHOPPER%s  not running\n' "$RED" "$NC"
    rc=1
  else
    printf '%s◐ GRAPHHOPPER%s  %s\n' "$YELLOW" "$NC" "$gh_health"
    rc=1
  fi
  local tiles_st
  tiles_st="$(tiles_health)"
  if [[ "$tiles_st" == "healthy" ]]; then
    printf '%s● TILES%s  healthy  http://localhost:%s\n' "$GREEN" "$NC" "$PORT_TILES"
  elif [[ "$tiles_st" == "missing" ]]; then
    printf '%s○ TILES%s  not running\n' "$RED" "$NC"
    rc=1
  else
    printf '%s◐ TILES%s  %s\n' "$YELLOW" "$NC" "$tiles_st"
    rc=1
  fi
  if pid_alive "$PID_BACKEND"; then
    printf '%s● BACKEND%s  PID %s  http://localhost:%s\n' "$GREEN" "$NC" "$(cat "$PID_BACKEND")" "$PORT_BACKEND"
  else
    printf '%s○ BACKEND%s  not running\n' "$RED" "$NC"
    rc=1
  fi
  if pid_alive "$PID_FRONTEND"; then
    printf '%s● FRONTEND%s PID %s  http://localhost:%s\n' "$GREEN" "$NC" "$(cat "$PID_FRONTEND")" "$PORT_FRONTEND"
  else
    printf '%s○ FRONTEND%s not running\n' "$RED" "$NC"
    rc=1
  fi
  return "$rc"
}

# ── CLI ───────────────────────────────────────────────────────────────────

cmd="${1:-start}"
target="${2:-all}"

case "$cmd" in
start)
  case "$target" in
  backend) start_backend ;;
  frontend) start_frontend ;;
  graphhopper) start_graphhopper ;;
  tiles) start_tiles ;;
  all)
    start_backend
    start_frontend
    ;;
  *)
    echo "Usage: $0 start [backend|frontend|graphhopper|tiles|all]"
    exit 1
    ;;
  esac
  ;;
stop)
  case "$target" in
  backend) stop_backend ;;
  frontend) stop_frontend ;;
  graphhopper) stop_graphhopper ;;
  tiles) stop_tiles ;;
  all)
    stop_frontend
    stop_backend
    ;;
  *)
    echo "Usage: $0 stop [backend|frontend|graphhopper|tiles|all]"
    exit 1
    ;;
  esac
  info "All services stopped."
  ;;
restart)
  case "$target" in
  backend) start_backend ;;
  frontend) start_frontend ;;
  graphhopper)
    stop_graphhopper
    start_graphhopper
    ;;
  tiles)
    stop_tiles
    start_tiles
    ;;
  all)
    stop_frontend
    stop_backend
    info "─── Restarting ───"
    start_backend
    start_frontend
    ;;
  *)
    echo "Usage: $0 restart [backend|frontend|graphhopper|tiles|all]"
    exit 1
    ;;
  esac
  ;;
status) status ;;
*)
  echo "Usage: $0 {start|stop|restart|status} [backend|frontend|graphhopper|tiles|all]"
  exit 1
  ;;
esac
