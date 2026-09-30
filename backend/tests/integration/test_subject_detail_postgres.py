"""Subject Detail read models, verified inside rolled-back PostgreSQL transactions."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from industry_intelligence.adapters.source.content_registry import NEWS_RANKINGS
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    CompositeRankingEntry,
    CompositeRankingRun,
    ContentItem,
    ContentSubjectLink,
    IngestionBatch,
    RankList,
    RankObservation,
    Source,
    Subject,
    SubjectAlias,
)
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.main import create_app
from industry_intelligence.services.resolution import merge_subjects, normalize_subject_name
from industry_intelligence.services.subject_detail import rank_history, subject_detail

pytestmark = pytest.mark.integration

# Later than any committed live run, so synthetic runs become "latest" inside the transaction.
NOW = datetime.now(UTC) + timedelta(hours=1)


@pytest.fixture
def db():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection)
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def client(db):
    app = create_app(get_settings())
    app.dependency_overrides[get_db_session] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


def make_subject(db, title, aliases=()):
    subject = Subject(
        domain=Domain.COMPUTER,
        subject_type=SubjectType.COMPANY,
        canonical_name=str(uuid4()),
        title=title,
    )
    db.add(subject)
    db.flush()
    for value, locale, alias_type in aliases:
        db.add(
            SubjectAlias(
                subject_id=subject.id,
                alias=value,
                normalized_alias=normalize_subject_name(value),
                locale=locale,
                alias_type=alias_type,
                alias_source="synthetic_test",
            )
        )
    db.flush()
    return subject


def make_list(db, name, language):
    source = Source(
        name=f"{name} {uuid4()}",
        domain=Domain.COMPUTER,
        source_type="test",
        base_url="https://example.test",
        primary_language=language,
    )
    db.add(source)
    db.flush()
    rank_list = RankList(source_id=source.id, name=f"{name} list", domain=Domain.COMPUTER)
    db.add(rank_list)
    db.flush()
    return source, rank_list


def observe(db, subject, source, rank_list, rank, observed_at, batch=True):
    db.add(
        RankObservation(
            subject_id=subject.id,
            source_id=source.id,
            rank_list_id=rank_list.id,
            rank=rank,
            title=f"{subject.title} at #{rank}",
            url=f"https://example.test/{subject.id}/{rank}",
            raw_metric=rank * 10,
            observed_at=observed_at,
        )
    )
    if batch:
        snapshot(db, rank_list, observed_at)


def snapshot(db, rank_list, observed_at):
    db.add(
        IngestionBatch(
            source_key=f"test-{rank_list.id}",
            batch_key=str(uuid4()),
            rank_list_id=rank_list.id,
            observed_at=observed_at,
            result={"failed_count": 0, "skipped_count": 0},
        )
    )
    db.flush()


def make_run(db, calculated_at, entries, status="success"):
    run = CompositeRankingRun(
        domain="computer",
        calculated_at=calculated_at,
        rrf_k=60,
        freshness_half_life=24,
        config_hash="test",
        input_hash=str(uuid4()),
        status=status,
        configuration={},
        diagnostics={},
    )
    db.add(run)
    db.flush()
    for rank, (subject, extra) in enumerate(entries, 1):
        db.add(
            CompositeRankingEntry(
                run_id=run.id,
                subject_id=subject.id,
                rank=rank,
                final_score=0.05 / rank,
                source_count=2,
                best_source_rank=1,
                details={
                    "canonical_name": subject.title,
                    "subject_type": "company",
                    "localized_names": {},
                    "language_coverage": "both",
                    "sources": [],
                    **extra,
                },
            )
        )
    db.flush()
    return run


def make_news(db, source, subject, count):
    items = []
    for index in range(count):
        item = ContentItem(
            source_id=source.id,
            domain=Domain.COMPUTER,
            content_type="news",
            identity_key=str(uuid4()),
            title=f"Synthetic related article {index}",
            url=f"https://news.example.test/article-{index}",
            published_at=NOW - timedelta(minutes=index),
            observed_at=NOW,
            language="en",
            item_metadata={},
            content_hash=str(uuid4()),
        )
        db.add(item)
        db.flush()
        db.add(
            ContentSubjectLink(
                content_item_id=item.id,
                subject_id=subject.id,
                strategy="synthetic_test",
                confidence=1.0,
            )
        )
        items.append(item)
    db.flush()
    return items


@pytest.fixture
def world(db):
    ranked = make_subject(
        db,
        "Detail Test Co",
        [
            ("Detail Test Co", "en", "source_name"),
            ("DTC", "en", "localized_name"),
            ("细节测试公司", "zh-CN", "localized_name"),
        ],
    )
    chinese = make_subject(db, "测试游戏", [("Test Game", "en", "localized_name")])
    unranked = make_subject(db, "Formerly Ranked Co")
    never = make_subject(db, "Never Ranked Co")
    english, english_list = make_list(db, "Detail test English", "en")
    chinese_source, chinese_list = make_list(db, "Detail test 中文", "zh-CN")
    old, latest = NOW - timedelta(days=2), NOW - timedelta(hours=1)
    observe(db, ranked, english, english_list, 4, old)
    observe(db, ranked, english, english_list, 2, latest)
    observe(db, ranked, chinese_source, chinese_list, 7, old)
    # The Chinese list has a newer snapshot without this subject.
    snapshot(db, chinese_list, latest)
    first = make_run(db, NOW - timedelta(days=100), [(unranked, {"trend": "new"})])
    second = make_run(
        db,
        NOW - timedelta(minutes=30),
        [(ranked, {"trend": "new"}), (chinese, {"trend": "new"})],
    )
    make_run(
        db,
        NOW,
        [
            (chinese, {"trend": "up", "previous_rank": 2, "rank_change": 1}),
            (ranked, {"trend": "down", "previous_rank": 1, "rank_change": -1}),
        ],
    )
    # A later run that did not succeed must never become the current context.
    make_run(db, NOW + timedelta(minutes=5), [(never, {"trend": "new"})], status="failed")
    return dict(
        ranked=ranked,
        chinese=chinese,
        unranked=unranked,
        never=never,
        english=english,
        english_list=english_list,
        chinese_list=chinese_list,
        runs=(first, second),
    )


def test_subject_detail_identity_aliases_and_current_ranking(db, world):
    detail = subject_detail(db, world["ranked"].id)
    assert detail["subject_id"] == world["ranked"].id
    assert not detail["redirected"]
    assert detail["canonical_name"] == "Detail Test Co"
    assert detail["display_names"] == {"en": "Detail Test Co", "zh-CN": "细节测试公司"}
    assert [a["alias_type"] for a in detail["aliases"]] == [
        "localized_name",
        "localized_name",
        "source_name",
    ]
    assert {a["alias"] for a in detail["aliases"]} == {"Detail Test Co", "DTC", "细节测试公司"}
    current = detail["current_ranking"]
    assert (current["rank"], current["trend"], current["rank_change"]) == (2, "down", -1)
    assert current["previous_rank"] == 1 and current["source_count"] == 2
    assert detail["related_news_count"] == 0
    chinese = subject_detail(db, world["chinese"].id)
    assert chinese["display_names"] == {"en": "Test Game", "zh-CN": "测试游戏"}
    assert chinese["current_ranking"]["rank"] == 1


def test_subject_not_currently_ranked_and_failed_runs_ignored(db, world):
    assert subject_detail(db, world["unranked"].id)["current_ranking"] is None
    never = subject_detail(db, world["never"].id)
    assert never["current_ranking"] is None
    assert never["source_appearances"] == [] and never["latest_observed_at"] is None


def test_source_appearances_latest_counts_and_current_snapshot(db, world):
    detail = subject_detail(db, world["ranked"].id)
    appearances = detail["source_appearances"]
    assert detail["source_appearance_count"] == 2 and detail["observation_count"] == 3
    current, historical = appearances
    assert current["in_current_snapshot"] and not historical["in_current_snapshot"]
    assert current["source"] == world["english"].name
    assert current["rank_list"] == world["english_list"].name
    assert (current["latest_rank"], current["best_rank"], current["appearance_count"]) == (2, 2, 2)
    assert current["url"] == f"https://example.test/{world['ranked'].id}/2"
    assert current["latest_observed_at"] == NOW - timedelta(hours=1)
    assert current["first_seen_at"] == NOW - timedelta(days=2)
    assert historical["latest_rank"] == 7 and historical["primary_language"] == "zh-CN"
    assert detail["latest_observed_at"] == NOW - timedelta(hours=1)
    assert all(item["source_key"] is None for item in appearances)
    assert all(item["metric_label"] is None for item in appearances)


def test_source_appearance_maps_registry_key(db):
    definition = SOURCES["hacker_news"]
    source = db.scalar(select(Source).where(Source.name == definition.name))
    if source is None:
        pytest.skip("Hacker News source row is not present in this database")
    rank_list = db.scalar(
        select(RankList).where(
            RankList.source_id == source.id, RankList.name == definition.list_name
        )
    )
    subject = make_subject(db, "Registry key subject")
    observe(db, subject, source, rank_list, 1, NOW, batch=False)
    [appearance] = subject_detail(db, subject.id)["source_appearances"]
    assert appearance["source_key"] == "hacker_news"
    assert appearance["metric_label"] == definition.metric_label


def test_rank_history_successful_runs_order_limit_and_empty(db, world):
    history = rank_history(db, world["ranked"].id)
    assert history["total"] == 2
    assert [item["rank"] for item in history["items"]] == [2, 1]
    calculated = [item["calculated_at"] for item in history["items"]]
    assert calculated == sorted(calculated, reverse=True)
    limited = rank_history(db, world["ranked"].id, limit=1)
    assert limited["total"] == 2 and len(limited["items"]) == 1
    assert limited["items"][0]["rank"] == 2
    assert rank_history(db, world["never"].id)["items"] == []
    assert rank_history(db, world["never"].id)["total"] == 0
    assert [item["rank"] for item in rank_history(db, world["unranked"].id)["items"]] == [1]


def test_redirected_subject_resolves_to_canonical_detail_and_history(db, world):
    duplicate = make_subject(db, "Detail Test Company", [("细节测试", "zh-CN", "localized_name")])
    make_run(db, NOW - timedelta(days=50), [(duplicate, {"trend": "new"})])
    make_news(db, world["english"], duplicate, 1)
    merge_subjects(db, world["ranked"].id, duplicate.id)
    db.flush()
    detail = subject_detail(db, duplicate.id)
    assert detail["subject_id"] == world["ranked"].id
    assert detail["requested_subject_id"] == duplicate.id and detail["redirected"]
    assert "细节测试" in {a["alias"] for a in detail["aliases"]}
    # Merged content links follow the canonical subject.
    assert detail["related_news_count"] == 1
    history = rank_history(db, duplicate.id)
    assert history["subject_id"] == world["ranked"].id
    assert history["total"] == 3 and history["items"][-1]["rank"] == 1


def test_related_news_empty_synthetic_original_url_and_pagination(db, world, client):
    sid = world["ranked"].id
    assert client.get("/api/v1/news", params={"subject_id": str(sid)}).json()["items"] == []
    items = make_news(db, world["english"], world["ranked"], 4)
    assert subject_detail(db, sid)["related_news_count"] == 4
    first = client.get("/api/v1/news", params={"subject_id": str(sid), "limit": 3}).json()
    second = client.get(
        "/api/v1/news", params={"subject_id": str(sid), "limit": 3, "offset": 3}
    ).json()
    assert [item["url"] for item in first["items"]] == [item.url for item in items[:3]]
    assert [item["title"] for item in second["items"]] == [items[3].title]
    assert first["items"][0]["linked_subjects"][0]["id"] == str(sid)


def test_subject_api_detail_history_and_not_found(db, world, client):
    sid = world["ranked"].id
    detail = client.get(f"/api/v1/subjects/{sid}").json()
    assert detail["subject_id"] == str(sid)
    assert detail["current_ranking"]["rank"] == 2
    history = client.get(f"/api/v1/subjects/{sid}/history", params={"limit": 1}).json()
    assert history["limit"] == 1 and len(history["items"]) == 1
    missing = uuid4()
    assert client.get(f"/api/v1/subjects/{missing}").status_code == 404
    assert client.get(f"/api/v1/subjects/{missing}").json() == {"detail": "Subject not found"}
    assert client.get(f"/api/v1/subjects/{missing}/history").status_code == 404


def test_sources_searchable_keys_are_accepted_by_search(db, client):
    listed = client.get("/api/v1/sources").json()["sources"]
    searchable = [source for source in listed if source["searchable"]]
    assert {source["key"] for source in searchable} == set(SOURCES)
    # Chronological feeds and publishers' ranked article lists are never Search sources.
    assert all(
        source["content_type"] in {"news", "feed", "news_ranking"}
        for source in listed
        if not source["searchable"]
    )
    assert {s["key"] for s in listed if s["content_type"] == "news_ranking"} == set(NEWS_RANKINGS)
    for source in searchable:
        response = client.get("/api/v1/search", params={"q": "zz", "source": source["key"]})
        assert response.status_code == 200, source["key"]
    content_key = next(source["key"] for source in listed if not source["searchable"])
    assert (
        client.get("/api/v1/search", params={"q": "zz", "source": content_key}).status_code == 422
    )
