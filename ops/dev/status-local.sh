#!/usr/bin/env bash
# Read-only status of the local app: database, backend (:8040, /health, /health/db), frontend
# (:3040), and the .runtime pid files. Exit 0 when everything is healthy, 1 otherwise.
set -euo pipefail

# shellcheck source=ops/dev/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ok=1
row() { printf '%-11s %-10s %s\n' "$1" "$2" "${3:-}"; }

describe_pid() {
  local file="$1" pattern="$2" pid
  pid="$(owned_pid "$file" "$pattern")"
  if [[ -n "$pid" ]]; then
    echo "PID $pid (start-local.sh)"
  elif [[ -f "$file" ]]; then
    echo "stale pid file ${file#"$ROOT"/}"
  else
    echo "no pid file"
  fi
}

# Database
detect_db_mode
if db_probe; then pg_state=healthy; else pg_state=stopped; ok=0; fi
case "$DB_MODE" in
  native)
    service="$(pg_service_state)"
    row Postgres "$pg_state" "native :$DB_PORT (launchd $PG_LABEL: ${service:-not loaded})"
    ;;
  docker) row Postgres "$pg_state" "docker compose :$DB_PORT ($(compose_db_health 2>/dev/null || echo 'docker not running'))" ;;
  external) row Postgres "$pg_state" "$DB_HOST:$DB_PORT (not managed)" ;;
esac

# Backend
backend_pid="$(describe_pid "$BACKEND_PID" "$BACKEND_PATTERN")"
if [[ -z "$(port_listener "$BACKEND_PORT")" ]]; then
  row Backend stopped ":$BACKEND_PORT not listening; $backend_pid"; ok=0
elif backend_ok; then
  row Backend healthy ":$BACKEND_PORT $backend_pid"
else
  row Backend unhealthy ":$BACKEND_PORT listening, /health failed; $backend_pid"; ok=0
fi
if backend_db_ok; then
  row Database connected "$BACKEND_URL/health/db"
else
  row Database unknown "$BACKEND_URL/health/db not ok"; ok=0
fi

# Frontend
frontend_pid="$(describe_pid "$FRONTEND_PID" "$FRONTEND_PATTERN")"
if [[ -z "$(port_listener "$FRONTEND_PORT")" ]]; then
  row Frontend stopped ":$FRONTEND_PORT not listening; $frontend_pid"; ok=0
else
  code="$(frontend_code 30)"
  if [[ "$code" == 200 ]]; then
    row Frontend healthy ":$FRONTEND_PORT $frontend_pid"
  else
    row Frontend unhealthy ":$FRONTEND_PORT HTTP $code; $frontend_pid"; ok=0
  fi
fi

# The Docker database is only the rollback copy while DATABASE_URL names the native service.
if [[ "$DB_MODE" == native ]] && command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  if [[ -n "$(cd "$ROOT" && docker compose ps -q --status running db 2>/dev/null)" ]]; then
    row "Docker DB" running "docker-compose.yml database is not used by the app (rollback copy; docker compose stop db)"
  fi
fi

[[ "$ok" == 1 ]]
