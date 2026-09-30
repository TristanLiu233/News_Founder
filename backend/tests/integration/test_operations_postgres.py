"""Operations-2 on PostgreSQL: the global update lock, run history, source attempts, and the
source-health read model, driven through the real ``execute_update`` and ingestion services.

Sources are synthetic registry entries (no network request; one adapter reads a mocked HTTP 200
response through the real ``HTTPClient``). Data written through the session factory is rolled
back; lock tests use extra connections that write nothing. Runs only against a ``*_test``
database (``DATABASE_URL`` override).
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.adapters.source.base import ContentSourceItem, SourceItem
from industry_intelligence.adapters.source.content_registry import (
    CONTENT_SOURCES,
    ContentSourceDefinition,
)
from industry_intelligence.adapters.source.registry import SOURCES, SourceDefinition
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    Company,
    CompanySubjectLink,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventCompanyLink,
    EventContentLink,
    EventSubjectLink,
    RankObservation,
    SourceAttempt,
    Subject,
    UpdateRun,
)
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.db.models.search import SearchDocument
from industry_intelligence.domain.enums import ContentType, Domain, SubjectType
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services import content_ingestion
from industry_intelligence.services import intelligence_update as update
from industry_intelligence.services.intelligence_update import (
    ALREADY_RUNNING,
    DEGRADED,
    SUCCESS,
    execute_update,
    plan_update,
)
from industry_intelligence.services.source_health import source_health
from industry_intelligence.services.update_operations import UPDATE_LOCK_KEY, UpdateLock

pytestmark = pytest.mark.integration

T0 = datetime(2034, 5, 1, 9, tzinfo=UTC)
DATABASE = make_url(get_settings().database_url).database or ""
KEYS = ["ops_rank", "ops_news", "ops_feed"]


@pytest.fixture(autouse=True)
def isolated_database():
    if not DATABASE.endswith("_test"):
        pytest.skip("writes every stage; run with DATABASE_URL pointing at a *_test database")


@pytest.fixture
def connection():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def db(connection):
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def factory(connection):
    return sessionmaker(bind=connection, join_transaction_mode="create_savepoint")


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def modes():
    """Per-source behaviour for the next run; ``fetches`` counts every adapter fetch."""

    return {"ops_rank": "ok", "ops_news": "ok", "ops_feed": "ok", "fetches": 0}


@pytest.fixture
def registry(monkeypatch, modes):
    tag = uuid4().hex[:8]
    game = f"Lantern Harbor {tag}"

    class Ranking:
        def __init__(self, http, limit):
            self.failed_count = 0

        async def fetch(self):
            modes["fetches"] += 1
            at = datetime.now(UTC)
            if modes["ops_rank"] == "invalid_all":
                return [SourceItem(f"slug-{tag}", "   ", None, 1, None, at)]
            return [SourceItem(f"slug-{tag}", game, None, 1, "8.8", at)]

    class News:
        def __init__(self, http, limit):
            self.failed_count = 0

        async def fetch(self):
            modes["fetches"] += 1
            mode = modes["ops_news"]
            if mode == "http_503":
                request = httpx.Request("GET", "https://ops.example.test/news")
                raise httpx.HTTPStatusError("503", request=request, response=httpx.Response(503))
            items = [
                ContentSourceItem(
                    f"{tag}-n1",
                    f"{game} studio opens a second office",
                    f"https://ops.example.test/{tag}/n1",
                    T0,
                    T0 + timedelta(minutes=1),
                    "en",
                )
            ]
            if mode == "invalid_row":
                items.append(
                    ContentSourceItem(f"{tag}-bad", "  ", "ftp://bad", T0, T0, "en"),
                )
            return items

    class Feed:
        """Reads a real (mocked) HTTP response; HTTP 200 with an unreadable body must fail."""

        def __init__(self, http, limit):
            self.http, self.failed_count = http, 0

        async def fetch(self):
            modes["fetches"] += 1
            response = await self.http.get("https://ops.example.test/feed")
            if not response.text.startswith("<rss"):
                raise ValueError("response is not an RSS document")
            return [
                ContentSourceItem(
                    f"{tag}-f1",
                    f"Harbor patch notes {tag} published",
                    f"https://ops.example.test/{tag}/f1",
                    T0 + timedelta(days=3),
                    T0 + timedelta(days=3),
                    "en",
                )
            ]

    def handler(request):
        body = "<html>maintenance</html>" if modes["ops_feed"] == "http_200_junk" else "<rss/>"
        return httpx.Response(200, text=body)

    monkeypatch.setattr(
        content_ingestion,
        "HTTPClient",
        lambda settings, **kw: HTTPClient(settings, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setitem(
        SOURCES,
        "ops_rank",
        SourceDefinition(
            "ops_rank",
            f"Ops ranking {tag}",
            Domain.GAMING,
            "Top games",
            SubjectType.GAME,
            "https://ops.example.test/rank",
            Ranking,
        ),
    )
    for key, adapter in (("ops_news", News), ("ops_feed", Feed)):
        monkeypatch.setitem(
            CONTENT_SOURCES,
            key,
            ContentSourceDefinition(
                key,
                f"Ops {key} {tag}",
                Domain.GAMING,
                ContentType.NEWS,
                f"https://ops.example.test/{key}",
                adapter,
                "RSS",
                "en",
            ),
        )
    return game


def run(settings, factory, sources=KEYS, **kwargs):
    kwargs.setdefault("skip_ai", True)
    return asyncio.run(execute_update(settings, factory, sources=sources, **kwargs))


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


CANONICAL = (
    Subject,
    SubjectAlias,
    SubjectExternalIdentity,
    Company,
    CompanySubjectLink,
    RankObservation,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventContentLink,
    EventSubjectLink,
    EventCompanyLink,
    SearchDocument,
)


def canonical(db, without=()):
    return {m.__tablename__: count(db, m) for m in CANONICAL if m.__tablename__ not in without}


def health(db):
    db.execute(text("SAVEPOINT health_read"))
    report = source_health(db)
    db.execute(text("ROLLBACK TO SAVEPOINT health_read"))
    return report, {row["source"]: row for row in report["sources"]}


def attempts(db, key):
    return db.scalars(
        select(SourceAttempt)
        .where(SourceAttempt.source_key == key)
        .order_by(SourceAttempt.started_at)
    ).all()


def held_lock(settings):
    lock = UpdateLock(settings.database_url)
    assert lock.acquire()
    return lock


# --- run history and attempts --------------------------------------------------------------


def test_run_records_history_attempts_and_health(db, factory, settings, registry, modes):
    modes["ops_news"] = "http_503"
    result = run(settings, factory, run_id="ops-1")
    assert (result.status, result.exit_code) == (DEGRADED, 3)
    recording = result.recording
    assert recording["run_recorded"] and recording["attempts_recorded"] == 3
    row = db.scalar(select(UpdateRun).where(UpdateRun.run_id == "ops-1"))
    assert (row.status, row.exit_code) == ("degraded", 3) and row.finished_at is not None
    assert row.options == {"sources": sorted(KEYS), "skip_ai": True, "ai_limit": 12}
    content = next(s for s in row.stage_summary["stages"] if s["name"] == "content")
    assert content["failed_sources"] == ["ops_news"]
    news = attempts(db, "ops_news")[-1]
    assert (news.outcome, news.failure_phase, news.error_category, news.http_status) == (
        "failed",
        "transport",
        "http_status",
        503,
    )
    assert news.error_summary == "transport: http_status 503 (HTTPStatusError)"
    assert attempts(db, "ops_rank")[-1].outcome == "success"
    assert (attempts(db, "ops_feed")[-1].outcome, attempts(db, "ops_feed")[-1].stored_count) == (
        "success",
        1,
    )
    report, by = health(db)
    assert by["ops_rank"]["health"] == "healthy" and by["ops_feed"]["health"] == "healthy"
    assert by["ops_news"]["health"] == "failing"
    assert by["ops_news"]["consecutive_failures"] == 1 and by["ops_news"]["last_success_at"] is None
    # The registry is the inventory: registered sources without an attempt are listed too.
    assert by["gematsu"]["health"] == "never_run" and by["gematsu"]["last_attempt_at"] is None
    assert "wikipedia_largest_banks_assets" not in by
    assert report["summary"]["sources"] == len(report["sources"])
    assert report["update"]["recent_runs"][0]["run_id"] == "ops-1"
    assert report["update"]["recent_runs"][0]["status"] == "degraded"


def test_failure_then_success_then_repeated_failures(db, factory, settings, registry, modes):
    modes["ops_news"] = "http_503"
    run(settings, factory, sources=["ops_news"], run_id="f1")
    modes["ops_news"] = "ok"
    ok = run(settings, factory, sources=["ops_news"], run_id="f2")
    assert ok.status == SUCCESS
    _, by = health(db)
    news = by["ops_news"]
    # Latest attempt clean → healthy; the earlier failure stays visible as history.
    assert (news["health"], news["consecutive_failures"], news["recent_failures"]) == (
        "healthy",
        0,
        1,
    )
    assert news["last_error"]["http_status"] == 503 and news["last_success_at"] is not None
    assert news["last_failure_at"] < news["last_success_at"]
    modes["ops_news"] = "http_503"
    for run_id in ("f3", "f4", "f5"):
        run(settings, factory, sources=["ops_news"], run_id=run_id)
    _, by = health(db)
    news = by["ops_news"]
    assert (news["health"], news["consecutive_failures"], news["recent_failures"]) == (
        "failing",
        3,
        4,
    )
    assert (news["total_attempts"], news["last_outcome"]) == (5, "failed")
    assert news["last_success_at"] < news["last_failure_at"]


def test_zero_new_items_is_unchanged_and_healthy(db, factory, settings, registry, modes):
    run(settings, factory, sources=["ops_news"], run_id="z1")
    before = canonical(db)
    again = run(settings, factory, sources=["ops_news"], run_id="z2")
    assert again.status == SUCCESS
    latest = attempts(db, "ops_news")[-1]
    assert (latest.outcome, latest.stored_count, latest.replayed_count) == ("unchanged", 0, 1)
    assert health(db)[1]["ops_news"]["health"] == "healthy"
    assert canonical(db) == before


def test_rejected_rows_degrade_health(db, factory, settings, registry, modes):
    modes["ops_news"] = "invalid_row"
    result = run(settings, factory, sources=["ops_news"])
    assert result.status == SUCCESS
    row = health(db)[1]["ops_news"]
    assert row["health"] == "degraded" and row["latest_counts"]["invalid"] == 1
    assert row["consecutive_failures"] == 0


def test_http_200_with_unreadable_body_is_a_parse_failure(db, factory, settings, registry, modes):
    modes["ops_feed"] = "http_200_junk"
    result = run(settings, factory, sources=["ops_feed"])
    feed = attempts(db, "ops_feed")[-1]
    assert (feed.outcome, feed.failure_phase, feed.http_status) == ("failed", "parse", None)
    assert feed.error_category == "invalid_response" and feed.exception_type == "ValueError"
    assert health(db)[1]["ops_feed"]["health"] == "failing"
    assert result.stage("content").reason == "all_sources_failed"


def test_all_rows_rejected_is_a_persist_failure(db, factory, settings, registry, modes):
    modes["ops_rank"] = "invalid_all"
    run(settings, factory, sources=["ops_rank"])
    rank = attempts(db, "ops_rank")[-1]
    assert (rank.outcome, rank.failure_phase) == ("failed", "persist")


def test_same_run_id_replays_and_is_recorded_as_a_new_invocation(
    db, factory, settings, registry, modes
):
    first = run(settings, factory, run_id="same")
    assert first.status == SUCCESS
    before = canonical(db)
    fetches = modes["fetches"]
    again = run(settings, factory, run_id="same")
    assert again.status == SUCCESS
    # The ranking batch replays without a request; content refetches but stores nothing.
    assert modes["fetches"] == fetches + 2
    assert attempts(db, "ops_rank")[-1].outcome == "replayed"
    assert {a.outcome for a in attempts(db, "ops_news")[-1:]} == {"unchanged"}
    assert canonical(db) == before
    # No uniqueness on run ID or day: two invocations, two rows.
    assert count(db, UpdateRun, UpdateRun.run_id == "same") == 2


def test_new_run_ids_only_append_rank_observations(db, factory, settings, registry, modes):
    run(settings, factory, run_id="n1")
    before = canonical(db)
    for run_id in ("n2", "n3"):
        assert run(settings, factory, run_id=run_id).status == SUCCESS
    after = canonical(db)
    assert after["rank_observations"] == before["rank_observations"] + 2
    assert canonical(db, without={"rank_observations"}) == {
        k: v for k, v in before.items() if k != "rank_observations"
    }


def test_partial_failure_then_rerun_stores_the_missing_source_once(
    db, factory, settings, registry, modes
):
    modes["ops_news"] = "http_503"
    run(settings, factory, run_id="p1")
    missing = count(db, ContentItem)
    modes["ops_news"] = "ok"
    recovered = run(settings, factory, run_id="p2")
    assert recovered.stage("content").counts["persisted_count"] == 1
    assert count(db, ContentItem) == missing + 1
    before = canonical(db)
    run(settings, factory, run_id="p2")
    assert canonical(db) == before


# --- crash, interruption, and the lock -----------------------------------------------------


def test_crash_leaves_running_row_releases_lock_and_next_run_recovers(
    db, factory, settings, registry, modes, monkeypatch
):
    real = update.events_stage

    def crash(*args, **kwargs):
        raise KeyboardInterrupt  # the process is stopped mid-run, after content was stored

    monkeypatch.setattr(update, "events_stage", crash)
    with pytest.raises(KeyboardInterrupt):
        run(settings, factory, run_id="crashed")
    crashed = db.scalar(select(UpdateRun).where(UpdateRun.run_id == "crashed"))
    assert crashed.status == "running" and crashed.finished_at is None
    stored = count(db, ContentItem)
    unassigned = count(
        db,
        ContentItem,
        ~ContentItem.id.in_(select(EventContentLink.content_item_id)),
    )
    assert unassigned == 2
    lock = held_lock(settings)  # the crashed process no longer holds the lock
    lock.release()
    monkeypatch.setattr(update, "events_stage", real)
    recovered = run(settings, factory, run_id="restart")
    assert recovered.status == SUCCESS
    assert recovered.recording["interrupted_runs_marked"] == ["crashed"]
    db.expire_all()
    crashed = db.get(UpdateRun, crashed.id)
    restart = db.scalar(select(UpdateRun).where(UpdateRun.run_id == "restart"))
    assert crashed.status == "interrupted" and crashed.interrupted_by == restart.id
    assert crashed.finished_at is None  # never invented
    assert count(db, ContentItem) == stored
    assert recovered.stage("events").counts["processed"] == 2


def test_running_rows_are_only_marked_interrupted_under_the_lock(
    db, factory, settings, registry, modes
):
    stale = UpdateRun(run_id="old", status="running", started_at=T0, options={}, stage_summary={})
    db.add(stale)
    db.commit()
    other = held_lock(settings)
    try:
        refused = run(settings, factory, run_id="refused")
    finally:
        other.release()
    assert refused.status == ALREADY_RUNNING
    db.expire_all()
    assert db.get(UpdateRun, stale.id).status == "running"  # no lock → no recovery
    run(settings, factory, run_id="owner")
    db.expire_all()
    assert db.get(UpdateRun, stale.id).status == "interrupted"


def test_second_runner_is_refused_with_no_request_and_no_write(
    db, factory, settings, registry, modes
):
    before = canonical(db)
    runs = count(db, UpdateRun)
    holder = held_lock(settings)
    try:
        refused = run(settings, factory, run_id="second")
        assert (refused.status, refused.exit_code) == (ALREADY_RUNNING, 4)
        assert modes["fetches"] == 0
        assert canonical(db) == before and count(db, UpdateRun) == runs
        assert count(db, SourceAttempt) == 0
    finally:
        holder.release()
    # Released: the next runner works.
    assert run(settings, factory, run_id="third").status == SUCCESS
    assert modes["fetches"] == 3


def test_lock_is_released_after_success_and_after_failure(db, factory, settings, registry, modes):
    assert run(settings, factory, run_id="ok").status == SUCCESS
    held_lock(settings).release()
    modes["ops_rank"] = "invalid_all"
    modes["ops_news"] = "http_503"
    modes["ops_feed"] = "http_200_junk"
    failed = run(settings, factory, run_id="bad")
    assert failed.exit_code == 1
    held_lock(settings).release()


def test_lock_is_released_when_the_holding_process_dies(settings):
    script = (
        "import sys, time\n"
        "from sqlalchemy import create_engine, text\n"
        "conn = create_engine(sys.argv[1]).connect()\n"
        f"conn.execute(text('SELECT pg_advisory_lock({UPDATE_LOCK_KEY[0]}, "
        f"{UPDATE_LOCK_KEY[1]})'))\n"
        "conn.commit()\n"
        "print('locked', flush=True)\n"
        "time.sleep(120)\n"
    )
    url = make_url(settings.database_url).render_as_string(hide_password=False)
    process = subprocess.Popen(
        [sys.executable, "-c", script, url], stdout=subprocess.PIPE, text=True, env=os.environ
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        assert UpdateLock(settings.database_url).acquire() is False
        process.kill()  # no unlock, no clean shutdown
        process.wait(timeout=10)
        deadline = time.monotonic() + 10
        while True:
            lock = UpdateLock(settings.database_url)
            if lock.acquire():
                lock.release()
                break
            lock.release()
            assert time.monotonic() < deadline, "lock stayed held after the process died"
            time.sleep(0.2)
    finally:
        process.kill()
        process.wait(timeout=10)


def test_update_lock_never_collides_with_service_transaction_locks(settings):
    keys = [
        "events",
        "company_rankings",
        "content_links",
        "subject_merge",
        *SOURCES,
        "intelligence_update",
    ]
    engine = create_engine(settings.database_url)
    holder = held_lock(settings)
    try:
        with engine.connect() as other:
            with other.begin():
                for key in keys:
                    assert other.scalar(
                        text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": key}
                    )
                # The single-bigint form of the same numbers is also a different lock.
                composite = (UPDATE_LOCK_KEY[0] << 32) | UPDATE_LOCK_KEY[1]
                for number in (UPDATE_LOCK_KEY[0], UPDATE_LOCK_KEY[1], composite):
                    assert other.scalar(text("SELECT pg_try_advisory_xact_lock(:n)"), {"n": number})
                subids = set(
                    other.scalars(
                        text(
                            "SELECT objsubid FROM pg_locks WHERE locktype = 'advisory' "
                            "AND classid::bigint = :c AND objid::bigint = :o AND granted"
                        ),
                        {"c": UPDATE_LOCK_KEY[0], "o": UPDATE_LOCK_KEY[1]},
                    )
                )
                # Both granted at once: the bigint lock with the same halves (objsubid 1) and
                # the update lock (objsubid 2) are different locks.
                assert subids == {1, 2}
    finally:
        holder.release()
    engine.dispose()
    # And with service locks held, the update lock is still available.
    with engine.connect() as other, other.begin():
        for key in keys:
            other.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
        held_lock(settings).release()
    engine.dispose()


# --- plan and read-only health -----------------------------------------------------------------


def test_plan_does_not_take_the_lock_and_writes_nothing(db, factory, settings, registry, modes):
    before, runs = canonical(db), count(db, UpdateRun)
    holder = held_lock(settings)
    try:
        plan = asyncio.run(plan_update(settings, factory, sources=KEYS, skip_ai=True))
        assert plan["status"] == "plan" and plan["lock"]["state"] == "held"
        assert plan["precheck"]["status"] == SUCCESS
    finally:
        holder.release()
    plan = asyncio.run(plan_update(settings, factory, sources=KEYS, skip_ai=True))
    assert plan["lock"]["state"] == "free"
    assert modes["fetches"] == 0 and canonical(db) == before and count(db, UpdateRun) == runs


def test_source_health_is_read_only_and_deterministic(db, factory, settings, registry, modes):
    modes["ops_news"] = "http_503"
    run(settings, factory, run_id="h1")
    db.execute(text("SAVEPOINT ro"))
    first = source_health(db)
    second = source_health(db)
    db.execute(text("ROLLBACK TO SAVEPOINT ro"))
    assert json.dumps(first, sort_keys=False) == json.dumps(second, sort_keys=False)
    assert [row["source"] for row in first["sources"]][:1] == [next(iter(SOURCES))]
    assert set(first["sources"][0]) == {
        "source",
        "kind",
        "health",
        "last_attempt_at",
        "last_outcome",
        "last_success_at",
        "last_failure_at",
        "consecutive_failures",
        "recent_failures",
        "recent_attempts",
        "total_attempts",
        "latest_counts",
        "last_error",
    }


def test_source_health_runs_inside_a_read_only_transaction(settings):
    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = source_health(session)
        session.rollback()
    engine.dispose()
    assert report["summary"]["sources"] == len(report["sources"]) > 0
