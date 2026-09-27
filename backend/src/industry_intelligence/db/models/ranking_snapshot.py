"""Immutable observations of an external company ranking and their entries."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import utc_now

if TYPE_CHECKING:
    from industry_intelligence.db.models.company import Company
    from industry_intelligence.db.models.ranking import Ranking


class RankingSnapshot(Base):
    """One successful observation of a ranking; never updated after insert.

    ``snapshot_key`` is the source-specific identity (a Wikipedia revision or the UTC day of a
    daily list) that makes repeated ingestion idempotent. ``effective_at`` orders history:
    the publisher's revision time when known, otherwise the observation time.
    """

    __tablename__ = "ranking_snapshots"
    __table_args__ = (
        UniqueConstraint("ranking_id", "snapshot_key", name="ranking_snapshot_identity"),
        CheckConstraint("status IN ('complete','partial')", name="status_valid"),
        CheckConstraint("entry_count > 0", name="entry_count_positive"),
        Index("ix_ranking_snapshots_ranking_effective", "ranking_id", "effective_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    ranking_id: Mapped[UUID] = mapped_column(
        ForeignKey("rankings.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_key: Mapped[str] = mapped_column(String(255), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_entry_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    ranking: Mapped[Ranking] = relationship("Ranking", back_populates="snapshots")
    entries: Mapped[list[RankingEntry]] = relationship(
        "RankingEntry", back_populates="snapshot", cascade="all, delete-orphan"
    )


class RankingEntry(Base):
    """A company's published position in one snapshot, keeping the raw representation."""

    __tablename__ = "ranking_entries"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "company_id", name="ranking_entry_company"),
        CheckConstraint("rank > 0", name="rank_positive"),
        Index("ix_ranking_entries_snapshot_rank", "snapshot_id", "rank"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("ranking_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_name: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    resolution_strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped[RankingSnapshot] = relationship("RankingSnapshot", back_populates="entries")
    company: Mapped[Company] = relationship("Company", back_populates="ranking_entries")
