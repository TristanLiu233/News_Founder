"""External data-source ORM model."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin
from industry_intelligence.db.models.types import domain_enum
from industry_intelligence.domain.enums import Domain

if TYPE_CHECKING:
    from industry_intelligence.db.models.rank_list import RankList
    from industry_intelligence.db.models.rank_observation import RankObservation


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    domain: Mapped[Domain] = mapped_column(domain_enum(), nullable=False, index=True)
    primary_language: Mapped[str] = mapped_column(String(20), default="en", server_default="en")
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    rank_lists: Mapped[list[RankList]] = relationship("RankList", back_populates="source")
    rank_observations: Mapped[list[RankObservation]] = relationship(
        "RankObservation", back_populates="source"
    )
