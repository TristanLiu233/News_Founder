#!/usr/bin/env bash
# Start the local app for viewing: database (if needed) → FastAPI on 127.0.0.1:8040 → Next.js dev
# server on localhost:3040. Safe to re-run: running services are reused, never started twice.
# It only serves persisted data; it never ingests, refreshes, or touches the 10:00 daily update.
set -euo pipefail

# shellcheck source=ops/dev/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

DB_TIMEOUT=60
BACKEND_TIMEOUT=60
FRONTEND_TIMEOUT=120

show_log_tail() {
  printf '\n--- last 40 lines of %s ---\n' "${1#"$ROOT"/}" >&2
  tail -n 40 "$1" >&2 2>/dev/null || true
  printf -- '---\n' >&2
}

# --- prerequisites ------------------------------------------------------------------------------

for tool in curl lsof launchctl; do
  command -v "$tool" >/dev/null || die "$tool is required"
done
[[ -x "$VENV_PY" ]] || die "missing $VENV_PY (create the virtualenv: see README \"Setup and commands\")"
[[ -f "$ROOT/.env" ]] || die "missing $ROOT/.env (cp .env.example .env and set DATABASE_URL)"
[[ -f "$NVM_SH" ]] || die "missing $NVM_SH (nvm is used to provide node/npm)"
[[ -x "$ROOT/frontend/node_modules/.bin/next" ]] || die "frontend dependencies missing: cd frontend && npm ci"
mkdir -p "$RUNTIME"

# --- database -----------------------------------------------------------------------------------

detect_db_mode
case "$DB_MODE" in
  native)
    if db_probe; then
      info "postgres     ready (native launchd service, $DB_HOST:$DB_PORT)"
    else
      if [[ -z "$(pg_service_state)" ]]; then
        info "postgres     loading launchd service $PG_LABEL"
        launchctl bootstrap "$GUI_DOMAIN" "$PG_PLIST"
      else
        info "postgres     starting launchd service $PG_LABEL"
        launchctl kickstart "$GUI_DOMAIN/$PG_LABEL"
      fi
    fi
    ;;
  docker)
    command -v docker >/dev/null || die "DATABASE_URL uses the docker-compose port $DB_PORT but docker is not installed"
    docker info >/dev/null 2>&1 || die "Docker is not running (start Docker Desktop, then re-run)"
    (cd "$ROOT" && docker compose up -d db)
    info "postgres     waiting for the compose container to become healthy"
    deadline=$((SECONDS + DB_TIMEOUT))
    while ((SECONDS < deadline)); do
      [[ "$(compose_db_health)" == healthy ]] && break
      sleep 1
    done
    [[ "$(compose_db_health)" == healthy ]] || die "compose database not healthy after ${DB_TIMEOUT}s (docker compose logs db)"
    ;;
  external)
    info "postgres     DATABASE_URL points at $DB_HOST:$DB_PORT (not managed by these scripts)"
    ;;
esac

if ! db_probe; then
  deadline=$((SECONDS + DB_TIMEOUT))
  while ((SECONDS < deadline)); do
    sleep 1
    db_probe && break
  done
  db_probe || die "database $DB_HOST:$DB_PORT not reachable after ${DB_TIMEOUT}s (check DATABASE_URL; native service log: ~/Library/Logs/industry-intelligence/postgresql.log)"
fi
info "postgres     connected ($DB_MODE, $DB_HOST:$DB_PORT)"

# --- backend ------------------------------------------------------------------------------------

wait_backend() {
  local pid="$1" deadline=$((SECONDS + BACKEND_TIMEOUT))
  while ((SECONDS < deadline)); do
    if backend_ok && backend_db_ok; then return 0; fi
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    sleep 1
  done
  return 1
}

pid="$(owned_pid "$BACKEND_PID" "$BACKEND_PATTERN")"
if [[ -n "$pid" ]]; then
  info "backend      already running (PID $pid), reusing"
  wait_backend "$pid" || { show_log_tail "$BACKEND_LOG"; die "backend PID $pid is running but $BACKEND_URL/health(/db) is not healthy"; }
else
  rm -f "$BACKEND_PID"
  listener="$(port_listener "$BACKEND_PORT")"
  if [[ -n "$listener" ]]; then
    if [[ "$(proc_command "$listener")" =~ $BACKEND_PATTERN ]] && backend_ok; then
      warn "backend on :$BACKEND_PORT was started outside start-local.sh (PID $listener); reusing it, stop-local.sh will not stop it"
    else
      die "port $BACKEND_PORT is in use by PID $listener ($(proc_command "$listener")); free it first"
    fi
  else
    printf '\n==== %s start-local.sh ====\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" >>"$BACKEND_LOG"
    start_detached "$ROOT/backend" "$BACKEND_LOG" \
      "$VENV_PY" -m uvicorn industry_intelligence.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
    write_pid "$BACKEND_PID" "$STARTED_PID"
    info "backend      starting (PID $STARTED_PID)"
    if ! wait_backend "$STARTED_PID"; then
      show_log_tail "$BACKEND_LOG"
      reason="exited during startup"
      if kill -0 "$STARTED_PID" 2>/dev/null; then
        reason="did not become healthy on $BACKEND_URL within ${BACKEND_TIMEOUT}s"
        stop_group "$STARTED_PID" || true
      fi
      rm -f "$BACKEND_PID"
      die "backend $reason; frontend not started"
    fi
  fi
fi
backend_db_ok || die "backend $BACKEND_URL/health/db is not connected"
info "backend      healthy ($BACKEND_URL, /health and /health/db ok)"

# --- frontend configuration ---------------------------------------------------------------------

# Only the NEXT_PUBLIC_API_BASE_URL line is managed; every other line of .env.local is kept.
env_local="$ROOT/frontend/.env.local"
env_changed=0
if [[ ! -f "$env_local" ]]; then
  printf 'NEXT_PUBLIC_API_BASE_URL=%s\n' "$FRONTEND_API_BASE" > "$env_local"
  info "frontend     created frontend/.env.local (NEXT_PUBLIC_API_BASE_URL=$FRONTEND_API_BASE)"
  env_changed=1
else
  current="$(sed -n 's/^[[:space:]]*NEXT_PUBLIC_API_BASE_URL=//p' "$env_local" | tail -1)"
  if [[ "$current" != "$FRONTEND_API_BASE" ]]; then
    tmp="$(mktemp "$RUNTIME/env-local.XXXXXX")"
    awk -v value="$FRONTEND_API_BASE" '
      /^[[:space:]]*NEXT_PUBLIC_API_BASE_URL=/ { if (!done) { print "NEXT_PUBLIC_API_BASE_URL=" value; done = 1 }; next }
      { print }
      END { if (!done) print "NEXT_PUBLIC_API_BASE_URL=" value }
    ' "$env_local" > "$tmp"
    cat "$tmp" > "$env_local" && rm -f "$tmp"
    info "frontend     updated frontend/.env.local NEXT_PUBLIC_API_BASE_URL: '${current:-<unset>}' -> '$FRONTEND_API_BASE'"
    env_changed=1
  fi
fi

# --- frontend -----------------------------------------------------------------------------------

set +eu
# shellcheck disable=SC1090
source "$NVM_SH" >/dev/null 2>&1
set -eu
command -v node >/dev/null && command -v npm >/dev/null || die "node/npm not available after sourcing $NVM_SH"
info "frontend     node $(node --version), npm $(npm --version)"

wait_frontend() {
  local pid="$1" code deadline=$((SECONDS + FRONTEND_TIMEOUT))
  while ((SECONDS < deadline)); do
    code="$(frontend_code 30)"
    [[ "$code" == 200 ]] && return 0
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    sleep 1
  done
  return 1
}

pid="$(owned_pid "$FRONTEND_PID" "$FRONTEND_PATTERN")"
if [[ -n "$pid" ]]; then
  info "frontend     already running (PID $pid), reusing"
  [[ "$env_changed" == 0 ]] || warn "frontend/.env.local changed; restart the frontend (stop-local.sh, start-local.sh) to apply it"
  wait_frontend "$pid" || { show_log_tail "$FRONTEND_LOG"; die "frontend PID $pid is running but $FRONTEND_URL does not answer 200"; }
else
  rm -f "$FRONTEND_PID"
  listener="$(port_listener "$FRONTEND_PORT")"
  if [[ -n "$listener" ]]; then
    if [[ "$(proc_command "$listener")" == *next-server* ]] && [[ "$(frontend_code 30)" == 200 ]]; then
      warn "frontend on :$FRONTEND_PORT was started outside start-local.sh (PID $listener); reusing it, stop-local.sh will not stop it"
    else
      die "port $FRONTEND_PORT is in use by PID $listener ($(proc_command "$listener")); free it first"
    fi
  else
    printf '\n==== %s start-local.sh ====\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" >>"$FRONTEND_LOG"
    start_detached "$ROOT/frontend" "$FRONTEND_LOG" npm run dev -- -p "$FRONTEND_PORT"
    write_pid "$FRONTEND_PID" "$STARTED_PID"
    info "frontend     starting (PID $STARTED_PID; the first page compile can take a while)"
    if ! wait_frontend "$STARTED_PID"; then
      show_log_tail "$FRONTEND_LOG"
      reason="exited during startup"
      if kill -0 "$STARTED_PID" 2>/dev/null; then
        reason="did not answer 200 on $FRONTEND_URL within ${FRONTEND_TIMEOUT}s"
        stop_group "$STARTED_PID" || true
      fi
      rm -f "$FRONTEND_PID"
      die "frontend $reason"
    fi
  fi
fi
info "frontend     healthy ($FRONTEND_URL)"

cat <<EOF

Local app is ready

Frontend:  $FRONTEND_URL
Backend:   $BACKEND_URL
Health:    $BACKEND_URL/health  ·  $BACKEND_URL/health/db
Database:  $DB_MODE, $DB_HOST:$DB_PORT
Logs:      .runtime/frontend.log
           .runtime/backend.log

Stop with ./ops/dev/stop-local.sh · status with ./ops/dev/status-local.sh
EOF
