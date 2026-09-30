"""Deployment-2A backup script: atomic publication, monthly hard links, retention, off-host hook,
and exit codes, with a stub in place of docker compose (no database, no Docker)."""

import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "bin" / "backup.sh"
DUMP = b"PGDMP" + b"\0" * 4096


def executable(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def env(tmp_path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes(DUMP)
    stub = executable(tmp_path / "compose", f'cat "{payload}"\n')
    return {
        **os.environ,
        "BACKUP_DIR": str(tmp_path / "backups"),
        "II_COMPOSE": str(stub),
        "BACKUP_REMOTE_HOOK": str(tmp_path / "no-hook"),
    }


def run(env, **extra):
    return subprocess.run(
        ["bash", str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True, check=False
    )


def dumps(env, kind):
    return sorted(p.name for p in (Path(env["BACKUP_DIR"]) / kind).glob("*.dump"))


def old(env, kind, count, year=2020):
    directory = Path(env["BACKUP_DIR"]) / kind
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        month = index % 12 + 1
        name = f"industry-intelligence-{year + index // 12}{month:02d}01T000000Z.dump"
        (directory / name).write_bytes(DUMP)


def test_stores_dump_and_monthly_link_but_reports_missing_offhost_copy(env):
    result = run(env)

    assert result.returncode == 3, result.stdout
    [daily] = dumps(env, "daily")
    assert dumps(env, "monthly") == [daily]
    this_month = datetime.now(UTC).strftime("%Y%m")
    assert daily.startswith(f"industry-intelligence-{this_month}")
    path = Path(env["BACKUP_DIR"]) / "daily" / daily
    assert path.read_bytes() == DUMP
    assert path.stat().st_nlink == 2  # the monthly copy is a hard link
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert "no_offhost_hook" in result.stdout
    assert not list((Path(env["BACKUP_DIR"]) / "daily").glob(".*partial"))


def test_offhost_hook_receives_the_new_dump(env, tmp_path):
    received = tmp_path / "received"
    hook = executable(tmp_path / "hook", f'echo "$1" > "{received}"\n')

    result = run(env, BACKUP_REMOTE_HOOK=str(hook))

    assert result.returncode == 0, result.stdout
    assert Path(received.read_text().strip()).name == dumps(env, "daily")[0]


def test_failing_offhost_hook_keeps_the_local_backup(env, tmp_path):
    hook = executable(tmp_path / "hook", "exit 7\n")

    result = run(env, BACKUP_REMOTE_HOOK=str(hook))

    assert result.returncode == 3
    assert len(dumps(env, "daily")) == 1


def test_retention_keeps_14_daily_and_6_monthly_without_touching_this_months_link(env):
    old(env, "daily", 20)
    old(env, "monthly", 9)

    result = run(env)

    assert result.returncode == 3
    daily, monthly = dumps(env, "daily"), dumps(env, "monthly")
    assert len(daily) == 14 and len(monthly) == 6
    newest = daily[-1]
    assert monthly[-1] == newest  # first dump of the month is kept monthly
    assert daily[0] == "industry-intelligence-20200801T000000Z.dump"  # oldest 7 of 21 pruned


def test_existing_monthly_dump_is_not_replaced(env):
    this_month = datetime.now(UTC).strftime("%Y%m")
    monthly = Path(env["BACKUP_DIR"]) / "monthly"
    monthly.mkdir(parents=True)
    first = monthly / f"industry-intelligence-{this_month}01T000000Z.dump"
    first.write_bytes(DUMP)

    assert run(env).returncode == 3
    assert dumps(env, "monthly") == [first.name]


def test_failed_or_invalid_dump_keeps_nothing(env, tmp_path):
    failing = executable(tmp_path / "failing", "echo partial; exit 1\n")
    assert run(env, II_COMPOSE=str(failing)).returncode == 1
    invalid = executable(tmp_path / "invalid", "head -c 5000 /dev/zero\n")
    result = run(env, II_COMPOSE=str(invalid))
    assert result.returncode == 1 and "invalid_dump" in result.stdout
    daily = Path(env["BACKUP_DIR"]) / "daily"
    assert list(daily.iterdir()) == []


def test_invalid_retention_is_refused(env):
    assert run(env, BACKUP_DAILY_KEEP="0").returncode == 1
    assert not (Path(env["BACKUP_DIR"]) / "daily").exists()
