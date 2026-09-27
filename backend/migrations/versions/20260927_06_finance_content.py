"""Finance content types, news history, and related canonical subjects."""

from alembic import op

revision = "20260927_06"
down_revision = "20260927_05"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE rank_lists ADD COLUMN list_type varchar(30) NOT NULL DEFAULT 'ranking'")
    op.execute("UPDATE rank_lists SET list_type='market_pulse' WHERE domain='finance'")
    op.execute(
        "ALTER TABLE rank_lists ADD CONSTRAINT rank_list_type_valid "
        "CHECK (list_type IN ('ranking','feed','news','market_pulse'))"
    )
    op.execute("""CREATE TABLE content_items (
        id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        domain varchar(20) NOT NULL, content_type varchar(30) NOT NULL,
        external_id varchar(1024), identity_key varchar(2048) NOT NULL,
        title varchar(1000) NOT NULL, url varchar(2048) NOT NULL,
        published_at timestamptz, observed_at timestamptz NOT NULL,
        language varchar(20) NOT NULL, description text, item_metadata json NOT NULL,
        content_hash varchar(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT content_source_identity UNIQUE(source_id, identity_key),
        CONSTRAINT content_type_valid CHECK (content_type IN ('feed','news')),
        CONSTRAINT content_domain_valid CHECK (domain IN ('computer','finance','gaming'))
    )""")
    op.execute(
        "CREATE INDEX ix_content_source_published ON content_items(source_id, published_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_content_domain_published ON content_items(domain, published_at DESC)"
    )
    op.execute("CREATE INDEX ix_content_url ON content_items(url)")
    op.execute("""CREATE TABLE content_subject_links (
        id uuid PRIMARY KEY,
        content_item_id uuid NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
        subject_id uuid NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        relation_type varchar(40) NOT NULL, strategy varchar(40) NOT NULL,
        confidence double precision NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT content_subject_identity UNIQUE(content_item_id, subject_id)
    )""")
    op.execute(
        "CREATE INDEX ix_content_subject_subject_item "
        "ON content_subject_links(subject_id, content_item_id)"
    )


def downgrade():
    op.execute("DROP TABLE content_subject_links")
    op.execute("DROP TABLE content_items")
    op.execute("ALTER TABLE rank_lists DROP CONSTRAINT rank_list_type_valid")
    op.execute("ALTER TABLE rank_lists DROP COLUMN list_type")
