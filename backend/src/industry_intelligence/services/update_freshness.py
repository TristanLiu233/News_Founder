"""Update freshness (Deployment-2A): did a fully successful ``intelligence-update`` start recently?

The production freshness timer runs this check after the daily update window to detect what the
update's own exit code cannot report: a job that never started (host down, timer broken, Docker
not running) or repeated failed runs. It reads ``update_runs`` only (the run history written under
the update lock), never scheduler logs, and is SELECT-only: no network request, provider request,
or write.

Policy: only ``success`` counts as fresh. ``degraded`` (exit 3: some sources or the optional AI
stage failed) is not treated as a successful update; its own non-zero exit already notifies, and a
single degraded day stays fresh here because the previous day's success is still inside the window.
``running``, ``failed``, and ``interrupted`` never count. A run whose ``started_at`` lies after
``now`` (clock skew) is ignored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from industry_intelligence.db.models.operations import UpdateRun

FRESHNESS_MAX_AGE = timedelta(hours=26)
FRESH_STATUSES = ("success",)


def is_fresh(last_success_started_at, now, max_age=FRESHNESS_MAX_AGE):
    """A success that started at most ``max_age`` before ``now`` (inclusive) is fresh."""

    if last_success_started_at is None:
        return False
    return now - max_age <= last_success_started_at <= now


def _latest(session, now, statuses=None):
    query = select(UpdateRun).where(UpdateRun.started_at <= now)
    if statuses is not None:
        query = query.where(UpdateRun.status.in_(statuses))
    query = query.order_by(UpdateRun.started_at.desc(), UpdateRun.id.desc()).limit(1)
    return session.scalars(query).first()


def _run(row):
    if row is None:
        return None
    return {
        "run_id": row.run_id,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "exit_code": row.exit_code,
    }


def update_freshness(session, *, now=None, max_age=FRESHNESS_MAX_AGE):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    last_success = _latest(session, now, FRESH_STATUSES)
    latest = _latest(session, now)
    fresh = is_fresh(last_success.started_at if last_success else None, now, max_age)
    age = (now - last_success.started_at).total_seconds() / 3600 if last_success else None
    return {
        "status": "fresh" if fresh else "stale",
        "checked_at": now.isoformat(),
        "max_age_hours": max_age.total_seconds() / 3600,
        "fresh_statuses": list(FRESH_STATUSES),
        "last_success": _run(last_success),
        "last_success_age_hours": round(age, 2) if age is not None else None,
        "latest_run": _run(latest),
    }


def render_text(report):
    success = report["last_success"]
    latest = report["latest_run"]
    lines = [
        f"Update freshness: {report['status'].upper()}",
        f"  checked at: {report['checked_at']}",
        f"  window: last {report['max_age_hours']:g} h, "
        f"statuses {', '.join(report['fresh_statuses'])}",
        "  last success: "
        + (
            f"{success['started_at']} (run {success['run_id']}, "
            f"{report['last_success_age_hours']:g} h ago)"
            if success
            else "none recorded"
        ),
        "  latest run: "
        + (
            f"{latest['started_at']} status={latest['status']} exit={latest['exit_code']} "
            f"(run {latest['run_id']})"
            if latest
            else "none recorded"
        ),
    ]
    return "\n".join(lines)
