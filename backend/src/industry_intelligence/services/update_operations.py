"""Operations-2: the global update lock and the operational run/attempt history.

Lock. A real ``intelligence-update`` holds a PostgreSQL *session-level* advisory lock on its own
dedicated connection (``NullPool``, autocommit, never an ordinary stage session) for the whole
run. ``pg_try_advisory_lock`` never waits: a second process gets ``already_running`` at once.
The lock is released explicitly at the end and, because it belongs to the database session,
by PostgreSQL itself when the process or its connection dies, so it cannot stay stuck. The key
is a pair of int4 values: PostgreSQL keeps two-key advisory locks (``pg_locks.objsubid = 2``)
apart from the one-bigint ``pg_advisory_xact_lock(hashtext(...))`` locks the services take
(``objsubid = 1``), so it never collides with them.

History. ``RunRecorder`` writes ``update_runs`` and ``source_attempts`` rows in their own short
transactions. It is only created after the lock is held; opening it first marks every row still
``running`` as ``interrupted`` (the held lock proves no other update process is alive) and then
records the current run. Recording is operational metadata only; a recording failure is counted
and degrades the run, it never fails a source or rolls back canonical data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

from industry_intelligence.db.models.operations import SourceAttempt, UpdateRun

logger = logging.getLogger(__name__)

# ("II" "UP" as ASCII = intelligence update), both int4.
UPDATE_LOCK_KEY = (0x49495550, 1)
ERROR_SUMMARY_LIMIT = 300


class UpdateLock:
    """The global ``intelligence-update`` lock on a dedicated, unpooled connection."""

    def __init__(self, database_url):
        self._engine = create_engine(database_url, poolclass=NullPool)
        self._connection = None

    def acquire(self):
        connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(:classid, :objid)"),
                {"classid": UPDATE_LOCK_KEY[0], "objid": UPDATE_LOCK_KEY[1]},
            )
        except Exception:
            connection.close()
            raise
        if not acquired:
            connection.close()
            return False
        self._connection = connection
        return True

    def release(self):
        connection, self._connection = self._connection, None
        try:
            if connection is not None:
                try:
                    connection.scalar(
                        text("SELECT pg_advisory_unlock(:classid, :objid)"),
                        {"classid": UPDATE_LOCK_KEY[0], "objid": UPDATE_LOCK_KEY[1]},
                    )
                finally:
                    # Closing the unpooled connection ends the session, which releases the lock
                    # even if the explicit unlock above failed.
                    connection.close()
        finally:
            self._engine.dispose()


def lock_state(session):
    """``held`` or ``free`` right now (read-only, from ``pg_locks``); informational only, since
    another process may take or release the lock at any moment."""

    held = session.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND objsubid = 2 AND classid::bigint = :classid AND objid::bigint = :objid "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
        ),
        {"classid": UPDATE_LOCK_KEY[0], "objid": UPDATE_LOCK_KEY[1]},
    )
    return "held" if held else "free"


def _count(value):
    return value if isinstance(value, int) else None


def attempt_fields(kind, result=None, detail=None):
    """The ``source_attempts`` columns for one source outcome: ``result`` is the ingestion
    service's result dict, ``detail`` the orchestrator's safe error detail of a failure."""

    if detail is not None:
        status = detail.get("status_code")
        summary = f"{detail['phase']}: {detail['category']}"
        if status is not None:
            summary += f" {status}"
        summary += f" ({detail['error']})"
        return {
            "outcome": "failed",
            "failure_phase": detail["phase"],
            "error_category": detail["category"][:40],
            "exception_type": detail["error"][:100],
            "http_status": status,
            "error_summary": summary[:ERROR_SUMMARY_LIMIT],
            "details": {},
        }
    fetched, stored = _count(result.get("fetched_count")), _count(result.get("persisted_count"))
    adapter_rejected = _count(result.get("failed_count")) or 0
    if kind == "ranking":
        if result.get("replayed"):
            # The run's batch already existed: nothing was requested or written.
            return {
                "outcome": "replayed",
                "fetched_count": None,
                "stored_count": 0,
                "replayed_count": _count(result.get("skipped_count")),
                "invalid_count": 0,
                "details": {"batch_id": result.get("batch_id")},
            }
        invalid = adapter_rejected + (_count(result.get("skipped_count")) or 0)
        return {
            "outcome": "success",
            "fetched_count": fetched,
            "stored_count": stored,
            "replayed_count": 0,
            "invalid_count": invalid,
            "details": {"batch_id": result.get("batch_id")},
        }
    if kind == "company_ranking":
        outcome = (
            "replayed"
            if result.get("replayed")
            else "unchanged"
            if result.get("unchanged")
            else "success"
        )
        invalid = adapter_rejected + (_count(result.get("skipped_count")) or 0)
        return {
            "outcome": outcome,
            "fetched_count": fetched,
            "stored_count": stored,
            "replayed_count": 0,
            "invalid_count": invalid,
            "details": {"snapshot_key": result.get("snapshot_key")},
        }
    details = {"link_error": result["link_error"]} if result.get("link_error") else {}
    if result.get("news_ranking"):
        # A publisher's ranked article list: every run stores one list snapshot (``success``),
        # whether or not its articles were new; the run's existing snapshot is a replay.
        details["batch_id"] = result.get("batch_id")
        return {
            "outcome": "replayed" if result.get("replayed") else "success",
            "fetched_count": fetched,
            "stored_count": stored,
            "replayed_count": _count(result.get("replayed_count")),
            "invalid_count": adapter_rejected + (_count(result.get("invalid_count")) or 0),
            "details": details,
        }
    return {
        # Zero new items is a successful, unchanged fetch, never a failure.
        "outcome": "success" if stored else "unchanged",
        "fetched_count": fetched,
        "stored_count": stored,
        "replayed_count": _count(result.get("replayed_count")),
        "invalid_count": adapter_rejected + (_count(result.get("invalid_count")) or 0),
        "details": details,
    }


class RunRecorder:
    """Writes one ``update_runs`` row and its ``source_attempts``; created only under the lock."""

    def __init__(self, session_factory):
        self._factory = session_factory
        self.update_run_id = None
        self.interrupted = []
        self.run_recorded = False
        self.attempts_recorded = 0
        self.attempts_unrecorded = 0
        self.errors = []

    @property
    def failed(self):
        return not self.run_recorded or self.attempts_unrecorded > 0 or bool(self.errors)

    def open(self, run_id, started_at, options):
        try:
            with self._factory.begin() as session:
                row = UpdateRun(
                    run_id=run_id,
                    status="running",
                    started_at=started_at,
                    options=options,
                    stage_summary={},
                )
                session.add(row)
                session.flush()
                stale = session.scalars(
                    select(UpdateRun)
                    .where(UpdateRun.status == "running", UpdateRun.id != row.id)
                    .order_by(UpdateRun.started_at, UpdateRun.id)
                ).all()
                for previous in stale:
                    previous.status = "interrupted"
                    previous.interrupted_by = row.id
                interrupted = [previous.run_id for previous in stale]
                update_run_id = row.id
        except Exception as exc:
            self.errors.append({"step": "open", "error": type(exc).__name__})
            logger.error("event=run_record_failed step=open error=%s", type(exc).__name__)
            return
        self.update_run_id, self.interrupted, self.run_recorded = update_run_id, interrupted, True
        for previous in interrupted:
            logger.warning("run=%s event=previous_run_interrupted previous=%s", run_id, previous)

    def attempt(self, kind, key, started_at, finished_at, result=None, detail=None):
        if self.update_run_id is None:
            self.attempts_unrecorded += 1
            return
        try:
            with self._factory.begin() as session:
                session.add(
                    SourceAttempt(
                        update_run_id=self.update_run_id,
                        source_key=key,
                        source_kind=kind,
                        started_at=started_at,
                        finished_at=finished_at,
                        **attempt_fields(kind, result, detail),
                    )
                )
        except Exception as exc:
            self.attempts_unrecorded += 1
            logger.error("source=%s event=attempt_record_failed error=%s", key, type(exc).__name__)
            return
        self.attempts_recorded += 1

    def close(self, status, exit_code, stage_summary):
        if self.update_run_id is None:
            return
        try:
            with self._factory.begin() as session:
                row = session.get(UpdateRun, self.update_run_id)
                row.status = status
                row.exit_code = exit_code
                row.finished_at = datetime.now(UTC)
                row.stage_summary = stage_summary
        except Exception as exc:
            self.errors.append({"step": "close", "error": type(exc).__name__})
            logger.error("event=run_record_failed step=close error=%s", type(exc).__name__)

    def summary(self):
        return {
            "update_run_id": str(self.update_run_id) if self.update_run_id else None,
            "run_recorded": self.run_recorded,
            "attempts_recorded": self.attempts_recorded,
            "attempts_unrecorded": self.attempts_unrecorded,
            "interrupted_runs_marked": self.interrupted,
            "errors": self.errors,
        }
