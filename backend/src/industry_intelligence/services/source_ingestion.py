"""Transactional ingestion, deterministic identities, and append-only history."""

import logging
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.models import RankList, RankObservation, Source
from industry_intelligence.db.models.ingestion_batch import IngestionBatch
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.resolution import resolve_subject

logger = logging.getLogger(__name__)


def resolve(session, model, values, keys):
    session.execute(insert(model).values(**values).on_conflict_do_nothing())
    return session.scalar(select(model).filter_by(**keys))


def persist(session, definition, items, batch_key, failed_count=0):
    started = perf_counter()
    # Serialize each source's commits; database constraints also enforce identities.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": definition.key})
    existing = session.scalar(
        select(IngestionBatch).filter_by(source_key=definition.key, batch_key=batch_key)
    )
    if existing:
        return {
            **existing.result,
            "persisted_count": 0,
            "skipped_count": existing.result["persisted_count"],
            "replayed": True,
        }
    timestamps = {item.observed_at for item in items}
    if len(timestamps) > 1:
        raise ValueError("A snapshot must share one observed_at")
    observed_at = next(iter(timestamps), datetime.now(UTC))
    source = resolve(
        session,
        Source,
        dict(
            name=definition.name,
            domain=definition.domain,
            source_type=definition.acquisition,
            base_url=definition.url,
            primary_language=definition.primary_language,
        ),
        {"name": definition.name},
    )
    rank_list = resolve(
        session,
        RankList,
        dict(
            source_id=source.id,
            name=definition.list_name,
            domain=definition.domain,
            list_type=str(definition.list_type),
        ),
        {"source_id": source.id, "name": definition.list_name},
    )
    persisted = skipped = 0
    seen = set()
    ranks = set()
    for item in items:
        title = " ".join(item.title.split())
        url = item.url
        if (
            not title
            or len(title) > 500
            or (url and (len(url) > 2048 or urlsplit(url).scheme not in {"http", "https"}))
        ):
            skipped += 1
            continue
        if item.rank in ranks:
            skipped += 1
            continue
        resolution = resolve_subject(session, definition, source, item)
        subject = resolution.subject
        if subject.id in seen:
            skipped += 1
            continue
        seen.add(subject.id)
        ranks.add(item.rank)
        session.add(
            RankObservation(
                subject_id=subject.id,
                source_id=source.id,
                rank_list_id=rank_list.id,
                rank=item.rank,
                raw_metric=item.raw_metric,
                observed_at=observed_at,
                title=title,
                url=url,
                item_metadata=item.metadata,
            )
        )
        persisted += 1
    if items and not persisted:
        raise ValueError("No valid entries; preserving previous snapshot")
    result = dict(
        source=definition.key,
        rank_list=definition.list_name,
        observed_at=observed_at.isoformat(),
        batch_id=batch_key,
        fetched_count=len(items),
        persisted_count=persisted,
        skipped_count=skipped,
        failed_count=failed_count,
        duration_ms=round((perf_counter() - started) * 1000),
    )
    session.add(
        IngestionBatch(
            source_key=definition.key,
            batch_key=batch_key,
            rank_list_id=rank_list.id,
            observed_at=observed_at,
            result=result,
        )
    )
    session.flush()
    from industry_intelligence.services.search_index import refresh_subject

    for subject_id in seen:
        refresh_subject(session, subject_id)
    return result


async def ingest(key, settings, session_factory, batch_key=None):
    definition = SOURCES[key]
    batch_key = batch_key or str(uuid4())
    started = perf_counter()
    with session_factory() as session:
        existing = session.scalar(
            select(IngestionBatch).filter_by(source_key=key, batch_key=batch_key)
        )
        if existing:
            return {
                **existing.result,
                "persisted_count": 0,
                "skipped_count": existing.result["persisted_count"],
                "replayed": True,
            }
    async with HTTPClient(settings) as http:
        adapter = definition.adapter(http, settings.source_fetch_limit)
        items = await adapter.fetch()
    with session_factory.begin() as session:
        result = persist(session, definition, items, batch_key, adapter.failed_count)
    result["duration_ms"] = round((perf_counter() - started) * 1000)
    logger.info(
        "source=%s event=ingestion_completed items_fetched=%s items_persisted=%s "
        "items_failed=%s duration_ms=%s",
        key,
        result["fetched_count"],
        result["persisted_count"],
        result["failed_count"],
        result["duration_ms"],
    )
    return result


async def ingest_all_enabled_sources(settings, session_factory):
    results = []
    for key in SOURCES:
        try:
            results.append(await ingest(key, settings, session_factory))
        except Exception as exc:
            logger.warning("source=%s event=ingestion_failed error=%s", key, type(exc).__name__)
            results.append({"source": key, "error": type(exc).__name__})
    return results
