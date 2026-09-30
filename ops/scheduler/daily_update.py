"""Scheduler-1 entry point for the macOS launchd agent.

Runs exactly one ``intelligence-update --ai-limit 12`` from the repository's virtualenv
(Product-1: bounded AI, at most 12 Events and so at most 24 primary-locale provider requests,
cached artifacts reused, provider preflighted, enabled after a verified 10-Event live smoke;
before Product-1 it ran ``--skip-ai``, which stays the rollback: set ``AI_ARGS`` back). The
launchd job starts this file with ``.venv/bin/python`` directly (not a shell: macOS privacy
protection denies a launchd-started ``/bin/sh`` access to ``~/Documents``). Configuration comes
only from the root ``.env`` through ``Settings``; the Operations-2 advisory lock, precheck,
postcheck, ``update_runs``, and ``source_attempts`` stay authoritative. Extra arguments are
appended to the command (used only for the ``--plan`` preflight). Output goes to launchd's
``StandardOutPath``; this wrapper adds one start and one finish line.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ZONE = "Australia/Sydney"
EX_CONFIG = 78
# Bounded scheduled AI (never unbounded; the CLI refuses a cap above 15).
AI_ARGS = ["--ai-limit", "12"]


def log(event, **fields):
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    extra = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{stamp} scheduler=daily-update event={event} {extra}".rstrip(), flush=True)


def system_zone():
    try:
        return os.readlink("/etc/localtime").split("/zoneinfo/", 1)[-1]
    except OSError:
        return "unknown"


def main(extra):
    # launchd's StartCalendarInterval fires in the system time zone, so the 10:00 contract holds
    # only while the system zone is Australia/Sydney. Refuse rather than run at another local time.
    zone = system_zone()
    if zone != EXPECTED_ZONE:
        log("refused", reason="system_timezone_not_expected", zone=zone)
        return EX_CONFIG
    command = [sys.executable, "-m", "industry_intelligence.cli", "intelligence-update"]
    command += [*AI_ARGS, *extra]
    env = {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }
    log("start", zone=zone, args=" ".join(command[3:]))
    status = subprocess.run(command, cwd=ROOT / "backend", env=env, check=False).returncode
    log("finish", exit=status)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
