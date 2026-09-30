"""Deployment-2A freshness check on PostgreSQL: reads ``update_runs`` only, inside a read-only
transaction that is rolled back. Rows are placed far from the live history (2034), and ``now`` is
passed explicitly, so earlier real runs never decide a result."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from industry_intelligence.config import get_settings
from industry_intelligence.db.models import UpdateRun
from industry_intelligence.services.update_freshness import update_freshness

pytestmark = pytest.mark.integration

NOW = datetime(2034, 5, 2, 2, 30, tzinfo=UTC)  # 12:30 Australia/Sydney (AEST)


@pytest.fixture
def db():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
    engine.dispose()


def run(db, status, hours_ago, exit_code=None):
    started = NOW - timedelta(hours=hours_ago)
    unfinished = status in {"running", "interrupted"}
    row = UpdateRun(
        id=uuid4(),
        run_id=f"freshness-{uuid4().hex[:8]}",
        status=status,
        started_at=started,
        finished_at=None if unfinished else started + timedelta(minutes=1),
        exit_code=exit_code,
        options={"skip_ai": True},
        stage_summary={},
    )
    db.add(row)
    db.flush()
    return row


def test_recent_success_is_fresh(db):
    success = run(db, "success", 2.5, 0)

    report = update_freshness(db, now=NOW)

    assert report["status"] == "fresh"
    assert report["last_success"]["run_id"] == success.run_id
    assert report["last_success_age_hours"] == 2.5
    assert report["latest_run"]["run_id"] == success.run_id


def test_missed_run_is_stale_after_26_hours(db):
    run(db, "success", 26.5, 0)  # yesterday 10:00, nothing today

    report = update_freshness(db, now=NOW)

    assert report["status"] == "stale"
    assert report["last_success_age_hours"] == 26.5


def test_failed_interrupted_running_and_degraded_runs_never_count(db):
    run(db, "success", 50, 0)
    run(db, "failed", 26.4, 1)
    run(db, "interrupted", 3)
    run(db, "degraded", 2.5, 3)
    latest = run(db, "running", 0.2)

    report = update_freshness(db, now=NOW)

    assert report["status"] == "stale"
    assert report["last_success_age_hours"] == 50
    assert report["latest_run"]["run_id"] == latest.run_id
    assert report["latest_run"]["status"] == "running"


def test_one_degraded_day_stays_fresh_on_the_previous_success(db):
    run(db, "success", 25.5, 0)  # the 25-hour day when daylight saving ends: 10:00 → 12:30 next day
    run(db, "degraded", 2.5, 3)

    report = update_freshness(db, now=NOW)

    assert report["status"] == "fresh"
    assert report["latest_run"]["status"] == "degraded"


def test_runs_after_now_are_ignored_and_window_is_configurable(db):
    run(db, "success", -1, 0)
    run(db, "success", 30, 0)

    assert update_freshness(db, now=NOW)["status"] == "stale"
    assert update_freshness(db, now=NOW, max_age=timedelta(hours=48))["status"] == "fresh"


def test_check_is_select_only_in_a_read_only_transaction(db):
    run(db, "success", 1, 0)
    before = db.scalar(select(func.count()).select_from(UpdateRun))
    db.commit()  # releases the savepoint; the outer transaction is still rolled back afterwards

    db.execute(text("SET TRANSACTION READ ONLY"))
    report = update_freshness(db, now=NOW)

    assert report["status"] == "fresh"
    assert db.scalar(select(func.count()).select_from(UpdateRun)) == before


def test_naive_now_is_rejected(db):
    with pytest.raises(ValueError):
        update_freshness(db, now=NOW.replace(tzinfo=None))
