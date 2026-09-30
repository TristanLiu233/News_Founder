"""Scheduler-1: the launchd wrapper runs only the bounded ``intelligence-update --ai-limit 12``
(Product-1; ``--skip-ai`` before) and refuses to run when the system time zone is not
Australia/Sydney."""

import importlib.util
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

SCHEDULER_DIR = Path(__file__).resolve().parents[3] / "ops" / "scheduler"
TEMPLATE = SCHEDULER_DIR / "local.industry-intelligence.daily-update.plist.template"


@pytest.fixture
def wrapper():
    spec = importlib.util.spec_from_file_location("daily_update", SCHEDULER_DIR / "daily_update.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runs_only_the_skip_ai_update_from_backend(wrapper, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(wrapper, "system_zone", lambda: "Australia/Sydney")
    monkeypatch.setattr(wrapper.subprocess, "run", run)

    assert wrapper.main([]) == 3
    [(command, kwargs)] = calls
    # Product-1.1: scheduled AI rolled back after the fresh live quality gate failed.
    assert command[1:] == ["-m", "industry_intelligence.cli", "intelligence-update", "--skip-ai"]
    assert kwargs["cwd"] == wrapper.ROOT / "backend"
    assert sorted(kwargs["env"]) == ["HOME", "LANG", "PATH", "PYTHONUNBUFFERED"]
    assert "--ai-limit" not in command


def test_refuses_outside_sydney_without_running(wrapper, monkeypatch, capsys):
    monkeypatch.setattr(wrapper, "system_zone", lambda: "Europe/London")
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *a, **k: pytest.fail("update started"))

    assert wrapper.main([]) == wrapper.EX_CONFIG
    assert "event=refused" in capsys.readouterr().out


def test_plist_template_schedules_10_00_with_the_wrapper():
    plist = plistlib.loads(TEMPLATE.read_bytes())

    assert plist["StartCalendarInterval"] == {"Hour": 10, "Minute": 0}
    assert plist["RunAtLoad"] is False
    assert plist["ProgramArguments"] == [
        "__PROJECT_ROOT__/.venv/bin/python",
        "__PROJECT_ROOT__/ops/scheduler/daily_update.py",
    ]
    assert plist["StandardOutPath"] == plist["StandardErrorPath"]


def test_bounded_ai_is_one_setting_away_and_stays_capped(wrapper, monkeypatch):
    """Re-enabling scheduled AI (only after a zero-finding live quality smoke) is
    ``AI_ARGS = ["--ai-limit", "12"]``; the cap stays within the CLI's hard limit."""

    from industry_intelligence.cli import _ai_event_cap

    calls = []
    monkeypatch.setattr(wrapper, "system_zone", lambda: "Australia/Sydney")
    monkeypatch.setattr(wrapper, "AI_ARGS", ["--ai-limit", "12"])
    monkeypatch.setattr(
        wrapper.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )
    assert wrapper.main(["--plan"]) == 0
    assert calls[0][3:] == ["intelligence-update", "--ai-limit", "12", "--plan"]
    assert _ai_event_cap(calls[0][5]) == 12
