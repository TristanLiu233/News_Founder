"""Source-1 PostgreSQL tests: IT之家 日榜 through the real pipeline.

Real ``HTTPClient`` over a counting ``MockTransport``, real ingestion services, the existing Event
builder and Trending read model. The captured pages are used with their news IDs remapped and
all publication times in 2033, far from the live corpus; everything runs in one rolled-back
connection.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.adapters.source.base import ContentSourceItem
from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.news_rankings import (
    ITHomeDailyContentAdapter,
    ithome_guid,
)
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import ContentItem, EventContentLink
from industry_intelligence.db.models.ingestion_batch import IngestionBatch
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services import intelligence_update as update
from industry_intelligence.services import news_ranking_ingestion as ranking_ingestion
from industry_intelligence.services import source_ingestion
from industry_intelligence.services.content_ingestion import persist_content
from industry_intelligence.services.event_ingestion import build_events
from industry_intelligence.services.trending import trending

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures"
T0 = datetime(2033, 6, 1, 9, tzinfo=UTC)
# Captured 日榜 with its news IDs moved to 9998xxx (no clash with live IT之家 rows).
ITHOME_PAGE = re.sub(
    r"1008(\d{3})", r"9998\1", (FIXTURES / "ithome_daily_live.html").read_text(encoding="utf-8")
)
ITHOME_IDS = re.findall(r'data-news-id="(\d+)"', ITHOME_PAGE)[:12]


@pytest.fixture
def connection():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def db(connection):
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def factory(connection):
    return sessionmaker(bind=connection, join_transaction_mode="create_savepoint")


def _beijing(moment):
    return (moment + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


@pytest.fixture
def http(monkeypatch):
    """IT之家 through the real ``HTTPClient``; article pages state 2033 times."""

    requests = []

    def handler(request):
        url = str(request.url)
        requests.append(url)
        if url == "https://m.ithome.com/rankm/":
            return httpx.Response(200, text=ITHOME_PAGE)
        if url.startswith("https://m.ithome.com/html/"):
            stamp = _beijing(T0 - timedelta(hours=10))
            return httpx.Response(200, text=f'<span class="news-time">{stamp}</span>')
        return httpx.Response(404)

    def client(settings, **kwargs):
        return HTTPClient(settings, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(source_ingestion, "HTTPClient", client)
    monkeypatch.setattr(ranking_ingestion, "HTTPClient", client)
    # The publishers' 1 s spacing between article pages is not needed against a mock.
    monkeypatch.setattr(ITHomeDailyContentAdapter, "lookup_delay", 0.0)
    return requests


def _batch(db, key, run):
    return db.scalar(select(IngestionBatch).filter_by(source_key=key, batch_key=f"update:{run}"))


def test_ithome_daily_reuses_the_run_snapshot_and_shares_rows_with_the_feed(
    db, factory, settings, http
):
    # The feed already stored the #1 article under its GUID (as the RSS adapter does).
    feed_guid = ithome_guid(ITHOME_IDS[0])
    persist_content(
        db,
        CONTENT_SOURCES["ithome_news"],
        [
            ContentSourceItem(
                feed_guid, "Feed headline", feed_guid, T0 - timedelta(hours=2), T0, "zh-CN"
            )
        ],
    )
    feed_row = db.scalar(select(ContentItem).where(ContentItem.url == feed_guid))
    ranking = asyncio.run(update.ranking_stage(settings, factory, ["ithome"], "IT1"))
    content = asyncio.run(update.content_stage(settings, factory, ["ithome_daily"], run_id="IT1"))
    assert ranking.status == "success" and content.status == "success"
    # The 日榜 page once for the whole run; one article page per new dateless row (11, not 12).
    assert http.count("https://m.ithome.com/rankm/") == 1
    articles = [url for url in http if url.startswith("https://m.ithome.com/html/")]
    assert len(articles) == 11 and f"https://m.ithome.com/html/{ITHOME_IDS[0]}.htm" not in articles
    batch = _batch(db, "ithome_daily", "IT1")
    assert batch.result["reused_ranking_batch_id"] == str(_batch(db, "ithome", "IT1").id)
    assert batch.result["article_lookups"] == {"lookups": 11, "dates_found": 11, "ids_found": 0}
    entries = batch.result["entries"]
    assert [entry["rank"] for entry in entries] == list(range(1, 13))
    assert entries[0]["content_item_id"] == str(feed_row.id)
    stored = {
        row.external_id: row
        for row in db.scalars(
            select(ContentItem).where(
                ContentItem.external_id.in_([ithome_guid(news_id) for news_id in ITHOME_IDS])
            )
        )
    }
    # One row per article, all under the feed's GUID; no mobile-URL duplicate.
    assert len(stored) == 12
    assert (
        db.scalar(
            select(func.count())
            .select_from(ContentItem)
            .where(ContentItem.url.like("%m.ithome.com%"))
        )
        == 0
    )
    assert stored[feed_guid].title == "Feed headline"  # the feed row is never modified
    other = stored[ithome_guid(ITHOME_IDS[1])]
    assert other.published_at == T0 - timedelta(hours=10)
    assert other.item_metadata == {"source_article_id": ITHOME_IDS[1]}
    # Replaying the run's content stage requests nothing and writes nothing.
    before = len(http)
    asyncio.run(update.content_stage(settings, factory, ["ithome_daily"], run_id="IT1"))
    assert len(http) == before
    # Existing Event pipeline, then Trending: one card per Event with the publisher's rank.
    build_events(db, content_ids=[row.id for row in stored.values()])
    linked = db.scalar(
        select(func.count())
        .select_from(EventContentLink)
        .where(EventContentLink.content_item_id.in_([row.id for row in stored.values()]))
    )
    assert linked == 12
    computer = trending(db, "computer", limit=None)["domains"][0]
    ithome = [
        ranking
        for card in computer["items"]
        for ranking in card["external_rankings"]
        if ranking["source_key"] == "ithome_daily"
    ]
    assert sorted(ranking["rank"] for ranking in ithome) == list(range(1, 13))
    assert {(ranking["source"], ranking["list_name"]) for ranking in ithome} == {("IT之家", "日榜")}
