"""Idempotent content persistence and conservative deterministic subject linking.

Content identity: one row per ``(source_id, identity_key)`` where the key is the publisher's
stable external ID (else the normalized URL); an incoming item whose normalized URL the same
source already stored is also a replay. Content duplicates are settled here, never by Events.

Linking rules live in ``content_linking`` (pure functions). Linking runs inside a SAVEPOINT
after the content rows are written, so a linking failure never rolls back ingested content.
"""

import hashlib
from collections import Counter, defaultdict
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.db.models import ContentItem, ContentSubjectLink, Source, Subject
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.content_linking import (
    LINKER_VERSION,
    REVIEWED_STRATEGY,
    STRATEGY_CONFIDENCE,
    build_catalog,
    match_content,
)
from industry_intelligence.services.ingestion_phase import FETCH, PERSIST, ingestion_phase
from industry_intelligence.services.resolution import normalize_url

LOCK_KEY = "content_links"


def load_catalog(session, domain):
    """Deterministic Subject evidence for ``domain``; merged-away Subjects are excluded."""

    merged = select(SubjectRedirect.duplicate_id)
    subjects = session.execute(
        select(Subject.id, Subject.subject_type, Subject.title).where(
            Subject.domain == domain, Subject.id.not_in(merged)
        )
    ).all()
    aliases = session.execute(
        select(SubjectAlias.subject_id, SubjectAlias.alias, SubjectAlias.alias_source)
        .join(Subject)
        .where(Subject.domain == domain, Subject.id.not_in(merged))
    ).all()
    identities = session.execute(
        select(
            SubjectExternalIdentity.subject_id,
            SubjectExternalIdentity.source_id,
            SubjectExternalIdentity.external_id,
            SubjectExternalIdentity.canonical_id,
            SubjectExternalIdentity.canonical_url,
        )
        .join(Subject)
        .where(Subject.domain == domain, Subject.id.not_in(merged))
    ).all()
    return build_catalog(subjects, aliases, identities)


def item_matches(item, catalog):
    return match_content(
        f"{item.title}\n{item.description or ''}",
        item.url,
        item.source_id,
        (item.item_metadata or {}).get("source_article_id"),
        catalog,
    )


def _catalogs(session):
    cache = {}

    def get(domain):
        if domain not in cache:
            cache[domain] = load_catalog(session, domain)
        return cache[domain]

    return get


def _insert_link(session, content_id, subject_id, strategy):
    """Insert one link; returns 1 when a row was written, 0 when it already existed."""

    return session.execute(
        insert(ContentSubjectLink)
        .values(
            content_item_id=content_id,
            subject_id=subject_id,
            relation_type="same_article" if strategy == "same_article" else "mentions",
            strategy=strategy,
            confidence=STRATEGY_CONFIDENCE[strategy],
        )
        .on_conflict_do_nothing()
    ).rowcount


def _refresh_search(session, subject_ids):
    from industry_intelligence.services.search_index import refresh_subject

    for subject_id in subject_ids:
        refresh_subject(session, subject_id)


def link_content_items(session, content_ids):
    """Add deterministic links for items that have none yet (new content); idempotent."""

    content_ids = [UUID(str(cid)) for cid in content_ids]
    catalog = _catalogs(session)
    already = set(
        session.scalars(
            select(ContentSubjectLink.content_item_id).where(
                ContentSubjectLink.content_item_id.in_(list(content_ids))
            )
        )
    )
    items = session.scalars(
        select(ContentItem).where(
            ContentItem.id.in_([cid for cid in content_ids if cid not in already])
        )
    ).all()
    subjects, strategies, links = set(), Counter(), 0
    for item in items:
        for subject_id, (strategy, _) in item_matches(item, catalog(item.domain)).items():
            if _insert_link(session, item.id, subject_id, strategy):
                subjects.add(subject_id)
                strategies[strategy] += 1
                links += 1
    session.flush()
    _refresh_search(session, subjects)
    return {
        "linker_version": LINKER_VERSION,
        "items": len(items),
        "links": links,
        "linked_subjects": len(subjects),
        "strategies": dict(sorted(strategies.items())),
    }


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
    invalid = replayed = 0
    created_ids = []
    for incoming in items:
        title = " ".join(incoming.title.split())
        url = normalize_url(incoming.url)
        if (
            not title
            or len(title) > 1000
            or not url
            or urlsplit(url).scheme not in {"http", "https"}
        ):
            invalid += 1
            continue
        if session.scalar(
            select(ContentItem.id).where(ContentItem.source_id == source.id, ContentItem.url == url)
        ):
            replayed += 1
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
            .returning(ContentItem.id)
        )
        if created is None:
            replayed += 1
            continue
        created_ids.append(created)
    if items and not created_ids and invalid == len(items):
        raise ValueError("No valid content items")
    session.flush()
    link_result = {"items": 0, "links": 0, "linked_subjects": 0}
    link_error = None
    if created_ids:
        try:
            with session.begin_nested():
                link_result = link_content_items(session, created_ids)
        except Exception as exc:  # linking is isolated from the persisted content
            link_error = type(exc).__name__
    return {
        "source": definition.key,
        "domain": str(definition.domain),
        "content_type": str(definition.content_type),
        "fetched_count": len(items),
        "persisted_count": len(created_ids),
        "replayed_count": replayed,
        "invalid_count": invalid,
        "skipped_count": replayed + invalid,
        "failed_count": failed_count,
        "linked_subject_count": link_result["linked_subjects"],
        "link_count": link_result["links"],
        **({"link_error": link_error} if link_error else {}),
        "content_ids": [str(cid) for cid in created_ids],
        "duration_ms": round((perf_counter() - started) * 1000),
    }


async def fetch_content(key, settings):
    definition = CONTENT_SOURCES[key]
    with ingestion_phase(FETCH):
        async with HTTPClient(settings) as http:
            adapter = definition.adapter(http, settings.source_fetch_limit)
            items = await adapter.fetch()
    return definition, items, adapter.failed_count


async def ingest_content(key, settings, session_factory):
    definition, items, failed = await fetch_content(key, settings)
    with ingestion_phase(PERSIST), session_factory.begin() as session:
        return persist_content(session, definition, items, failed)


async def ingest_all_content(settings, session_factory, keys=None):
    results = []
    for key in keys or CONTENT_SOURCES:
        try:
            results.append(await ingest_content(key, settings, session_factory))
        except Exception as exc:
            results.append({"source": key, "error": type(exc).__name__})
    return results


def relink_all_content(session, dry_run=False, content_ids=None):
    """Recompute deterministic links and reconcile them with the stored ones.

    This is the documented rebuild semantics (run after changing matching rules or after new
    Subjects arrive): links whose evidence no longer holds are removed and reported, links with
    new evidence are added, unchanged links keep their rows. ``dry_run`` writes nothing.
    """

    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    query = select(ContentItem).order_by(ContentItem.id)
    if content_ids is not None:
        query = query.where(ContentItem.id.in_(list(content_ids)))
    items = session.scalars(query).all()
    catalog = _catalogs(session)
    desired = {}
    for item in items:
        for subject_id, (strategy, evidence) in item_matches(item, catalog(item.domain)).items():
            desired[(item.id, subject_id)] = (strategy, evidence, item)
    stored = session.scalars(
        select(ContentSubjectLink).where(
            ContentSubjectLink.content_item_id.in_([item.id for item in items])
        )
    ).all()
    # Reviewed links are human decisions: never removed, replaced, or re-added by the rules.
    reviewed = {
        (link.content_item_id, link.subject_id)
        for link in stored
        if link.strategy == REVIEWED_STRATEGY
    }
    desired = {key: value for key, value in desired.items() if key not in reviewed}
    existing = {
        (link.content_item_id, link.subject_id): link
        for link in stored
        if link.strategy != REVIEWED_STRATEGY
    }
    added = [key for key in desired if key not in existing]
    removed = [key for key in existing if key not in desired]
    changed = [
        key for key in desired if key in existing and existing[key].strategy != desired[key][0]
    ]
    by_domain = defaultdict(Counter)
    for strategy, _, item in desired.values():
        by_domain[str(item.domain)][strategy] += 1
    result = {
        "linker_version": LINKER_VERSION,
        "dry_run": dry_run,
        "content_items": len(items),
        "links": len(desired),
        "linked_content_items": len({content_id for content_id, _ in desired}),
        "linked_subjects": len({subject_id for _, subject_id in desired}),
        "added": len(added),
        "removed": len(removed),
        "strategy_changed": len(changed),
        "unchanged": len(desired) - len(added) - len(changed),
        "reviewed_kept": len(reviewed),
        "by_domain": {domain: dict(sorted(c.items())) for domain, c in sorted(by_domain.items())},
    }
    if dry_run:
        result["links_detail"] = [
            {
                "content_item_id": str(content_id),
                "subject_id": str(subject_id),
                "strategy": strategy,
                "evidence": evidence,
                "title": item.title[:120],
            }
            for (content_id, subject_id), (strategy, evidence, item) in desired.items()
        ]
        return result
    for key in removed + changed:
        session.execute(
            delete(ContentSubjectLink).where(
                ContentSubjectLink.content_item_id == key[0],
                ContentSubjectLink.subject_id == key[1],
            )
        )
    for key in added + changed:
        _insert_link(session, key[0], key[1], desired[key][0])
    session.flush()
    _refresh_search(session, {key[1] for key in added + removed + changed})
    return result
