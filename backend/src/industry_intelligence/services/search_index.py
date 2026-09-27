"""Bounded search projections refreshed outside read-only search requests."""

import hashlib
import json

from sqlalchemy import select, text

from industry_intelligence.ai.embeddings import embedding_provider, validate_vector
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    ContentItem,
    ContentSubjectLink,
    RankObservation,
    Subject,
)
from industry_intelligence.db.models.canonical import SubjectAlias
from industry_intelligence.services.resolution import normalize_subject_name


def refresh_subject(session, subject_id, settings=None):
    settings = settings or get_settings()
    session.flush()
    if session.scalar(
        text("SELECT 1 FROM subject_redirects WHERE duplicate_id=:id"), {"id": subject_id}
    ):
        return
    subject = session.get(Subject, subject_id)
    if subject is None:
        return
    aliases = session.scalars(
        select(SubjectAlias)
        .where(SubjectAlias.subject_id == subject_id)
        .order_by(SubjectAlias.locale, SubjectAlias.alias)
    ).all()
    titles = list(
        session.scalars(
            select(RankObservation.title)
            .where(RankObservation.subject_id == subject_id, RankObservation.title.is_not(None))
            .order_by(RankObservation.observed_at.desc(), RankObservation.id)
            .limit(settings.search_recent_titles)
        )
    )
    titles = list(dict.fromkeys(titles))
    content_titles = list(
        session.scalars(
            select(ContentItem.title)
            .join(ContentSubjectLink)
            .where(ContentSubjectLink.subject_id == subject_id)
            .order_by(ContentItem.published_at.desc().nullslast(), ContentItem.observed_at.desc())
            .limit(settings.search_recent_titles)
        )
    )
    titles = list(dict.fromkeys([*content_titles, *titles]))[: settings.search_recent_titles]
    alias_data = [dict(alias=a.alias, locale=a.locale, alias_type=a.alias_type) for a in aliases]
    corpus = "\n".join(
        [
            subject.title,
            subject.canonical_name,
            *[a.alias for a in aliases],
            subject.description or "",
            *titles,
        ]
    )
    corpus = corpus[: settings.search_document_chars]
    digest = hashlib.sha256(corpus.encode()).hexdigest()
    session.execute(
        text("""
        INSERT INTO search_documents(subject_id, canonical_name, normalized_name, domain,
            subject_type, aliases, recent_titles, searchable_text, content_hash)
        VALUES (:id, :name, :norm, :domain, :type, CAST(:aliases AS jsonb),
            CAST(:titles AS jsonb), :corpus, :hash)
        ON CONFLICT(subject_id) DO UPDATE SET canonical_name=EXCLUDED.canonical_name,
            normalized_name=EXCLUDED.normalized_name, domain=EXCLUDED.domain,
            subject_type=EXCLUDED.subject_type, aliases=EXCLUDED.aliases,
            recent_titles=EXCLUDED.recent_titles, searchable_text=EXCLUDED.searchable_text,
            content_hash=EXCLUDED.content_hash, updated_at=now()
    """),
        dict(
            id=subject_id,
            name=subject.title,
            norm=normalize_subject_name(subject.title),
            domain=str(subject.domain),
            type=str(subject.subject_type),
            aliases=json.dumps(alias_data),
            titles=json.dumps(titles),
            corpus=corpus,
            hash=digest,
        ),
    )


def reindex(session, settings, subject_id=None):
    ids = (
        [subject_id]
        if subject_id
        else session.scalars(
            text(
                "SELECT id FROM subjects WHERE id NOT IN "
                "(SELECT duplicate_id FROM subject_redirects)"
            )
        ).all()
    )
    for sid in ids:
        refresh_subject(session, sid, settings)
    return {"indexed_subjects": len(ids)}


def embed_documents(session, settings, provider=None):
    provider = provider or embedding_provider(settings)
    if provider is None:
        return {"embedded": 0, "status": "provider_not_configured"}
    rows = (
        session.execute(
            text("""
        SELECT d.subject_id, d.searchable_text, d.content_hash FROM search_documents d
        LEFT JOIN subject_embeddings e USING(subject_id)
        WHERE e.subject_id IS NULL OR e.content_hash != d.content_hash
            OR e.embedding_model != :model OR e.dimension != :dim
        ORDER BY d.subject_id
    """),
            {"model": provider.model, "dim": provider.dimension},
        )
        .mappings()
        .all()
    )
    for row in rows:
        vector = validate_vector(provider.embed(row["searchable_text"]), provider.dimension)
        session.execute(
            text("""
            INSERT INTO subject_embeddings(subject_id, embedding, embedding_model,
                dimension, content_hash) VALUES (:id, CAST(:v AS vector), :model, :dim, :hash)
            ON CONFLICT(subject_id) DO UPDATE SET embedding=EXCLUDED.embedding,
                embedding_model=EXCLUDED.embedding_model, dimension=EXCLUDED.dimension,
                content_hash=EXCLUDED.content_hash, embedded_at=now()
        """),
            dict(
                id=row["subject_id"],
                v=json.dumps(vector),
                model=provider.model,
                dim=provider.dimension,
                hash=row["content_hash"],
            ),
        )
    return {"embedded": len(rows), "status": "ready"}
