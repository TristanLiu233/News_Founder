"""Canonical multilingual search projections and optional embeddings."""

from alembic import op

revision = "20260927_04"
down_revision = "20260926_03"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TABLE search_documents (
            subject_id uuid PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
            canonical_name text NOT NULL,
            normalized_name text NOT NULL,
            domain text NOT NULL,
            subject_type text NOT NULL,
            aliases jsonb NOT NULL,
            recent_titles jsonb NOT NULL,
            searchable_text text NOT NULL,
            search_vector tsvector GENERATED ALWAYS AS
                (to_tsvector('simple', searchable_text)) STORED,
            content_hash varchar(64) NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_search_fts ON search_documents USING gin(search_vector)")
    op.execute(
        "CREATE INDEX ix_search_name_trgm ON search_documents "
        "USING gin(normalized_name gin_trgm_ops)"
    )
    op.execute("CREATE INDEX ix_search_domain_type ON search_documents(domain, subject_type)")
    op.execute(
        "CREATE INDEX ix_search_alias_trgm ON subject_aliases "
        "USING gin(normalized_alias gin_trgm_ops)"
    )
    op.execute("""
        CREATE TABLE subject_embeddings (
            subject_id uuid PRIMARY KEY REFERENCES search_documents(subject_id) ON DELETE CASCADE,
            embedding vector NOT NULL,
            embedding_model text NOT NULL,
            dimension integer NOT NULL CHECK (dimension > 0),
            content_hash varchar(64) NOT NULL,
            embedded_at timestamptz NOT NULL DEFAULT now(),
            CHECK (vector_dims(embedding) = dimension)
        )
    """)


def downgrade():
    op.execute("DROP TABLE subject_embeddings")
    op.execute("DROP TABLE search_documents")
    op.execute("DROP INDEX ix_search_alias_trgm")
