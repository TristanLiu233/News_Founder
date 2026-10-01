"""Deterministic search fixtures live only inside rolled-back PostgreSQL transactions."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from industry_intelligence.config import get_settings
from industry_intelligence.db.models import RankList, RankObservation, Source, Subject, SubjectAlias
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.main import create_app
from industry_intelligence.services.resolution import (
    locale_for,
    merge_subjects,
    normalize_subject_name,
    seeded_canonical,
)
from industry_intelligence.services.search import search
from industry_intelligence.services.search_index import embed_documents, refresh_subject

pytestmark = pytest.mark.integration


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
def corpus(db):
    # Isolate retrieval from the user's live index, without deleting persisted data.
    db.execute(text("DELETE FROM subject_embeddings"))
    db.execute(text("DELETE FROM search_documents"))
    subjects = {}
    for title, domain, kind in [
        ("NVIDIA", Domain.COMPUTER, SubjectType.COMPANY),
        ("Black Myth: Wukong", Domain.GAMING, SubjectType.GAME),
        ("Bitcoin", Domain.FINANCE, SubjectType.TECHNOLOGY),
        ("C++", Domain.COMPUTER, SubjectType.TECHNOLOGY),
        ("C#", Domain.COMPUTER, SubjectType.TECHNOLOGY),
        (".NET", Domain.COMPUTER, SubjectType.TECHNOLOGY),
        ("GPT-5", Domain.COMPUTER, SubjectType.MODEL),
        ("DOOM", Domain.GAMING, SubjectType.GAME),
        ("DOOM II", Domain.GAMING, SubjectType.GAME),
    ]:
        subject = Subject(
            id=uuid5(NAMESPACE_URL, "industry-intelligence:phase3-test:" + title),
            domain=domain,
            subject_type=kind,
            canonical_name=str(uuid4()),
            title=title,
        )
        db.add(subject)
        db.flush()
        for alias in seeded_canonical(title)[1]:
            db.add(
                SubjectAlias(
                    subject_id=subject.id,
                    alias=alias,
                    normalized_alias=normalize_subject_name(alias),
                    locale=locale_for(alias),
                    alias_type="localized_name",
                    alias_source="synthetic_test",
                )
            )
        subjects[title] = subject
    for name, language in [("Search test English", "en"), ("Search test 中文", "zh-CN")]:
        source = Source(
            name=name,
            domain=Domain.COMPUTER,
            source_type="test",
            base_url="https://example.test",
            primary_language=language,
        )
        db.add(source)
        db.flush()
        rank_list = RankList(source_id=source.id, name="Test ranking", domain=Domain.COMPUTER)
        db.add(rank_list)
        db.flush()
        db.add(
            RankObservation(
                subject_id=subjects["NVIDIA"].id,
                source_id=source.id,
                rank_list_id=rank_list.id,
                rank=1,
                title="Archived quantum accelerator benchmark",
                observed_at=datetime.now(UTC) - timedelta(days=100),
            )
        )
    for subject in subjects.values():
        refresh_subject(db, subject.id)
    return subjects


@pytest.mark.parametrize(
    ("query", "canonical"),
    [
        ("英伟达", "NVIDIA"),
        ("NVIDIA", "NVIDIA"),
        ("nvid", "NVIDIA"),
        ("NVDIA", "NVIDIA"),
        ("黑神话", "Black Myth: Wukong"),
        ("黑神话悟空", "Black Myth: Wukong"),
        ("黑神话： 悟空", "Black Myth: Wukong"),
        ("Black Myth", "Black Myth: Wukong"),
        ("Black Myth 悟空", "Black Myth: Wukong"),
        ("Bitcoin", "Bitcoin"),
        ("比特币", "Bitcoin"),
        ("C++", "C++"),
        ("C#", "C#"),
        (".NET", ".NET"),
        ("GPT-5", "GPT-5"),
        ("DOOM", "DOOM"),
        ("DOOM II", "DOOM II"),
    ],
)
def test_multilingual_lexical(db, corpus, query, canonical):
    result = search(db, get_settings(), query)
    assert result["items"][0]["subject_id"] == str(corpus[canonical].id)
    assert len({i["subject_id"] for i in result["items"]}) == result["total"]
    assert result["mode_used"] == "lexical"


def test_fts_historical_and_filters_pagination(db, corpus):
    settings = get_settings()
    result = search(db, settings, "quantum accelerator")
    assert result["items"][0]["subject_id"] == str(corpus["NVIDIA"].id)
    assert result["items"][0]["matched_on"] == "source_title"
    assert result == search(db, settings, "quantum accelerator") | {"timings": result["timings"]}
    for filters in [
        dict(domain="computer"),
        dict(subject_type="company"),
        dict(source="Search test English"),
        dict(language="zh-CN"),
        dict(
            domain="computer", subject_type="company", source="Search test 中文", language="zh-CN"
        ),
    ]:
        assert search(db, settings, "NVIDIA", **filters)["total"] == 1
    for filters in [dict(domain="gaming"), dict(language="zh-CN", source="Search test English")]:
        assert search(db, settings, "NVIDIA", **filters)["total"] == 0
    first = search(db, settings, "DOOM", limit=1)
    second = search(db, settings, "DOOM", limit=1, offset=1)
    assert first["total"] == 2 and second["total"] == 2
    assert first["items"][0]["subject_id"] != second["items"][0]["subject_id"]
    assert search(db, settings, "zzzzunlikelyquery")["total"] == 0


class FakeProvider:
    model = "synthetic-test-vector"
    dimension = 3
    calls = 0

    def embed(self, value):
        self.calls += 1
        return [1.0, 0.0, 0.0] if "NVIDIA" in value or value == "英伟达" else [0.0, 1.0, 0.0]


def test_pgvector_hash_dimension_semantic_and_fallback(db, corpus):
    settings = get_settings()
    provider = FakeProvider()
    assert embed_documents(db, settings, provider)["embedded"] == len(corpus)
    assert embed_documents(db, settings, provider)["embedded"] == 0
    assert provider.calls == len(corpus)
    assert (
        db.scalar(text("SELECT count(*) FROM pg_extension WHERE extname IN ('vector','pg_trgm')"))
        == 2
    )
    result = search(db, settings, "英伟达", provider=provider)
    assert result["mode_used"] == "hybrid"
    assert result["items"][0]["subject_id"] == str(corpus["NVIDIA"].id)
    assert result["items"][0]["semantic_rank"] == 1
    assert (
        search(db, settings, "英伟达", mode="semantic", provider=provider)["items"][0][
            "semantic_rank"
        ]
        == 1
    )
    sid = corpus["NVIDIA"].id
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text("UPDATE subject_embeddings SET embedding=CAST(:v AS vector) WHERE subject_id=:id"),
            dict(v="[1,2]", id=sid),
        )
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("UPDATE subject_embeddings SET subject_id=:id"), dict(id=uuid4()))
    corpus["NVIDIA"].description = "new stable description"
    refresh_subject(db, sid)
    assert embed_documents(db, settings, provider)["embedded"] == 1

    def fail(_):
        raise httpx.ConnectError("offline")

    provider.embed = fail
    result = search(db, settings, "英伟达", provider=provider)
    assert result["mode_used"] == "lexical" and not result["semantic_search_available"]
    assert result["items"][0]["subject_id"] == str(sid)


def test_alias_refresh_merge_and_api(db, corpus):
    sid = corpus["NVIDIA"].id
    alias = SubjectAlias(
        subject_id=sid,
        alias="GPU Pioneer",
        normalized_alias="gpu pioneer",
        locale="en",
        alias_type="official_name",
        alias_source="synthetic_test",
    )
    db.add(alias)
    refresh_subject(db, sid)
    assert search(db, get_settings(), "GPU Pioneer")["items"][0]["subject_id"] == str(sid)
    other = corpus["DOOM II"].id
    merge_subjects(db, corpus["DOOM"].id, other)
    assert (
        db.scalar(
            text("SELECT count(*) FROM search_documents WHERE subject_id=:id"), dict(id=other)
        )
        == 0
    )
    assert search(db, get_settings(), "DOOM II")["items"][0]["subject_id"] == str(corpus["DOOM"].id)
    app = create_app(get_settings())
    app.dependency_overrides[get_db_session] = lambda: db
    with TestClient(app) as client:
        english = client.get("/api/v1/search", params={"q": "NVIDIA"}).json()
        chinese = client.get("/api/v1/search", params={"q": "英伟达", "locale": "zh-CN"}).json()
        assert english["items"][0]["subject_id"] == chinese["items"][0]["subject_id"]
        assert english["items"][0]["display_name"] == "NVIDIA"
        assert chinese["items"][0]["display_name"] == "英伟达"
        assert client.get("/api/v1/search", params={"q": "zzzzunlikelyquery"}).json()["total"] == 0


def test_merge_preserves_composite_history_and_reindex_excludes_redirect(db, corpus):
    from industry_intelligence.db.models.canonical import CompositeRankingEntry, CompositeRankingRun
    from industry_intelligence.services.search_index import reindex

    original = corpus["DOOM II"]
    canonical = corpus["DOOM"]
    run = CompositeRankingRun(
        domain="gaming",
        calculated_at=datetime.now(UTC),
        rrf_k=60,
        freshness_half_life=24,
        config_hash="test",
        input_hash=str(uuid4()),
        status="success",
        configuration={},
        diagnostics={},
    )
    db.add(run)
    db.flush()
    entry = CompositeRankingEntry(
        run_id=run.id,
        subject_id=original.id,
        rank=1,
        final_score=0.1,
        source_count=1,
        best_source_rank=1,
        details={"canonical_name": "DOOM II"},
    )
    db.add(entry)
    db.flush()
    merge_subjects(db, canonical.id, original.id)
    db.flush()
    assert db.get(CompositeRankingEntry, entry.id).subject_id == original.id
    assert db.get(Subject, original.id) is not None
    reindex(db, get_settings(), original.id)
    assert not db.scalar(
        text("SELECT 1 FROM search_documents WHERE subject_id=:id"), {"id": original.id}
    )


def test_stale_vectors_excluded_and_search_has_no_indexing_side_effect(db, corpus):
    settings = get_settings()
    provider = FakeProvider()
    assert not search(db, settings, "NVIDIA", provider=provider)["semantic_search_available"]
    embed_documents(db, settings, provider)
    db.execute(text("UPDATE subject_embeddings SET content_hash='stale'"))
    before = list(
        db.execute(text("SELECT subject_id, updated_at FROM search_documents ORDER BY subject_id"))
    )
    result = search(db, settings, "NVIDIA", provider=provider)
    after = list(
        db.execute(text("SELECT subject_id, updated_at FROM search_documents ORDER BY subject_id"))
    )
    assert before == after
    assert result["mode_used"] == "lexical"
    assert not result["semantic_search_available"]


def _story(db, title, domain=Domain.COMPUTER):
    """A ranked-story Subject (``SubjectType.EVENT``), as ranking sources create them."""

    story = Subject(
        domain=domain, subject_type=SubjectType.EVENT, canonical_name=str(uuid4()), title=title
    )
    db.add(story)
    db.flush()
    refresh_subject(db, story.id)
    return story


def test_topic_results_can_leave_out_ranked_stories_structurally(db, corpus):
    story = _story(db, "NVIDIA ships a new accelerator board")
    game_story = _story(db, "DOOM sequel announced at showcase", Domain.GAMING)
    settings = get_settings()
    everything = search(db, settings, "NVIDIA")
    assert {item["subject_id"] for item in everything["items"]} >= {
        str(corpus["NVIDIA"].id),
        str(story.id),
    }
    assert everything["excluded_story_count"] == 0
    topics = search(db, settings, "NVIDIA", exclude_stories=True)
    # The reusable Topic stays; the one-article story is only counted (persisted type).
    assert [item["subject_id"] for item in topics["items"]] == [str(corpus["NVIDIA"].id)]
    assert topics["total"] == 1 and topics["excluded_story_count"] == 1
    # Asked for by type, the story is still found: nothing is lost, only not presented as a Topic.
    stories = search(db, settings, "NVIDIA", subject_type="event", exclude_stories=True)
    assert [item["subject_id"] for item in stories["items"]] == [str(story.id)]
    assert stories["excluded_story_count"] == 0
    # The domain filter still applies before counting.
    gaming = search(db, settings, "DOOM", domain="gaming", exclude_stories=True)
    assert str(game_story.id) not in {item["subject_id"] for item in gaming["items"]}
    assert {item["subject_type"] for item in gaming["items"]} == {"game"}
    assert gaming["excluded_story_count"] == 1
    assert (
        search(db, settings, "NVIDIA", domain="gaming", exclude_stories=True)[
            "excluded_story_count"
        ]
        == 0
    )
    app = create_app(settings)
    app.dependency_overrides[get_db_session] = lambda: db
    with TestClient(app) as client:
        body = client.get("/api/v1/search", params={"q": "NVIDIA", "stories": "exclude"}).json()
        assert body["total"] == 1 and body["excluded_story_count"] == 1
        default = client.get("/api/v1/search", params={"q": "NVIDIA"}).json()
        assert default["excluded_story_count"] == 0 and default["total"] == 2
        assert (
            client.get("/api/v1/search", params={"q": "NVIDIA", "stories": "x"}).status_code == 422
        )
