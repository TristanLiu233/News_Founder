"""AI-LP1: Event AI is generated only in the Event's primary locale (no database needed)."""

import inspect
import json
import sys

import pytest

from industry_intelligence import cli
from industry_intelligence.ai.prompts.event_insights import INSIGHT_PROMPTS
from industry_intelligence.services import ai_enrichment


def test_default_selection_is_primary_locale_only():
    parameters = inspect.signature(ai_enrichment.enrich_events).parameters
    assert parameters["locales"].default is None
    assert parameters["artifact_types"].default == tuple(INSIGHT_PROMPTS)
    assert not any(spec.cross_language for spec in INSIGHT_PROMPTS.values())
    assert not hasattr(ai_enrichment, "LOCALES")


def test_task_locale_refuses_non_primary_locales():
    class Event:
        id = "e1"

    class Evidence:
        def __init__(self, *languages):
            self.items = [type("Item", (), {"language": language}) for language in languages]

    chinese = Evidence("zh-CN")
    assert ai_enrichment._task_locale(Event, chinese, None) == ("zh-CN", [])
    assert ai_enrichment._task_locale(Event, chinese, ("zh-CN",)) == ("zh-CN", [])
    assert ai_enrichment._task_locale(Event, chinese, ("en", "zh-CN")) == (
        "zh-CN",
        [{"event_id": "e1", "locale": "en", "primary_locale": "zh-CN"}],
    )
    mixed = Evidence("en", "zh-CN")
    assert ai_enrichment._task_locale(Event, mixed, None) == (None, [])
    assert ai_enrichment._task_locale(Event, mixed, ("en",)) == (
        None,
        [{"event_id": "e1", "locale": "en", "primary_locale": None}],
    )


def test_cli_reports_a_cross_language_request_as_a_clear_refusal(monkeypatch, capsys):
    refused = [{"event_id": "e1", "locale": "en", "primary_locale": "zh-CN"}]

    async def refuse(*_args, **kwargs):
        assert kwargs["locales"] == ("en",)
        raise ai_enrichment.CrossLanguageGenerationDisabled(refused)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a refused request must not build a provider")

    monkeypatch.setattr(ai_enrichment, "enrich_events", refuse)
    monkeypatch.setattr(ai_enrichment, "build_provider", forbidden)
    monkeypatch.setattr(sys, "argv", ["cli", "ai-event-enrich", "--locale", "en", "--dry-run"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    output = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert output["error"] == "cross_language_generation_disabled"
    assert "disabled by product policy" in output["detail"]
    assert output["refused"] == refused


def test_cli_without_locale_uses_the_primary_locale_policy(monkeypatch, capsys):
    seen = {}

    async def plan(*_args, **kwargs):
        seen.update(kwargs)
        return {"dry_run": True, "tasks": 0}

    monkeypatch.setattr(ai_enrichment, "enrich_events", plan)
    monkeypatch.setattr(sys, "argv", ["cli", "ai-event-enrich", "--dry-run"])
    cli.main()
    assert seen["locales"] is None and seen["dry_run"] is True
