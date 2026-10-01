"""Read models for Events and Event Detail; persisted data only.

Events are evidence-backed clusters of existing ContentItems. Nothing here fetches, ingests,
builds Events, or scores them: ``source_count`` is coverage, not importance. Time fields are
content publication times (``time_basis: content_publication``), not verified occurrence times.
"""

from __future__ import annotations

import re
from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select

from industry_intelligence.db.models import (
    Company,
    ContentItem,
    Event,
    EventCompanyLink,
    EventContentLink,
    EventRedirect,
    EventRetiredId,
    EventSubjectLink,
    Source,
    Subject,
    SubjectAlias,
)
from industry_intelligence.services.company_profiles import (
    _aliases as _company_aliases,
)
from industry_intelligence.services.company_profiles import (
    _linked_subjects,
    chinese_name,
)
from industry_intelligence.services.company_profiles import (
    display_names as company_display_names,
)
from industry_intelligence.services.company_resolution import resolve_company_redirect
from industry_intelligence.services.resolution import display_names
from industry_intelligence.services.subject_detail import resolve_canonical

TIME_BASIS = "content_publication"
LIST_ENTITY_PREVIEW = 3
LIST_SOURCE_PREVIEW = 4
DETAIL_CONTENT_PREVIEW = 5
EFFECTIVE_AT = func.coalesce(ContentItem.published_at, ContentItem.observed_at)
# Feed boilerplate after a stored excerpt ("… Read more", "[…]"); the excerpt text is not rewritten.
EXCERPT_TRAILER = re.compile(
    r"\s*(?:\[(?:…|\.\.\.)\]|read more|continue reading)[.…]?\s*$", re.IGNORECASE
)
MIN_EXCERPT_CHARS = 20


def _iso(value):
    return value.isoformat() if value else None


def resolve_event_redirect(session, event_id):
    """Resolve reviewed merges, then rebuild-retired IDs through current content ownership."""

    seen = set()
    while event_id not in seen:
        seen.add(event_id)
        target = session.scalar(
            select(EventRedirect.canonical_id).where(EventRedirect.duplicate_id == event_id)
        )
        if target is None:
            break
        event_id = target
    if session.get(Event, event_id) is not None:
        return event_id
    retired = session.get(EventRetiredId, event_id)
    if retired is None:
        return event_id
    candidates = []
    if retired.representative_content_id is not None:
        candidates.append(retired.representative_content_id)
    for raw_id in retired.content_item_ids:
        try:
            content_id = UUID(raw_id)
        except (TypeError, ValueError):
            continue
        if content_id not in candidates:
            candidates.append(content_id)
    for content_id in candidates:
        current = session.scalar(
            select(EventContentLink.event_id).where(EventContentLink.content_item_id == content_id)
        )
        if current is not None:
            # A reviewed merge can subsequently retire the current content owner.
            return resolve_event_redirect(session, current)
    return event_id


def _subjects(session, event_ids):
    rows = session.execute(
        select(EventSubjectLink, Subject)
        .join(Subject, Subject.id == EventSubjectLink.subject_id)
        .where(EventSubjectLink.event_id.in_(event_ids))
        .order_by(EventSubjectLink.content_count.desc(), Subject.title)
    ).all()
    aliases = defaultdict(list)
    subject_ids = {subject.id for _, subject in rows}
    if subject_ids:
        for alias in session.scalars(
            select(SubjectAlias)
            .where(SubjectAlias.subject_id.in_(subject_ids))
            .order_by(SubjectAlias.locale, SubjectAlias.alias)
        ):
            aliases[alias.subject_id].append(
                {"alias": alias.alias, "locale": alias.locale, "alias_type": alias.alias_type}
            )
    by_event = defaultdict(list)
    for link, subject in rows:
        by_event[link.event_id].append(
            {
                "id": str(subject.id),
                "canonical_name": subject.title,
                "display_names": display_names(subject.title, aliases[subject.id]),
                "domain": str(subject.domain),
                "subject_type": str(subject.subject_type),
                "content_count": link.content_count,
                "strategy": link.strategy,
            }
        )
    return by_event


def _companies(session, event_ids):
    rows = session.execute(
        select(EventCompanyLink, Company)
        .join(Company, Company.id == EventCompanyLink.company_id)
        .where(EventCompanyLink.event_id.in_(event_ids))
        .order_by(EventCompanyLink.content_count.desc(), Company.canonical_name)
    ).all()
    company_ids = list({company.id for _, company in rows})
    aliases = _company_aliases(session, company_ids) if company_ids else {}
    subjects = _linked_subjects(session, company_ids) if company_ids else {}
    by_event = defaultdict(list)
    for link, company in rows:
        zh, _ = chinese_name(
            aliases.get(company.id, []), [s["title"] for s in subjects.get(company.id, [])]
        )
        by_event[link.event_id].append(
            {
                "id": str(company.id),
                "canonical_name": company.canonical_name,
                "display_names": company_display_names(company.canonical_name, zh),
                "content_count": link.content_count,
                "strategy": link.strategy,
            }
        )
    return by_event


def _coverage(session, event_ids):
    """Per-event source names and languages with content counts, most frequent first."""

    sources, languages = defaultdict(list), defaultdict(list)
    for event_id, name, count in session.execute(
        select(EventContentLink.event_id, Source.name, func.count())
        .join(ContentItem, ContentItem.id == EventContentLink.content_item_id)
        .join(Source, Source.id == ContentItem.source_id)
        .where(EventContentLink.event_id.in_(event_ids))
        .group_by(EventContentLink.event_id, Source.name)
        .order_by(func.count().desc(), Source.name)
    ):
        sources[event_id].append({"name": name, "content_count": count})
    for event_id, language, count in session.execute(
        select(EventContentLink.event_id, ContentItem.language, func.count())
        .join(ContentItem, ContentItem.id == EventContentLink.content_item_id)
        .where(EventContentLink.event_id.in_(event_ids))
        .group_by(EventContentLink.event_id, ContentItem.language)
        .order_by(func.count().desc(), ContentItem.language)
    ):
        languages[event_id].append({"language": language, "content_count": count})
    return sources, languages


def _time(event):
    return {
        "basis": TIME_BASIS,
        "first_published_at": _iso(event.first_published_at),
        "last_published_at": _iso(event.last_published_at),
        "first_observed_at": _iso(event.first_observed_at),
        "last_observed_at": _iso(event.last_observed_at),
    }


def _summary(event, sources, languages, subjects, companies, preview=True):
    limit = LIST_ENTITY_PREVIEW if preview else None
    return {
        "id": str(event.id),
        "canonical_title": event.canonical_title,
        "title_language": event.title_language,
        "domain": str(event.domain),
        "event_type": event.event_type,
        "time": _time(event),
        "content_count": event.content_count,
        "source_count": event.source_count,
        "sources": sources[:LIST_SOURCE_PREVIEW] if preview else sources,
        "languages": languages,
        "subjects": subjects[:limit],
        "subject_count": len(subjects),
        "companies": companies[:limit],
        "company_count": len(companies),
    }


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_events(
    session,
    domain=None,
    subject_id=None,
    company_id=None,
    event_type=None,
    from_=None,
    to=None,
    limit=20,
    offset=0,
    q=None,
):
    """Canonical Events, most recently reported first; redirected duplicates are excluded.

    ``q`` (Product-2) is a plain case-insensitive substring match on the Event's headline or on
    any of its reports' headlines, exactly as published: no scoring, translation, or fuzzy
    matching, and the order stays most recently reported first.
    """

    query = select(Event).where(
        Event.content_count > 0, Event.id.not_in(select(EventRedirect.duplicate_id))
    )
    if q and q.strip():
        pattern = "%" + _escape_like(" ".join(q.split())) + "%"
        query = query.where(
            Event.canonical_title.ilike(pattern, escape="\\")
            | Event.id.in_(
                select(EventContentLink.event_id)
                .join(ContentItem, ContentItem.id == EventContentLink.content_item_id)
                .where(ContentItem.title.ilike(pattern, escape="\\"))
            )
        )
    if domain:
        query = query.where(Event.domain == domain)
    if event_type:
        query = query.where(Event.event_type == event_type)
    if from_ is not None:
        query = query.where(Event.last_published_at >= from_)
    if to is not None:
        query = query.where(Event.first_published_at <= to)
    if subject_id is not None:
        subject = resolve_canonical(session, subject_id)
        if subject is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        query = query.where(
            Event.id.in_(
                select(EventSubjectLink.event_id).where(EventSubjectLink.subject_id == subject.id)
            )
        )
    if company_id is not None:
        company_id = resolve_company_redirect(session, company_id)
        query = query.where(
            Event.id.in_(
                select(EventCompanyLink.event_id).where(EventCompanyLink.company_id == company_id)
            )
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    events = session.scalars(
        query.order_by(Event.last_published_at.desc(), Event.id).limit(limit).offset(offset)
    ).all()
    ids = [event.id for event in events]
    sources, languages = _coverage(session, ids) if ids else ({}, {})
    subjects = _subjects(session, ids) if ids else {}
    companies = _companies(session, ids) if ids else {}
    return {
        "items": [
            _summary(
                event,
                sources.get(event.id, []),
                languages.get(event.id, []),
                subjects.get(event.id, []),
                companies.get(event.id, []),
            )
            for event in events
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _content_rows(session, event_id, limit, offset, order="asc"):
    direction = EFFECTIVE_AT.asc() if order == "asc" else EFFECTIVE_AT.desc()
    rows = session.execute(
        select(ContentItem, Source.name, EventContentLink)
        .join(Source, Source.id == ContentItem.source_id)
        .join(EventContentLink, EventContentLink.content_item_id == ContentItem.id)
        .where(EventContentLink.event_id == event_id)
        .order_by(direction, Source.name, ContentItem.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "url": item.url,
            "source": source,
            "content_type": item.content_type,
            "language": item.language,
            "published_at": _iso(item.published_at),
            "observed_at": _iso(item.observed_at),
            "link_strategy": link.strategy,
            "link_confidence": link.confidence,
            # Product-2: the stored publisher excerpt when fit to show (never AI text).
            "excerpt": clean_excerpt(item.description, item.title),
        }
        for item, source, link in rows
    ]


def event_detail(session, event_id):
    """One canonical Event with its entities, coverage, and a short content preview."""

    canonical_id = resolve_event_redirect(session, event_id)
    event = session.get(Event, canonical_id)
    if event is None:
        return None
    sources, languages = _coverage(session, [event.id])
    detail = _summary(
        event,
        sources.get(event.id, []),
        languages.get(event.id, []),
        _subjects(session, [event.id]).get(event.id, []),
        _companies(session, [event.id]).get(event.id, []),
        preview=False,
    )
    return {
        **detail,
        "requested_id": str(event_id),
        "redirected": event.id != event_id,
        "representative_content_id": (
            str(event.representative_content_id) if event.representative_content_id else None
        ),
        "content_preview": _content_rows(session, event.id, DETAIL_CONTENT_PREVIEW, 0),
    }


def event_content(session, event_id, limit=10, offset=0, order="asc"):
    """The Event's content timeline (publication order), paginated; None for an unknown ID."""

    canonical_id = resolve_event_redirect(session, event_id)
    event = session.get(Event, canonical_id)
    if event is None:
        return None
    return {
        "event_id": str(event.id),
        "items": _content_rows(session, event.id, limit, offset, order),
        "total": event.content_count,
        "limit": limit,
        "offset": offset,
        "order": order,
    }


def clean_excerpt(text, title=""):
    """A stored publisher excerpt fit to show under its headline, or None.

    Whitespace is collapsed and a trailing feed marker ("Read more", "[…]") is dropped; nothing
    else is changed. Excerpts that are too short or only repeat the headline are not shown.
    """

    text = EXCERPT_TRAILER.sub("", " ".join((text or "").split())).strip()
    if len(text) < MIN_EXCERPT_CHARS:
        return None
    headline = " ".join((title or "").split())
    if text == headline or text in headline:
        return None
    return text


def event_excerpts(session, event_ids):
    """Per Event, the stored excerpt of one of its reports (never AI text, never generated).

    The representative report is preferred; otherwise the first report in publication order
    whose excerpt is fit to show (``clean_excerpt``). Events without one are absent.
    """

    if not event_ids:
        return {}
    representative = dict(
        session.execute(
            select(Event.id, Event.representative_content_id).where(Event.id.in_(event_ids))
        ).all()
    )
    rows = session.execute(
        select(EventContentLink.event_id, ContentItem, Source.name)
        .join(ContentItem, ContentItem.id == EventContentLink.content_item_id)
        .join(Source, Source.id == ContentItem.source_id)
        .where(EventContentLink.event_id.in_(event_ids), ContentItem.description.is_not(None))
        .order_by(EFFECTIVE_AT, Source.name, ContentItem.id)
    ).all()
    candidates = defaultdict(list)
    for event_id, item, source in rows:
        text = clean_excerpt(item.description, item.title)
        if text is None:
            continue
        candidates[event_id].append(
            {
                "kind": "excerpt",
                "text": text,
                "language": item.language,
                "source": source,
                "content_item_id": str(item.id),
            }
        )
    output = {}
    for event_id, options in candidates.items():
        preferred = str(representative.get(event_id))
        output[event_id] = next(
            (row for row in options if row["content_item_id"] == preferred), options[0]
        )
    return output
