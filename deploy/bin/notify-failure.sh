#!/usr/bin/env bash
# Failure notification (Deployment-2A), run by industry-intelligence-notify@<unit>.service through
# OnFailure= of the update, backup, and freshness units.
#
# Builds a short plain-text message (host, unit, result, exit status, recent journal lines of the
# failed unit) and passes it on stdin to the owner's notification hook, called with a one-line
# subject as $1. The hook is provider-specific (email, chat webhook, …) and is installed on the
# server only; nothing about the channel is in the repository.
#
#   NOTIFY_HOOK  default /etc/industry-intelligence/hooks/notify (executable)
#
# Exit 0 when the hook accepted the message; 1 when no hook is installed or it failed (the
# failure then stays visible in `systemctl --failed` and the journal).
set -uo pipefail

unit="${1:?usage: notify-failure.sh <failed unit>}"
hook="${NOTIFY_HOOK:-/etc/industry-intelligence/hooks/notify}"
host="$(hostname)"
result="$(systemctl show --property=Result --value "$unit" 2>/dev/null || echo unknown)"
status="$(systemctl show --property=ExecMainStatus --value "$unit" 2>/dev/null || echo unknown)"
subject="[industry-intelligence] $unit failed on $host (result=$result, exit=$status)"

message() {
    echo "$subject"
    echo
    echo "Exit codes of the daily update: 1 failed, 3 degraded, 4 already running."
    echo "Freshness: non-zero means no successful update started within 26 hours."
    echo "Backup: 1 failed, 3 stored locally without an off-host copy."
    echo
    echo "Recent log lines:"
    journalctl --unit "$unit" --since "-3h" --no-pager --output cat 2>/dev/null | tail -n 40
}

if [[ ! -x "$hook" ]]; then
    echo "notify: event=unsent reason=no_hook hook=$hook unit=$unit result=$result exit=$status"
    exit 1
fi
if ! message | "$hook" "$subject"; then
    echo "notify: event=unsent reason=hook_failed unit=$unit"
    exit 1
fi
echo "notify: event=sent unit=$unit result=$result exit=$status"
