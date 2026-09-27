"""Create the Phase 0 foundation schema.

Revision ID: 20260926_01
Revises:
Create Date: 2026-09-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260926_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOMAIN_ENUM = sa.Enum(
    "computer",
    "finance",
    "gaming",
    name="domain_enum",
    native_enum=False,
    create_constraint=True,
)
SUBJECT_TYPE_ENUM = sa.Enum(
    "event",
    "company",
    "stock",
    "game",
    "repository",
    "paper",
    "model",
    "technology",
    name="subject_type_enum",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The image supports this extension; Phase 0 intentionally creates no vector column.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", DOMAIN_ENUM, nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("name", name=op.f("uq_sources_name")),
    )
    op.create_index(op.f("ix_sources_domain"), "sources", ["domain"], unique=False)

    op.create_table(
        "subjects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", DOMAIN_ENUM, nullable=False),
        sa.Column("subject_type", SUBJECT_TYPE_ENUM, nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subjects")),
        sa.UniqueConstraint(
            "domain",
            "subject_type",
            "canonical_name",
            name=op.f("uq_subjects_subject_identity"),
        ),
    )
    op.create_index(op.f("ix_subjects_domain"), "subjects", ["domain"], unique=False)

    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("domain", DOMAIN_ENUM, nullable=False),
        sa.Column("website", sa.String(length=2048), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("canonical_name", name=op.f("uq_companies_canonical_name")),
    )
    op.create_index(op.f("ix_companies_domain"), "companies", ["domain"], unique=False)

    op.create_table(
        "rankings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", DOMAIN_ENUM, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=False),
        sa.Column("methodology", sa.Text(), nullable=True),
        sa.Column("metric_type", sa.String(length=100), nullable=False),
        sa.Column("frequency", sa.String(length=100), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rankings")),
        sa.UniqueConstraint(
            "domain",
            "publisher",
            "name",
            name=op.f("uq_rankings_publisher_ranking_name"),
        ),
    )
    op.create_index(op.f("ix_rankings_domain"), "rankings", ["domain"], unique=False)

    op.create_table(
        "rank_lists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", DOMAIN_ENUM, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_rank_lists_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rank_lists")),
        sa.UniqueConstraint("source_id", "name", name=op.f("uq_rank_lists_source_rank_list_name")),
    )
    op.create_index(op.f("ix_rank_lists_source_id"), "rank_lists", ["source_id"], unique=False)
    op.create_index(op.f("ix_rank_lists_domain"), "rank_lists", ["domain"], unique=False)

    op.create_table(
        "rank_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("rank_list_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("raw_metric", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rank > 0", name=op.f("ck_rank_observations_rank_positive")),
        sa.ForeignKeyConstraint(
            ["rank_list_id"],
            ["rank_lists.id"],
            name=op.f("fk_rank_observations_rank_list_id_rank_lists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_rank_observations_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_rank_observations_subject_id_subjects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rank_observations")),
    )
    op.create_index(
        op.f("ix_rank_observations_subject_id"), "rank_observations", ["subject_id"], unique=False
    )
    op.create_index(
        op.f("ix_rank_observations_source_id"), "rank_observations", ["source_id"], unique=False
    )
    op.create_index(
        op.f("ix_rank_observations_rank_list_id"),
        "rank_observations",
        ["rank_list_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rank_observations_observed_at"), "rank_observations", ["observed_at"], unique=False
    )

    op.create_table(
        "ranking_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ranking_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("metric_value", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.CheckConstraint("rank > 0", name=op.f("ck_ranking_snapshots_rank_positive")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_ranking_snapshots_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ranking_id"],
            ["rankings.id"],
            name=op.f("fk_ranking_snapshots_ranking_id_rankings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ranking_snapshots")),
    )
    op.create_index(
        op.f("ix_ranking_snapshots_ranking_id"), "ranking_snapshots", ["ranking_id"], unique=False
    )
    op.create_index(
        op.f("ix_ranking_snapshots_company_id"), "ranking_snapshots", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_ranking_snapshots_observed_at"), "ranking_snapshots", ["observed_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ranking_snapshots_observed_at"), table_name="ranking_snapshots")
    op.drop_index(op.f("ix_ranking_snapshots_company_id"), table_name="ranking_snapshots")
    op.drop_index(op.f("ix_ranking_snapshots_ranking_id"), table_name="ranking_snapshots")
    op.drop_table("ranking_snapshots")

    op.drop_index(op.f("ix_rank_observations_observed_at"), table_name="rank_observations")
    op.drop_index(op.f("ix_rank_observations_rank_list_id"), table_name="rank_observations")
    op.drop_index(op.f("ix_rank_observations_source_id"), table_name="rank_observations")
    op.drop_index(op.f("ix_rank_observations_subject_id"), table_name="rank_observations")
    op.drop_table("rank_observations")

    op.drop_index(op.f("ix_rank_lists_domain"), table_name="rank_lists")
    op.drop_index(op.f("ix_rank_lists_source_id"), table_name="rank_lists")
    op.drop_table("rank_lists")

    op.drop_index(op.f("ix_rankings_domain"), table_name="rankings")
    op.drop_table("rankings")

    op.drop_index(op.f("ix_companies_domain"), table_name="companies")
    op.drop_table("companies")

    op.drop_index(op.f("ix_subjects_domain"), table_name="subjects")
    op.drop_table("subjects")

    op.drop_index(op.f("ix_sources_domain"), table_name="sources")
    op.drop_table("sources")
