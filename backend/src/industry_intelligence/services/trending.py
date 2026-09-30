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

Trending-2 adds, still without any score:

- Rank movement (``rank_movement``): each external rank is compared with the same article's
  rank in the immediately previous stored snapshot of the *same* list (same ``source_key``,
  ``list_key``, ``ranking_kind``, and publisher-stated window). ``rank_delta = previous_rank -
  current_rank``; movement is ``up``/``down``/``stable``, ``new`` when the article is absent
  from a complete previous snapshot, and ``None`` when no comparable previous snapshot exists.
  Ranks of different lists or publishers are never compared. ``first_ranked_at`` is the
  earliest stored snapshot of the list (same window) that listed the article, so a ``new``
  article that was listed before is marked ``reentered``.
- Multi-list evidence (``native_list_evidence``): how many distinct lists and publishers list
  the Event. Descriptive only; it never changes the order.
- Home selection (``selection="home"``): Home-eligible Events (latest report at most
  ``HOME_MAX_REPORT_AGE`` older than a list observation that lists them) taken one list at a
  time in catalog order, each list in its own native order, skipping Events already taken
  (``home_round_robin``). A presentation sample, not an overall rank; ``/trending`` itself is
  unchanged by it.

Queries do not grow with the number of listed articles: the latest two snapshots of every list
come from one windowed query, first listings from one aggregate query, and Event resolution is
batched (only redirected or retired IDs fall back to ``resolve_event_redirect``).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text

from industry_intelligence.adapters.source.content_registry import NEWS_RANKINGS
from industry_intelligence.db.models import ContentItem, Event, EventContentLink, EventRedirect
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
HOME_ORDERING = "home_list_round_robin"
SELECTIONS = ("native", "home")
DEFAULT_LIMIT = 20
# Trending-2: Home shows an Event only when its latest report was published at most this long
# before a list observation that lists it (chosen from the live age distribution; see
# DEVELOPMENT.md). /trending itself never filters by age.
HOME_MAX_REPORT_AGE = timedelta(hours=48)
HOME_RULE = "latest_report_within_hours_of_list_observation"


def _iso(value):
    return value.isoformat() if value else None


def _domain_definitions(domain):
    return [d for d in NEWS_RANKINGS.values() if str(d.domain) == str(domain)]


def snapshot_pairs(session, domain):
    """(definition, latest snapshot, immediately previous snapshot or None) of every ranked list
    of ``domain``, in catalog order, from one query. A list whose latest snapshot is not a
    ranked-article snapshot is left out (as before)."""

    definitions = _domain_definitions(domain)
    if not definitions:
        return []
    position = (
        func.row_number()
        .over(partition_by=IngestionBatch.source_key, order_by=IngestionBatch.observed_at.desc())
        .label("position")
    )
    recent = (
        select(IngestionBatch.id, position)
        .where(IngestionBatch.source_key.in_([d.key for d in definitions]))
        .subquery()
    )
    batches = defaultdict(list)
    for batch in session.scalars(
        select(IngestionBatch)
        .join(recent, recent.c.id == IngestionBatch.id)
        .where(recent.c.position <= 2)
        .order_by(IngestionBatch.source_key, IngestionBatch.observed_at.desc())
    ):
        batches[batch.source_key].append(batch)
    output = []
    for definition in definitions:
        history = batches.get(definition.key, [])
        if history and history[0].result.get("kind") == RESULT_KIND:
            output.append((definition, history[0], history[1] if len(history) > 1 else None))
    return output


def current_snapshots(session, domain):
    """(definition, latest snapshot) of every ranked list of ``domain``, in catalog order."""

    return [(definition, latest) for definition, latest, _ in snapshot_pairs(session, domain)]


def list_comparison(latest, previous):
    """(comparable, reason, previous_complete) for two snapshots of one list.

    Comparable only when the previous snapshot is a ranked-article snapshot of the same list
    (``list_key`` and ``ranking_kind``) with the same publisher-stated window; e.g. two
    新浪财经 daily lists of different Beijing dates are different lists. A previous snapshot that
    dropped adapter-rejected or invalid rows is still comparable for articles it holds, but an
    absent article is then not known to be new."""

    if previous is None or previous.result.get("kind") != RESULT_KIND:
        return False, "no_previous_snapshot", True
    for field in ("list_key", "ranking_kind"):
        if previous.result.get(field) != latest.result.get(field):
            return False, "list_changed", True
    if previous.result.get("window") != latest.result.get("window"):
        return False, "window_changed", True
    complete = not (previous.result.get("invalid_count") or previous.result.get("failed_count"))
    return True, None, complete


def rank_movement(current_rank, previous_rank, comparable, previous_complete=True):
    """(previous_rank, rank_delta, movement) of one article on one list; a same-list fact,
    never a score. ``rank_delta = previous_rank - current_rank`` (positive = moved up)."""

    if not comparable:
        return None, None, None
    if previous_rank is None:
        return (None, None, "new") if previous_complete else (None, None, None)
    delta = previous_rank - current_rank
    return previous_rank, delta, "up" if delta > 0 else "down" if delta < 0 else "stable"


def native_list_evidence(rankings):
    """Distinct lists (``source_key``) and publishers listing one Event: descriptive only."""

    lists, publishers = [], []
    for ranking in rankings:
        if ranking["source_key"] not in lists:
            lists.append(ranking["source_key"])
        if ranking["source"] not in publishers:
            publishers.append(ranking["source"])
    return {
        "native_list_count": len(lists),
        "native_publisher_count": len(publishers),
        "native_publishers": publishers,
    }


def home_eligible(last_published_at, observations):
    """True when the Event's latest report is at most ``HOME_MAX_REPORT_AGE`` older than the
    most recent observation of a list that lists it (ages are measured when the publisher
    listed it, so the rule does not change between update runs)."""

    if last_published_at is None or not observations:
        return False
    return max(observations) - last_published_at <= HOME_MAX_REPORT_AGE


def home_round_robin(queues, limit):
    """Take the next not-yet-taken Event of each list in turn (lists in the given order, each
    list in its own native order) until ``limit`` or every list is exhausted. Pure. The result
    is a presentation order, not an overall rank."""

    positions = [0] * len(queues)
    selected, taken = [], set()
    while limit is None or len(selected) < limit:
        progressed = False
        for index, queue in enumerate(queues):
            if limit is not None and len(selected) >= limit:
                break
            while positions[index] < len(queue) and queue[positions[index]] in taken:
                positions[index] += 1
            if positions[index] < len(queue):
                event_id = queue[positions[index]]
                positions[index] += 1
                selected.append(event_id)
                taken.add(event_id)
                progressed = True
        if not progressed:
            break
    return selected


FIRST_RANKED_SQL = text(
    """
    SELECT b.source_key, coalesce(b.result->>'window', '') AS list_window,
           entry->>'content_item_id' AS content_item_id, min(b.observed_at) AS first_ranked_at
    FROM ingestion_batches AS b
    CROSS JOIN LATERAL json_array_elements(b.result->'entries') AS entry
    WHERE b.source_key = ANY(:keys)
      AND b.result->>'kind' = :kind
      AND entry->>'content_item_id' = ANY(:content_ids)
    GROUP BY 1, 2, 3
    """
)


def first_rankings(session, keys, content_ids):
    """{(source_key, window, content_item_id): earliest observation listing it}: one query."""

    if not keys or not content_ids:
        return {}
    rows = session.execute(
        FIRST_RANKED_SQL,
        {"keys": list(keys), "kind": RESULT_KIND, "content_ids": [str(c) for c in content_ids]},
    )
    return {
        (row.source_key, row.list_window, row.content_item_id): row.first_ranked_at for row in rows
    }


def resolve_owners(session, owners):
    """{owner Event ID: current Event ID}; batched, falling back to ``resolve_event_redirect``
    only for owners that were merged away or no longer exist (rebuild-retired)."""

    owners = set(owners)
    if not owners:
        return {}
    redirected = set(
        session.scalars(
            select(EventRedirect.duplicate_id).where(EventRedirect.duplicate_id.in_(owners))
        )
    )
    existing = set(session.scalars(select(Event.id).where(Event.id.in_(owners))))
    return {
        owner: owner
        if owner in existing and owner not in redirected
        else resolve_event_redirect(session, owner)
        for owner in owners
    }


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


def domain_trending(session, domain, limit=DEFAULT_LIMIT, summaries=True, selection="native"):
    if selection not in SELECTIONS:
        raise ValueError(f"Unknown Trending selection: {selection}")
    snapshots = snapshot_pairs(session, domain)
    lists, evidence, comparisons, previous_ranks = [], [], [], []
    for definition, batch, previous in snapshots:
        entries = sorted(batch.result.get("entries", []), key=lambda entry: entry["rank"])
        comparable, reason, complete = list_comparison(batch, previous)
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
                "previous_observed_at": _iso(previous.observed_at) if comparable else None,
                "movement_comparable": comparable,
                "movement_unavailable_reason": reason,
            }
        )
        comparisons.append((comparable, complete))
        ranks = {}
        if comparable:
            for entry in previous.result.get("entries", []):
                ranks.setdefault(str(entry.get("content_item_id")), entry.get("rank"))
        previous_ranks.append(ranks)
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
    resolved = resolve_owners(session, owners.values())
    first_ranked = first_rankings(session, [d.key for d, *_ in snapshots], content_ids)
    order, rankings = [], defaultdict(list)
    queues = [[] for _ in lists]
    observations = defaultdict(list)
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
        event_id = resolved[owner]
        if event_id not in rankings:
            order.append(event_id)
        if event_id not in queues[index]:
            queues[index].append(event_id)
        observations[event_id].append(batch.observed_at)
        comparable, complete = comparisons[index]
        previous_rank, delta, movement = rank_movement(
            entry["rank"], previous_ranks[index].get(str(content_id)), comparable, complete
        )
        window = batch.result.get("window")
        first = first_ranked.get((definition.key, window or "", str(content_id)))
        rankings[event_id].append(
            {
                "source_key": definition.key,
                "source": definition.name,
                "list_key": definition.list_key,
                "list_name": definition.list_name,
                "ranking_kind": definition.ranking_kind,
                "window": window,
                "rank": entry["rank"],
                "native_value": entry.get("native_value"),
                "observed_at": _iso(batch.observed_at),
                "previous_rank": previous_rank,
                "rank_delta": delta,
                "movement": movement,
                "previous_observed_at": lists[index]["previous_observed_at"],
                "first_ranked_at": _iso(first),
                "reentered": movement == "new" and first is not None and first < batch.observed_at,
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
    result = {
        "domain": str(domain),
        "status": "ready" if snapshots else "unavailable",
        "selection": selection,
        "ordering": ORDERING,
        "lists": lists,
    }
    if selection == "home":
        eligible = {
            event_id
            for event_id in order
            if home_eligible(events[event_id].last_published_at, observations[event_id])
        }
        order = home_round_robin(
            [[event_id for event_id in queue if event_id in eligible] for queue in queues], None
        )
        result["ordering"] = HOME_ORDERING
        result["home_eligibility"] = {
            "rule": HOME_RULE,
            "max_report_age_hours": int(HOME_MAX_REPORT_AGE.total_seconds() // 3600),
            "eligible_count": len(eligible),
            "excluded_count": len(events) - len(eligible),
        }
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
                **native_list_evidence(rankings[event_id]),
                "topics": [
                    subject
                    for subject in event_subjects
                    if subject["subject_type"] != str(SubjectType.EVENT)
                ],
                "summary": summaries.get(event_id),
            }
        )
    return {**result, "total": len(order), "items": items}


def trending(session, domain=None, limit=DEFAULT_LIMIT, selection="native"):
    domains = [domain] if domain else [str(value) for value in Domain]
    return {
        "generated_at": datetime.now(UTC),
        "domains": [
            domain_trending(session, value, limit, selection=selection) for value in domains
        ],
    }
