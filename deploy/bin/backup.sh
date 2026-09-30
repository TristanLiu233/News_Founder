#!/usr/bin/env bash
# Daily PostgreSQL backup (Deployment-2A): pg_dump -Fc of the production database.
#
#   1. dump through the postgres container into <dir>/daily/.<name>.partial (mode 600),
#   2. check the custom-format header (PGDMP) and a non-trivial size, then publish it atomically
#      under its final name (never replacing an existing file),
#   3. keep the first backup of each UTC month as a hard link in <dir>/monthly/ (so daily cleanup
#      never removes it), then keep the newest BACKUP_DAILY_KEEP daily and BACKUP_MONTHLY_KEEP
#      monthly dumps,
#   4. hand the new file to the off-host hook, if one is installed.
#
# Exit: 0 backup stored and copied off-host; 1 backup failed (nothing new kept); 3 backup stored
# locally but no off-host copy (hook missing or failed). Never prints credentials: pg_dump uses the
# container's local socket as the database owner.
#
# Settings (environment; systemd drop-ins may override):
#   BACKUP_DIR            default /var/backups/industry-intelligence
#   BACKUP_DAILY_KEEP     default 14
#   BACKUP_MONTHLY_KEEP   default 6
#   BACKUP_REMOTE_HOOK    default /etc/industry-intelligence/hooks/backup-remote (executable,
#                         called with the new dump path; provider-specific, installed by the owner)
#   II_COMPOSE            default deploy/bin/ii-compose next to this script
set -Eeuo pipefail
shopt -s nullglob
export LC_ALL=C
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/industry-intelligence}"
DAILY_KEEP="${BACKUP_DAILY_KEEP:-14}"
MONTHLY_KEEP="${BACKUP_MONTHLY_KEEP:-6}"
REMOTE_HOOK="${BACKUP_REMOTE_HOOK:-/etc/industry-intelligence/hooks/backup-remote}"
COMPOSE="${II_COMPOSE:-$SCRIPT_DIR/ii-compose}"
MIN_BYTES=1024

log() { echo "backup: $*"; }

[[ "$DAILY_KEEP" =~ ^[1-9][0-9]*$ && "$MONTHLY_KEEP" =~ ^[0-9]+$ ]] \
    || { log "event=refused reason=invalid_retention"; exit 1; }

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/monthly"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
month="${stamp:0:6}"
name="industry-intelligence-$stamp.dump"
final="$BACKUP_DIR/daily/$name"
partial="$BACKUP_DIR/daily/.$name.partial"
trap 'rm -f "$partial"' EXIT

log "event=start file=$name"
if ! "$COMPOSE" exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
        >"$partial"; then
    log "event=failed reason=pg_dump"
    exit 1
fi
size="$(wc -c <"$partial" | tr -d ' ')"
if [[ "$size" -lt "$MIN_BYTES" || "$(head -c 5 "$partial")" != "PGDMP" ]]; then
    log "event=failed reason=invalid_dump bytes=$size"
    exit 1
fi
sync "$partial" 2>/dev/null || sync
# ln is atomic and never replaces an existing dump (which could be a monthly hard link).
if ! ln "$partial" "$final" 2>/dev/null; then
    log "event=failed reason=name_exists file=$name"
    exit 1
fi
rm -f "$partial"
log "event=stored file=$final bytes=$size"

# Monthly: the first dump of the month becomes a hard link, independent of daily cleanup.
if ! compgen -G "$BACKUP_DIR/monthly/industry-intelligence-${month}*.dump" >/dev/null; then
    ln "$final" "$BACKUP_DIR/monthly/$name"
    log "event=monthly file=$name"
fi

prune() { # prune <dir> <keep>: names sort chronologically (UTC timestamps, C collation)
    local dir="$1" keep="$2" i
    local files=("$dir"/industry-intelligence-*.dump)
    local excess=$(( ${#files[@]} - keep ))
    for (( i = 0; i < excess; i++ )); do
        rm -f -- "${files[$i]}"
        log "event=pruned file=${files[$i]}"
    done
}
prune "$BACKUP_DIR/daily" "$DAILY_KEEP"
prune "$BACKUP_DIR/monthly" "$MONTHLY_KEEP"

if [[ ! -x "$REMOTE_HOOK" ]]; then
    log "event=finish status=local_only reason=no_offhost_hook hook=$REMOTE_HOOK"
    exit 3
fi
if ! "$REMOTE_HOOK" "$final"; then
    log "event=finish status=local_only reason=offhost_hook_failed"
    exit 3
fi
log "event=finish status=ok"
