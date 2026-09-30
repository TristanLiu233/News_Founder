"""Native news rankings → ContentItem → Event → Event-centric Trending (PostgreSQL).

Everything runs inside one rolled-back connection; sessions from the session factory join it
through SAVEPOINTs. The ranked-list registry is replaced by synthetic lists (their own Sources)
and all content is published and observed in 2033, far from the live corpus, so live
snapshots, content, and Events never take part.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.adapters.source.base import ContentSourceItem, RankedContentItem
from industry_intelligence.adapters.source.content_registry import (
    ContentSourceDefinition,
    NewsRankingDefinition,
)
from industry_intelligence.adapters.source.news_rankings import (
    ArticleDetails,
    NewsRankingAdapter,
)
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    ContentItem,
    ContentSubjectLink,
    EventContentLink,
    RankList,
    RankObservation,
    Subject,
)
from industry_intelligence.db.models.ingestion_batch import IngestionBatch
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import ContentType, Domain, SubjectType
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.main import create_app
from industry_intelligence.services import intelligence_update as update
from industry_intelligence.services import news_ranking_ingestion as ranking_ingestion
from industry_intelligence.services import source_ingestion
from industry_intelligence.services import trending as trending_service
from industry_intelligence.services.content_ingestion import persist_content
from industry_intelligence.services.event_ingestion import build_events
from industry_intelligence.services.event_merge import merge_events
from industry_intelligence.services.news_ranking_ingestion import (
    ingest_news_ranking,
    persist_news_ranking,
)
from industry_intelligence.services.trending import trending

pytestmark = pytest.mark.integration

T0 = datetime(2033, 6, 1, 9, tzinfo=UTC)
FORBIDDEN = {
    "score",
    "final_score",
    "trend_score",
    "hot_score",
    "popularity_score",
    "weighted_score",
    "combined_rank",
    "average_rank",
    "contribution",
    "weight",
    "freshness",
}
PAYLOAD = {}
LOOKUPS = {}
FETCHES = []


class StaticRanking(NewsRankingAdapter):
    """Returns the entries the test placed in ``PAYLOAD`` for its Source name."""

    source_name = ""
    lookup_delay = 0.0
    lookup_articles = True

    async def fetch(self):
        FETCHES.append(self.source_name)
        self.window = "day:2033-06-01"
        return PAYLOAD[self.source_name]

    async def article_details(self, url):
        return LOOKUPS.get(url)


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


@pytest.fixture
def registry(monkeypatch):
    tag = uuid4().hex[:8]
    PAYLOAD.clear()
    LOOKUPS.clear()
    FETCHES.clear()

    def adapter(name):
        return type(f"Static{abs(hash(name))}", (StaticRanking,), {"source_name": name})

    names = {
        "popular": f"Trend Popular {tag}",
        "hot": f"Trend Hot {tag}",
        "clicks": f"Trend Clicks {tag}",
    }
    lists = {
        "t_popular": NewsRankingDefinition(
            "t_popular",
            names["popular"],
            Domain.GAMING,
            "popular_posts",
            "Popular Posts",
            "popular",
            "https://popular.example.test/",
            adapter(names["popular"]),
            "HTML",
            "en",
        ),
        "t_hot": NewsRankingDefinition(
            "t_hot",
            names["hot"],
            Domain.GAMING,
            "recent_hot",
            "近期热门",
            "hot",
            "https://hot.example.test/",
            adapter(names["hot"]),
            "HTML",
            "en",
        ),
        "t_clicks": NewsRankingDefinition(
            "t_clicks",
            names["clicks"],
            Domain.FINANCE,
            "news_click_day",
            "新闻榜 · 点击量排行",
            "click_count",
            "https://clicks.example.test/",
            adapter(names["clicks"]),
            "API",
            "zh-CN",
        ),
    }
    for module in (trending_service, ranking_ingestion, update):
        monkeypatch.setattr(module, "NEWS_RANKINGS", lists)
    feed = ContentSourceDefinition(
        "t_feed",
        names["popular"],
        Domain.GAMING,
        ContentType.NEWS,
        "https://popular.example.test/feed",
        StaticRanking,
        "RSS",
        "en",
    )
    return {"lists": lists, "feed": feed, "names": names, "tag": tag}


def item(url, title, published=T0, observed=T0, language="en"):
    return ContentSourceItem(None, title, url, published, observed, language)


def ranked(rank, url, title, published=T0, observed=T0, value=None):
    return RankedContentItem(rank, item(url, title, published, observed), value)


def content_id(db, url):
    return db.scalar(select(ContentItem.id).where(ContentItem.url == url))


def keys_of(value):
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in keys_of(child)}
    if isinstance(value, list):
        return {key for child in value for key in keys_of(child)}
    return set()


def test_snapshot_keeps_native_rank_and_reuses_the_feeds_article(db, registry):
    popular = registry["lists"]["t_popular"]
    old = "https://popular.example.test/2033/06/already-in-the-feed"
    persist_content(
        db, registry["feed"], [item(old, "Studio announces sequel", T0 - timedelta(hours=3))]
    )
    stored = db.scalar(select(ContentItem).where(ContentItem.url == old))
    before = (stored.published_at, stored.updated_at, stored.title)
    new = "https://popular.example.test/2033/06/new-popular-post"
    result = persist_news_ranking(
        db,
        popular,
        [
            ranked(1, new, "Publisher reveals release date", published=None),
            ranked(2, old, "Studio announces sequel", published=None),
            ranked(3, "ftp://popular.example.test/x", "Not a web article"),
        ],
        "run:1",
    )
    assert (result["persisted_count"], result["replayed_count"]) == (1, 1)
    assert (result["invalid_count"], result["ranked_count"]) == (1, 2)
    batch = db.scalar(select(IngestionBatch).filter_by(source_key="t_popular"))
    assert batch.result["kind"] == "news_ranking"
    assert batch.result["list_name"] == "Popular Posts"
    assert batch.result["ranking_kind"] == "popular" and batch.result["window"] is None
    assert [(e["rank"], e["content_item_id"]) for e in batch.result["entries"]] == [
        (1, str(content_id(db, new))),
        (2, str(stored.id)),
    ]
    db.refresh(stored)
    # The article stored from the chronological feed is the same row and is not modified.
    assert (stored.published_at, stored.updated_at, stored.title) == before
    fresh = db.scalar(select(ContentItem).where(ContentItem.url == new))
    assert fresh.published_at is None and fresh.content_type == "news"
    rank_list = db.get(RankList, batch.rank_list_id)
    assert (rank_list.name, rank_list.list_type) == ("news:popular_posts", "news")
    assert (
        db.scalar(
            select(func.count()).select_from(RankObservation).filter_by(rank_list_id=rank_list.id)
        )
        == 0
    )


def test_same_run_replays_and_new_runs_append_history(db, registry):
    popular = registry["lists"]["t_popular"]
    a, b = "https://popular.example.test/a", "https://popular.example.test/b"
    first = [ranked(1, a, "Alpha headline story"), ranked(2, b, "Bravo headline story")]
    persist_news_ranking(db, popular, first, "run:1")
    replay = persist_news_ranking(db, popular, first, "run:1")
    assert replay["replayed"] is True and replay["persisted_count"] == 0
    later = T0 + timedelta(hours=1)
    second = [
        ranked(1, b, "Bravo headline story", observed=later),
        ranked(2, a, "Alpha headline story", observed=later),
    ]
    persist_news_ranking(db, popular, second, "run:2")
    batches = db.scalars(
        select(IngestionBatch)
        .filter_by(source_key="t_popular")
        .order_by(IngestionBatch.observed_at)
    ).all()
    assert [batch.batch_key for batch in batches] == ["run:1", "run:2"]
    assert (
        db.scalar(select(func.count()).select_from(ContentItem).where(ContentItem.url.in_([a, b])))
        == 2
    )
    # History is kept; Trending reads the latest snapshot only.
    assert [e["rank"] for e in batches[0].result["entries"]] == [1, 2]
    build_events(db, content_ids=[content_id(db, a), content_id(db, b)])
    gaming = trending(db, "gaming")["domains"][0]
    assert [row["external_rankings"][0]["title"] for row in gaming["items"]] == [
        "Bravo headline story",
        "Alpha headline story",
    ]
    assert gaming["lists"][0]["observed_at"] == later.isoformat()


def test_invalid_snapshots_are_refused(db, registry):
    popular = registry["lists"]["t_popular"]
    a = "https://popular.example.test/dup"
    with pytest.raises(ValueError, match="repeat a rank"):
        persist_news_ranking(db, popular, [ranked(1, a, "One"), ranked(1, a + "2", "Two")], "r")
    with pytest.raises(ValueError, match="observed_at"):
        persist_news_ranking(
            db,
            popular,
            [ranked(1, a, "One"), ranked(2, a + "2", "Two", observed=T0 + timedelta(seconds=1))],
            "r",
        )
    with pytest.raises(ValueError, match="No valid"):
        persist_news_ranking(db, popular, [ranked(1, "mailto:x@example.test", "Bad")], "r")


def _world(db, registry):
    """Gaming: Popular Posts A #1, B #2; 近期热门 B2 #1 (same story as B), C #2 (no Event), D #3."""

    popular, hot = registry["lists"]["t_popular"], registry["lists"]["t_hot"]
    urls = {
        "a": "https://popular.example.test/a-story",
        "b": "https://popular.example.test/b-story",
        "b2": "https://hot.example.test/b-story",
        "c": "https://hot.example.test/c-story",
        "d": "https://hot.example.test/d-story",
    }
    persist_news_ranking(
        db,
        popular,
        [
            ranked(1, urls["a"], "Nebula Forge announces a new strategy game", value="9,001"),
            ranked(2, urls["b"], "Harbor Lights delayed to spring 2034"),
        ],
        "run:1",
    )
    persist_news_ranking(
        db,
        hot,
        [
            ranked(1, urls["b2"], "Harbor Lights delayed to spring 2034"),
            ranked(2, urls["c"], "Quiet Orchard sells one million copies"),
            ranked(3, urls["d"], "Tidal Circuit adds cross-play support"),
        ],
        "run:1",
    )
    ids = {key: content_id(db, url) for key, url in urls.items()}
    game = Subject(
        domain=Domain.GAMING,
        subject_type=SubjectType.GAME,
        title=f"Nebula Forge {registry['tag']}",
        canonical_name=f"nebula forge {registry['tag']}",
    )
    story = Subject(
        domain=Domain.GAMING,
        subject_type=SubjectType.EVENT,
        title=f"Nebula Forge story {registry['tag']}",
        canonical_name=f"nebula forge story {registry['tag']}",
    )
    db.add_all([game, story])
    db.flush()
    for subject, strategy in ((game, "exact_name"), (story, "same_article")):
        db.add(
            ContentSubjectLink(
                content_item_id=ids["a"],
                subject_id=subject.id,
                relation_type="mentions",
                strategy=strategy,
                confidence=1.0,
            )
        )
    db.flush()
    # C is deliberately left without an Event (as between ingestion and the events stage).
    build_events(db, content_ids=[ids["a"], ids["b"], ids["b2"], ids["d"]])
    return ids, game, story


def _event_of(db, content):
    return db.scalar(
        select(EventContentLink.event_id).where(EventContentLink.content_item_id == content)
    )


def test_trending_is_event_centric_with_every_native_rank_kept(db, registry):
    ids, game, story = _world(db, registry)
    assert _event_of(db, ids["b"]) == _event_of(db, ids["b2"])
    result = trending(db, "gaming", view="native")
    gaming = result["domains"][0]
    assert gaming["status"] == "ready"
    assert gaming["ordering"] == "list_catalog_order_then_native_rank"
    events = [row["event"]["id"] for row in gaming["items"]]
    # One card per Event: A (Popular #1), B (Popular #2 + 近期热门 #1), D (近期热门 #3).
    assert events == [
        str(_event_of(db, ids["a"])),
        str(_event_of(db, ids["b"])),
        str(_event_of(db, ids["d"])),
    ]
    assert gaming["total"] == 3
    a_card, b_card, d_card = gaming["items"]
    assert a_card["event"]["canonical_title"] == "Nebula Forge announces a new strategy game"
    assert [(r["source"], r["list_name"], r["rank"]) for r in b_card["external_rankings"]] == [
        (registry["names"]["popular"], "Popular Posts", 2),
        (registry["names"]["hot"], "近期热门", 1),
    ]
    assert a_card["external_rankings"][0]["native_value"] == "9,001"
    assert d_card["external_rankings"][0]["rank"] == 3
    assert a_card["external_rankings"][0]["url"] == "https://popular.example.test/a-story"
    # Topics are context: the game, not the ranked-story Subject (which is the listed story).
    assert [topic["id"] for topic in a_card["topics"]] == [str(game.id)]
    assert {s["id"] for s in a_card["event"]["subjects"]} == {str(game.id), str(story.id)}
    lists = {row["source_key"]: row for row in gaming["lists"]}
    assert lists["t_popular"]["entry_count"] == 2 and lists["t_popular"]["pending_event_count"] == 0
    assert lists["t_hot"]["entry_count"] == 3 and lists["t_hot"]["pending_event_count"] == 1
    # No Event was made for Trending: the unassigned article stays without one.
    assert _event_of(db, ids["c"]) is None
    assert not keys_of(result) & FORBIDDEN
    assert "rank" not in a_card and "rank" not in a_card["event"]


def test_aggregate_view_consolidates_one_canonical_event_and_puts_corroboration_first(db, registry):
    ids, _, _ = _world(db, registry)
    gaming = trending(db, "gaming")["domains"][0]
    assert gaming["view"] == "aggregate"
    assert gaming["ordering"] == "best_native_rank_plus_publisher_bonus"
    # Case G: B's two articles (Popular #2, 近期热门 #1) are one canonical Event, so one card
    # with both ranks; listed by two publishers, it comes before A (Popular #1 only), then D (#3).
    assert [row["event"]["id"] for row in gaming["items"]] == [
        str(_event_of(db, ids["b"])),
        str(_event_of(db, ids["a"])),
        str(_event_of(db, ids["d"])),
    ]
    b_card = gaming["items"][0]
    assert [r["rank"] for r in b_card["external_rankings"]] == [2, 1]
    assert (b_card["best_native_rank"], b_card["native_publisher_count"]) == (1, 2)
    assert b_card["latest_seen_at"] == T0.isoformat()
    assert gaming["eligibility"] == {
        "rule": "latest_report_within_hours_of_list_observation",
        "max_report_age_hours": 48,
        "eligible_count": 3,
        "excluded_count": 0,
    }
    # The internal ordering value is never part of the response; only the debug CLI sees it.
    assert "ordering_inputs" not in b_card
    explained = trending_service.domain_trending(db, "gaming", summaries=False, explain=True)
    assert explained["items"][0]["ordering_inputs"] == {
        "best_native_rank": 1,
        "native_publisher_count": 2,
        "rank_signal": 1.0,
        "publisher_bonus": 0.1,
        "ordering_value": 1.1,
        "latest_report_at": T0.isoformat(),
    }
    text = trending_service.render_order([explained])
    assert "value 1.1 (signal 1.0 + bonus 0.1)" in text and "3 eligible" in text
    assert not keys_of(gaming) & (FORBIDDEN | {"ordering_value", "rank_signal"})


def test_similar_articles_of_different_events_stay_separate_cards(db, registry):
    popular, hot = registry["lists"]["t_popular"], registry["lists"]["t_hot"]
    base_p, base_h = popular.url.rstrip("/"), hot.url.rstrip("/")
    persist_news_ranking(
        db, popular, [ranked(1, f"{base_p}/two", "Harbor Lights sells 2 million copies")], "run:1"
    )
    persist_news_ranking(
        db, hot, [ranked(1, f"{base_h}/three", "Harbor Lights sells 3 million copies")], "run:1"
    )
    ids = [content_id(db, f"{base_p}/two"), content_id(db, f"{base_h}/three")]
    build_events(db, content_ids=ids)
    assert _event_of(db, ids[0]) != _event_of(db, ids[1])
    # Case H: Trending never groups by itself; two canonical Events are two cards, no bonus.
    cards = trending(db, "gaming")["domains"][0]["items"]
    assert len(cards) == 2
    assert [card["native_publisher_count"] for card in cards] == [1, 1]


def test_domain_without_a_ranked_snapshot_is_unavailable(db, registry):
    finance = trending(db, "finance")["domains"][0]
    assert finance == {
        "domain": "finance",
        "status": "unavailable",
        "view": "aggregate",
        "ordering": "best_native_rank_plus_publisher_bonus",
        "lists": [],
        "eligibility": {
            "rule": "latest_report_within_hours_of_list_observation",
            "max_report_age_hours": 48,
            "eligible_count": 0,
            "excluded_count": 0,
        },
        "total": 0,
        "items": [],
    }
    native = trending(db, "finance", view="native")["domains"][0]
    assert native["view"] == "native" and "eligibility" not in native


def test_reviewed_merge_resolves_to_one_canonical_card(db, registry):
    ids, _, _ = _world(db, registry)
    canonical, duplicate = _event_of(db, ids["b"]), _event_of(db, ids["d"])
    merge_events(db, canonical, duplicate, "same delay announcement (test)")
    gaming = trending(db, "gaming")["domains"][0]
    cards = {row["event"]["id"]: row for row in gaming["items"]}
    assert str(duplicate) not in cards
    ranks = [(r["list_key"], r["rank"]) for r in cards[str(canonical)]["external_rankings"]]
    assert ranks == [("popular_posts", 2), ("recent_hot", 1), ("recent_hot", 3)]
    assert gaming["total"] == 2


def test_trending_api_is_event_centric(db, registry):
    _world(db, registry)
    app = create_app(get_settings())
    app.dependency_overrides[get_db_session] = lambda: db
    with TestClient(app) as client:
        body = client.get("/api/v1/trending", params={"domain": "gaming", "limit": 1}).json()
        everything = client.get("/api/v1/trending").json()
        assert client.get("/api/v1/trending", params={"domain": "sports"}).status_code == 422
    gaming = body["domains"][0]
    assert gaming["total"] == 3 and len(gaming["items"]) == 1
    card = gaming["items"][0]
    assert set(card) == {
        "event",
        "external_rankings",
        "topics",
        "summary",
        # Trending-2: descriptive multi-list evidence (never a score).
        "native_list_count",
        "native_publisher_count",
        "native_publishers",
        # Trending-3: descriptive evidence behind the aggregate order.
        "best_native_rank",
        "latest_seen_at",
    }
    # Product-1: the listed articles carry no excerpt and no AI exists, so there is no summary.
    assert card["summary"] is None
    assert set(card["external_rankings"][0]) >= {"source", "list_name", "rank", "observed_at"}
    assert [row["domain"] for row in everything["domains"]] == ["computer", "finance", "gaming"]
    assert not keys_of(body) & FORBIDDEN


def test_ingest_looks_up_times_only_for_new_undated_articles_and_replays_runs(
    db, factory, registry, settings
):
    popular = registry["lists"]["t_popular"]
    known = "https://popular.example.test/known"
    persist_content(
        db, registry["feed"], [item(known, "Known feed story", T0 - timedelta(hours=5))]
    )
    new = "https://popular.example.test/new"
    LOOKUPS[new] = ArticleDetails(T0 - timedelta(hours=2))
    LOOKUPS[known] = ArticleDetails(T0 + timedelta(days=9))  # must never be used
    PAYLOAD[popular.name] = [
        ranked(1, new, "Brand new popular post", published=None),
        ranked(2, known, "Known feed story", published=None),
    ]
    result = asyncio.run(ingest_news_ranking("t_popular", settings, factory, "update:r1"))
    assert result["replayed"] is False and result["window"] == "day:2033-06-01"
    batch = db.scalar(select(IngestionBatch).filter_by(source_key="t_popular"))
    assert batch.result["article_lookups"] == {"lookups": 1, "dates_found": 1, "ids_found": 0}
    assert db.scalar(
        select(ContentItem.published_at).where(ContentItem.url == new)
    ) == T0 - timedelta(hours=2)
    assert db.scalar(
        select(ContentItem.published_at).where(ContentItem.url == known)
    ) == T0 - timedelta(hours=5)
    again = asyncio.run(ingest_news_ranking("t_popular", settings, factory, "update:r1"))
    assert again["replayed"] is True and FETCHES == [popular.name]


def test_content_stage_records_a_snapshot_per_run(db, factory, registry, settings):
    popular = registry["lists"]["t_popular"]
    PAYLOAD[popular.name] = [ranked(1, "https://popular.example.test/s", "Stage story one")]
    attempts = []

    def record(kind, key, started, finished, result=None, detail=None):
        attempts.append((kind, key, result or detail))

    stage = asyncio.run(
        update.content_stage(settings, factory, ["t_popular"], record=record, run_id="R1")
    )
    again = asyncio.run(
        update.content_stage(settings, factory, ["t_popular"], record=record, run_id="R1")
    )
    assert stage.status == "success" and again.status == "success"
    assert [(kind, key) for kind, key, _ in attempts] == [
        ("content", "t_popular"),
        ("content", "t_popular"),
    ]
    assert attempts[0][2]["replayed"] is False and attempts[1][2]["replayed"] is True
    batch = db.scalar(select(IngestionBatch).filter_by(source_key="t_popular"))
    assert batch.batch_key == "update:R1"


# --- Trending-1.1 hardening ---------------------------------------------------------------------


@pytest.fixture
def hn_http(monkeypatch):
    """Hacker News through the real ``HTTPClient`` and a counting ``MockTransport``."""

    tag = uuid4().hex[:8]
    stories = {
        990000001: f"Synthetic HN story alpha {tag}",
        990000002: f"Synthetic HN story bravo {tag}",
    }
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/topstories.json"):
            return httpx.Response(200, json=list(stories))
        story = int(request.url.path.rsplit("/", 1)[1].split(".")[0])
        return httpx.Response(
            200,
            json={
                "id": story,
                "title": stories[story],
                "url": f"https://hn-test.example.test/{tag}/{story}",
                "score": 100 + story % 10,
                "by": "tester",
                "descendants": 3,
                "time": int(T0.timestamp()),
            },
        )

    def client(settings, **kwargs):
        return HTTPClient(settings, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(source_ingestion, "HTTPClient", client)
    monkeypatch.setattr(ranking_ingestion, "HTTPClient", client)
    return {"requests": requests, "stories": stories}


def test_hn_top_stories_are_requested_once_per_run_for_both_consumers(
    db, factory, settings, hn_http
):
    ranking = asyncio.run(update.ranking_stage(settings, factory, ["hacker_news"], "HN1"))
    content = asyncio.run(
        update.content_stage(settings, factory, ["hacker_news_top"], run_id="HN1")
    )
    assert ranking.status == "success" and content.status == "success"
    requests = hn_http["requests"]
    # One list request and one request per story for the whole run.
    assert sum(path.endswith("/topstories.json") for path in requests) == 1
    assert sorted(path.rsplit("/", 1)[1] for path in requests if "/item/" in path) == [
        "990000001.json",
        "990000002.json",
    ]
    ranking_batch = db.scalar(
        select(IngestionBatch).filter_by(source_key="hacker_news", batch_key="update:HN1")
    )
    news_batch = db.scalar(
        select(IngestionBatch).filter_by(source_key="hacker_news_top", batch_key="update:HN1")
    )
    # The Subject ranking still stores its observations; the article list reuses them exactly.
    assert (
        db.scalar(
            select(func.count())
            .select_from(RankObservation)
            .where(
                RankObservation.rank_list_id == ranking_batch.rank_list_id,
                RankObservation.observed_at == ranking_batch.observed_at,
            )
        )
        == 2
    )
    assert news_batch.result["reused_ranking_batch_id"] == str(ranking_batch.id)
    assert news_batch.observed_at == ranking_batch.observed_at
    assert [e["rank"] for e in news_batch.result["entries"]] == [1, 2]
    items = {
        row.external_id: row
        for row in db.scalars(
            select(ContentItem).where(ContentItem.external_id.in_(["990000001", "990000002"]))
        )
    }
    assert items["990000001"].published_at == T0
    assert items["990000001"].title == hn_http["stories"][990000001]
    build_events(db, content_ids=[row.id for row in items.values()])
    computer = trending(db, "computer")["domains"][0]
    shown = [
        (card["event"]["canonical_title"], card["external_rankings"][0]["rank"])
        for card in computer["items"][:2]
    ]
    assert shown == [
        (hn_http["stories"][990000001], 1),
        (hn_http["stories"][990000002], 2),
    ]


def test_hn_article_list_fetches_itself_only_without_a_same_run_snapshot(
    db, factory, settings, hn_http
):
    stage = asyncio.run(update.content_stage(settings, factory, ["hacker_news_top"], run_id="HN2"))
    assert stage.status == "success"
    assert sum(path.endswith("/topstories.json") for path in hn_http["requests"]) == 1
    batch = db.scalar(
        select(IngestionBatch).filter_by(source_key="hacker_news_top", batch_key="update:HN2")
    )
    assert batch.result["reused_ranking_batch_id"] is None
    assert len(batch.result["entries"]) == 2


def test_edited_url_resolves_to_the_feed_row_by_the_publishers_stable_id(
    db, factory, registry, settings
):
    popular = registry["lists"]["t_popular"]
    old = "https://popular.example.test/2033/06/harbor-lights-sequel-announced"
    edited = "https://popular.example.test/2033/06/harbor-lights-sequel-officially-announced"
    other = "https://popular.example.test/2033/06/harbor-lights-sequel-announced-for-2035"
    feed_row = ContentSourceItem(
        "https://popular.example.test/?p=77",
        "Harbor Lights sequel announced",
        old,
        T0 - timedelta(hours=3),
        T0 - timedelta(hours=3),
        "en",
    )
    persist_content(db, registry["feed"], [feed_row])
    stored = db.scalar(select(ContentItem).where(ContentItem.url == old))
    before = (stored.url, stored.title, stored.updated_at)
    LOOKUPS[edited] = ArticleDetails(T0 - timedelta(hours=3), "https://popular.example.test/?p=77")
    # Similar title, different stable ID: a different article that must stay separate.
    LOOKUPS[other] = ArticleDetails(T0 - timedelta(hours=2), "https://popular.example.test/?p=78")
    PAYLOAD[popular.name] = [
        ranked(1, edited, "Harbor Lights sequel officially announced", published=None),
        ranked(2, other, "Harbor Lights sequel announced for 2035", published=None),
    ]
    result = asyncio.run(ingest_news_ranking("t_popular", settings, factory, "update:id1"))
    assert (result["persisted_count"], result["replayed_count"]) == (1, 1)
    batch = db.scalar(select(IngestionBatch).filter_by(source_key="t_popular"))
    assert batch.result["article_lookups"] == {"lookups": 2, "dates_found": 2, "ids_found": 2}
    first, second = batch.result["entries"]
    assert first["content_item_id"] == str(stored.id)
    assert db.scalar(select(ContentItem.url).where(ContentItem.url == edited)) is None
    db.refresh(stored)
    assert (stored.url, stored.title, stored.updated_at) == before
    separate = db.scalar(select(ContentItem).where(ContentItem.url == other))
    assert second["content_item_id"] == str(separate.id)
    assert separate.external_id == "https://popular.example.test/?p=78"
    build_events(db, content_ids=[stored.id, separate.id])
    assert _event_of(db, stored.id) != _event_of(db, separate.id)
    gaming = trending(db, "gaming")["domains"][0]
    assert [card["event"]["id"] for card in gaming["items"]] == [
        str(_event_of(db, stored.id)),
        str(_event_of(db, separate.id)),
    ]


def test_topics_come_from_the_existing_linker_and_are_never_invented(db, registry):
    popular = registry["lists"]["t_popular"]
    game = Subject(
        domain=Domain.GAMING,
        subject_type=SubjectType.GAME,
        title=f"Quasar Drift {registry['tag']}",
        canonical_name=f"quasar drift {registry['tag']}",
    )
    db.add(game)
    db.flush()
    named = "https://popular.example.test/quasar-drift-date"
    unnamed = "https://popular.example.test/unannounced-project"
    result = persist_news_ranking(
        db,
        popular,
        [
            ranked(1, named, f"Quasar Drift {registry['tag']} gets a March release date"),
            ranked(2, unnamed, "Studio teases an unannounced Quasar project"),
        ],
        "run:topics",
    )
    assert result["link_count"] == 1
    ids = {url: content_id(db, url) for url in (named, unnamed)}
    link = db.scalar(
        select(ContentSubjectLink).where(ContentSubjectLink.content_item_id == ids[named])
    )
    assert (link.subject_id, link.strategy) == (game.id, "exact_name")
    assert (
        db.scalar(
            select(func.count())
            .select_from(ContentSubjectLink)
            .where(ContentSubjectLink.content_item_id == ids[unnamed])
        )
        == 0
    )
    build_events(db, content_ids=list(ids.values()))
    cards = trending(db, "gaming")["domains"][0]["items"]
    assert [[t["id"] for t in card["topics"]] for card in cards] == [[str(game.id)], []]
    assert cards[0]["topics"][0]["strategy"] == "content_subject_link"


# --- Trending-2: same-list movement, multi-list evidence, Home selection -----------------------


def _snapshot(db, definition, rows, run, observed, window=None):
    """``rows``: [(rank, key, published)] stored as one snapshot of ``definition``. The same key
    on two lists is the same story (identical headline, so one Event)."""

    base = definition.url.rstrip("/")
    persist_news_ranking(
        db,
        definition,
        [
            ranked(rank, f"{base}/{key}", f"Harbor bulletin story {key}", published, observed)
            for rank, key, published in rows
        ],
        run,
        window=window,
    )
    return {(definition.key, key): content_id(db, f"{base}/{key}") for _, key, _ in rows}


def _rankings_by_url(domain_result, source_key="t_popular"):
    """External rankings of one list, keyed by the article's URL tail."""

    return {
        ranking["url"].rsplit("/", 1)[1]: ranking
        for card in domain_result["items"]
        for ranking in card["external_rankings"]
        if ranking["source_key"] == source_key
    }


def test_rank_movement_compares_only_the_same_list(db, registry):
    popular, hot = registry["lists"]["t_popular"], registry["lists"]["t_hot"]
    t1 = T0 + timedelta(hours=1)
    first = [(rank, f"p{rank}", T0) for rank in range(1, 9)]
    ids = _snapshot(db, popular, first, "run:1", T0)
    second = [
        (1, "p1", T0),
        (2, "p3", T0),
        (3, "p8", T0),
        (4, "p4", T0),
        (5, "p5", T0),
        (6, "new", t1),
        (7, "p2", T0),
        (8, "p6", T0),
    ]
    ids |= _snapshot(db, popular, second, "run:2", t1)
    # The other list has one snapshot only: its ranks are never compared with Popular Posts.
    ids |= _snapshot(db, hot, [(1, "p8", T0), (2, "h2", T0)], "run:2", t1)
    build_events(db, content_ids=list(ids.values()))
    gaming = trending(db, "gaming", limit=None, view="native")["domains"][0]
    lists = {row["source_key"]: row for row in gaming["lists"]}
    assert lists["t_popular"]["movement_comparable"] is True
    assert lists["t_popular"]["previous_observed_at"] == T0.isoformat()
    assert lists["t_hot"]["movement_comparable"] is False
    assert lists["t_hot"]["movement_unavailable_reason"] == "no_previous_snapshot"
    rows = _rankings_by_url(gaming)
    moves = {
        key: (row["previous_rank"], row["rank_delta"], row["movement"]) for key, row in rows.items()
    }
    assert moves["p8"] == (8, 5, "up")
    assert moves["p2"] == (2, -5, "down")
    assert moves["p4"] == (4, 0, "stable")
    assert moves["new"] == (None, None, "new")
    assert rows["new"]["reentered"] is False and rows["new"]["first_ranked_at"] == t1.isoformat()
    assert rows["p1"]["first_ranked_at"] == T0.isoformat()
    hot_rows = _rankings_by_url(gaming, "t_hot")
    assert {key: row["movement"] for key, row in hot_rows.items()} == {"p8": None, "h2": None}
    # p8 is one Event on two lists: each external rank keeps its own movement.
    card = next(c for c in gaming["items"] if len(c["external_rankings"]) == 2)
    assert [(r["source_key"], r["rank"], r["movement"]) for r in card["external_rankings"]] == [
        ("t_popular", 3, "up"),
        ("t_hot", 1, None),
    ]
    assert card["native_list_count"] == 2 and card["native_publisher_count"] == 2
    assert card["native_publishers"] == [registry["names"]["popular"], registry["names"]["hot"]]
    # Native order is unchanged by movement (Popular Posts in its own order first).
    assert [c["external_rankings"][0]["rank"] for c in gaming["items"][:8]] == list(range(1, 9))
    assert not keys_of(gaming) & FORBIDDEN


def test_a_returning_article_is_marked_reentered_and_windows_are_not_compared(db, registry):
    popular, clicks = registry["lists"]["t_popular"], registry["lists"]["t_clicks"]
    t1, t2 = T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    ids = _snapshot(db, popular, [(1, "r", T0), (2, "s", T0)], "run:1", T0)
    ids |= _snapshot(db, popular, [(1, "s", T0)], "run:2", t1)
    ids |= _snapshot(db, popular, [(1, "s", T0), (2, "r", T0)], "run:3", t2)
    build_events(db, content_ids=list(ids.values()))
    rows = _rankings_by_url(trending(db, "gaming")["domains"][0])
    assert (rows["r"]["movement"], rows["r"]["reentered"]) == ("new", True)
    assert rows["r"]["first_ranked_at"] == T0.isoformat()
    assert (rows["s"]["movement"], rows["s"]["rank_delta"]) == ("stable", 0)
    # A daily list of another date is a different list: no movement across windows.
    ids = _snapshot(db, clicks, [(1, "k", T0)], "run:1", T0, "day:2033-05-31")
    ids |= _snapshot(db, clicks, [(1, "k", T0)], "run:2", t1, "day:2033-06-01")
    build_events(db, content_ids=list(ids.values()))
    finance = trending(db, "finance")["domains"][0]
    assert finance["lists"][0]["window"] == "day:2033-06-01"
    assert finance["lists"][0]["movement_unavailable_reason"] == "window_changed"
    assert finance["items"][0]["external_rankings"][0]["movement"] is None


def _home_world(db, registry):
    """Popular Posts: A #1, X #2, B #3 · 近期热门: X #1, OLD #2, C #3, D #4 (OLD is 3 days old)."""

    popular, hot = registry["lists"]["t_popular"], registry["lists"]["t_hot"]
    old = T0 - timedelta(days=3)
    base_p, base_h = popular.url.rstrip("/"), hot.url.rstrip("/")
    shared = "Harbor Lights delayed to spring 2034"
    persist_news_ranking(
        db,
        popular,
        [
            ranked(1, f"{base_p}/a", "Nebula Forge announces a new strategy game"),
            ranked(2, f"{base_p}/x", shared),
            ranked(3, f"{base_p}/b", "Quiet Orchard sells one million copies"),
        ],
        "run:1",
    )
    persist_news_ranking(
        db,
        hot,
        [
            ranked(1, f"{base_h}/x", shared),
            ranked(2, f"{base_h}/old", "Ancient Relic remaster announced", published=old),
            ranked(3, f"{base_h}/c", "Tidal Circuit adds cross-play support"),
            ranked(4, f"{base_h}/d", "Copper Valley opens a new studio"),
        ],
        "run:1",
    )
    urls = {
        "a": base_p,
        "x": base_p,
        "b": base_p,
        "x2": base_h,
        "old": base_h,
        "c": base_h,
        "d": base_h,
    }
    ids = {key: content_id(db, f"{base}/{key.rstrip('2')}") for key, base in urls.items()}
    build_events(db, content_ids=list(ids.values()))
    assert _event_of(db, ids["x"]) == _event_of(db, ids["x2"])
    return {key: str(_event_of(db, value)) for key, value in ids.items()}


def test_home_and_trending_share_the_aggregate_order_and_skip_old_events(db, registry):
    events = _home_world(db, registry)
    home = trending(db, "gaming", limit=4)["domains"][0]
    assert home["view"] == "aggregate"
    # X (近期热门 #1 + Popular #2, two publishers) first, then A (Popular #1); B and C are both
    # #3 on their lists (a tie resolved by Event ID); D is #4; OLD (三天前) is not eligible.
    shown = [card["event"]["id"] for card in home["items"]]
    assert shown[:2] == [events["x"], events["a"]]
    assert shown[2:] == sorted([events["b"], events["c"]])
    full = trending(db, "gaming", limit=None)["domains"][0]
    assert [card["event"]["id"] for card in full["items"]] == [*shown, events["d"]]
    x_card = home["items"][0]
    assert [(r["source_key"], r["rank"]) for r in x_card["external_rankings"]] == [
        ("t_popular", 2),
        ("t_hot", 1),
    ]
    assert x_card["native_list_count"] == 2
    assert home["eligibility"] == {
        "rule": "latest_report_within_hours_of_list_observation",
        "max_report_age_hours": 48,
        "eligible_count": 5,
        "excluded_count": 1,
    }
    assert home["total"] == 5
    # The native view still shows the old Event at its native rank; eligibility never touches it.
    native = trending(db, "gaming", limit=None, view="native")["domains"][0]
    assert events["old"] in [card["event"]["id"] for card in native["items"]]
    assert native["ordering"] == "list_catalog_order_then_native_rank"
    assert "eligibility" not in native
    assert not keys_of(home) & FORBIDDEN


def test_aggregate_shows_fewer_cards_when_too_few_events_are_recent(db, registry):
    popular = registry["lists"]["t_popular"]
    old = T0 - timedelta(days=5)
    ids = _snapshot(db, popular, [(1, "o1", old), (2, "f1", T0), (3, "o2", old)], "run:1", T0)
    build_events(db, content_ids=list(ids.values()))
    home = trending(db, "gaming", limit=4)["domains"][0]
    assert [card["external_rankings"][0]["rank"] for card in home["items"]] == [2]
    assert home["total"] == 1 and home["eligibility"]["excluded_count"] == 2


def test_trending_api_views_are_validated_and_selection_is_an_alias(db, registry):
    _home_world(db, registry)
    app = create_app(get_settings())
    app.dependency_overrides[get_db_session] = lambda: db
    with TestClient(app) as client:
        get = client.get
        default = get("/api/v1/trending", params={"domain": "gaming", "limit": 4}).json()
        home = get("/api/v1/trending", params={"domain": "gaming", "limit": 4, "selection": "home"})
        native = get("/api/v1/trending", params={"domain": "gaming", "view": "native"}).json()
        legacy = get("/api/v1/trending", params={"domain": "gaming", "selection": "native"}).json()
        assert get("/api/v1/trending", params={"selection": "score"}).status_code == 422
        assert get("/api/v1/trending", params={"view": "score"}).status_code == 422
    assert home.status_code == 200
    body = home.json()["domains"][0]
    assert body == default["domains"][0]
    assert len(body["items"]) == 4 and body["eligibility"]["max_report_age_hours"] == 48
    assert native["domains"][0]["view"] == legacy["domains"][0]["view"] == "native"
    ranking = native["domains"][0]["items"][0]["external_rankings"][0]
    assert {
        "rank",
        "previous_rank",
        "rank_delta",
        "movement",
        "previous_observed_at",
        "first_ranked_at",
        "reentered",
    } <= set(ranking)
    assert not keys_of(body) & FORBIDDEN and not keys_of(native) & FORBIDDEN


def test_trending_query_count_does_not_grow_with_listed_articles(db, registry, connection):
    from sqlalchemy import event as sa_event

    popular = registry["lists"]["t_popular"]
    statements = []

    def count(*_args):
        statements.append(1)

    def measure(view):
        statements.clear()
        sa_event.listen(connection, "before_cursor_execute", count)
        try:
            trending_service.domain_trending(db, "gaming", limit=None, summaries=False, view=view)
        finally:
            sa_event.remove(connection, "before_cursor_execute", count)
        return len(statements)

    ids = _snapshot(db, popular, [(r, f"q{r}", T0) for r in range(1, 4)], "run:1", T0)
    build_events(db, content_ids=list(ids.values()))
    small = {view: measure(view) for view in ("aggregate", "native")}
    t1 = T0 + timedelta(hours=1)
    ids = _snapshot(db, popular, [(r, f"q{r}", T0) for r in range(1, 13)], "run:2", t1)
    build_events(db, content_ids=list(ids.values()))
    # The aggregate order is computed in memory: no query per Event or per listed article.
    assert {view: measure(view) for view in ("aggregate", "native")} == small
    assert small["aggregate"] == small["native"]
