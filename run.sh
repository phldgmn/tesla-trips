#!/usr/bin/env bash
# run.sh — Centralized service runner for Tesla-Tripplaner
#
# Usage:
#   ./run.sh start   [backend|frontend|all]  (default: all)
#   ./run.sh stop    [backend|frontend|all]
#   ./run.sh restart [backend|frontend|all]
#   ./run.sh status
#
# Manages backend (uvicorn on :8000) and frontend (Vite on :3000).
# Stops already-running services before starting, so it's safe to call
# repeatedly. All output is prefixed with [BACKEND] / [FRONTENT] for
# clear identification in a shared terminal session.

set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$0")")"

RUN_DIR=".run"
PID_BACKEND="$RUN_DIR/backend.pid"
PID_FRONTEND="$RUN_DIR/frontend.pid"
LOG_BACKEND="$RUN_DIR/backend.log"
LOG_FRONTEND="$RUN_DIR/frontend.log"

PORT_BACKEND=8000
PORT_FRONTEND=3000

# ── Colors ────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  NC='\033[0m' # No Colour
else
  RED=''; GREEN=''; YELLOW=''; NC=''
fi

# ── Helpers ───────────────────────────────────────────────────────────────

info()  { printf "${GREEN}%s${NC}\n" "$*"; }
warn()  { printf "${YELLOW}%s${NC}\n" "$*"; }
err()   { printf "${RED}%s${NC}\n" "$*" >&2; }

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
  info "BACKEND: starting uvicorn on :$PORT_BACKEND …"
  # Use exec -a so the process has a recognisable name
  uv run uvicorn tripplanner.trip_input.api:app \
    --host 0.0.0.0 --port "$PORT_BACKEND" \
    --reload \
    > "$LOG_BACKEND" 2>&1 &
  echo $! > "$PID_BACKEND"
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
  info "FRONTEND: starting Vite dev server on :$PORT_FRONTEND …"
  npm --prefix frontend run dev \
    > "$LOG_FRONTEND" 2>&1 &
  echo $! > "$PID_FRONTEND"
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
      backend)  start_backend ;;
      frontend) start_frontend ;;
      all)      start_backend; start_frontend ;;
      *)        echo "Usage: $0 start [backend|frontend|all]"; exit 1 ;;
    esac
    ;;
  stop)
    case "$target" in
      backend)  stop_backend  ;;
      frontend) stop_frontend ;;
      all)      stop_frontend; stop_backend ;;
      *)        echo "Usage: $0 stop [backend|frontend|all]"; exit 1 ;;
    esac
    info "All services stopped."
    ;;
  restart)
    case "$target" in
      backend)  start_backend  ;;
      frontend) start_frontend ;;
      all)
        stop_frontend
        stop_backend
        info "─── Restarting ───"
        start_backend
        start_frontend
        ;;
      *)        echo "Usage: $0 restart [backend|frontend|all]"; exit 1 ;;
    esac
    ;;
  status) status ;;
  *)
    echo "Usage: $0 {start|stop|restart|status} [backend|frontend|all]"
    exit 1
    ;;
esac
