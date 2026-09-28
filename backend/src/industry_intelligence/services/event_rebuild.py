"""Bounded, deterministic rebuild of the derived Event layer for a publication window (CLI-only).

Events are derived data. ``rebuild_events(start, end)`` re-runs the current resolver over the
content published in ``[start, end)`` (``coalesce(published_at, observed_at)``, the same basis as
``event-build``):

* ContentItems, their external identity, and ContentSubjectLinks are never touched;
* the *affected* Events are those with at least one member in the window; all of their members
  are reprocessed (so an Event that straddles a window edge is rebuilt whole, and the reported
  ``content_range`` shows the effective extent); every other Event is untouched;
* rebuilt Events get new IDs — old ``/event/{id}`` URLs of affected Events stop resolving.
  Cluster membership, not the ID, is the stable result: the same content, rules, and window
  produce the same partition;
* a window touching a reviewed merge (an ``EventRedirect`` on either side, or a content link
  carrying ``reviewed_merge`` evidence) or an Event with a reviewed Company link (Phase 7) is
  refused, because a rebuild would discard a human decision (reviewed content ↔ subject links
  live on content and survive a rebuild);
* ``dry_run`` is handled by the caller rolling back the transaction.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, or_, select, text

from industry_intelligence.db.models import (
    ContentItem,
    Event,
    EventCompanyLink,
    EventContentLink,
    EventRedirect,
    EventSubjectLink,
    Source,
)
from industry_intelligence.services.content_linking import REVIEWED_STRATEGY
from industry_intelligence.services.event_ingestion import (
    EFFECTIVE_AT,
    LOCK_KEY,
    build_events,
)
from industry_intelligence.services.event_resolution import extract_headline


class EventRebuildRefused(ValueError):
    """The rebuild is invalid or would discard reviewed decisions; nothing was changed."""


def _partition(session, event_ids):
    """event_id → frozenset(content_ids) for ``event_ids``."""

    members = defaultdict(set)
    if event_ids:
        for event_id, content_id in session.execute(
            select(EventContentLink.event_id, EventContentLink.content_item_id).where(
                EventContentLink.event_id.in_(list(event_ids))
            )
        ):
            members[event_id].add(content_id)
    return {event_id: frozenset(ids) for event_id, ids in members.items()}


def _clusters(session, partition):
    output = []
    for event_id, content_ids in partition.items():
        if len(content_ids) < 2:
            continue
        event = session.get(Event, event_id)
        rows = session.execute(
            select(Source.name, ContentItem.title)
            .join(Source, Source.id == ContentItem.source_id)
            .where(ContentItem.id.in_(list(content_ids)))
            .order_by(EFFECTIVE_AT, ContentItem.id)
        ).all()
        output.append(
            {
                "event_id": str(event_id),
                "domain": str(event.domain),
                "event_type": event.event_type,
                "canonical_title": event.canonical_title,
                "content": [
                    {"source": name, "headline": extract_headline(title)[:120]}
                    for name, title in rows
                ],
            }
        )
    return sorted(output, key=lambda cluster: cluster["canonical_title"])


def rebuild_events(session, start, end):
    """Rebuild the Events of content published in ``[start, end)``; returns an audit summary."""

    if start is None or end is None or start.tzinfo is None or end.tzinfo is None:
        raise EventRebuildRefused("start and end must be timezone-aware")
    if start >= end:
        raise EventRebuildRefused("start must be before end")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    window_ids = set(
        session.scalars(select(ContentItem.id).where(EFFECTIVE_AT >= start, EFFECTIVE_AT < end))
    )
    affected = set(
        session.scalars(
            select(EventContentLink.event_id).where(
                EventContentLink.content_item_id.in_(list(window_ids))
            )
        )
    )
    reviewed = set(
        session.scalars(
            select(EventRedirect.duplicate_id).where(
                or_(
                    EventRedirect.duplicate_id.in_(list(affected)),
                    EventRedirect.canonical_id.in_(list(affected)),
                )
            )
        )
    ) | set(
        session.scalars(
            select(EventRedirect.canonical_id).where(
                or_(
                    EventRedirect.duplicate_id.in_(list(affected)),
                    EventRedirect.canonical_id.in_(list(affected)),
                )
            )
        )
    )
    for event_id, evidence in session.execute(
        select(EventContentLink.event_id, EventContentLink.evidence).where(
            EventContentLink.event_id.in_(list(affected))
        )
    ):
        if (evidence or {}).get("reviewed_merge"):
            reviewed.add(event_id)
    if reviewed:
        raise EventRebuildRefused(
            "window contains reviewed-merged Events: "
            + ", ".join(sorted(str(event_id) for event_id in reviewed))
        )
    reviewed_links = set(
        session.scalars(
            select(EventCompanyLink.event_id).where(
                EventCompanyLink.event_id.in_(list(affected)),
                EventCompanyLink.strategy == REVIEWED_STRATEGY,
            )
        )
    )
    if reviewed_links:
        raise EventRebuildRefused(
            "window contains Events with reviewed Company links: "
            + ", ".join(sorted(str(event_id) for event_id in reviewed_links))
        )
    old = _partition(session, affected)
    content_ids = window_ids | set().union(*old.values()) if old else set(window_ids)
    if not content_ids:
        return {
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "content_items": 0,
            "old_events": 0,
            "new_events": 0,
        }
    effective = session.execute(
        select(EFFECTIVE_AT).where(ContentItem.id.in_(list(content_ids)))
    ).scalars()
    times = sorted(effective)
    if affected:
        session.execute(
            delete(EventSubjectLink).where(EventSubjectLink.event_id.in_(list(affected)))
        )
        session.execute(
            delete(EventCompanyLink).where(EventCompanyLink.event_id.in_(list(affected)))
        )
        session.execute(
            delete(EventContentLink).where(EventContentLink.event_id.in_(list(affected)))
        )
        session.execute(delete(Event).where(Event.id.in_(list(affected))))
        session.flush()
    build = build_events(session, content_ids=content_ids)
    new_ids = set(
        session.scalars(
            select(EventContentLink.event_id).where(
                EventContentLink.content_item_id.in_(list(content_ids))
            )
        )
    )
    new = _partition(session, new_ids)
    old_sets, new_sets = set(old.values()), set(new.values())
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "content_range": {"first": times[0].isoformat(), "last": times[-1].isoformat()},
        "content_items": len(content_ids),
        "content_items_in_window": len(window_ids),
        "previously_unprocessed": len(content_ids - set().union(*old.values()))
        if old
        else len(content_ids),
        "old_events": len(old),
        "old_multi_content_events": sum(1 for ids in old.values() if len(ids) > 1),
        "new_events": len(new),
        "new_multi_content_events": sum(1 for ids in new.values() if len(ids) > 1),
        "unchanged_clusters": len(old_sets & new_sets),
        "dissolved_clusters": len(old_sets - new_sets),
        "new_clusters": len(new_sets - old_sets),
        "build": build,
        "clusters": _clusters(session, new),
        "partition": sorted(sorted(str(c) for c in ids) for ids in new.values()),
    }
