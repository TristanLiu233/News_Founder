"""Source health (Operations-2): a read model over the registry and ``source_attempts``.

The inventory is the current automatic source registry (``intelligence_update.select_sources``
without narrowing: ranking sources, company rankings with ``daily_update``, content sources),
left-joined with attempt history, so a registered source that was never attempted is listed as
``never_run``; attempts of sources no longer registered are kept but not shown. Health is
derived on every read, never stored:

* ``never_run``: no attempt.
* ``failing``: the latest attempt failed.
* ``degraded``: the latest attempt succeeded but rejected malformed rows (``invalid_count > 0``).
* ``healthy``: the latest attempt succeeded without rejected rows.

``success``, ``replayed``, and ``unchanged`` are successful outcomes; zero new items is not a
failure. Earlier failures do not change the state of a source whose latest attempt is clean;
they are reported separately (``consecutive_failures``, ``recent_failures``, ``last_error``).
Everything here is SELECT-only: no network request, provider request, or write.
"""

from __future__ import annotations

from sqlalchemy import func, select

from industry_intelligence.db.models.operations import SourceAttempt, UpdateRun

HEALTH_STATES = ("healthy", "degraded", "failing", "never_run")
RECENT_WINDOW = 10
RECENT_RUNS = 5


def source_inventory():
    """(key, kind) of every automatic source, in registry order."""

    from industry_intelligence.services.intelligence_update import select_sources

    ranking, company, content = select_sources()
    return (
        [(key, "ranking") for key in ranking]
        + [(key, "company_ranking") for key in company]
        + [(key, "content") for key in content]
    )


def _iso(value):
    return value.isoformat() if value is not None else None


def health_state(latest):
    if latest is None:
        return "never_run"
    if latest.outcome == "failed":
        return "failing"
    if latest.invalid_count:
        return "degraded"
    return "healthy"


def _source_row(session, key, kind):
    recent = session.scalars(
        select(SourceAttempt)
        .where(SourceAttempt.source_key == key)
        .order_by(SourceAttempt.started_at.desc(), SourceAttempt.id.desc())
        .limit(RECENT_WINDOW)
    ).all()
    latest = recent[0] if recent else None
    row = {
        "source": key,
        "kind": kind,
        "health": health_state(latest),
        "last_attempt_at": None,
        "last_outcome": None,
        "last_success_at": None,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "recent_failures": 0,
        "recent_attempts": 0,
        "total_attempts": 0,
        "latest_counts": None,
        "last_error": None,
    }
    if latest is None:
        return row
    failed = SourceAttempt.outcome == "failed"
    of_source = SourceAttempt.source_key == key
    last_success = session.execute(
        select(SourceAttempt.started_at, SourceAttempt.finished_at)
        .where(of_source, ~failed)
        .order_by(SourceAttempt.started_at.desc(), SourceAttempt.id.desc())
        .limit(1)
    ).first()
    last_failure = session.scalar(
        select(SourceAttempt)
        .where(of_source, failed)
        .order_by(SourceAttempt.started_at.desc(), SourceAttempt.id.desc())
        .limit(1)
    )
    since = [SourceAttempt.started_at > last_success.started_at] if last_success else []
    row.update(
        last_attempt_at=_iso(latest.finished_at),
        last_outcome=latest.outcome,
        last_success_at=_iso(last_success.finished_at) if last_success else None,
        last_failure_at=_iso(last_failure.finished_at) if last_failure else None,
        consecutive_failures=session.scalar(
            select(func.count()).select_from(SourceAttempt).where(of_source, failed, *since)
        ),
        recent_failures=sum(1 for attempt in recent if attempt.outcome == "failed"),
        recent_attempts=len(recent),
        total_attempts=session.scalar(
            select(func.count()).select_from(SourceAttempt).where(of_source)
        ),
        latest_counts={
            "fetched": latest.fetched_count,
            "stored": latest.stored_count,
            "replayed": latest.replayed_count,
            "invalid": latest.invalid_count,
        },
    )
    if last_failure is not None:
        row["last_error"] = {
            "at": _iso(last_failure.finished_at),
            "phase": last_failure.failure_phase,
            "category": last_failure.error_category,
            "exception_type": last_failure.exception_type,
            "http_status": last_failure.http_status,
            "summary": last_failure.error_summary,
        }
    return row


def source_health(session):
    """The deterministic health report (read-only; the caller owns the read-only transaction)."""

    from industry_intelligence.services.update_operations import lock_state

    sources = [_source_row(session, key, kind) for key, kind in source_inventory()]
    states = [row["health"] for row in sources]
    runs = session.scalars(
        select(UpdateRun)
        .order_by(UpdateRun.started_at.desc(), UpdateRun.id.desc())
        .limit(RECENT_RUNS)
    ).all()
    return {
        "summary": {
            "sources": len(sources),
            **{state: states.count(state) for state in HEALTH_STATES},
        },
        "update": {
            "lock": lock_state(session),
            "recent_runs": [
                {
                    "run_id": run.run_id,
                    "status": run.status,
                    "started_at": _iso(run.started_at),
                    "finished_at": _iso(run.finished_at),
                    "exit_code": run.exit_code,
                }
                for run in runs
            ],
        },
        "sources": sources,
    }


def _short(value):
    # 2026-09-30T01:02:03.456+00:00 → 2026-09-30 01:02Z (text output only; JSON keeps ISO).
    return f"{value[:10]} {value[11:16]}Z" if value else "-"


def render_text(report):
    summary = report["summary"]
    counts = ", ".join(f"{state} {summary[state]}" for state in HEALTH_STATES)
    lines = [f"Source Health ({summary['sources']} automatic sources: {counts})"]
    runs = report["update"]["recent_runs"]
    latest = (
        f"latest run {runs[0]['run_id']} {runs[0]['status'].upper()} "
        f"(started {_short(runs[0]['started_at'])})"
        if runs
        else "no recorded run"
    )
    lines += [f"Update lock: {report['update']['lock']} · {latest}", ""]
    header = f"{'source':<28} {'kind':<16} {'health':<10} {'last success':<18} {'fails':>5}  "
    lines.append(header + "last error")
    for row in report["sources"]:
        fails = f"{row['consecutive_failures']}/{row['recent_failures']}"
        error = row["last_error"]["summary"] if row["last_error"] else ""
        lines.append(
            f"{row['source']:<28} {row['kind']:<16} {row['health']:<10} "
            f"{_short(row['last_success_at']):<18} {fails:>5}  {error}"
        )
    lines += ["", f"fails = consecutive / within the last {RECENT_WINDOW} attempts"]
    return "\n".join(lines)
