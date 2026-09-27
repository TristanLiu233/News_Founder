"""Canonical cross-domain subject ORM model."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin
from industry_intelligence.db.models.types import domain_enum, subject_type_enum
from industry_intelligence.domain.enums import Domain, SubjectType

if TYPE_CHECKING:
    from industry_intelligence.db.models.rank_observation import RankObservation


class Subject(TimestampMixin, Base):
    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("domain", "subject_type", "canonical_name", name="subject_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    domain: Mapped[Domain] = mapped_column(domain_enum(), nullable=False, index=True)
    subject_type: Mapped[SubjectType] = mapped_column(subject_type_enum(), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    rank_observations: Mapped[list[RankObservation]] = relationship(
        "RankObservation", back_populates="subject"
    )
