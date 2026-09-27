"""Phase 4 company ranking tracker: company identity, snapshots, and entries.

Replaces the unused Phase 0 placeholders. ``ranking_snapshots`` previously held one row per
company without a snapshot header, so it is rebuilt as a snapshot table with a separate
``ranking_entries`` child table. The upgrade refuses to run if any placeholder row exists, so
no data can be silently discarded.

Revision ID: 20260927_07
Revises: 20260927_06
"""

import sqlalchemy as sa
from alembic import op

revision = "20260927_07"
down_revision = "20260927_06"
branch_labels = None
depends_on = None

DOMAIN_ENUM = sa.Enum(
    "computer",
    "finance",
    "gaming",
    name="domain_enum",
    native_enum=False,
    create_constraint=True,
)


def _timestamps():
    return [
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
    ]


def _require_empty(*tables):
    bind = op.get_bind()
    for table in tables:
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError(f"{table} is not empty; refusing to rebuild placeholder schema")


def upgrade():
    _require_empty("ranking_snapshots", "rankings", "companies")

    # Old one-row-per-company placeholder; replaced by snapshot header + entries below.
    op.drop_table("ranking_snapshots")

    op.drop_constraint(op.f("uq_companies_canonical_name"), "companies", type_="unique")
    op.drop_index(op.f("ix_companies_domain"), table_name="companies")
    op.drop_column("companies", "domain")
    op.add_column("companies", sa.Column("normalized_name", sa.String(255), nullable=False))
    op.create_index(op.f("ix_companies_normalized_name"), "companies", ["normalized_name"])

    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_aliases")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id",
                ondelete="CASCADE",
                name=op.f("fk_company_aliases_company_id_companies"),
            ),
            nullable=False,
        ),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.Column("locale", sa.String(20), nullable=True),
        sa.Column("alias_type", sa.String(40), nullable=False),
        sa.Column("alias_source", sa.String(100), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("company_id", "normalized_alias", name=op.f("company_alias_identity")),
    )
    op.create_index(op.f("ix_company_aliases_company_id"), "company_aliases", ["company_id"])
    op.create_index(
        op.f("ix_company_aliases_normalized_alias"), "company_aliases", ["normalized_alias"]
    )

    op.create_table(
        "company_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_identities")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id",
                ondelete="CASCADE",
                name=op.f("fk_company_identities_company_id_companies"),
            ),
            nullable=False,
        ),
        sa.Column("scheme", sa.String(64), nullable=False),
        sa.Column("value", sa.String(512), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("scheme", "value", name=op.f("company_identity_value")),
    )
    op.create_index(op.f("ix_company_identities_company_id"), "company_identities", ["company_id"])

    op.add_column("rankings", sa.Column("key", sa.String(100), nullable=False))
    op.add_column("rankings", sa.Column("source_key", sa.String(100), nullable=False))
    op.add_column("rankings", sa.Column("metric_label", sa.String(100), nullable=False))
    op.add_column("rankings", sa.Column("metric_unit", sa.String(30), nullable=True))
    op.add_column("rankings", sa.Column("acquisition", sa.String(50), nullable=False))
    op.add_column("rankings", sa.Column("primary_language", sa.String(20), nullable=False))
    op.add_column(
        "rankings",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_unique_constraint(op.f("uq_rankings_key"), "rankings", ["key"])
    op.create_index(op.f("ix_rankings_source_key"), "rankings", ["source_key"])

    op.create_table(
        "ranking_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ranking_snapshots")),
        sa.Column(
            "ranking_id",
            sa.Uuid(),
            sa.ForeignKey(
                "rankings.id",
                ondelete="CASCADE",
                name=op.f("fk_ranking_snapshots_ranking_id_rankings"),
            ),
            nullable=False,
        ),
        sa.Column("snapshot_key", sa.String(255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("source_entry_count", sa.Integer(), nullable=True),
        sa.Column("snapshot_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ranking_id", "snapshot_key", name=op.f("ranking_snapshot_identity")),
        sa.CheckConstraint(
            "status IN ('complete','partial')", name=op.f("ck_ranking_snapshots_status_valid")
        ),
        sa.CheckConstraint(
            "entry_count > 0", name=op.f("ck_ranking_snapshots_entry_count_positive")
        ),
    )
    op.create_index(
        op.f("ix_ranking_snapshots_ranking_effective"),
        "ranking_snapshots",
        ["ranking_id", "effective_at"],
    )

    op.create_table(
        "ranking_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ranking_entries")),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ranking_snapshots.id",
                ondelete="CASCADE",
                name=op.f("fk_ranking_entries_snapshot_id_ranking_snapshots"),
            ),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id",
                ondelete="RESTRICT",
                name=op.f("fk_ranking_entries_company_id_companies"),
            ),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("raw_name", sa.String(500), nullable=False),
        sa.Column("raw_value", sa.String(255), nullable=True),
        sa.Column("metric_value", sa.Numeric(24, 4), nullable=True),
        sa.Column("resolution_strategy", sa.String(40), nullable=False),
        sa.Column("entry_metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "company_id", name=op.f("ranking_entry_company")),
        sa.CheckConstraint("rank > 0", name=op.f("ck_ranking_entries_rank_positive")),
    )
    op.create_index(
        op.f("ix_ranking_entries_snapshot_rank"), "ranking_entries", ["snapshot_id", "rank"]
    )
    op.create_index(op.f("ix_ranking_entries_company_id"), "ranking_entries", ["company_id"])


def downgrade():
    _require_empty("ranking_entries", "ranking_snapshots", "rankings", "companies")

    op.drop_table("ranking_entries")
    op.drop_table("ranking_snapshots")

    op.drop_index(op.f("ix_rankings_source_key"), table_name="rankings")
    op.drop_constraint(op.f("uq_rankings_key"), "rankings", type_="unique")
    for column in (
        "is_active",
        "primary_language",
        "acquisition",
        "metric_unit",
        "metric_label",
        "source_key",
        "key",
    ):
        op.drop_column("rankings", column)

    op.drop_table("company_identities")
    op.drop_table("company_aliases")
    op.drop_index(op.f("ix_companies_normalized_name"), table_name="companies")
    op.drop_column("companies", "normalized_name")
    op.add_column("companies", sa.Column("domain", DOMAIN_ENUM, nullable=False))
    op.create_index(op.f("ix_companies_domain"), "companies", ["domain"])
    op.create_unique_constraint(
        op.f("uq_companies_canonical_name"), "companies", ["canonical_name"]
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
    op.create_index(op.f("ix_ranking_snapshots_ranking_id"), "ranking_snapshots", ["ranking_id"])
    op.create_index(op.f("ix_ranking_snapshots_company_id"), "ranking_snapshots", ["company_id"])
    op.create_index(op.f("ix_ranking_snapshots_observed_at"), "ranking_snapshots", ["observed_at"])
