"""Idempotent, append-only ingestion of external company-ranking snapshots.

Fetching happens outside any transaction; ``persist_company_snapshot`` then writes one
snapshot inside the caller's transaction. A snapshot is never updated: the same published
version (``snapshot_key``) is a replay, and a version whose entries equal its neighbour in
history is recorded as unchanged instead of duplicated.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.db.models import Ranking, RankingEntry, RankingSnapshot
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.company_resolution import (
    normalize_company_name,
    resolve_company,
)
from industry_intelligence.services.ingestion_phase import FETCH, PERSIST, ingestion_phase

logger = logging.getLogger(__name__)


class SourceContactRequired(RuntimeError):
    """The publisher's User-Agent policy requires SOURCE_CONTACT; nothing was requested."""


# One lock for all company rankings: resolution may create companies shared across rankings.
LOCK_KEY = "company_rankings"


def upsert_ranking(session, definition) -> Ranking:
    """Create the ranking definition or refresh its descriptive metadata by stable key."""

    values = dict(
        key=definition.key,
        source_key=definition.source_key,
        domain=definition.domain,
        name=definition.name,
        publisher=definition.publisher,
        methodology=definition.methodology,
        metric_type=definition.metric_type,
        metric_label=definition.metric_label,
        metric_unit=definition.metric_unit,
        frequency=definition.frequency,
        acquisition=definition.acquisition,
        primary_language=definition.primary_language,
        source_url=definition.url,
        is_active=True,
    )
    updates = {key: value for key, value in values.items() if key != "key"}
    session.execute(
        insert(Ranking)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["key"], set_={**updates, "updated_at": datetime.now(UTC)}
        )
    )
    return session.scalar(select(Ranking).where(Ranking.key == definition.key))


def content_hash(entries) -> str:
    payload = [
        [entry.rank, normalize_company_name(entry.name), sorted(entry.identities), entry.raw_value]
        for entry in sorted(entries, key=lambda item: item.rank)
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _valid(entry) -> bool:
    return len(entry.name) <= 255 and (entry.raw_value is None or len(entry.raw_value) <= 255)


def _neighbour_hashes(session, ranking_id, effective_at):
    before = session.scalar(
        select(RankingSnapshot.content_hash)
        .where(
            RankingSnapshot.ranking_id == ranking_id, RankingSnapshot.effective_at <= effective_at
        )
        .order_by(RankingSnapshot.effective_at.desc(), RankingSnapshot.observed_at.desc())
        .limit(1)
    )
    after = session.scalar(
        select(RankingSnapshot.content_hash)
        .where(
            RankingSnapshot.ranking_id == ranking_id, RankingSnapshot.effective_at > effective_at
        )
        .order_by(RankingSnapshot.effective_at, RankingSnapshot.observed_at)
        .limit(1)
    )
    return {value for value in (before, after) if value}


def persist_company_snapshot(session, definition, snapshot, failed_count=0):
    started = perf_counter()
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    ranking = upsert_ranking(session, definition)
    base = dict(
        ranking=definition.key,
        domain=str(definition.domain),
        snapshot_key=snapshot.snapshot_key,
        observed_at=snapshot.observed_at.isoformat(),
        published_at=snapshot.published_at.isoformat() if snapshot.published_at else None,
        source_url=snapshot.source_url,
        fetched_count=len(snapshot.entries),
        source_entry_count=snapshot.source_entry_count,
        failed_count=failed_count,
    )
    existing = session.scalar(
        select(RankingSnapshot).where(
            RankingSnapshot.ranking_id == ranking.id,
            RankingSnapshot.snapshot_key == snapshot.snapshot_key,
        )
    )
    if existing:
        return {**base, "snapshot_id": str(existing.id), "persisted_count": 0, "replayed": True}

    valid = [entry for entry in snapshot.entries if _valid(entry)]
    if not valid:
        raise ValueError("No valid entries; preserving previous snapshots")
    digest = content_hash(valid)
    effective_at = snapshot.published_at or snapshot.observed_at
    if digest in _neighbour_hashes(session, ranking.id, effective_at):
        return {**base, "persisted_count": 0, "unchanged": True}

    strategies = Counter()
    resolved = []
    seen = set()
    skipped = len(snapshot.entries) - len(valid)
    for entry in valid:
        resolution = resolve_company(session, entry.name, entry.identities, definition.key)
        if resolution.company.id in seen:
            # One company appears once per snapshot; a second row is kept out, not merged.
            skipped += 1
            continue
        seen.add(resolution.company.id)
        strategies[resolution.strategy] += 1
        resolved.append((entry, resolution))
    # Written once, complete: snapshots are never updated after insert.
    row = RankingSnapshot(
        ranking_id=ranking.id,
        snapshot_key=snapshot.snapshot_key,
        observed_at=snapshot.observed_at,
        published_at=snapshot.published_at,
        effective_at=effective_at,
        source_url=snapshot.source_url,
        content_hash=digest,
        status="partial" if skipped or failed_count else "complete",
        entry_count=len(resolved),
        source_entry_count=snapshot.source_entry_count,
        snapshot_metadata=snapshot.metadata,
    )
    session.add(row)
    session.flush()
    session.add_all(
        RankingEntry(
            snapshot_id=row.id,
            company_id=resolution.company.id,
            rank=entry.rank,
            raw_name=entry.name,
            raw_value=entry.raw_value,
            metric_value=entry.metric_value,
            resolution_strategy=resolution.strategy,
            entry_metadata={**entry.metadata, "identities": [list(i) for i in entry.identities]},
        )
        for entry, resolution in resolved
    )
    session.flush()
    return {
        **base,
        "snapshot_id": str(row.id),
        "effective_at": effective_at.isoformat(),
        "persisted_count": len(resolved),
        "skipped_count": skipped,
        "status": row.status,
        "resolution": dict(strategies),
        "duration_ms": round((perf_counter() - started) * 1000),
    }


async def ingest_company_ranking(key, settings, session_factory):
    """Ingest the ranking's current published version; history comes from repeated runs."""

    definition = COMPANY_RANKINGS[key]
    if definition.requires_contact and not settings.source_contact:
        raise SourceContactRequired(
            f"{key}: set SOURCE_CONTACT to the deployment maintainer's own URL or email "
            "(placeholders are rejected), as required by the publisher's User-Agent policy"
        )
    started = perf_counter()
    contact = settings.source_contact if definition.requires_contact else None
    with ingestion_phase(FETCH):
        async with HTTPClient(settings, contact=contact) as http:
            adapter = definition.adapter(http, definition)
            snapshot = await adapter.fetch()
    with ingestion_phase(PERSIST), session_factory.begin() as session:
        result = persist_company_snapshot(session, definition, snapshot, adapter.failed_count)
    result["duration_ms"] = round((perf_counter() - started) * 1000)
    logger.info(
        "ranking=%s event=company_ranking_ingested snapshot=%s persisted=%s failed=%s",
        key,
        snapshot.snapshot_key,
        result["persisted_count"],
        result["failed_count"],
    )
    return result


async def ingest_all_company_rankings(settings, session_factory):
    """Ingest every registered ranking; one failure never blocks the others."""

    results = []
    for key in COMPANY_RANKINGS:
        try:
            results.append(await ingest_company_ranking(key, settings, session_factory))
        except Exception as exc:
            logger.warning(
                "ranking=%s event=company_ranking_failed error=%s", key, type(exc).__name__
            )
            results.append({"ranking": key, "error": type(exc).__name__})
    return results
