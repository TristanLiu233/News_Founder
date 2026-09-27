"""An externally published company ranking definition.

One row is one ranking that the publisher keeps updating over time; every fetch of it is a
separate immutable ``RankingSnapshot``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, String, Text, UniqueConstraint, Uuid, true
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
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    domain: Mapped[Domain] = mapped_column(domain_enum(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    methodology: Mapped[str | None] = mapped_column(Text, nullable=True)
    metric_type: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_label: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    frequency: Mapped[str] = mapped_column(String(100), nullable=False)
    acquisition: Mapped[str] = mapped_column(String(50), nullable=False)
    primary_language: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    snapshots: Mapped[list[RankingSnapshot]] = relationship(
        "RankingSnapshot", back_populates="ranking"
    )
