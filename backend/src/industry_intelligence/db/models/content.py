"""General source content and deterministic canonical-subject associations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import TimestampMixin
from industry_intelligence.db.models.types import domain_enum
from industry_intelligence.domain.enums import Domain


class ContentItem(TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (
        UniqueConstraint("source_id", "identity_key", name="content_source_identity"),
        Index("ix_content_source_published", "source_id", "published_at"),
        Index("ix_content_domain_published", "domain", "published_at"),
        Index("ix_content_url", "url"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    domain: Mapped[Domain] = mapped_column(domain_enum())
    content_type: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str | None] = mapped_column(String(1024))
    identity_key: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(1000))
    url: Mapped[str] = mapped_column(String(2048))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    item_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))


class ContentSubjectLink(TimestampMixin, Base):
    __tablename__ = "content_subject_links"
    __table_args__ = (
        UniqueConstraint("content_item_id", "subject_id", name="content_subject_identity"),
        Index("ix_content_subject_subject_item", "subject_id", "content_item_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    content_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE")
    )
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(40), default="mentions")
    strategy: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
