"""Preserve ranked subject history when canonical search identities merge."""

from alembic import op

revision = "20260927_05"
down_revision = "20260927_04"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE subject_redirects (
        duplicate_id uuid PRIMARY KEY REFERENCES subjects(id),
        canonical_id uuid NOT NULL REFERENCES subjects(id),
        CHECK (duplicate_id != canonical_id)
    )""")
    op.execute("CREATE INDEX ix_redirect_canonical ON subject_redirects(canonical_id)")


def downgrade():
    op.execute("DROP TABLE subject_redirects")
