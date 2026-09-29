"""Operational history of ``intelligence-update`` (Operations-2); never canonical data.

* ``UpdateRun``: one row per real update invocation, written only after the process acquired
  the global update lock. A retry with the same ``--run-id`` is a new invocation (a new row).
  A row left ``running`` by a process that died is marked ``interrupted`` by the next process
  that acquires the lock (the lock is the evidence that the old process no longer runs); time
  alone never marks a run interrupted.
* ``SourceAttempt``: one append-only row per source attempted by a run, with the outcome
  (``success``, ``replayed``, ``unchanged``, ``failed``), the failing phase and a sanitized error
  summary, and the attempt's counts. Source health is derived from these rows on read; there is
  no current-health table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from industry_intelligence.db.base import Base

RUN_STATUSES = ("running", "success", "degraded", "failed", "interrupted")
ATTEMPT_OUTCOMES = ("success", "replayed", "unchanged", "failed")
FAILURE_PHASES = ("transport", "parse", "persist", "other")
SOURCE_KINDS = ("ranking", "company_ranking", "content")


def _in(column, values):
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class UpdateRun(Base):
    __tablename__ = "update_runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        Index("ix_update_runs_started_at", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stage_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # The run whose lock acquisition proved this ``running`` row was abandoned.
    interrupted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("update_runs.id", ondelete="SET NULL")
    )


class SourceAttempt(Base):
    __tablename__ = "source_attempts"
    __table_args__ = (
        CheckConstraint(_in("outcome", ATTEMPT_OUTCOMES), name="outcome"),
        CheckConstraint(_in("source_kind", SOURCE_KINDS), name="source_kind"),
        CheckConstraint(
            f"failure_phase IS NULL OR {_in('failure_phase', FAILURE_PHASES)}",
            name="failure_phase",
        ),
        # A failed attempt always says where it failed; a successful one never does.
        CheckConstraint(
            "(outcome = 'failed') = (failure_phase IS NOT NULL)", name="failure_consistency"
        ),
        Index("ix_source_attempts_source_started", "source_key", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    update_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("update_runs.id", ondelete="CASCADE"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(100))
    source_kind: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(20))
    failure_phase: Mapped[str | None] = mapped_column(String(20))
    error_category: Mapped[str | None] = mapped_column(String(40))
    exception_type: Mapped[str | None] = mapped_column(String(100))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_summary: Mapped[str | None] = mapped_column(String(300))
    fetched_count: Mapped[int | None] = mapped_column(Integer)
    stored_count: Mapped[int | None] = mapped_column(Integer)
    replayed_count: Mapped[int | None] = mapped_column(Integer)
    invalid_count: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
