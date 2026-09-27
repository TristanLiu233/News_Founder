"""Company profiles (Phase 5): sourced facts, their provenance, subject links, and redirects.

``CompanyProfileSource`` keeps one current normalized record per (company, source) with its
retrieval provenance; ``CompanyProfile`` is the field-level merge of those records, and
``field_sources`` names the source behind every value. Nothing here is generated or scored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin, utc_now


class CompanyProfile(TimestampMixin, Base):
    __tablename__ = "company_profiles"

    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    legal_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    legal_name_locale: Mapped[str | None] = mapped_column(String(20), nullable=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # ISO 3166-1 alpha-2 country of the headquarters.
    country: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    headquarters: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    business_areas: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    key_products: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # field name -> source key of the record that supplied the value.
    field_sources: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    profile_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyProfileSource(TimestampMixin, Base):
    """The current normalized record one source publishes about one company."""

    __tablename__ = "company_profile_sources"
    __table_args__ = (
        UniqueConstraint("company_id", "source_key", name="company_profile_source_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # When the current content was first retrieved, and when it was last confirmed unchanged.
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class CompanySubjectLink(Base):
    """A deterministic association between a company and a canonical subject."""

    __tablename__ = "company_subject_links"
    __table_args__ = (UniqueConstraint("company_id", "subject_id", name="company_subject_link"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class CompanyRedirect(Base):
    """A reviewed merge: requests for ``duplicate_id`` resolve to ``canonical_id``."""

    __tablename__ = "company_redirects"
    __table_args__ = (
        CheckConstraint("duplicate_id != canonical_id", name="company_redirect_distinct"),
    )

    duplicate_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    canonical_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    merged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
