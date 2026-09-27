"""Auditable aliases, external identities, and immutable composite runs."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin


class SubjectAlias(TimestampMixin, Base):
    __tablename__ = "subject_aliases"
    __table_args__ = (
        UniqueConstraint("subject_id", "alias", "locale", name="subject_alias_identity"),
        Index("ix_alias_normalized", "normalized_alias"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), index=True)
    alias: Mapped[str] = mapped_column(String(500))
    normalized_alias: Mapped[str] = mapped_column(String(500))
    locale: Mapped[str] = mapped_column(String(20))
    alias_type: Mapped[str] = mapped_column(String(40))
    alias_source: Mapped[str] = mapped_column(String(100))


class SubjectExternalIdentity(TimestampMixin, Base):
    __tablename__ = "subject_external_identities"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="external_identity_unique"),
        Index("ix_identity_canonical_url", "canonical_url"),
        Index("ix_identity_canonical_id", "canonical_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), index=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str | None] = mapped_column(String(2048))
    canonical_id: Mapped[str | None] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(500), index=True)
    asset_class: Mapped[str | None] = mapped_column(String(30))
    resolution_strategy: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    matched_alias: Mapped[str | None] = mapped_column(String(500))


class CompositeRankingRun(Base):
    __tablename__ = "composite_ranking_runs"
    __table_args__ = (
        UniqueConstraint("domain", "input_hash", name="composite_run_identity"),
        Index("ix_composite_latest", "domain", "status", "calculated_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    domain: Mapped[str] = mapped_column(String(30))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rrf_k: Mapped[float] = mapped_column(Float)
    freshness_half_life: Mapped[float] = mapped_column(Float)
    config_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    configuration: Mapped[dict] = mapped_column(JSON)
    diagnostics: Mapped[dict] = mapped_column(JSON)


class CompositeRankingEntry(Base):
    __tablename__ = "composite_ranking_entries"
    __table_args__ = (
        UniqueConstraint("run_id", "subject_id", name="composite_entry_subject"),
        UniqueConstraint("run_id", "rank", name="composite_entry_rank"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("composite_ranking_runs.id"), index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    final_score: Mapped[float] = mapped_column(Float)
    source_count: Mapped[int] = mapped_column(Integer)
    best_source_rank: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON)
