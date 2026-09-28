"""Phase 7 AI enrichment: generated artifacts and reviewable suggestions.

Adds ``ai_artifacts`` and ``ai_suggestions``. Existing tables and rows are not modified; AI rows
never change canonical data. The downgrade refuses to discard human review decisions (reviewed
suggestions or reviewer-rejected artifacts).

Revision ID: 20260928_10
Revises: 20260928_09
"""

import sqlalchemy as sa
from alembic import op

revision = "20260928_10"
down_revision = "20260928_09"
branch_labels = None
depends_on = None


def _timestamp(name, nullable=False):
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if not nullable else None,
        nullable=nullable,
    )


def upgrade():
    op.create_table(
        "ai_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_artifacts")),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_key", sa.String(120), nullable=False),
        sa.Column("artifact_type", sa.String(60), nullable=False),
        sa.Column("locale", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("source_content_ids", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(60), nullable=True),
        _timestamp("generated_at"),
        _timestamp("reviewed_at", nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_ai_artifacts_entity",
        "ai_artifacts",
        ["entity_type", "entity_key", "artifact_type", "locale"],
    )
    op.create_index(
        "uq_ai_artifacts_valid_cache",
        "ai_artifacts",
        [
            "entity_type",
            "entity_key",
            "artifact_type",
            "locale",
            "input_hash",
            "provider",
            "model",
        ],
        unique=True,
        postgresql_where=sa.text("status = 'valid'"),
    )

    op.create_table(
        "ai_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_suggestions")),
        sa.Column("suggestion_type", sa.String(40), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_key", sa.String(120), nullable=False),
        sa.Column("candidate_type", sa.String(20), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_text", sa.String(500), nullable=False),
        sa.Column("candidate_key", sa.String(200), nullable=False),
        sa.Column("evidence_content_ids", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "artifact_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ai_artifacts.id",
                ondelete="SET NULL",
                name=op.f("fk_ai_suggestions_artifact_id_ai_artifacts"),
            ),
            nullable=True,
        ),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _timestamp("created_at"),
        _timestamp("reviewed_at", nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("review_result", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "suggestion_type",
            "entity_type",
            "entity_key",
            "candidate_key",
            name=op.f("ai_suggestion_identity"),
        ),
    )
    op.create_index("ix_ai_suggestions_status", "ai_suggestions", ["status", "suggestion_type"])


def downgrade():
    bind = op.get_bind()
    reviewed = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM ai_suggestions WHERE status <> 'pending') "
            "OR EXISTS (SELECT 1 FROM ai_artifacts WHERE status = 'rejected')"
        )
    ).scalar()
    if reviewed:
        raise RuntimeError("AI review decisions exist; refusing to discard them")
    op.drop_table("ai_suggestions")
    op.drop_table("ai_artifacts")
