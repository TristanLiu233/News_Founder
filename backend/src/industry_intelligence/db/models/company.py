"""Minimal company identity layer for external company rankings (Phase 4).

A company is one real-world entity across domains; the domains it appears in come from the
rankings that list it. Full company profiles are Phase 5 and intentionally absent here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from industry_intelligence.db.models.ranking_snapshot import RankingEntry


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Not unique: two distinct companies may share a display name.
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    aliases: Mapped[list[CompanyAlias]] = relationship(
        "CompanyAlias", back_populates="company", cascade="all, delete-orphan"
    )
    identities: Mapped[list[CompanyIdentity]] = relationship(
        "CompanyIdentity", back_populates="company", cascade="all, delete-orphan"
    )
    ranking_entries: Mapped[list[RankingEntry]] = relationship(
        "RankingEntry", back_populates="company"
    )


class CompanyAlias(TimestampMixin, Base):
    """A name under which a company was published, with provenance."""

    __tablename__ = "company_aliases"
    __table_args__ = (
        UniqueConstraint("company_id", "normalized_alias", name="company_alias_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    locale: Mapped[str | None] = mapped_column(String(20), nullable=True)
    alias_type: Mapped[str] = mapped_column(String(40), nullable=False)
    alias_source: Mapped[str] = mapped_column(String(100), nullable=False)

    company: Mapped[Company] = relationship("Company", back_populates="aliases")


class CompanyIdentity(TimestampMixin, Base):
    """A stable external identifier such as a source ticker symbol or wiki article."""

    __tablename__ = "company_identities"
    __table_args__ = (UniqueConstraint("scheme", "value", name="company_identity_value"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheme: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)

    company: Mapped[Company] = relationship("Company", back_populates="identities")
