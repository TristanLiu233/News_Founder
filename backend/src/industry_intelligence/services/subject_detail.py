"""Read model for one canonical subject: identity, current context and rank history.

Related news is served by ``services.news.list_news`` (``GET /news?subject_id=``);
this module only reports its count.
"""

from sqlalchemy import func, select

from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.models import (
    CompositeRankingEntry,
    CompositeRankingRun,
    IngestionBatch,
    RankList,
    RankObservation,
    Source,
    Subject,
    SubjectAlias,
)
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.services.news import related_counts
from industry_intelligence.services.ranking import latest_successful_run
from industry_intelligence.services.resolution import display_names

SOURCE_APPEARANCE_LIMIT = 20
ALIAS_TYPE_ORDER = {"official_name": 0, "localized_name": 1, "source_name": 2}


def resolve_canonical(session, subject_id):
    """Follow merge redirects; returns the canonical Subject, or None when unknown."""
    seen = set()
    while subject_id not in seen:
        seen.add(subject_id)
        target = session.scalar(
            select(SubjectRedirect.canonical_id).where(SubjectRedirect.duplicate_id == subject_id)
        )
        if target is None:
            break
        subject_id = target
    return session.get(Subject, subject_id)


def _identity_ids(session, subject):
    # Merges keep historical CompositeRankingEntry rows on the duplicate ID.
    duplicates = session.scalars(
        select(SubjectRedirect.duplicate_id).where(SubjectRedirect.canonical_id == subject.id)
    ).all()
    return [subject.id, *duplicates]


def _aliases(session, subject_id):
    rows = session.scalars(
        select(SubjectAlias)
        .where(SubjectAlias.subject_id == subject_id)
        .order_by(SubjectAlias.locale, SubjectAlias.alias)
    ).all()
    return [
        {
            "alias": row.alias,
            "locale": row.locale,
            "alias_type": row.alias_type,
            "alias_source": row.alias_source,
        }
        for row in rows
    ]


def _current_ranking(session, subject, ids):
    run = latest_successful_run(session, subject.domain)
    if run is None:
        return None
    entry = session.scalar(
        select(CompositeRankingEntry)
        .where(CompositeRankingEntry.run_id == run.id, CompositeRankingEntry.subject_id.in_(ids))
        .order_by(CompositeRankingEntry.rank)
        .limit(1)
    )
    if entry is None:
        return None
    return {
        "ranking_run_id": run.id,
        "calculated_at": run.calculated_at,
        "rank": entry.rank,
        "final_score": entry.final_score,
        "source_count": entry.source_count,
        "best_source_rank": entry.best_source_rank,
        "previous_rank": entry.details.get("previous_rank"),
        "rank_change": entry.details.get("rank_change"),
        "trend": entry.details.get("trend"),
        "language_coverage": entry.details.get("language_coverage"),
    }


def _source_appearances(session, ids):
    """Latest observation per rank list, with bounded aggregate history."""
    per_list = RankObservation.rank_list_id
    ranked = (
        select(
            RankObservation.id,
            func.row_number()
            .over(
                partition_by=per_list,
                order_by=(
                    RankObservation.observed_at.desc(),
                    RankObservation.rank,
                    RankObservation.id,
                ),
            )
            .label("position"),
            func.count().over(partition_by=per_list).label("appearance_count"),
            func.min(RankObservation.rank).over(partition_by=per_list).label("best_rank"),
            func.min(RankObservation.observed_at).over(partition_by=per_list).label("first_seen"),
        )
        .where(RankObservation.subject_id.in_(ids))
        .subquery()
    )
    rows = session.execute(
        select(
            RankObservation,
            Source,
            RankList,
            ranked.c.appearance_count,
            ranked.c.best_rank,
            ranked.c.first_seen,
        )
        .join(ranked, ranked.c.id == RankObservation.id)
        .join(Source, Source.id == RankObservation.source_id)
        .join(RankList, RankList.id == RankObservation.rank_list_id)
        .where(ranked.c.position == 1)
        .order_by(RankObservation.observed_at.desc(), Source.name)
    ).all()
    snapshots = dict(
        session.execute(
            select(IngestionBatch.rank_list_id, func.max(IngestionBatch.observed_at))
            .where(IngestionBatch.rank_list_id.in_([row.RankList.id for row in rows]))
            .group_by(IngestionBatch.rank_list_id)
        ).all()
    )
    definitions = {(item.name, item.list_name): item for item in SOURCES.values()}
    appearances = []
    for observation, source, rank_list, appearance_count, best_rank, first_seen in rows:
        definition = definitions.get((source.name, rank_list.name))
        appearances.append(
            {
                "source": source.name,
                "source_key": definition.key if definition else None,
                "metric_label": definition.metric_label if definition else None,
                "primary_language": source.primary_language,
                "rank_list": rank_list.name,
                "list_type": rank_list.list_type,
                "in_current_snapshot": snapshots.get(rank_list.id) == observation.observed_at,
                "latest_rank": observation.rank,
                "latest_observed_at": observation.observed_at,
                "title": observation.title,
                "url": observation.url,
                "raw_metric": observation.raw_metric,
                "appearance_count": appearance_count,
                "best_rank": best_rank,
                "first_seen_at": first_seen,
            }
        )
    appearances.sort(key=lambda item: not item["in_current_snapshot"])
    return appearances


def subject_detail(session, subject_id):
    subject = resolve_canonical(session, subject_id)
    if subject is None:
        return None
    ids = _identity_ids(session, subject)
    aliases = _aliases(session, subject.id)
    appearances = _source_appearances(session, ids)
    return {
        "subject_id": subject.id,
        "requested_subject_id": subject_id,
        "redirected": subject.id != subject_id,
        "canonical_name": subject.title,
        "display_names": display_names(subject.title, aliases),
        "domain": subject.domain,
        "subject_type": subject.subject_type,
        "description": subject.description,
        "aliases": sorted(aliases, key=lambda a: ALIAS_TYPE_ORDER.get(a["alias_type"], 3)),
        "current_ranking": _current_ranking(session, subject, ids),
        "source_appearances": appearances[:SOURCE_APPEARANCE_LIMIT],
        "source_appearance_count": len(appearances),
        "observation_count": sum(item["appearance_count"] for item in appearances),
        "latest_observed_at": max(
            (item["latest_observed_at"] for item in appearances), default=None
        ),
        "related_news_count": related_counts(session, [subject.id]).get(subject.id, 0),
    }


def rank_history(session, subject_id, limit=30):
    """Successful composite runs containing the subject, newest first."""
    subject = resolve_canonical(session, subject_id)
    if subject is None:
        return None
    ids = _identity_ids(session, subject)
    appears = (
        CompositeRankingEntry.subject_id.in_(ids),
        CompositeRankingRun.status == "success",
        CompositeRankingRun.domain == str(subject.domain),
    )
    total = session.scalar(
        select(func.count(func.distinct(CompositeRankingRun.id)))
        .select_from(CompositeRankingEntry)
        .join(CompositeRankingRun, CompositeRankingRun.id == CompositeRankingEntry.run_id)
        .where(*appears)
    )
    rows = session.execute(
        select(CompositeRankingEntry, CompositeRankingRun)
        .join(CompositeRankingRun, CompositeRankingRun.id == CompositeRankingEntry.run_id)
        .where(*appears)
        .order_by(CompositeRankingRun.calculated_at.desc(), CompositeRankingEntry.rank)
        .limit(limit * len(ids))
    ).all()
    items, seen_runs = [], set()
    for entry, run in rows:
        # A pre-merge run may rank both identities; keep the better position.
        if run.id in seen_runs:
            continue
        seen_runs.add(run.id)
        items.append(
            {
                "ranking_run_id": run.id,
                "calculated_at": run.calculated_at,
                "rank": entry.rank,
                "final_score": entry.final_score,
                "source_count": entry.source_count,
                "trend": entry.details.get("trend"),
                "rank_change": entry.details.get("rank_change"),
            }
        )
    return {
        "subject_id": subject.id,
        "requested_subject_id": subject_id,
        "domain": subject.domain,
        "limit": limit,
        "total": total,
        "items": items[:limit],
    }
