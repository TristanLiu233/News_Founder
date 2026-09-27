"""Foundation company entity, deliberately separate from future profiles."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin
from industry_intelligence.db.models.types import domain_enum
from industry_intelligence.domain.enums import Domain

if TYPE_CHECKING:
    from industry_intelligence.db.models.ranking_snapshot import RankingSnapshot


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    domain: Mapped[Domain] = mapped_column(domain_enum(), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    ranking_snapshots: Mapped[list[RankingSnapshot]] = relationship(
        "RankingSnapshot", back_populates="company"
    )
