"""A concrete ranked list published by a source."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin
from industry_intelligence.db.models.types import domain_enum
from industry_intelligence.domain.enums import Domain

if TYPE_CHECKING:
    from industry_intelligence.db.models.rank_observation import RankObservation
    from industry_intelligence.db.models.source import Source


class RankList(TimestampMixin, Base):
    __tablename__ = "rank_lists"
    __table_args__ = (UniqueConstraint("source_id", "name", name="source_rank_list_name"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[Domain] = mapped_column(domain_enum(), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    list_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ranking", server_default="ranking"
    )

    source: Mapped[Source] = relationship("Source", back_populates="rank_lists")
    observations: Mapped[list[RankObservation]] = relationship(
        "RankObservation", back_populates="rank_list"
    )
