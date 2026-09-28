"""Phase 6 canonical Events: evidence-backed clusters of persisted content items.

Adds ``events`` and their content, subject, and company links plus reviewed-merge redirects.
Existing tables and rows are not modified. The downgrade refuses to discard reviewed merges.

Revision ID: 20260928_09
Revises: 20260927_08
"""

import sqlalchemy as sa
from alembic import op

revision = "20260928_09"
down_revision = "20260927_08"
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


def _created_at():
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _fk(table, column, target, ondelete=None):
    return sa.ForeignKey(
        f"{target}.id", ondelete=ondelete, name=op.f(f"fk_{table}_{column}_{target}")
    )


def _event_link_table(table, column, target, unique_name):
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.Column(
            "event_id", sa.Uuid(), _fk(table, "event_id", "events", "CASCADE"), nullable=False
        ),
        sa.Column(column, sa.Uuid(), _fk(table, column, target, "CASCADE"), nullable=False),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("content_count", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("event_id", column, name=op.f(unique_name)),
    )
    op.create_index(op.f(f"ix_{table}_event_id"), table, ["event_id"])
    op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def upgrade():
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.Column("domain", DOMAIN_ENUM, nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("canonical_title", sa.String(1000), nullable=False),
        sa.Column("normalized_title", sa.String(1000), nullable=False),
        sa.Column("title_language", sa.String(20), nullable=False),
        sa.Column(
            "representative_content_id",
            sa.Uuid(),
            _fk("events", "representative_content_id", "content_items", "SET NULL"),
            nullable=True,
        ),
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_count", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
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
    )
    op.create_index("ix_events_domain_last_published", "events", ["domain", "last_published_at"])

    op.create_table(
        "event_content_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_content_links")),
        sa.Column(
            "event_id",
            sa.Uuid(),
            _fk("event_content_links", "event_id", "events", "CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "content_item_id",
            sa.Uuid(),
            _fk("event_content_links", "content_item_id", "content_items", "CASCADE"),
            nullable=False,
        ),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("content_item_id", name=op.f("event_content_primary")),
    )
    op.create_index(op.f("ix_event_content_links_event_id"), "event_content_links", ["event_id"])

    _event_link_table("event_subject_links", "subject_id", "subjects", "event_subject_link")
    _event_link_table("event_company_links", "company_id", "companies", "event_company_link")

    op.create_table(
        "event_redirects",
        sa.Column(
            "duplicate_id",
            sa.Uuid(),
            _fk("event_redirects", "duplicate_id", "events"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("duplicate_id", name=op.f("pk_event_redirects")),
        sa.Column(
            "canonical_id",
            sa.Uuid(),
            _fk("event_redirects", "canonical_id", "events"),
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
            name=op.f("ck_event_redirects_event_redirect_distinct"),
        ),
    )
    op.create_index(op.f("ix_event_redirects_canonical_id"), "event_redirects", ["canonical_id"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM event_redirects)")).scalar():
        raise RuntimeError("event_redirects is not empty; refusing to discard reviewed merges")
    op.drop_table("event_redirects")
    op.drop_table("event_company_links")
    op.drop_table("event_subject_links")
    op.drop_table("event_content_links")
    op.drop_table("events")
