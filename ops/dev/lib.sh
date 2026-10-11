# Shared helpers for ops/dev/{start,stop,status}-local.sh. Sourced, never executed directly.
#
# Local development convention: frontend http://localhost:3040, backend http://127.0.0.1:8040,
# database = whatever the root .env DATABASE_URL names (native PostgreSQL launchd service on
# 127.0.0.1:5433 since Local-1, or the optional docker-compose.yml database when DATABASE_URL uses
# its published port). These scripts never touch the daily update agent, ingestion, or data.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME="$ROOT/.runtime"
VENV_PY="$ROOT/.venv/bin/python"
NVM_SH="${NVM_DIR:-$HOME/.nvm}/nvm.sh"

BACKEND_HOST=127.0.0.1
BACKEND_PORT=8040
BACKEND_URL="http://$BACKEND_HOST:$BACKEND_PORT"
FRONTEND_PORT=3040
FRONTEND_URL="http://localhost:$FRONTEND_PORT"
FRONTEND_API_BASE="http://127.0.0.1:$BACKEND_PORT"

BACKEND_PID="$RUNTIME/backend.pid"
FRONTEND_PID="$RUNTIME/frontend.pid"
BACKEND_LOG="$RUNTIME/backend.log"
FRONTEND_LOG="$RUNTIME/frontend.log"

# Regexes for the commands these scripts start (checked before any signal). npm rewrites its
# process title to "npm run dev -p 3040" (without the "--").
BACKEND_PATTERN="uvicorn industry_intelligence\.main:app --host $BACKEND_HOST --port $BACKEND_PORT( |$)"
FRONTEND_PATTERN="npm run dev (-- )?-p $FRONTEND_PORT( |$)"

PG_LABEL=local.industry-intelligence.postgresql
PG_PLIST="$HOME/Library/LaunchAgents/$PG_LABEL.plist"
GUI_DOMAIN="gui/$(id -u)"

info() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- database ---------------------------------------------------------------------------------

# Prints "<host> <port>" of DATABASE_URL as Settings resolves it (never the password).
db_endpoint() {
  "$VENV_PY" - <<'PY' 2>/dev/null
from sqlalchemy.engine import make_url
from industry_intelligence.config import get_settings
url = make_url(get_settings().database_url)
print(url.host or "localhost", url.port or 5432)
PY
}

# Exit 0 when the application role can run SELECT 1 against DATABASE_URL.
db_probe() {
  "$VENV_PY" - <<'PY' >/dev/null 2>&1
from sqlalchemy import create_engine, text
from industry_intelligence.config import get_settings
engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 3})
with engine.connect() as connection:
    connection.execute(text("SELECT 1"))
PY
}

# Sets DB_HOST, DB_PORT, and DB_MODE (native | docker | external).
detect_db_mode() {
  local endpoint compose_port
  endpoint="$(db_endpoint)" || die "cannot read DATABASE_URL from $ROOT/.env (is the backend installed in .venv?)"
  DB_HOST="${endpoint% *}"
  DB_PORT="${endpoint#* }"
  compose_port="$(sed -n 's/^POSTGRES_PORT=//p' "$ROOT/.env" 2>/dev/null | tail -1)"
  compose_port="${compose_port:-5432}"
  if [[ "$DB_PORT" == "$compose_port" ]]; then
    DB_MODE=docker
  elif [[ -f "$PG_PLIST" ]]; then
    DB_MODE=native
  else
    DB_MODE=external
  fi
}

pg_service_state() {
  { launchctl print "$GUI_DOMAIN/$PG_LABEL" 2>/dev/null || true; } | awk '/^\tstate = / {print $3; exit}'
}

compose_db_health() {
  local id
  id="$(cd "$ROOT" && docker compose ps -q db 2>/dev/null)" || return 1
  [[ -n "$id" ]] || { echo missing; return 0; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null
}

# --- processes ----------------------------------------------------------------------------------

# Query helpers print nothing (and succeed) when there is nothing to report, so they are safe in
# assignments under set -euo pipefail.
proc_start_time() { { ps -o lstart= -p "$1" 2>/dev/null || true; } | sed -e 's/^ *//' -e 's/ *$//'; }
proc_command() { ps -o command= -p "$1" 2>/dev/null || true; }

# Prints the PID recorded in a pid file when that process is alive, is the same process (start
# time recorded at launch), and still runs the expected command; otherwise prints nothing.
owned_pid() {
  local file="$1" pattern="$2" pid started
  [[ -f "$file" ]] || return 0
  pid="$(sed -n 1p "$file")"
  started="$(sed -n 2p "$file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  [[ "$(proc_start_time "$pid")" == "$started" ]] || return 0
  [[ "$(proc_command "$pid")" =~ $pattern ]] || return 0
  echo "$pid"
}

write_pid() { printf '%s\n%s\n' "$2" "$(proc_start_time "$2")" > "$1"; }

port_listener() { { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true; } | head -1; }

# Starts a command in the background in its own process group (pgid = pid), so stopping it
# signals exactly that process tree. Sets STARTED_PID.
start_detached() {
  local dir="$1" log="$2"; shift 2
  set -m
  (cd "$dir" && exec nohup "$@") >>"$log" 2>&1 </dev/null &
  STARTED_PID=$!
  set +m
}

# Sends TERM to the process group, then KILL after the grace period. Returns 0 once it is gone.
stop_group() {
  local pid="$1" i
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for i in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.5
  done
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  sleep 0.5
  ! kill -0 "$pid" 2>/dev/null
}

# --- HTTP -------------------------------------------------------------------------------------

backend_ok() { curl -fsS --max-time 3 "$BACKEND_URL/health" 2>/dev/null | grep -q '"status":"ok"'; }
backend_db_ok() {
  curl -fsS --max-time 5 "$BACKEND_URL/health/db" 2>/dev/null | grep -q '"database":"connected"'
}
frontend_code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time "${1:-10}" "$FRONTEND_URL/" 2>/dev/null || true
}
