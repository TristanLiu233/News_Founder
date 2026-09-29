"""Operations-2 without a database: failure phases, attempt rows, health states, and the
lock / run-history wrapper around ``intelligence-update``."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.config import Settings
from industry_intelligence.services import intelligence_update as update
from industry_intelligence.services.ingestion_phase import (
    FETCH,
    PERSIST,
    failure_phase,
    ingestion_phase,
)
from industry_intelligence.services.intelligence_update import (
    ALREADY_RUNNING,
    DEGRADED,
    FAILED,
    SUCCESS,
    StageResult,
    UpdateResult,
)
from industry_intelligence.services.source_health import (
    health_state,
    render_text,
    source_inventory,
)
from industry_intelligence.services.update_operations import UPDATE_LOCK_KEY, attempt_fields


def _http_error(status):
    request = httpx.Request("GET", "https://source.example.test/feed")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status))


def _raised_in(phase, exc):
    with pytest.raises(type(exc)) as caught, ingestion_phase(phase):
        raise exc
    return caught.value


# --- failure phases ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phase", "exc", "expected"),
    [
        (FETCH, _http_error(503), "transport"),
        (FETCH, httpx.ConnectError("offline"), "transport"),
        (FETCH, httpx.ReadTimeout("slow"), "transport"),
        (FETCH, ValueError("unexpected markup"), "parse"),
        (FETCH, KeyError("rank"), "parse"),
        (PERSIST, ValueError("No valid entries"), "persist"),
        (PERSIST, RuntimeError("deadlock"), "persist"),
    ],
)
def test_failure_phase_separates_transport_parse_and_persist(phase, exc, expected):
    assert failure_phase(_raised_in(phase, exc)) == expected


def test_http_200_with_unreadable_body_is_a_parse_failure_not_a_success():
    # The request succeeded; the adapter could not read the response into items.
    assert failure_phase(_raised_in(FETCH, ValueError("feed has no items element"))) == "parse"


def test_phase_tag_keeps_the_exception_and_the_innermost_phase():
    exc = ValueError("same message")
    with pytest.raises(ValueError, match="same message"), ingestion_phase(PERSIST):
        with ingestion_phase(FETCH):
            raise exc
    assert exc.ingestion_phase == FETCH
    assert failure_phase(RuntimeError("untagged")) == "other"


# --- attempt rows ------------------------------------------------------------------------------


def test_ranking_attempts_success_and_replay():
    fresh = attempt_fields(
        "ranking",
        {"fetched_count": 30, "persisted_count": 29, "skipped_count": 1, "failed_count": 2},
    )
    assert (fresh["outcome"], fresh["stored_count"], fresh["invalid_count"]) == ("success", 29, 3)
    replay = attempt_fields(
        "ranking",
        {"fetched_count": 30, "persisted_count": 0, "skipped_count": 30, "replayed": True},
    )
    assert replay["outcome"] == "replayed"
    assert (replay["fetched_count"], replay["replayed_count"]) == (None, 30)
    assert replay["invalid_count"] == 0


def test_company_ranking_attempt_outcomes():
    base = {"fetched_count": 100, "failed_count": 0}
    assert attempt_fields("company_ranking", {**base, "persisted_count": 0, "replayed": True})[
        "outcome"
    ] == ("replayed")
    unchanged = attempt_fields("company_ranking", {**base, "persisted_count": 0, "unchanged": True})
    assert unchanged["outcome"] == "unchanged"
    fresh = attempt_fields("company_ranking", {**base, "persisted_count": 99, "skipped_count": 1})
    assert (fresh["outcome"], fresh["invalid_count"]) == ("success", 1)


def test_content_with_no_new_items_is_unchanged_never_failed():
    result = {
        "fetched_count": 20,
        "persisted_count": 0,
        "replayed_count": 20,
        "invalid_count": 0,
        "failed_count": 0,
    }
    row = attempt_fields("content", result)
    assert row["outcome"] == "unchanged" and row.get("failure_phase") is None
    assert (row["fetched_count"], row["replayed_count"], row["invalid_count"]) == (20, 20, 0)
    new = attempt_fields(
        "content", {**result, "persisted_count": 3, "invalid_count": 1, "link_error": "X"}
    )
    assert (new["outcome"], new["invalid_count"], new["details"]) == (
        "success",
        1,
        {"link_error": "X"},
    )


def test_failed_attempt_is_sanitized_and_bounded():
    exc = _raised_in(FETCH, _http_error(503))
    row = attempt_fields("content", detail=update.error_detail("down", exc))
    assert row == {
        "outcome": "failed",
        "failure_phase": "transport",
        "error_category": "http_status",
        "exception_type": "HTTPStatusError",
        "http_status": 503,
        "error_summary": "transport: http_status 503 (HTTPStatusError)",
        "details": {},
    }
    secret = RuntimeError("token=abc https://user:pw@host/?key=1")
    row = attempt_fields("ranking", detail=update.error_detail("x", secret))
    assert row["http_status"] is None and "token" not in row["error_summary"]
    long = {"phase": "other", "category": "error", "error": "E" * 500}
    assert len(attempt_fields("content", detail=long)["error_summary"]) <= 300


def test_each_source_records_every_attempt_with_times():
    recorded = []

    def record(kind, key, started_at, finished_at, result=None, detail=None):
        assert started_at <= finished_at and started_at.tzinfo is not None
        recorded.append((kind, key, result is not None, detail and detail["category"]))

    async def ingest(key):
        if key == "down":
            raise _raised_in(FETCH, _http_error(500))
        return {"persisted_count": 1}

    stage = StageResult("content")
    asyncio.run(update._each_source(stage, ["a", "down"], ingest, (), record, "content"))
    assert recorded == [("content", "a", True, None), ("content", "down", False, "http_status")]
    assert stage.sources[1]["phase"] == "transport"


# --- health states -----------------------------------------------------------------------------


def _attempt(outcome, invalid=0):
    return SimpleNamespace(outcome=outcome, invalid_count=invalid)


def test_health_states():
    assert health_state(None) == "never_run"
    assert health_state(_attempt("failed")) == "failing"
    assert health_state(_attempt("success", invalid=2)) == "degraded"
    for outcome in ("success", "replayed", "unchanged"):
        assert health_state(_attempt(outcome)) == "healthy"


def test_inventory_is_the_automatic_registry():
    inventory = source_inventory()
    daily = [key for key, d in COMPANY_RANKINGS.items() if d.daily_update]
    assert [key for key, _ in inventory] == [*SOURCES, *daily, *CONTENT_SOURCES]
    assert len(inventory) == len(SOURCES) + len(daily) + len(CONTENT_SOURCES)
    assert "wikipedia_largest_banks_assets" not in dict(inventory)


def test_health_text_is_compact():
    report = {
        "summary": {
            "sources": 2,
            "healthy": 1,
            "degraded": 0,
            "failing": 1,
            "never_run": 0,
        },
        "update": {"lock": "free", "recent_runs": []},
        "sources": [
            {
                "source": "gematsu",
                "kind": "content",
                "health": "healthy",
                "last_success_at": "2026-09-30T01:02:03+00:00",
                "consecutive_failures": 0,
                "recent_failures": 1,
                "last_error": None,
            },
            {
                "source": "down",
                "kind": "ranking",
                "health": "failing",
                "last_success_at": None,
                "consecutive_failures": 2,
                "recent_failures": 2,
                "last_error": {"summary": "transport: http_status 503 (HTTPStatusError)"},
            },
        ],
    }
    text = render_text(report)
    assert "healthy 1, degraded 0, failing 1, never_run 0" in text
    assert "Update lock: free · no recorded run" in text
    assert "2026-09-30 01:02Z" in text and "2/2  transport: http_status 503" in text


# --- lock + run history ------------------------------------------------------------------------


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_url="postgresql+psycopg://localhost/none_test")


class FakeLock:
    def __init__(self, acquired=True, error=None):
        self.acquired, self.error, self.released = acquired, error, 0

    def acquire(self):
        if self.error:
            raise self.error
        return self.acquired

    def release(self):
        self.released += 1


class NoHistory:
    """A session factory whose writes fail: the run itself must not depend on history."""

    def begin(self):
        raise ConnectionError("history unavailable")


def test_exit_codes():
    assert update.EXIT_CODES == {SUCCESS: 0, FAILED: 1, DEGRADED: 3, ALREADY_RUNNING: 4}
    assert UpdateResult("r", "t", status=ALREADY_RUNNING).exit_code == 4


def test_lock_key_is_a_two_int4_key():
    assert len(UPDATE_LOCK_KEY) == 2
    assert all(0 < part < 2**31 for part in UPDATE_LOCK_KEY)


def test_held_lock_refuses_without_running_or_recording(settings, monkeypatch):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("a refused run must not start any stage")

    monkeypatch.setattr(update, "run_update", must_not_run)
    lock = FakeLock(acquired=False)
    result = asyncio.run(update.execute_update(settings, None, lock=lock, run_id="r2"))
    assert (result.status, result.exit_code, result.run_id) == (ALREADY_RUNNING, 4, "r2")
    assert result.recording is None and lock.released == 1
    assert "nothing was requested or written" in update.render_text(result)


def test_lock_error_fails_closed(settings, monkeypatch):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("no stage without the lock")

    monkeypatch.setattr(update, "run_update", must_not_run)
    result = asyncio.run(
        update.execute_update(settings, None, lock=FakeLock(error=ConnectionError("down")))
    )
    assert (result.status, result.exit_code) == (FAILED, 1)
    assert result.stages[0].reason == "database_unavailable"


def test_lock_released_after_success_and_missing_history_degrades(settings, monkeypatch):
    async def fine(*args, **kwargs):
        return UpdateResult(kwargs["run_id"], "t", status=SUCCESS)

    monkeypatch.setattr(update, "run_update", fine)
    lock = FakeLock()
    result = asyncio.run(update.execute_update(settings, NoHistory(), lock=lock))
    assert lock.released == 1
    assert result.recording["run_recorded"] is False
    # The orchestration completed, but recording the run (an isolated operation) failed.
    assert (result.status, result.exit_code) == (DEGRADED, 3)


def test_lock_released_after_an_unexpected_crash(settings, monkeypatch):
    async def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(update, "run_update", crash)
    lock = FakeLock()
    result = asyncio.run(update.execute_update(settings, NoHistory(), lock=lock))
    assert lock.released == 1
    assert (result.status, result.stages[0].name, result.stages[0].reason) == (
        FAILED,
        "orchestration",
        "unexpected_error",
    )


def test_lock_released_when_the_process_is_interrupted(settings, monkeypatch):
    async def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(update, "run_update", interrupted)
    lock = FakeLock()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(update.execute_update(settings, NoHistory(), lock=lock))
    assert lock.released == 1


def test_run_id_and_start_time_are_shared_with_the_run(settings, monkeypatch):
    seen = {}

    async def capture(*args, **kwargs):
        seen.update(kwargs)
        return UpdateResult(kwargs["run_id"], kwargs["started_at"].isoformat(), status=SUCCESS)

    monkeypatch.setattr(update, "run_update", capture)
    result = asyncio.run(update.execute_update(settings, NoHistory(), lock=FakeLock()))
    assert seen["run_id"] == result.run_id and seen["recorder"] is not None
    assert seen["started_at"] <= datetime.now(UTC)
