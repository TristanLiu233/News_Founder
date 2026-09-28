"""One bounded, manual content update run (CLI ``content-update-all``); no daemon, no scheduler.

Stages keep their own responsibilities and transactions:

1. per source: fetch → persist (one transaction per source; a failing source is reported and the
   others continue) → deterministic linking of the new items (a SAVEPOINT inside that
   transaction; a linking failure is reported and never rolls back the content);
2. once, after all sources: ``build_events`` over every content item without an Event
   (its own transaction; a failure is reported and never deletes content). New Events' Subject
   and Company links are derived in the same step.

Each stage is also available as its own command (``ingest-content``, ``content-relink``,
``event-build``, ``event-relink``) for debugging. Source adapters never cluster anything.
"""

from __future__ import annotations

from collections import Counter

from industry_intelligence.services.content_ingestion import ingest_all_content
from industry_intelligence.services.event_ingestion import build_events

SUMMED = ("fetched_count", "persisted_count", "replayed_count", "invalid_count", "failed_count")


def run_event_build(session_factory):
    session = session_factory()
    try:
        result = build_events(session)
        session.commit()
        return result
    except Exception as exc:
        session.rollback()
        return {"error": type(exc).__name__}
    finally:
        session.close()


async def update_all_content(settings, session_factory, keys=None, events=True):
    sources = await ingest_all_content(settings, session_factory, keys)
    totals, by_domain = Counter(), {}
    for result in sources:
        result.pop("content_ids", None)
        if "error" in result:
            totals["failed_sources"] += 1
            continue
        domain = by_domain.setdefault(result["domain"], Counter())
        for key in SUMMED + ("link_count",):
            totals[key] += result[key]
            domain[key] += result[key]
        if "link_error" in result:
            totals["link_errors"] += 1
    output = {
        "sources": sources,
        "totals": dict(totals),
        "by_domain": {domain: dict(counts) for domain, counts in sorted(by_domain.items())},
    }
    if events:
        output["events"] = run_event_build(session_factory)
    return output
