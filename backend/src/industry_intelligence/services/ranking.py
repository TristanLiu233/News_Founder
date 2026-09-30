"""Weighted freshness-adjusted reciprocal rank fusion and history."""

import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select

from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.models import RankObservation, Source, Subject, SubjectAlias
from industry_intelligence.db.models.canonical import CompositeRankingEntry, CompositeRankingRun
from industry_intelligence.db.models.ingestion_batch import IngestionBatch


def freshness(age_hours, half_life_hours):
    return math.exp(-math.log(2) * max(age_hours, 0) / half_life_hours)


def contribution(rank, weight=1.0, rrf_k=60.0, freshness_factor=1.0):
    return weight / (rrf_k + rank) * freshness_factor


def trend_for(previous_rank, current_rank):
    if previous_rank is None:
        return "new", None
    change = previous_rank - current_rank
    return ("up" if change > 0 else "down" if change < 0 else "unchanged"), change


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def latest_successful_run(session, domain):
    """The only composite run exposed to readers; failed/running runs are never shown."""
    return session.scalar(
        select(CompositeRankingRun)
        .where(CompositeRankingRun.domain == str(domain), CompositeRankingRun.status == "success")
        .order_by(CompositeRankingRun.calculated_at.desc())
        .limit(1)
    )


def rank_domain(session, domain, settings, calculated_at=None):
    calculated_at = calculated_at or datetime.now(UTC)
    definitions = [definition for definition in SOURCES.values() if definition.domain == domain]
    batches = []
    for definition in definitions:
        batch = session.scalar(
            select(IngestionBatch)
            .where(IngestionBatch.source_key == definition.key)
            .order_by(IngestionBatch.observed_at.desc())
            .limit(1)
        )
        if batch:
            batches.append((definition, batch))
    config = {
        "rrf_k": settings.rrf_k,
        "freshness_half_life": settings.freshness_half_life,
        "weights": settings.rank_list_weights,
    }
    input_hash = _digest(
        {"domain": str(domain), "batches": [str(batch.id) for _, batch in batches], **config}
    )
    existing = session.scalar(
        select(CompositeRankingRun).where(
            CompositeRankingRun.domain == str(domain), CompositeRankingRun.input_hash == input_hash
        )
    )
    if existing:
        return existing, False
    previous = latest_successful_run(session, domain)
    previous_ranks = (
        {
            entry.subject_id: entry.rank
            for entry in session.scalars(
                select(CompositeRankingEntry).where(CompositeRankingEntry.run_id == previous.id)
            ).all()
        }
        if previous
        else {}
    )
    scores = defaultdict(float)
    breakdowns = defaultdict(list)
    best_ranks = {}
    source_ids = defaultdict(set)
    for definition, batch in batches:
        rows = session.execute(
            select(RankObservation, Source)
            .join(Source, Source.id == RankObservation.source_id)
            .where(
                RankObservation.rank_list_id == batch.rank_list_id,
                RankObservation.observed_at == batch.observed_at,
            )
            .order_by(RankObservation.rank)
        ).all()
        best = {}
        for observation, source in rows:
            best.setdefault(observation.subject_id, (observation, source))
        weight = settings.rank_list_weights.get(definition.key, 1.0)
        age = max((calculated_at - batch.observed_at).total_seconds() / 3600, 0)
        factor = freshness(age, settings.freshness_half_life)
        for subject_id, (observation, source) in best.items():
            value = contribution(observation.rank, weight, settings.rrf_k, factor)
            scores[subject_id] += value
            best_ranks[subject_id] = min(
                best_ranks.get(subject_id, observation.rank), observation.rank
            )
            source_ids[subject_id].add(source.id)
            breakdowns[subject_id].append(
                {
                    "source": source.name,
                    "source_key": definition.key,
                    "primary_language": source.primary_language,
                    "rank_list": definition.list_name,
                    "source_rank": observation.rank,
                    "weight": weight,
                    "snapshot_time": batch.observed_at.isoformat(),
                    "age_hours": age,
                    "freshness": factor,
                    "contribution": value,
                }
            )
    subject_names = {
        subject_id: session.get(Subject, subject_id).canonical_name for subject_id in scores
    }
    ordered = sorted(
        scores,
        key=lambda subject_id: (
            -scores[subject_id],
            -len(source_ids[subject_id]),
            best_ranks[subject_id],
            subject_names[subject_id],
        ),
    )
    run = CompositeRankingRun(
        domain=str(domain),
        calculated_at=calculated_at,
        rrf_k=settings.rrf_k,
        freshness_half_life=settings.freshness_half_life,
        config_hash=_digest(config),
        input_hash=input_hash,
        status="running",
        configuration=config,
        diagnostics={},
    )
    session.add(run)
    session.flush()
    coverage_counts = defaultdict(int)
    for rank, subject_id in enumerate(ordered, 1):
        subject = session.get(Subject, subject_id)
        aliases = session.scalars(
            select(SubjectAlias).where(SubjectAlias.subject_id == subject_id)
        ).all()
        languages = {item["primary_language"] for item in breakdowns[subject_id]}
        coverage = (
            "both"
            if "zh-CN" in languages and any(value != "zh-CN" for value in languages)
            else ("chinese" if languages == {"zh-CN"} else "international")
        )
        coverage_counts[coverage] += 1
        previous_rank = previous_ranks.get(subject_id)
        trend, change = trend_for(previous_rank, rank)
        localized = {
            alias.locale: alias.alias
            for alias in aliases
            if alias.alias_type in {"localized_name", "official_name"}
        }
        details = {
            "canonical_name": subject.title,
            "subject_type": str(subject.subject_type),
            "localized_names": localized,
            "language_coverage": coverage,
            "previous_rank": previous_rank,
            "rank_change": change,
            "trend": trend,
            "sources": breakdowns[subject_id],
        }
        session.add(
            CompositeRankingEntry(
                run_id=run.id,
                subject_id=subject_id,
                rank=rank,
                final_score=scores[subject_id],
                source_count=len(source_ids[subject_id]),
                best_source_rank=best_ranks[subject_id],
                details=details,
            )
        )
    run.status = "success"
    run.diagnostics = {
        "subjects_ranked": len(ordered),
        "cross_source": sum(len(value) > 1 for value in source_ids.values()),
        "coverage": dict(coverage_counts),
        "sources": len(batches),
    }
    session.flush()
    return run, True
