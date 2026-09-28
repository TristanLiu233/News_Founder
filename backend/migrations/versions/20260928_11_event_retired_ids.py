"""Stable permalinks for Event IDs retired by deterministic rebuilds.

Revision ID: 20260928_11
Revises: 20260928_10
"""

import sqlalchemy as sa
from alembic import op

revision = "20260928_11"
down_revision = "20260928_10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_retired_ids",
        sa.Column("retired_id", sa.Uuid(), nullable=False),
        sa.Column("representative_content_id", sa.Uuid(), nullable=True),
        sa.Column("content_item_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column(
            "retired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["representative_content_id"],
            ["content_items.id"],
            name=op.f("fk_event_retired_ids_representative_content_id_content_items"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("retired_id", name=op.f("pk_event_retired_ids")),
    )
    op.create_index(
        op.f("ix_event_retired_ids_representative_content_id"),
        "event_retired_ids",
        ["representative_content_id"],
    )


def downgrade():
    op.drop_table("event_retired_ids")
