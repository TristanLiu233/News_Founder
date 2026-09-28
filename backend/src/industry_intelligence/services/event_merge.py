"""Reviewed, explicit Event merges with redirects; never triggered automatically.

``merge_events`` moves a duplicate Event's content links to the canonical Event, records an
``EventRedirect``, rebuilds both Events' aggregates and derived entity links, and keeps the
duplicate row so old IDs keep resolving. It refuses instead of guessing when:

* the Events belong to different domains;
* both have a specific (non-``general``) Event type and the types differ;
* both have Subject (or Company) links and they share none (distinct strong entity evidence);
* their publication ranges are more than ``REVIEWED_MERGE_MAX_GAP`` apart;
* the merge would create a redirect cycle or re-merge an already merged Event.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, text, update

from industry_intelligence.db.models import (
    Event,
    EventCompanyLink,
    EventContentLink,
    EventRedirect,
    EventSubjectLink,
)
from industry_intelligence.services.event_ingestion import (
    LOCK_KEY,
    refresh_event,
    refresh_event_entities,
)
from industry_intelligence.services.events import resolve_event_redirect

REVIEWED_MERGE_MAX_GAP = timedelta(days=7)


class EventMergeRefused(ValueError):
    """The merge is unsafe or invalid; nothing was changed."""


def _linked(session, model, column, event_id):
    return set(session.scalars(select(column).where(model.event_id == event_id)))


def merge_events(session, canonical_id, duplicate_id, reason):
    """Merge ``duplicate_id`` into ``canonical_id`` inside the caller's transaction."""

    reason = " ".join((reason or "").split())
    if not reason:
        raise EventMergeRefused("A review reason is required")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    canonical_id = resolve_event_redirect(session, canonical_id)
    if canonical_id == duplicate_id:
        raise EventMergeRefused("Cannot merge an Event into itself or create a redirect cycle")
    if session.get(EventRedirect, duplicate_id) is not None:
        raise EventMergeRefused("The duplicate Event is already merged")
    canonical = session.get(Event, canonical_id)
    duplicate = session.get(Event, duplicate_id)
    if canonical is None or duplicate is None:
        raise EventMergeRefused("Both Events must exist")
    if canonical.domain != duplicate.domain:
        raise EventMergeRefused("Events belong to different domains")
    if (
        "general" not in {canonical.event_type, duplicate.event_type}
        and canonical.event_type != duplicate.event_type
    ):
        raise EventMergeRefused("Events have incompatible Event types")
    for model, column, label in (
        (EventSubjectLink, EventSubjectLink.subject_id, "Subjects"),
        (EventCompanyLink, EventCompanyLink.company_id, "Companies"),
    ):
        held = _linked(session, model, column, canonical_id)
        incoming = _linked(session, model, column, duplicate_id)
        if held and incoming and not held & incoming:
            raise EventMergeRefused(f"Events are linked to different {label}")
    gap = max(
        duplicate.first_published_at - canonical.last_published_at,
        canonical.first_published_at - duplicate.last_published_at,
        timedelta(0),
    )
    if gap > REVIEWED_MERGE_MAX_GAP:
        raise EventMergeRefused(f"Events are {gap} apart, beyond the reviewed-merge window")

    links = session.scalars(
        select(EventContentLink).where(EventContentLink.event_id == duplicate_id)
    ).all()
    for link in links:
        link.event_id = canonical_id
        link.evidence = {
            **(link.evidence or {}),
            "reviewed_merge": {"from_event_id": str(duplicate_id), "reason": reason},
        }
    session.flush()
    session.execute(
        update(EventRedirect)
        .where(EventRedirect.canonical_id == duplicate_id)
        .values(canonical_id=canonical_id)
    )
    session.add(EventRedirect(duplicate_id=duplicate_id, canonical_id=canonical_id, reason=reason))
    session.flush()
    refresh_event(session, canonical_id)
    refresh_event(session, duplicate_id)
    refresh_event_entities(session, [canonical_id, duplicate_id])
    return {
        "canonical_id": str(canonical_id),
        "duplicate_id": str(duplicate_id),
        "moved_content_links": len(links),
        "reason": reason,
    }
