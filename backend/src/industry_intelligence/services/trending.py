"""Event-centric Trending: Events whose reports are on publishers' own ranked article lists.

For each domain, the latest snapshot of every ``NEWS_RANKINGS`` list is read (one committed
snapshot per list, never mixed with older ones). Each listed article is a ``ContentItem``; its
existing ``EventContentLink`` gives the Event (reviewed merges and rebuild-retired IDs resolve
through ``resolve_event_redirect``). An Event listed by several articles or lists is one card
that keeps every external rank as separate evidence.

Nothing is scored. There is no trend, hot, weighted, combined, or AI score, and ranks from
different publishers are never compared. Display order (``ORDERING``): lists in fixed catalog
order, each in its publisher's own rank order; an Event appears once, at its first listing.
The order is therefore not a popularity judgement across publishers.

A listed article that has no Event yet (it is assigned by the next ``events`` stage) is not
shown as a card and never gets an Event made for Trending; it is counted per list as
``pending_event_count``. Topics are the Event's derived Subject links, excluding ranked-story
Subjects (``event`` type), which are the listed story itself rather than a topic. Company links
are shown exactly as stored (DQ-3 rules unchanged). Page requests read persisted data only.

Product-1: each card carries one short ``summary`` from persisted data only, in this order: the
first point of the Event's displayable AI Summary (``ai_insights.available_summaries``: valid,
current evidence and prompt, primary locale), else a stored publisher excerpt of one of its
reports (``events.event_excerpts``), else ``None``. Nothing is generated on a request.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from industry_intelligence.adapters.source.content_registry import NEWS_RANKINGS
from industry_intelligence.db.models import ContentItem, Event, EventContentLink
from industry_intelligence.db.models.ingestion_batch import IngestionBatch
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.services.ai_insights import available_summaries
from industry_intelligence.services.events import (
    _companies,
    _coverage,
    _subjects,
    _summary,
    event_excerpts,
    resolve_event_redirect,
)
from industry_intelligence.services.news_ranking_ingestion import RESULT_KIND

ORDERING = "list_catalog_order_then_native_rank"
DEFAULT_LIMIT = 20


def _iso(value):
    return value.isoformat() if value else None


def current_snapshots(session, domain):
    """(definition, latest snapshot) of every ranked list of ``domain``, in catalog order."""

    output = []
    for definition in NEWS_RANKINGS.values():
        if str(definition.domain) != str(domain):
            continue
        batch = session.scalar(
            select(IngestionBatch)
            .where(IngestionBatch.source_key == definition.key)
            .order_by(IngestionBatch.observed_at.desc())
            .limit(1)
        )
        if batch is not None and batch.result.get("kind") == RESULT_KIND:
            output.append((definition, batch))
    return output


def _content_ids(entries):
    ids = []
    for entry in entries:
        try:
            ids.append(UUID(str(entry.get("content_item_id"))))
        except (TypeError, ValueError):
            continue
    return ids


def card_summaries(session, event_ids):
    """One short persisted summary per Event: displayable AI Summary, else a stored excerpt."""

    if not event_ids:
        return {}
    output = event_excerpts(session, event_ids)
    output.update(available_summaries(session, event_ids))
    return output


def domain_trending(session, domain, limit=DEFAULT_LIMIT, summaries=True):
    snapshots = current_snapshots(session, domain)
    lists, evidence = [], []
    for definition, batch in snapshots:
        entries = sorted(batch.result.get("entries", []), key=lambda entry: entry["rank"])
        lists.append(
            {
                "source_key": definition.key,
                "source": definition.name,
                "list_key": definition.list_key,
                "list_name": definition.list_name,
                "ranking_kind": definition.ranking_kind,
                "window": batch.result.get("window"),
                "observed_at": _iso(batch.observed_at),
                "entry_count": len(entries),
            }
        )
        for entry in entries:
            evidence.append((len(lists) - 1, definition, batch, entry))
    content_ids = _content_ids(entry for *_, entry in evidence)
    content = {
        item.id: item
        for item in session.scalars(select(ContentItem).where(ContentItem.id.in_(content_ids)))
    }
    owners = dict(
        session.execute(
            select(EventContentLink.content_item_id, EventContentLink.event_id).where(
                EventContentLink.content_item_id.in_(content_ids)
            )
        ).all()
    )
    resolved, order, rankings = {}, [], defaultdict(list)
    pending = defaultdict(int)
    for index, definition, batch, entry in evidence:
        try:
            content_id = UUID(str(entry.get("content_item_id")))
        except (TypeError, ValueError):
            continue
        item = content.get(content_id)
        owner = owners.get(content_id)
        if item is None or owner is None:
            pending[index] += 1
            continue
        if owner not in resolved:
            resolved[owner] = resolve_event_redirect(session, owner)
        event_id = resolved[owner]
        if event_id not in rankings:
            order.append(event_id)
        rankings[event_id].append(
            {
                "source_key": definition.key,
                "source": definition.name,
                "list_key": definition.list_key,
                "list_name": definition.list_name,
                "ranking_kind": definition.ranking_kind,
                "window": batch.result.get("window"),
                "rank": entry["rank"],
                "native_value": entry.get("native_value"),
                "observed_at": _iso(batch.observed_at),
                "content_item_id": str(content_id),
                "title": item.title,
                "url": item.url,
                "language": item.language,
            }
        )
    for index, row in enumerate(lists):
        row["pending_event_count"] = pending.get(index, 0)
    events = {
        event.id: event
        for event in session.scalars(select(Event).where(Event.id.in_(order)))
        if event.content_count > 0
    }
    order = [event_id for event_id in order if event_id in events]
    shown = order[:limit] if limit is not None else order
    sources, languages = _coverage(session, shown) if shown else ({}, {})
    subjects = _subjects(session, shown) if shown else {}
    companies = _companies(session, shown) if shown else {}
    summaries = card_summaries(session, shown) if summaries else {}
    items = []
    for event_id in shown:
        event_subjects = subjects.get(event_id, [])
        items.append(
            {
                "event": _summary(
                    events[event_id],
                    sources.get(event_id, []),
                    languages.get(event_id, []),
                    event_subjects,
                    companies.get(event_id, []),
                ),
                "external_rankings": rankings[event_id],
                "topics": [
                    subject
                    for subject in event_subjects
                    if subject["subject_type"] != str(SubjectType.EVENT)
                ],
                "summary": summaries.get(event_id),
            }
        )
    return {
        "domain": str(domain),
        "status": "ready" if snapshots else "unavailable",
        "ordering": ORDERING,
        "lists": lists,
        "total": len(order),
        "items": items,
    }


def trending(session, domain=None, limit=DEFAULT_LIMIT):
    domains = [domain] if domain else [str(value) for value in Domain]
    return {
        "generated_at": datetime.now(UTC),
        "domains": [domain_trending(session, value, limit) for value in domains],
    }
