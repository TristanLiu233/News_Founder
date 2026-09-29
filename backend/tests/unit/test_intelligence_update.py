"""Operations-1: stage order, failure policy, statuses, and source selection of the daily
``intelligence-update`` run. Stages are replaced by fakes; no database or network is used."""

import asyncio

import httpx
import pytest

from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.config import Settings
from industry_intelligence.services import intelligence_update as update
from industry_intelligence.services.intelligence_update import (
    DEGRADED,
    FAILED,
    SKIPPED,
    SUCCESS,
    StageResult,
)

ORDER = [
    "precheck",
    "rankings",
    "composite",
    "company_rankings",
    "content",
    "links",
    "events",
    "ai",
    "postcheck",
]


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_url="postgresql+psycopg://localhost/none_test")


@pytest.fixture
def stages(monkeypatch):
    """Replace every stage with a recording fake; ``outcomes`` sets a stage's status."""

    calls, outcomes = [], {}

    def fake(name):
        def stage(*args, **kwargs):
            calls.append(name)
            return StageResult(name, status=outcomes.get(name, SUCCESS))

        return stage

    def fake_async(name):
        async def stage(*args, **kwargs):
            return fake(name)()

        return stage

    monkeypatch.setattr(update, "table_counts", lambda factory: {"events": 0})

    def check(factory, settings, check=None, audit=None, name="precheck"):
        return fake(name)()

    monkeypatch.setattr(update, "check_state", check)
    async_stages = {
        "rankings": "ranking_stage",
        "company_rankings": "company_ranking_stage",
        "content": "content_stage",
        "ai": "ai_stage",
    }
    for name, target in async_stages.items():
        monkeypatch.setattr(update, target, fake_async(name))
    for name in ("composite", "links", "events"):
        monkeypatch.setattr(update, f"{name}_stage", fake(name))
    return calls, outcomes


def run(settings, **kwargs):
    return asyncio.run(update.run_update(settings, None, **kwargs))


def test_success_runs_every_stage_in_dependency_order(settings, stages):
    calls, _ = stages
    result = run(settings, run_id="r1")
    assert calls == ORDER
    assert [stage.name for stage in result.stages] == ORDER
    assert (result.status, result.exit_code, result.run_id) == (SUCCESS, 0, "r1")


def test_precheck_failure_stops_before_any_write(settings, stages):
    calls, outcomes = stages
    outcomes["precheck"] = FAILED
    result = run(settings)
    assert calls == ["precheck"]
    assert (result.status, result.exit_code) == (FAILED, 1)


def test_partial_source_failure_degrades_but_continues(settings, stages):
    calls, outcomes = stages
    outcomes["content"] = DEGRADED
    result = run(settings)
    assert calls == ORDER
    assert (result.status, result.exit_code) == (DEGRADED, 3)


def test_event_failure_skips_ai_and_still_audits(settings, stages):
    calls, outcomes = stages
    outcomes["events"] = FAILED
    result = run(settings)
    assert "ai" not in calls and calls[-1] == "postcheck"
    ai = result.stage("ai")
    assert (ai.status, ai.reason) == (SKIPPED, "blocked_by_events")
    assert result.status == FAILED


def test_link_failure_skips_events_and_ai(settings, stages):
    calls, outcomes = stages
    outcomes["links"] = FAILED
    result = run(settings)
    assert "events" not in calls and "ai" not in calls and "postcheck" in calls
    assert result.stage("events").reason == "blocked_by_links"
    assert result.stage("ai").reason == "blocked_by_events"
    assert result.status == FAILED


def test_ai_failure_only_degrades_the_run(settings, stages):
    _, outcomes = stages
    outcomes["ai"] = FAILED
    result = run(settings)
    assert result.stage("events").status == SUCCESS
    assert (result.status, result.exit_code) == (DEGRADED, 3)


def test_skip_ai_completes_successfully(settings, stages):
    calls, _ = stages
    result = run(settings, skip_ai=True)
    assert "ai" not in calls
    assert (result.stage("ai").status, result.stage("ai").reason) == (SKIPPED, "skip_ai")
    assert result.status == SUCCESS


def test_postcheck_error_fails_the_run(settings, stages):
    _, outcomes = stages
    outcomes["postcheck"] = FAILED
    assert run(settings).status == FAILED


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([SUCCESS, SKIPPED], SUCCESS),
        ([SUCCESS, DEGRADED], DEGRADED),
        ([DEGRADED, FAILED], FAILED),
        ([SUCCESS, ("ai", FAILED)], DEGRADED),
        ([("ai", FAILED), FAILED], FAILED),
    ],
)
def test_overall_status(statuses, expected):
    stages = [
        StageResult(*status) if isinstance(status, tuple) else StageResult("x", status=status)
        for status in statuses
    ]
    assert update.overall_status(stages) == expected


# --- source isolation ----------------------------------------------------------------------


def _http_error(status):
    request = httpx.Request("GET", "https://source.example.test/feed")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status))


def test_one_failing_source_does_not_stop_the_others():
    async def ingest(key):
        if key == "down":
            raise _http_error(503)
        return {"persisted_count": 2, "secret": "never copied"}

    stage = StageResult("content")
    asyncio.run(update._each_source(stage, ["a", "down", "b"], ingest, ("persisted_count",)))
    assert [row["source"] for row in stage.sources] == ["a", "down", "b"]
    assert stage.sources[1] == {
        "source": "down",
        "error": "HTTPStatusError",
        "phase": "other",  # raised outside a tagged fetch/persist phase
        "category": "http_status",
        "status_code": 503,
    }
    assert stage.sources[0] == {"source": "a", "persisted_count": 2}
    assert stage.counts == {"attempted": 3, "succeeded": 2, "failed": 1}
    assert stage.status == DEGRADED


def test_every_source_failing_fails_the_stage_and_no_source_skips_it():
    async def down(key):
        raise httpx.ConnectError("offline")

    stage = StageResult("rankings")
    asyncio.run(update._each_source(stage, ["a", "b"], down, ()))
    assert (stage.status, stage.reason) == (FAILED, "all_sources_failed")
    assert {row["category"] for row in stage.sources} == {"network"}
    empty = StageResult("rankings")
    asyncio.run(update._each_source(empty, [], down, ()))
    assert (empty.status, empty.reason) == (SKIPPED, "no_sources_selected")


def test_error_detail_categories_carry_no_message():
    assert update.error_detail("s", httpx.ReadTimeout("slow"))["category"] == "timeout"
    invalid = update.error_detail("s", ValueError("No valid entries"))
    assert invalid["category"] == "invalid_response"
    detail = update.error_detail("s", RuntimeError("token=abc"))
    assert detail == {"source": "s", "error": "RuntimeError", "phase": "other", "category": "error"}


def test_default_sources_exclude_manual_only_rankings_and_explicit_keys_narrow():
    rankings, companies, content = update.select_sources()
    assert rankings == list(SOURCES) and content == list(CONTENT_SOURCES)
    assert companies == [key for key, d in COMPANY_RANKINGS.items() if d.daily_update]
    assert "wikipedia_largest_banks_assets" not in companies
    assert not COMPANY_RANKINGS["wikipedia_video_game_revenue"].daily_update
    assert update.select_sources(["gematsu", "game_3dm", "wikipedia_video_game_revenue"]) == (
        ["game_3dm"],
        ["wikipedia_video_game_revenue"],
        ["gematsu"],
    )


def test_ranking_batch_key_is_derived_from_the_run_id():
    assert update.ranking_batch_key("20260929T000000Z-abcd1234") == (
        "update:20260929T000000Z-abcd1234"
    )
    assert len(update.ranking_batch_key("x" * 200)) == 100
    assert update.new_run_id() != update.new_run_id()


# --- precheck ------------------------------------------------------------------------------


class _Session:
    def execute(self, statement):
        return None

    def rollback(self):
        pass

    def close(self):
        pass


def _audit(errors=0, warnings=0):
    return lambda factory: {"errors": errors, "warnings": warnings, "passed": not errors}


def test_precheck_requires_alembic_head_and_no_audit_errors(settings):
    at_head = lambda session: (["h1"], ["h1"])  # noqa: E731
    stage = update.check_state(_Session, settings, at_head, _audit(warnings=4))
    assert stage.status == SUCCESS and stage.counts["audit_warnings"] == 4
    behind = update.check_state(_Session, settings, lambda s: (["h0"], ["h1"]), _audit())
    assert (behind.status, behind.reason) == (FAILED, "alembic_not_at_head")
    broken = update.check_state(_Session, settings, at_head, _audit(errors=1))
    assert (broken.status, broken.reason) == (FAILED, "data_quality_errors")


def test_precheck_fails_closed_without_a_database(settings):
    def unreachable():
        raise ConnectionError("no database")

    stage = update.check_state(unreachable, settings, None, _audit())
    assert (stage.status, stage.reason) == (FAILED, "database_unavailable")
    assert stage.errors == [{"error": "ConnectionError"}]


def test_ai_stage_is_skipped_without_a_provider(settings):
    stage = asyncio.run(update.ai_stage(settings, None, 10))
    assert (stage.status, stage.reason) == (SKIPPED, "provider_not_configured")


def test_render_text_reports_failures_and_overall_status():
    result = update.UpdateResult(run_id="r", started_at="t", status=DEGRADED)
    stage = StageResult("content", status=DEGRADED, counts={"attempted": 2})
    stage.sources = [{"source": "down", "error": "HTTPStatusError", "category": "http_status",
                      "status_code": 503}]  # fmt: skip
    result.stages.append(stage)
    text = update.render_text(result)
    assert "content: DEGRADED" in text and "FAILED down: http_status 503" in text
    assert text.endswith("Overall: DEGRADED")
