"""Search-only projections. Canonical identity remains in Subject/SubjectAlias."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from industry_intelligence.db.base import Base


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "VECTOR"


class SearchDocument(Base):
    __tablename__ = "search_documents"
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    subject_type: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list] = mapped_column(JSONB)
    recent_titles: Mapped[list] = mapped_column(JSONB)
    searchable_text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', searchable_text)", persisted=True)
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubjectEmbedding(Base):
    __tablename__ = "subject_embeddings"
    __table_args__ = (
        CheckConstraint("dimension > 0", name="dimension_positive"),
        CheckConstraint("vector_dims(embedding) = dimension", name="dimension_matches"),
    )
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_documents.subject_id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list] = mapped_column(Vector)
    embedding_model: Mapped[str] = mapped_column(Text)
    dimension: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SubjectRedirect(Base):
    __tablename__ = "subject_redirects"
    __table_args__ = (CheckConstraint("duplicate_id != canonical_id", name="redirect_distinct"),)
    duplicate_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), primary_key=True)
    canonical_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), index=True)
