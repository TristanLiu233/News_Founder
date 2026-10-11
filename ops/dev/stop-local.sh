#!/usr/bin/env bash
# Stop the frontend and backend started by start-local.sh (only the processes recorded in
# .runtime/*.pid and verified by start time and command), then the database:
#   - docker-compose database (DATABASE_URL on the compose port): docker compose stop (volume kept)
#   - native PostgreSQL launchd service: left running by default, because the 10:00 daily update
#     needs it; --with-db unloads it until the next login or start-local.sh.
# Idempotent: running it again reports "not running".
set -euo pipefail

# shellcheck source=ops/dev/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

with_db=0
case "${1:-}" in
  "") ;;
  --with-db) with_db=1 ;;
  -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
  *) die "unknown option: $1 (usage: stop-local.sh [--with-db])" ;;
esac

stop_service() {
  local name="$1" file="$2" pattern="$3" port="$4" pid listener
  pid="$(owned_pid "$file" "$pattern")"
  if [[ -n "$pid" ]]; then
    if stop_group "$pid"; then
      rm -f "$file"
      info "$name stopped (PID $pid)"
    else
      die "$name PID $pid did not exit"
    fi
  elif [[ -f "$file" ]]; then
    rm -f "$file"
    info "$name not running (removed stale pid file)"
  else
    info "$name not running"
  fi
  listener="$(port_listener "$port")"
  if [[ -n "$listener" ]]; then
    warn "port $port is still in use by PID $listener ($(proc_command "$listener")), which start-local.sh did not start; left running"
  fi
}

stop_service frontend "$FRONTEND_PID" "$FRONTEND_PATTERN" "$FRONTEND_PORT"
stop_service backend "$BACKEND_PID" "$BACKEND_PATTERN" "$BACKEND_PORT"

detect_db_mode
case "$DB_MODE" in
  docker)
    if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
      (cd "$ROOT" && docker compose stop db >/dev/null 2>&1)
      info "postgres stopped (docker compose stop; volume kept)"
    else
      info "postgres not running (Docker is not running)"
    fi
    ;;
  native)
    if [[ "$with_db" == 1 ]]; then
      if [[ -n "$(pg_service_state)" ]]; then
        launchctl bootout "$GUI_DOMAIN/$PG_LABEL"
        info "postgres stopped (launchd service unloaded until next login or start-local.sh)"
        warn "the 10:00 daily update fails while PostgreSQL is stopped"
      else
        info "postgres not running"
      fi
    else
      info "postgres left running (native launchd service on $DB_HOST:$DB_PORT, used by the 10:00 daily update; --with-db stops it)"
    fi
    ;;
  external)
    info "postgres not managed by these scripts ($DB_HOST:$DB_PORT)"
    ;;
esac
