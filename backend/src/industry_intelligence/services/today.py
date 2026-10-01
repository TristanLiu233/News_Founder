"""Today: source lists (one committed snapshot per list, never mixed) and the newest Events.

Product-2 adds ``today_events``: per domain, the canonical Events whose latest report was
published within ``TODAY_WINDOW`` before the domain's most recent collection, newest report
first (``events.list_events`` order: latest report time, then Event ID). It is recency only —
no popularity, Trending order, AI, or new grouping — and Events are the existing canonical
ones, so a story reported several times is one row. The window is anchored to the latest
collection (the newest ``ContentItem.observed_at`` of the domain, UTC), not to the request
time or a calendar date, so the result is deterministic between update runs and never depends
on a publisher's time zone.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES, NEWS_RANKINGS
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.models import ContentItem, RankObservation, Source, Subject
from industry_intelligence.db.models.ingestion_batch import IngestionBatch
from industry_intelligence.domain.enums import Domain
from industry_intelligence.services.events import list_events
from industry_intelligence.services.news import list_news

TODAY_WINDOW = timedelta(hours=24)
TODAY_DEFAULT_LIMIT = 10


def current_snapshot_batch(session, key):
    return session.scalar(
        select(IngestionBatch)
        .where(IngestionBatch.source_key == key)
        .order_by(IngestionBatch.observed_at.desc())
        .limit(1)
    )


def current_snapshot(session, key):
    batch = current_snapshot_batch(session, key)
    if batch is None:
        return None, []
    rows = session.execute(
        select(RankObservation, Subject.subject_type)
        .join(Subject)
        .where(
            RankObservation.rank_list_id == batch.rank_list_id,
            RankObservation.observed_at == batch.observed_at,
        )
        .order_by(RankObservation.rank)
    ).all()
    return batch, rows


def sources(session):
    output = []
    for definition in SOURCES.values():
        batch, rows = current_snapshot(session, definition.key)
        source = session.scalar(select(Source).where(Source.name == definition.name))
        output.append(
            dict(
                key=definition.key,
                name=definition.name,
                domain=definition.domain,
                enabled=source.enabled if source else True,
                configured=True,
                latest_observed_at=batch.observed_at if batch else None,
                latest_item_count=len(rows),
                content_type=str(definition.list_type),
                # Search filters canonical subjects by rank-list appearance, so only
                # ranking/market-pulse sources are valid `/search?source=` keys.
                searchable=True,
            )
        )
    for definition in CONTENT_SOURCES.values():
        count = len(
            list_news(
                session,
                source=definition.name,
                limit=100,
                content_type=str(definition.content_type),
            )
        )
        output.append(
            dict(
                key=definition.key,
                name=definition.name,
                domain=definition.domain,
                enabled=True,
                configured=True,
                latest_observed_at=None,
                latest_item_count=count,
                content_type=str(definition.content_type),
                searchable=False,
            )
        )
    for definition in NEWS_RANKINGS.values():
        # A publisher's ranked article list (read by Trending, never by Today or Search).
        batch = current_snapshot_batch(session, definition.key)
        output.append(
            dict(
                key=definition.key,
                name=definition.name,
                domain=definition.domain,
                enabled=True,
                configured=True,
                latest_observed_at=batch.observed_at if batch else None,
                latest_item_count=len(batch.result.get("entries", [])) if batch else 0,
                content_type="news_ranking",
                list_name=definition.list_name,
                searchable=False,
            )
        )
    return output


def today(session):
    domains = []
    for domain in Domain:
        lists = []
        for definition in SOURCES.values():
            if definition.domain != domain:
                continue
            batch, rows = current_snapshot(session, definition.key)
            lists.append(
                dict(
                    source_key=definition.key,
                    source=definition.name,
                    list_name=definition.list_name,
                    observed_at=batch.observed_at if batch else None,
                    status="ready" if batch else "unavailable",
                    partial=bool(
                        batch and (batch.result["failed_count"] or batch.result["skipped_count"])
                    ),
                    metric_label=definition.metric_label,
                    content_type=str(definition.list_type),
                    items=[
                        dict(
                            rank=row.rank,
                            title=row.title,
                            url=row.url,
                            raw_metric=row.raw_metric,
                            subject_type=kind,
                            metadata=row.item_metadata or {},
                        )
                        for row, kind in rows
                    ],
                )
            )
        domains.append(dict(domain=domain, lists=lists))
    finance_news = []
    for definition in CONTENT_SOURCES.values():
        # Finance News Intelligence only; Computer/Gaming news reach the UI through Events.
        if definition.domain != Domain.FINANCE:
            continue
        finance_news.append(
            {
                "source_key": definition.key,
                "source": definition.name,
                "content_type": str(definition.content_type),
                "status": "ready",
                "items": list_news(
                    session,
                    source=definition.name,
                    limit=5,
                    content_type=str(definition.content_type),
                ),
            }
        )
    return dict(generated_at=datetime.now(UTC), domains=domains, finance_news=finance_news)


def today_events(session, domain=None, limit=TODAY_DEFAULT_LIMIT):
    """The newest canonical Events per domain within ``TODAY_WINDOW`` of the latest collection."""

    domains = [domain] if domain else [str(value) for value in Domain]
    output = []
    for value in domains:
        collected_at = session.scalar(
            select(func.max(ContentItem.observed_at)).where(ContentItem.domain == value)
        )
        if collected_at is None:
            output.append(
                {
                    "domain": str(value),
                    "status": "unavailable",
                    "collected_at": None,
                    "window_start": None,
                    "total": 0,
                    "items": [],
                }
            )
            continue
        start = collected_at - TODAY_WINDOW
        page = list_events(session, value, from_=start, limit=limit)
        output.append(
            {
                "domain": str(value),
                "status": "ready",
                "collected_at": collected_at,
                "window_start": start,
                "total": page["total"],
                "items": page["items"],
            }
        )
    return {
        "generated_at": datetime.now(UTC),
        "window_hours": int(TODAY_WINDOW.total_seconds() // 3600),
        "ordering": "latest_report_first",
        "domains": output,
    }
