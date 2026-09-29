"""Operations-2: update run history and append-only source attempts.

Only adds two operational tables; no existing table is touched. The downgrade drops exactly
these two tables (operational history, not canonical data).

Revision ID: 20260930_12
Revises: 20260928_11
"""

import sqlalchemy as sa
from alembic import op

revision = "20260930_12"
down_revision = "20260928_11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "update_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("stage_summary", sa.JSON(), nullable=False),
        sa.Column("interrupted_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'degraded', 'failed', 'interrupted')",
            name=op.f("ck_update_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["interrupted_by"],
            ["update_runs.id"],
            name=op.f("fk_update_runs_interrupted_by_update_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_update_runs")),
    )
    op.create_index(op.f("ix_update_runs_run_id"), "update_runs", ["run_id"])
    op.create_index("ix_update_runs_started_at", "update_runs", ["started_at"])
    op.create_table(
        "source_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("update_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("failure_phase", sa.String(20), nullable=True),
        sa.Column("error_category", sa.String(40), nullable=True),
        sa.Column("exception_type", sa.String(100), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.String(300), nullable=True),
        sa.Column("fetched_count", sa.Integer(), nullable=True),
        sa.Column("stored_count", sa.Integer(), nullable=True),
        sa.Column("replayed_count", sa.Integer(), nullable=True),
        sa.Column("invalid_count", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'replayed', 'unchanged', 'failed')",
            name=op.f("ck_source_attempts_outcome"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('ranking', 'company_ranking', 'content')",
            name=op.f("ck_source_attempts_source_kind"),
        ),
        sa.CheckConstraint(
            "failure_phase IS NULL OR failure_phase IN ('transport', 'parse', 'persist', 'other')",
            name=op.f("ck_source_attempts_failure_phase"),
        ),
        sa.CheckConstraint(
            "(outcome = 'failed') = (failure_phase IS NOT NULL)",
            name=op.f("ck_source_attempts_failure_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["update_run_id"],
            ["update_runs.id"],
            name=op.f("fk_source_attempts_update_run_id_update_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_attempts")),
    )
    op.create_index(op.f("ix_source_attempts_update_run_id"), "source_attempts", ["update_run_id"])
    op.create_index(
        "ix_source_attempts_source_started", "source_attempts", ["source_key", "started_at"]
    )


def downgrade():
    op.drop_table("source_attempts")
    op.drop_table("update_runs")
