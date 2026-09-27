"""A point-in-time subject position on an external ranked list."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base

if TYPE_CHECKING:
    from industry_intelligence.db.models.rank_list import RankList
    from industry_intelligence.db.models.source import Source
    from industry_intelligence.db.models.subject import Subject


class RankObservation(Base):
    __tablename__ = "rank_observations"
    __table_args__ = (
        CheckConstraint("rank > 0", name="rank_positive"),
        UniqueConstraint("rank_list_id", "observed_at", "rank", name="observation_snapshot_rank"),
    )
    title: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(2048))
    item_metadata: Mapped[dict | None] = mapped_column(JSON)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank_list_id: Mapped[UUID] = mapped_column(
        ForeignKey("rank_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_metric: Mapped[dict[str, Any] | str | int | float | None] = mapped_column(
        JSON, nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    subject: Mapped[Subject] = relationship("Subject", back_populates="rank_observations")
    source: Mapped[Source] = relationship("Source", back_populates="rank_observations")
    rank_list: Mapped[RankList] = relationship("RankList", back_populates="observations")
