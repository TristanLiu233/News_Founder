"""Phase 1 committed ingestion snapshots and immutable display fields."""

import sqlalchemy as sa
from alembic import op

revision = "20260926_02"
down_revision = "20260926_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rank_observations", sa.Column("title", sa.String(500)))
    op.add_column("rank_observations", sa.Column("url", sa.String(2048)))
    op.add_column("rank_observations", sa.Column("item_metadata", sa.JSON()))
    op.create_unique_constraint(
        "observation_snapshot_subject",
        "rank_observations",
        ["rank_list_id", "observed_at", "subject_id"],
    )
    op.create_unique_constraint(
        "observation_snapshot_rank", "rank_observations", ["rank_list_id", "observed_at", "rank"]
    )
    op.create_table(
        "ingestion_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("batch_key", sa.String(100), nullable=False),
        sa.Column("rank_list_id", sa.Uuid(), sa.ForeignKey("rank_lists.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("source_key", "batch_key", name="ingestion_batch_identity"),
        sa.UniqueConstraint("rank_list_id", "observed_at", name="list_snapshot_time"),
    )
    op.create_index("ix_batch_latest", "ingestion_batches", ["rank_list_id", "observed_at"])


def downgrade():
    op.drop_table("ingestion_batches")
    for name in ("observation_snapshot_subject", "observation_snapshot_rank"):
        op.drop_constraint(name, "rank_observations", type_="unique")
    for name in ("title", "url", "item_metadata"):
        op.drop_column("rank_observations", name)
