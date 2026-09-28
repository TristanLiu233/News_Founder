"""Incremental, idempotent Event building from persisted ContentItems (CLI-only).

``build_events`` processes content items that have no ``EventContentLink`` yet, in publication
order, inside the caller's transaction. Each item is compared (``event_resolution.match_event``)
with the members of existing Events in the same domain whose content falls inside the time
window. It joins the single matching Event; with no match it seeds a new Event, and with more
than one matching Event it also seeds a new Event (ambiguity is never resolved by guessing).
Re-running over the same content creates nothing: processed items are skipped.

Event ↔ Subject links come only from the Event's existing ``ContentSubjectLink`` rows (merged
subjects resolve to their canonical subject); Event ↔ Company links only from those subjects'
``CompanySubjectLink`` rows. Nothing is fuzzy-matched here.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import delete, func, select, text

from industry_intelligence.db.models import (
    CompanySubjectLink,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventCompanyLink,
    EventContentLink,
    EventRedirect,
    EventSubjectLink,
    Source,
)
from industry_intelligence.services.company_resolution import resolve_company_redirect
from industry_intelligence.services.content_linking import REVIEWED_STRATEGY
from industry_intelligence.services.event_resolution import (
    RESOLVER_VERSION,
    TIME_WINDOW,
    aggregate_event_type,
    content_features,
    extract_headline,
    match_event,
    normalize_title,
    representative_key,
    strip_boilerplate,
)
from industry_intelligence.services.subject_detail import resolve_canonical

LOCK_KEY = "events"
EFFECTIVE_AT = func.coalesce(ContentItem.published_at, ContentItem.observed_at)


def _effective(item):
    return item.published_at or item.observed_at


def content_entities(session, content_ids):
    """content_id → (canonical subject IDs, canonical company IDs) from deterministic links."""

    subjects = defaultdict(set)
    if not content_ids:
        return {}
    canonical = {}
    for content_id, subject_id in session.execute(
        select(ContentSubjectLink.content_item_id, ContentSubjectLink.subject_id).where(
            ContentSubjectLink.content_item_id.in_(content_ids)
        )
    ):
        if subject_id not in canonical:
            subject = resolve_canonical(session, subject_id)
            canonical[subject_id] = subject.id if subject else None
        if canonical[subject_id] is not None:
            subjects[content_id].add(canonical[subject_id])
    all_subjects = set().union(*subjects.values()) if subjects else set()
    companies_by_subject = defaultdict(set)
    if all_subjects:
        for company_id, subject_id in session.execute(
            select(CompanySubjectLink.company_id, CompanySubjectLink.subject_id).where(
                CompanySubjectLink.subject_id.in_(all_subjects)
            )
        ):
            companies_by_subject[subject_id].add(resolve_company_redirect(session, company_id))
    return {
        content_id: (
            frozenset(subject_ids),
            frozenset(set().union(*(companies_by_subject[s] for s in subject_ids))),
        )
        for content_id, subject_ids in subjects.items()
    }


def _features(session, items):
    entities = content_entities(session, [item.id for item in items])
    return {
        item.id: content_features(
            item.id,
            item.source_id,
            item.domain,
            _effective(item),
            item.title,
            item.description,
            *entities.get(item.id, ((), ())),
        )
        for item in items
    }


def _candidate_events(session, item, window=TIME_WINDOW):
    """Existing Event members in the item's domain published inside the time window."""

    at = _effective(item)
    rows = session.execute(
        select(EventContentLink.event_id, ContentItem)
        .join(ContentItem, ContentItem.id == EventContentLink.content_item_id)
        .where(
            ContentItem.domain == item.domain,
            EFFECTIVE_AT >= at - window,
            EFFECTIVE_AT <= at + window,
        )
        .order_by(EventContentLink.event_id, EFFECTIVE_AT, ContentItem.id)
    ).all()
    features = _features(session, [content for _, content in rows])
    members = defaultdict(list)
    for event_id, content in rows:
        members[event_id].append(features[content.id])
    return members


def refresh_event(session, event_id):
    """Recompute an Event's times, counts, type, and representative title from its content."""

    event = session.get(Event, event_id)
    rows = session.execute(
        select(ContentItem, Source.name)
        .join(Source, Source.id == ContentItem.source_id)
        .join(EventContentLink, EventContentLink.content_item_id == ContentItem.id)
        .where(EventContentLink.event_id == event_id)
    ).all()
    event.content_count = len(rows)
    if not rows:
        # A reviewed merge moved every content link away; the row is kept for its redirect.
        event.source_count = 0
        event.representative_content_id = None
        return event
    event.source_count = len({item.source_id for item, _ in rows})
    effective = [_effective(item) for item, _ in rows]
    event.first_published_at, event.last_published_at = min(effective), max(effective)
    observed = [item.observed_at for item, _ in rows]
    event.first_observed_at, event.last_observed_at = min(observed), max(observed)
    representative, _ = min(
        rows,
        key=lambda row: representative_key(row[0].title, _effective(row[0]), row[1], row[0].id),
    )
    headline = extract_headline(representative.title)
    event.representative_content_id = representative.id
    event.canonical_title = headline[:1000]
    event.normalized_title = normalize_title(strip_boilerplate(headline))[:1000]
    event.title_language = representative.language
    event.event_type = aggregate_event_type(
        content_features(
            item.id, item.source_id, item.domain, _effective(item), item.title
        ).event_type
        for item, _ in rows
    )
    return event


def refresh_event_entities(session, event_ids):
    """Rebuild the derived Event ↔ Subject/Company links of ``event_ids`` from content links."""

    event_ids = list(event_ids)
    if not event_ids:
        return {"subject_links": 0, "company_links": 0}
    session.execute(delete(EventSubjectLink).where(EventSubjectLink.event_id.in_(event_ids)))
    # Reviewed Event ↔ Company links (explicit reviewed actions) are kept; derived ones rebuilt.
    session.execute(
        delete(EventCompanyLink).where(
            EventCompanyLink.event_id.in_(event_ids),
            EventCompanyLink.strategy != REVIEWED_STRATEGY,
        )
    )
    reviewed_companies = set(
        session.execute(
            select(EventCompanyLink.event_id, EventCompanyLink.company_id).where(
                EventCompanyLink.event_id.in_(event_ids)
            )
        ).all()
    )
    members = defaultdict(list)
    for event_id, content_id in session.execute(
        select(EventContentLink.event_id, EventContentLink.content_item_id).where(
            EventContentLink.event_id.in_(event_ids)
        )
    ):
        members[event_id].append(content_id)
    entities = content_entities(session, [c for ids in members.values() for c in ids])
    subject_links = company_links = 0
    for event_id, content_ids in members.items():
        by_subject, by_company = defaultdict(list), defaultdict(list)
        company_subjects = defaultdict(set)
        for content_id in sorted(content_ids, key=str):
            subject_ids, company_ids = entities.get(content_id, ((), ()))
            for subject_id in subject_ids:
                by_subject[subject_id].append(str(content_id))
            for company_id in company_ids:
                by_company[company_id].append(str(content_id))
                company_subjects[company_id].update(str(s) for s in subject_ids)
        for subject_id, contents in by_subject.items():
            session.add(
                EventSubjectLink(
                    event_id=event_id,
                    subject_id=subject_id,
                    strategy="content_subject_link",
                    content_count=len(contents),
                    evidence={"content_item_ids": contents},
                )
            )
            subject_links += 1
        for company_id, contents in by_company.items():
            if (event_id, company_id) in reviewed_companies:
                continue
            session.add(
                EventCompanyLink(
                    event_id=event_id,
                    company_id=company_id,
                    strategy="content_subject_company",
                    content_count=len(contents),
                    evidence={
                        "content_item_ids": contents,
                        "subject_ids": sorted(company_subjects[company_id]),
                    },
                )
            )
            company_links += 1
    session.flush()
    return {"subject_links": subject_links, "company_links": company_links}


def _nearest(rejections):
    if not rejections:
        return None
    decision, member, event_id = max(rejections, key=lambda r: r[0].similarity)
    return {
        "event_id": str(event_id),
        "content_item_id": str(member.content_id),
        "reason": decision.reason,
        "title_similarity": decision.similarity,
        "time_delta_seconds": decision.time_delta_seconds,
    }


def _resolve(session, item, features):
    """(event_id or None, strategy, confidence, evidence) for one unprocessed content item."""

    candidate = features[item.id]
    matches, rejections = [], []
    for event_id, members in _candidate_events(session, item).items():
        best, rejected = match_event(candidate, members)
        if best is not None:
            matches.append((event_id, *best))
        else:
            rejections.extend((decision, member, event_id) for decision, member in rejected)
    base = {"resolver_version": RESOLVER_VERSION, "event_type": candidate.event_type}
    if len(matches) == 1:
        event_id, decision, member = matches[0]
        return (
            event_id,
            decision.strategy,
            decision.confidence,
            {
                **base,
                "matched_event_id": str(event_id),
                "matched_content_item_id": str(member.content_id),
                "title_similarity": decision.similarity,
                "time_delta_seconds": decision.time_delta_seconds,
                "attribution": candidate.attribution,
                "shared_subject_ids": [str(s) for s in decision.shared_subject_ids],
                "shared_company_ids": [str(c) for c in decision.shared_company_ids],
            },
        )
    if matches:
        return (
            None,
            "seed",
            "origin",
            {
                **base,
                "reason": "ambiguous",
                "ambiguous_event_ids": sorted(str(m[0]) for m in matches),
            },
        )
    return None, "seed", "origin", {**base, "reason": "no_match", "nearest": _nearest(rejections)}


def build_events(session, since=None, limit=None, content_ids=None):
    """Assign every unprocessed content item (optionally filtered) to an Event."""

    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    query = (
        select(ContentItem)
        .outerjoin(EventContentLink, EventContentLink.content_item_id == ContentItem.id)
        .where(EventContentLink.id.is_(None))
        .order_by(EFFECTIVE_AT, ContentItem.id)
    )
    if since is not None:
        query = query.where(EFFECTIVE_AT >= since)
    if content_ids is not None:
        query = query.where(ContentItem.id.in_(list(content_ids)))
    if limit is not None:
        query = query.limit(limit)
    items = session.scalars(query).all()
    strategies = Counter()
    created, touched = 0, set()
    for item in items:
        features = _features(session, [item])
        event_id, strategy, confidence, evidence = _resolve(session, item, features)
        if event_id is None:
            feature = features[item.id]
            at = _effective(item)
            event = Event(
                domain=item.domain,
                event_type=feature.event_type,
                canonical_title=feature.headline[:1000],
                normalized_title=feature.normalized[:1000],
                title_language=item.language,
                representative_content_id=item.id,
                first_published_at=at,
                last_published_at=at,
                first_observed_at=item.observed_at,
                last_observed_at=item.observed_at,
                content_count=0,
                source_count=0,
            )
            session.add(event)
            session.flush()
            event_id = event.id
            created += 1
        session.add(
            EventContentLink(
                event_id=event_id,
                content_item_id=item.id,
                strategy=strategy,
                confidence=confidence,
                evidence=evidence,
            )
        )
        session.flush()
        refresh_event(session, event_id)
        session.flush()
        strategies[strategy] += 1
        if evidence.get("reason") == "ambiguous":
            strategies["ambiguous"] += 1
        touched.add(event_id)
    links = refresh_event_entities(session, touched)
    return {
        "resolver_version": RESOLVER_VERSION,
        "processed": len(items),
        "created_events": created,
        "joined_existing": len(items) - created,
        "strategies": dict(sorted(strategies.items())),
        "touched_events": len(touched),
        **links,
    }


def relink_all_events(session):
    """Rebuild every Event's derived Subject/Company links (run after ``content-relink``)."""

    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    event_ids = session.scalars(
        select(Event.id).where(Event.id.not_in(select(EventRedirect.duplicate_id)))
    ).all()
    return {"events": len(event_ids), **refresh_event_entities(session, event_ids)}


def preview_clusters(session, limit=50):
    """Multi-content Events with their member headlines, for audit output."""

    events = session.scalars(
        select(Event)
        .where(Event.content_count > 1)
        .order_by(Event.content_count.desc(), Event.last_published_at.desc())
        .limit(limit)
    ).all()
    output = []
    for event in events:
        rows = session.execute(
            select(Source.name, ContentItem.title, EventContentLink.strategy)
            .join(ContentItem, ContentItem.id == EventContentLink.content_item_id)
            .join(Source, Source.id == ContentItem.source_id)
            .where(EventContentLink.event_id == event.id)
            .order_by(EFFECTIVE_AT)
        ).all()
        output.append(
            {
                "event_id": str(event.id),
                "canonical_title": event.canonical_title,
                "event_type": event.event_type,
                "content": [
                    {"source": name, "headline": extract_headline(title)[:120], "strategy": s}
                    for name, title, s in rows
                ],
            }
        )
    return output
