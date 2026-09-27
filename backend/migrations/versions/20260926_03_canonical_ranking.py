"""Phase 2 canonical identities and composite ranking history.

The old subject-per-snapshot constraint is removed to retain every original
observation when two subjects merge. Fusion takes the best rank per list.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260926_03"
down_revision = "20260926_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sources", sa.Column("primary_language", sa.String(20), nullable=False, server_default="en")
    )
    op.drop_constraint("observation_snapshot_subject", "rank_observations", type_="unique")

    # Explicit, frozen table definitions; never import mutable application metadata.
    def stamp():
        return [
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        ]

    op.create_table(
        "subject_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column("alias", sa.String(500), nullable=False),
        sa.Column("normalized_alias", sa.String(500), nullable=False),
        sa.Column("locale", sa.String(20), nullable=False),
        sa.Column("alias_type", sa.String(40), nullable=False),
        sa.Column("alias_source", sa.String(100), nullable=False),
        *stamp(),
        sa.UniqueConstraint("subject_id", "alias", "locale", name="subject_alias_identity"),
    )
    op.create_index("ix_subject_aliases_subject_id", "subject_aliases", ["subject_id"])
    op.create_index("ix_alias_normalized", "subject_aliases", ["normalized_alias"])
    op.create_table(
        "subject_external_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.String(2048), nullable=False),
        sa.Column("canonical_url", sa.String(2048)),
        sa.Column("canonical_id", sa.String(255)),
        sa.Column("normalized_name", sa.String(500), nullable=False),
        sa.Column("asset_class", sa.String(30)),
        sa.Column("resolution_strategy", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("matched_alias", sa.String(500)),
        *stamp(),
        sa.UniqueConstraint("source_id", "external_id", name="external_identity_unique"),
    )
    for name, col in [
        ("ix_subject_external_identities_subject_id", "subject_id"),
        ("ix_identity_canonical_url", "canonical_url"),
        ("ix_identity_canonical_id", "canonical_id"),
        ("ix_subject_external_identities_normalized_name", "normalized_name"),
    ]:
        op.create_index(name, "subject_external_identities", [col])
    op.create_table(
        "composite_ranking_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("domain", sa.String(30), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rrf_k", sa.Float(), nullable=False),
        sa.Column("freshness_half_life", sa.Float(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.UniqueConstraint("domain", "input_hash", name="composite_run_identity"),
    )
    op.create_index(
        "ix_composite_latest", "composite_ranking_runs", ["domain", "status", "calculated_at"]
    )
    op.create_table(
        "composite_ranking_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("composite_ranking_runs.id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("best_source_rank", sa.Integer(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "subject_id", name="composite_entry_subject"),
        sa.UniqueConstraint("run_id", "rank", name="composite_entry_rank"),
    )
    for col in ["run_id", "subject_id"]:
        op.create_index("ix_composite_ranking_entries_" + col, "composite_ranking_entries", [col])


def downgrade():
    # A merge can legitimately create duplicate canonical appearances. Refuse
    # a destructive downgrade rather than discard original observations.
    op.create_unique_constraint(
        "observation_snapshot_subject",
        "rank_observations",
        ["rank_list_id", "observed_at", "subject_id"],
    )
    for table in [
        "composite_ranking_entries",
        "composite_ranking_runs",
        "subject_external_identities",
        "subject_aliases",
    ]:
        op.drop_table(table)
    op.drop_column("sources", "primary_language")
