"""Historical observation of a company in an external ranking."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base

if TYPE_CHECKING:
    from industry_intelligence.db.models.company import Company
    from industry_intelligence.db.models.ranking import Ranking


class RankingSnapshot(Base):
    __tablename__ = "ranking_snapshots"
    __table_args__ = (CheckConstraint("rank > 0", name="rank_positive"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    ranking_id: Mapped[UUID] = mapped_column(
        ForeignKey("rankings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)

    ranking: Mapped[Ranking] = relationship("Ranking", back_populates="snapshots")
    company: Mapped[Company] = relationship("Company", back_populates="ranking_snapshots")
