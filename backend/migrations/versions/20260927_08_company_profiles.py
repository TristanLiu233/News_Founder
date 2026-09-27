"""Phase 5 company profiles: sourced profile facts, provenance, subject links, redirects.

Moves profile facts out of the unused Phase 0 ``companies.website``/``companies.description``
columns into ``company_profiles`` (a field-level merge with per-field provenance) and
``company_profile_sources`` (one current record per company and source). The upgrade refuses to
drop those columns if any row holds a value, so no data can be silently discarded.

Revision ID: 20260927_08
Revises: 20260927_07
"""

import sqlalchemy as sa
from alembic import op

revision = "20260927_08"
down_revision = "20260927_07"
branch_labels = None
depends_on = None


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


def _company_fk(name, ondelete=None):
    return sa.ForeignKey("companies.id", ondelete=ondelete, name=op.f(name))


def upgrade():
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM companies "
            "WHERE website IS NOT NULL OR description IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("companies.website/description hold data; refusing to drop them")
    op.drop_column("companies", "website")
    op.drop_column("companies", "description")

    op.create_table(
        "company_profiles",
        sa.Column(
            "company_id",
            sa.Uuid(),
            _company_fk("fk_company_profiles_company_id_companies", "CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("company_id", name=op.f("pk_company_profiles")),
        sa.Column("legal_name", sa.String(500), nullable=True),
        sa.Column("legal_name_locale", sa.String(20), nullable=True),
        sa.Column("website", sa.String(2048), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("headquarters", sa.String(255), nullable=True),
        sa.Column("jurisdiction", sa.String(20), nullable=True),
        sa.Column("industry", sa.String(255), nullable=True),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("description_language", sa.String(20), nullable=True),
        sa.Column("business_areas", sa.JSON(), nullable=False),
        sa.Column("key_products", sa.JSON(), nullable=False),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("profile_updated_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
    )
    op.create_index(op.f("ix_company_profiles_country"), "company_profiles", ["country"])

    op.create_table(
        "company_profile_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_profile_sources")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            _company_fk("fk_company_profile_sources_company_id_companies", "CASCADE"),
            nullable=False,
        ),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "company_id", "source_key", name=op.f("company_profile_source_identity")
        ),
    )
    op.create_index(
        op.f("ix_company_profile_sources_company_id"), "company_profile_sources", ["company_id"]
    )

    op.create_table(
        "company_subject_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_subject_links")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            _company_fk("fk_company_subject_links_company_id_companies", "CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            sa.Uuid(),
            sa.ForeignKey(
                "subjects.id",
                ondelete="CASCADE",
                name=op.f("fk_company_subject_links_subject_id_subjects"),
            ),
            nullable=False,
        ),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("company_id", "subject_id", name=op.f("company_subject_link")),
    )
    op.create_index(
        op.f("ix_company_subject_links_company_id"), "company_subject_links", ["company_id"]
    )
    op.create_index(
        op.f("ix_company_subject_links_subject_id"), "company_subject_links", ["subject_id"]
    )

    op.create_table(
        "company_redirects",
        sa.Column(
            "duplicate_id",
            sa.Uuid(),
            _company_fk("fk_company_redirects_duplicate_id_companies"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("duplicate_id", name=op.f("pk_company_redirects")),
        sa.Column(
            "canonical_id",
            sa.Uuid(),
            _company_fk("fk_company_redirects_canonical_id_companies"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "merged_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duplicate_id != canonical_id",
            name=op.f("ck_company_redirects_company_redirect_distinct"),
        ),
    )
    op.create_index(
        op.f("ix_company_redirects_canonical_id"), "company_redirects", ["canonical_id"]
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM company_redirects)")).scalar():
        raise RuntimeError("company_redirects is not empty; refusing to discard reviewed merges")
    op.drop_table("company_redirects")
    op.drop_table("company_subject_links")
    op.drop_table("company_profile_sources")
    op.drop_table("company_profiles")
    op.add_column("companies", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("website", sa.String(2048), nullable=True))
