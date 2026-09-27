"""Select one committed snapshot per list, never mix historical observations."""

from datetime import UTC, datetime

from sqlalchemy import select

from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.models import RankObservation, Source, Subject
from industry_intelligence.db.models.ingestion_batch import IngestionBatch
from industry_intelligence.domain.enums import Domain
from industry_intelligence.services.news import list_news


def current_snapshot(session, key):
    batch = session.scalar(
        select(IngestionBatch)
        .where(IngestionBatch.source_key == key)
        .order_by(IngestionBatch.observed_at.desc())
        .limit(1)
    )
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
        count = len(list_news(session, source=definition.name, limit=100))
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
        finance_news.append(
            {
                "source_key": definition.key,
                "source": definition.name,
                "content_type": str(definition.content_type),
                "status": "ready",
                "items": list_news(session, source=definition.name, limit=5),
            }
        )
    return dict(generated_at=datetime.now(UTC), domains=domains, finance_news=finance_news)
