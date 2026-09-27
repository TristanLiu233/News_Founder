"""Committed snapshots, including empty ones; batch IDs provide retry identity."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from industry_intelligence.db.base import Base


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    __table_args__ = (
        UniqueConstraint("source_key", "batch_key", name="ingestion_batch_identity"),
        UniqueConstraint("rank_list_id", "observed_at", name="list_snapshot_time"),
        Index("ix_batch_latest", "rank_list_id", "observed_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_key: Mapped[str] = mapped_column(String(100))
    batch_key: Mapped[str] = mapped_column(String(100))
    rank_list_id: Mapped[UUID] = mapped_column(ForeignKey("rank_lists.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON)
