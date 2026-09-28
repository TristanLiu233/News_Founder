"""Phase 7 AI enrichment: generated artifacts and reviewable suggestions.

AI output is an enhancement layer, never canonical truth. Nothing in these tables changes
Events, Subjects, Companies, links, identities, or rankings; a suggestion reaches the canonical
layer only through an explicit reviewed action (CLI) that calls an existing reviewed service.

* ``AIArtifact``: one generation attempt for one entity (``entity_type`` + ``entity_key``), one
  ``artifact_type``, and one ``locale``, with full provenance: provider, served model,
  ``prompt_version``, ``input_hash`` over the exact evidence sent, and the evidence content IDs.
  ``status``: ``valid`` (schema- and evidence-validated), ``failed`` (provider error or
  validation failure; no output kept), ``stale`` (the entity's evidence changed after
  generation), ``rejected`` (hidden by a reviewer). At most one ``valid`` artifact exists per
  (entity, type, locale, input hash, provider, model), which is the replay cache.
* ``AISuggestion``: a pending/accepted/rejected proposal (an entity link candidate, an entity
  not in the catalog, or a same-event merge candidate) with the evidence content IDs and the
  artifact it came from. Candidate IDs are resolved by deterministic catalog lookup, never
  chosen by the model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from industry_intelligence.db.base import Base
from industry_intelligence.db.models.mixins import utc_now

ARTIFACT_STATUSES = ("valid", "failed", "stale", "rejected")
SUGGESTION_STATUSES = ("pending", "accepted", "rejected")


class AIArtifact(Base):
    __tablename__ = "ai_artifacts"
    __table_args__ = (
        Index("ix_ai_artifacts_entity", "entity_type", "entity_key", "artifact_type", "locale"),
        Index(
            "uq_ai_artifacts_valid_cache",
            "entity_type",
            "entity_key",
            "artifact_type",
            "locale",
            "input_hash",
            "provider",
            "model",
            unique=True,
            postgresql_where=text("status = 'valid'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # "event" (entity_key = Event UUID) or "event_pair" (entity_key = "uuidA:uuidB", sorted).
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(120), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(60), nullable=False)
    # UI locale of the text ("en", "zh-CN"), or "und" for non-displayed, language-neutral output.
    locale: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_content_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Short, non-sensitive failure label (timeout, rate_limited, schema_validation, …).
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Attempts, token usage, validation messages; never API keys or raw provider payloads.
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class AISuggestion(Base):
    __tablename__ = "ai_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "suggestion_type",
            "entity_type",
            "entity_key",
            "candidate_key",
            name="ai_suggestion_identity",
        ),
        Index("ix_ai_suggestions_status", "status", "suggestion_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # event_subject_candidate | event_company_candidate | new_entity_candidate
    # | event_merge_candidate
    suggestion_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(120), nullable=False)
    # subject | company | event | none (not in the catalog)
    candidate_type: Mapped[str] = mapped_column(String(20), nullable=False)
    candidate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    candidate_text: Mapped[str] = mapped_column(String(500), nullable=False)
    # Deduplication key: the candidate ID, or "text:<normalized name>" when not in the catalog.
    candidate_key: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_content_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What an accepted review applied (link rows, merge result), for the audit trail.
    review_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    suggestion_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
