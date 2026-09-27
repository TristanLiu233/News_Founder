"""An externally published company ranking definition."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin
from industry_intelligence.db.models.types import domain_enum
from industry_intelligence.domain.enums import Domain

if TYPE_CHECKING:
    from industry_intelligence.db.models.ranking_snapshot import RankingSnapshot


class Ranking(TimestampMixin, Base):
    __tablename__ = "rankings"
    __table_args__ = (
        UniqueConstraint("domain", "publisher", "name", name="publisher_ranking_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    domain: Mapped[Domain] = mapped_column(domain_enum(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    methodology: Mapped[str | None] = mapped_column(Text, nullable=True)
    metric_type: Mapped[str] = mapped_column(String(100), nullable=False)
    frequency: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    snapshots: Mapped[list[RankingSnapshot]] = relationship(
        "RankingSnapshot", back_populates="ranking"
    )
