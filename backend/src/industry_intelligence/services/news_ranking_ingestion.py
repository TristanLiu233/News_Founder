"""Append-only snapshots of publishers' own ranked article lists (native news rankings).

One run of a ``NEWS_RANKINGS`` definition:

1. fetch the list (outside any transaction) — entries keep the publisher's own rank;
   — or, for a list that a Subject ranking of the same run already stored
   (``reuse_ranking``, e.g. Hacker News Top Stories), build the entries from that stored
   snapshot with no request at all;
2. for listed articles whose URL is not stored yet and whose row lacks a date or the
   publisher's stable article ID, read both from the article page (``adapter.article_details``),
   serially and bounded by the list length; a failed lookup leaves them unknown and never fails
   the source. A stable ID equal to one the publisher's feed already stored (e.g. the Gematsu
   WordPress GUID) resolves an edited URL to that existing row instead of a duplicate;
3. in one transaction: store the articles as ordinary ``ContentItem``s of the publisher's
   ``Source`` (an article already stored from its chronological feed is the same row and is
   never modified), link new rows deterministically, and append one ``IngestionBatch``
   snapshot whose ``result.entries`` hold ``{rank, content_item_id, native_value}``.

The snapshot is anchored to an internal ``RankList`` (``list_type = news``, one of the values
the ``rank_list_type_valid`` constraint already allows; name ``news:<list_key>``) and marked
``result.kind = news_ranking``. It has no ``RankObservation``s, so Subject rankings, Today,
Search, and the composite never read it. A repeated ``batch_key`` (the update run) is a replay
with no request. Nothing here scores, merges, or re-orders: ranks from different publishers are
never compared.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.source.content_registry import NEWS_RANKINGS
from industry_intelligence.db.models import ContentItem, RankList, RankObservation, Source
from industry_intelligence.db.models.ingestion_batch import IngestionBatch
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.content_ingestion import (
    link_new_content,
    store_content_items,
)
from industry_intelligence.services.ingestion_phase import FETCH, PERSIST, ingestion_phase
from industry_intelligence.services.resolution import normalize_url

LIST_TYPE = "news"
RESULT_KIND = "news_ranking"


def rank_list_name(definition):
    return f"news:{definition.list_key}"


def _replay_result(definition, batch):
    return {
        "source": definition.key,
        "domain": str(definition.domain),
        "content_type": str(definition.content_type),
        "news_ranking": True,
        "replayed": True,
        "batch_id": str(batch.id),
        "fetched_count": None,
        "persisted_count": 0,
        "replayed_count": len(batch.result.get("entries", [])),
        "invalid_count": 0,
        "failed_count": 0,
        "ranked_count": len(batch.result.get("entries", [])),
        "link_count": 0,
        "content_ids": [],
    }


def existing_batch(session, definition, batch_key):
    return session.scalar(
        select(IngestionBatch).filter_by(source_key=definition.key, batch_key=batch_key)
    )


def stored_urls(session, definition, urls):
    """Normalized URLs of ``urls`` already stored for the publisher's Source."""

    source_id = session.scalar(select(Source.id).where(Source.name == definition.name))
    if source_id is None or not urls:
        return set()
    return set(
        session.scalars(
            select(ContentItem.url).where(
                ContentItem.source_id == source_id, ContentItem.url.in_(list(urls))
            )
        )
    )


def reused_ranking_snapshot(session, definition, batch_key):
    """(batch, entries) from the Subject-ranking snapshot this run stored for the same upstream
    list, or None when the definition reuses none or this run stored no such snapshot."""

    if not definition.reuse_ranking:
        return None
    batch = session.scalar(
        select(IngestionBatch).filter_by(source_key=definition.reuse_ranking, batch_key=batch_key)
    )
    if batch is None:
        return None
    observations = session.scalars(
        select(RankObservation)
        .where(
            RankObservation.rank_list_id == batch.rank_list_id,
            RankObservation.observed_at == batch.observed_at,
        )
        .order_by(RankObservation.rank)
    ).all()
    return batch, definition.adapter.entries_from_observations(observations)


def _needs_lookup(adapter, entry, known_urls):
    if not adapter.lookup_articles or normalize_url(entry.item.url) in known_urls:
        return False
    return entry.item.published_at is None or entry.item.external_id is None


async def complete_article_details(adapter, entries, known_urls, sleep=asyncio.sleep):
    """Publication time and stable article ID for new entries that lack either: serial
    requests, each preceded by ``adapter.lookup_delay`` seconds (the publisher's crawl delay).
    Values the list row already carries are never replaced."""

    completed, counts = [], {"lookups": 0, "dates_found": 0, "ids_found": 0}
    for entry in entries:
        if not _needs_lookup(adapter, entry, known_urls):
            completed.append(entry)
            continue
        if adapter.lookup_delay:
            await sleep(adapter.lookup_delay)
        counts["lookups"] += 1
        try:
            details = await adapter.article_details(entry.item.url)
        except Exception:  # an article page failure leaves both unknown
            details = None
        item = entry.item
        if details is not None and item.published_at is None and details.published_at:
            counts["dates_found"] += 1
            item = replace(item, published_at=details.published_at)
        if details is not None and item.external_id is None and details.external_id:
            counts["ids_found"] += 1
            item = replace(item, external_id=details.external_id)
        completed.append(replace(entry, item=item))
    return completed, counts


def persist_news_ranking(
    session,
    definition,
    entries,
    batch_key,
    window=None,
    failed_count=0,
    lookups=None,
    reused_batch_id=None,
):
    """Store the listed articles and append one snapshot; the caller owns the transaction."""

    started = perf_counter()
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": definition.key})
    batch = existing_batch(session, definition, batch_key)
    if batch is not None:
        return _replay_result(definition, batch)
    ranks = [entry.rank for entry in entries]
    if len(ranks) != len(set(ranks)):
        raise ValueError("A ranked list must not repeat a rank")
    timestamps = {entry.item.observed_at for entry in entries}
    if len(timestamps) > 1:
        raise ValueError("A snapshot must share one observed_at")
    observed_at = next(iter(timestamps), datetime.now(UTC))
    stored = store_content_items(session, definition, [entry.item for entry in entries])
    if entries and all(content_id is None for content_id in stored["ids"]):
        # Keep the previous snapshot as the current one rather than storing an empty list.
        raise ValueError("No valid ranked items")
    session.flush()
    session.execute(
        insert(RankList)
        .values(
            source_id=stored["source"].id,
            name=rank_list_name(definition),
            domain=definition.domain,
            description=definition.list_name,
            list_type=LIST_TYPE,
        )
        .on_conflict_do_nothing()
    )
    rank_list = session.scalar(
        select(RankList).filter_by(source_id=stored["source"].id, name=rank_list_name(definition))
    )
    ranked = [
        {
            "rank": entry.rank,
            "content_item_id": str(content_id),
            "native_value": entry.native_value,
        }
        for entry, content_id in zip(entries, stored["ids"], strict=True)
        if content_id is not None
    ]
    counts = {
        "fetched_count": len(entries),
        "persisted_count": len(stored["created_ids"]),
        "replayed_count": stored["replayed"],
        "invalid_count": stored["invalid"],
        "failed_count": failed_count,
        "ranked_count": len(ranked),
    }
    batch = IngestionBatch(
        source_key=definition.key,
        batch_key=batch_key,
        rank_list_id=rank_list.id,
        observed_at=observed_at,
        result={
            "kind": RESULT_KIND,
            "list_key": definition.list_key,
            "list_name": definition.list_name,
            "ranking_kind": definition.ranking_kind,
            "window": window,
            "entries": ranked,
            "article_lookups": lookups or {"lookups": 0, "dates_found": 0, "ids_found": 0},
            "reused_ranking_batch_id": reused_batch_id,
            **counts,
        },
    )
    session.add(batch)
    session.flush()
    link_result, link_error = link_new_content(session, stored["created_ids"])
    return {
        "source": definition.key,
        "domain": str(definition.domain),
        "content_type": str(definition.content_type),
        "news_ranking": True,
        "replayed": False,
        "batch_id": str(batch.id),
        "observed_at": observed_at.isoformat(),
        "window": window,
        "reused_ranking_batch_id": reused_batch_id,
        **counts,
        "skipped_count": stored["replayed"] + stored["invalid"],
        "linked_subject_count": link_result["linked_subjects"],
        "link_count": link_result["links"],
        **({"link_error": link_error} if link_error else {}),
        "content_ids": [str(content_id) for content_id in stored["created_ids"]],
        "duration_ms": round((perf_counter() - started) * 1000),
    }


async def ingest_news_ranking(key, settings, session_factory, batch_key=None):
    definition = NEWS_RANKINGS[key]
    batch_key = batch_key or f"manual:{uuid4()}"
    with session_factory() as session:
        batch = existing_batch(session, definition, batch_key)
        if batch is not None:
            return _replay_result(definition, batch)
        reused = reused_ranking_snapshot(session, definition, batch_key)
    if reused is not None:
        # Same upstream list already stored by this run's Subject ranking: no request at all.
        ranking_batch, entries = reused
        with ingestion_phase(PERSIST), session_factory.begin() as session:
            return persist_news_ranking(
                session, definition, entries, batch_key, reused_batch_id=str(ranking_batch.id)
            )
    with ingestion_phase(FETCH):
        async with HTTPClient(settings) as http:
            adapter = definition.adapter(http, settings.source_fetch_limit)
            entries = await adapter.fetch()
            with session_factory() as session:
                known = stored_urls(
                    session, definition, {normalize_url(entry.item.url) for entry in entries}
                )
            entries, lookups = await complete_article_details(adapter, entries, known)
    with ingestion_phase(PERSIST), session_factory.begin() as session:
        return persist_news_ranking(
            session, definition, entries, batch_key, adapter.window, adapter.failed_count, lookups
        )
