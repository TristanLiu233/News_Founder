"""Idempotent content persistence and conservative deterministic subject linking."""

import hashlib
import re
from time import perf_counter
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.db.models import ContentItem, ContentSubjectLink, Source, Subject
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.resolution import normalize_subject_name, normalize_url


def _mentions(text: str, candidate: str) -> bool:
    candidate = candidate.strip()
    if len(candidate) < 2:
        return False
    if re.search(r"[A-Za-z0-9]", candidate):
        return (
            re.search(rf"(?<![\w]){re.escape(candidate)}(?![\w])", text, re.IGNORECASE) is not None
        )
    return candidate in text


def _ticker_mentions(text: str, token: str) -> bool:
    if len(token) < 2 or not token.isalpha() or token != token.upper():
        return False
    return re.search(rf"(?<![\w])\$?{re.escape(token)}(?![\w])", text) is not None


def deterministic_matches(haystack, canonicals, aliases, identities):
    candidates: dict = {}
    for subject_id, canonical_name, title in canonicals:
        if _mentions(haystack, canonical_name) or _mentions(haystack, title):
            candidates[subject_id] = ("exact_canonical", 1.0)
    alias_subjects: dict[str, set] = {}
    for subject_id, alias in aliases:
        alias_subjects.setdefault(normalize_subject_name(alias), set()).add(subject_id)
    for subject_id, alias in aliases:
        if len(alias_subjects[normalize_subject_name(alias)]) == 1 and _mentions(haystack, alias):
            candidates.setdefault(subject_id, ("exact_alias", 0.98))
    for subject_id, identity in identities:
        token = str(identity).split(":")[-1]
        if _ticker_mentions(haystack, token):
            candidates.setdefault(subject_id, ("external_identity", 0.96))
    return candidates


def link_content_item(session, item):
    haystack = f"{item.title}\n{item.description or ''}"
    canonicals = session.execute(
        select(Subject.id, Subject.canonical_name, Subject.title).where(
            Subject.domain == item.domain
        )
    ).all()
    aliases = session.execute(
        select(SubjectAlias.subject_id, SubjectAlias.alias)
        .join(Subject)
        .where(Subject.domain == item.domain)
    ).all()
    identities = session.execute(
        select(SubjectExternalIdentity.subject_id, SubjectExternalIdentity.canonical_id)
        .join(Subject)
        .where(Subject.domain == item.domain, SubjectExternalIdentity.canonical_id.is_not(None))
    ).all()
    candidates = deterministic_matches(haystack, canonicals, aliases, identities)
    linked = []
    for subject_id, (strategy, confidence) in candidates.items():
        session.execute(
            insert(ContentSubjectLink)
            .values(
                content_item_id=item.id,
                subject_id=subject_id,
                relation_type="mentions",
                strategy=strategy,
                confidence=confidence,
            )
            .on_conflict_do_nothing()
        )
        linked.append(subject_id)
    return linked


def persist_content(session, definition, items, failed_count=0):
    started = perf_counter()
    source = session.scalar(select(Source).where(Source.name == definition.name))
    if source is None:
        source = Source(
            name=definition.name,
            domain=definition.domain,
            source_type=definition.acquisition,
            base_url=definition.url,
            primary_language=definition.primary_language,
        )
        session.add(source)
        session.flush()
    persisted = skipped = 0
    linked_subjects = set()
    for incoming in items:
        title = " ".join(incoming.title.split())
        url = normalize_url(incoming.url)
        if (
            not title
            or len(title) > 1000
            or not url
            or urlsplit(url).scheme not in {"http", "https"}
        ):
            skipped += 1
            continue
        identity = f"external:{incoming.external_id}" if incoming.external_id else f"url:{url}"
        digest = hashlib.sha256(f"{title}\n{incoming.description or ''}".encode()).hexdigest()
        created = session.scalar(
            insert(ContentItem)
            .values(
                source_id=source.id,
                domain=definition.domain,
                content_type=str(definition.content_type),
                external_id=incoming.external_id,
                identity_key=identity,
                title=title,
                url=url,
                published_at=incoming.published_at,
                observed_at=incoming.observed_at,
                language=incoming.language,
                description=incoming.description,
                item_metadata=incoming.metadata,
                content_hash=digest,
            )
            .on_conflict_do_nothing()
            .returning(ContentItem)
        )
        if created is None:
            skipped += 1
            continue
        persisted += 1
        linked_subjects.update(link_content_item(session, created))
    if items and not persisted and skipped != len(items):
        raise ValueError("No valid content items")
    session.flush()
    from industry_intelligence.services.search_index import refresh_subject

    for subject_id in linked_subjects:
        refresh_subject(session, subject_id)
    return {
        "source": definition.key,
        "content_type": str(definition.content_type),
        "fetched_count": len(items),
        "persisted_count": persisted,
        "skipped_count": skipped,
        "failed_count": failed_count,
        "linked_subject_count": len(linked_subjects),
        "duration_ms": round((perf_counter() - started) * 1000),
    }


async def ingest_content(key, settings, session_factory):
    definition = CONTENT_SOURCES[key]
    async with HTTPClient(settings) as http:
        adapter = definition.adapter(http, settings.source_fetch_limit)
        items = await adapter.fetch()
    with session_factory.begin() as session:
        return persist_content(session, definition, items, adapter.failed_count)


async def ingest_all_content(settings, session_factory):
    results = []
    for key in CONTENT_SOURCES:
        try:
            results.append(await ingest_content(key, settings, session_factory))
        except Exception as exc:
            results.append({"source": key, "error": type(exc).__name__})
    return results


def relink_all_content(session):
    session.query(ContentSubjectLink).delete()
    linked = set()
    for item in session.scalars(select(ContentItem)).all():
        linked.update(link_content_item(session, item))
    session.flush()
    from industry_intelligence.services.search_index import refresh_subject

    for subject_id in linked:
        refresh_subject(session, subject_id)
    return {"content_items": session.query(ContentItem).count(), "linked_subjects": len(linked)}
