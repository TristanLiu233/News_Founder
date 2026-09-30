"""Product-1 (PostgreSQL): bounded AI selection, cache-first generation, provider preflight,
refused credentials, validator rejection, the Home card summary, and read-only GET requests.

Everything runs inside one rolled-back connection. Content is published in 2031, far after the
live corpus, so the synthetic Events are the most recent of their domains; the Trending read
model is replaced where a test does not exercise it. The bounded selection reads the whole
database, so these tests run only against a ``*_test`` database (``DATABASE_URL`` override).
No test calls a real AI provider.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.ai.provider import AIAuthenticationFailed
from industry_intelligence.ai.providers.fixture import FixtureProvider
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    AIArtifact,
    ContentItem,
    Event,
    EventContentLink,
    Source,
)
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain
from industry_intelligence.main import create_app
from industry_intelligence.services import ai_enrichment
from industry_intelligence.services import intelligence_update as update
from industry_intelligence.services import trending as trending_service
from industry_intelligence.services.ai_enrichment import select_bounded_events
from industry_intelligence.services.ai_insights import available_summaries, event_ai
from industry_intelligence.services.event_ingestion import build_events
from industry_intelligence.services.event_merge import merge_events
from industry_intelligence.services.trending import card_summaries

pytestmark = pytest.mark.integration

T0 = datetime(2031, 7, 1, 9, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_database():
    if not get_settings().database_url.endswith("_test"):
        pytest.skip("selects from every Event; run with DATABASE_URL pointing at a *_test database")


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
def settings():
    return get_settings().model_copy(update={"ai_request_interval": 0, "ai_max_retries": 1})


@pytest.fixture
def sources(db):
    tag = uuid4().hex[:8]
    rows = {}
    for domain in Domain:
        for language in ("en", "zh-CN"):
            row = Source(
                name=f"Product-1 {domain} {language} {tag}",
                domain=domain,
                source_type="test",
                base_url="https://example.test",
                primary_language=language,
            )
            db.add(row)
            rows[(str(domain), language)] = row
    db.flush()
    return rows


@pytest.fixture
def no_trending(monkeypatch):
    """Selection tests that are not about Trending see no Trending Events."""

    monkeypatch.setattr(
        trending_service, "domain_trending", lambda *_args, **_kwargs: {"items": []}
    )


def make_event(db, sources, domain, title, minutes=0, language="en", description=None):
    source = sources[(str(domain), language)]
    at = T0 + timedelta(minutes=minutes)
    item = ContentItem(
        source_id=source.id,
        domain=domain,
        content_type="news",
        identity_key=str(uuid4()),
        title=title,
        url=f"https://news.example.test/{uuid4()}",
        published_at=at,
        observed_at=at,
        language=language,
        description=description,
        item_metadata={},
        content_hash=str(uuid4()),
    )
    db.add(item)
    db.flush()
    build_events(db, content_ids=[item.id])
    db.commit()
    link = db.scalar(select(EventContentLink).where(EventContentLink.content_item_id == item.id))
    return db.get(Event, link.event_id)


def refs(user):
    import json

    return [report["ref"] for report in json.loads(user.split("\n", 1)[1])["reports"]]


def responder(**request):
    ref = refs(request["user"])[0]
    chinese = "Simplified Chinese" in request["system"]
    if request["schema_name"] == "event_summary":
        text = "据来源报道，工作室公布了新作。"
        if not chinese:
            text = "The source reports a new studio game."
        return {"points": [{"text": text, "evidence": [ref]}], "insufficient_evidence": False}
    return {"points": [], "insufficient_evidence": True}


class Provider(FixtureProvider):
    """A fixture provider that also counts (or refuses) the zero-generation preflight."""

    def __init__(self, answer=responder, refuse_preflight=False):
        super().__init__(answer)
        self.preflights, self.refuse_preflight = 0, refuse_preflight

    async def preflight(self):
        self.preflights += 1
        if self.refuse_preflight:
            raise AIAuthenticationFailed("HTTP 401")


def artifacts(db, event_id):
    return db.scalars(select(AIArtifact).where(AIArtifact.entity_key == str(event_id))).all()


# --- bounded, deterministic selection ------------------------------------------------------


def test_selection_is_capped_deterministic_and_round_robin(db, sources, no_trending):
    events = {
        key: make_event(db, sources, domain, f"{key} story {uuid4().hex[:6]}", minutes)
        for key, domain, minutes in (
            ("c1", Domain.COMPUTER, 30),
            ("c2", Domain.COMPUTER, 20),
            ("c3", Domain.COMPUTER, 10),
            ("f1", Domain.FINANCE, 25),
            ("f2", Domain.FINANCE, 15),
            ("g1", Domain.GAMING, 5),
            ("g2", Domain.GAMING, 1),
        )
    }
    ids = {event.id: key for key, event in events.items()}
    first = select_bounded_events(db, 5)
    assert [ids.get(event_id) for event_id in first] == ["c1", "f1", "g1", "c2", "f2"]
    assert select_bounded_events(db, 5) == first
    assert len(select_bounded_events(db, 15)) <= 15
    with pytest.raises(ValueError):
        select_bounded_events(db, 16)


def test_trending_events_come_before_more_recent_ones(db, sources, monkeypatch):
    older = make_event(db, sources, Domain.COMPUTER, f"listed story {uuid4().hex[:6]}", 1)
    newer = make_event(db, sources, Domain.COMPUTER, f"newer story {uuid4().hex[:6]}", 50)

    def fake_trending(_session, domain, limit=None, summaries=True):
        assert summaries is False and limit is None
        items = [{"event": {"id": str(older.id)}}] if str(domain) == "computer" else []
        return {"items": items}

    monkeypatch.setattr(trending_service, "domain_trending", fake_trending)
    selected = select_bounded_events(db, 4)
    computer = [event_id for event_id in selected if event_id in {older.id, newer.id}]
    assert computer[0] == older.id and selected[0] == older.id


def test_ineligible_events_are_never_selected(db, sources, no_trending):
    english = make_event(db, sources, Domain.GAMING, f"Studio {uuid4().hex[:6]} game", 40)
    chinese = make_event(
        db, sources, Domain.GAMING, f"工作室{uuid4().hex[:6]}新作", 41, language="zh-CN"
    )
    merge_events(db, english.id, chinese.id, "test: mixed-language Event")
    db.commit()
    selected = select_bounded_events(db, 15)
    # The canonical Event is mixed-language (no primary locale); the duplicate is a redirect.
    assert english.id not in selected and chinese.id not in selected


# --- the bounded AI stage: cache first, preflight, refused credentials, validators ----------


def stage(settings, factory, limit, provider):
    return asyncio.run(update.ai_stage(settings, factory, limit, provider))


def two_events(db, sources):
    return [
        make_event(db, sources, Domain.COMPUTER, f"Chipmaker {uuid4().hex[:6]} update", 60),
        make_event(db, sources, Domain.FINANCE, f"Bank {uuid4().hex[:6]} update", 60),
    ]


def test_bounded_stage_is_cache_first_and_preflights_only_before_generation(
    db, factory, settings, sources, no_trending
):
    events = two_events(db, sources)
    provider = Provider()
    first = stage(settings, factory, 2, provider)
    assert first.status == "success" and first.counts["selected_events"] == 2
    assert first.counts["requests"] == 4 and first.counts["generated"] == 4
    assert provider.preflights == 1 and len(provider.calls) == 4
    assert {row["event_id"] for row in first.details} == {str(event.id) for event in events}
    assert all(row["tasks"] == {"would_generate": 2} for row in first.details)
    again = stage(settings, factory, 2, provider)
    # Cached artifacts are reused: no request and no preflight.
    assert again.counts["cached"] == 4 and again.counts["requests"] == 0
    assert provider.preflights == 1 and len(provider.calls) == 4
    assert all(row["tasks"] == {"cached": 2} for row in again.details)


def test_refused_preflight_sends_no_generation_request(db, factory, settings, sources, no_trending):
    events = two_events(db, sources)
    provider = Provider(refuse_preflight=True)
    result = stage(settings, factory, 2, provider)
    assert (result.status, result.reason) == ("failed", "provider_preflight_failed")
    assert result.counts["requests"] == 0 and provider.calls == []
    assert result.errors == [{"error": "request_rejected"}]
    assert all(artifacts(db, event.id) == [] for event in events)
    # A failed optional AI stage only degrades a run.
    assert update.overall_status([update.StageResult("rankings"), result]) == "degraded"


def test_refused_credential_stops_the_batch_after_one_request(
    db, factory, settings, sources, no_trending
):
    events = two_events(db, sources)

    def refuse(**_request):
        raise AIAuthenticationFailed("HTTP 401")

    provider = Provider(refuse)
    result = stage(settings, factory, 2, provider)
    assert (result.status, result.reason) == ("failed", "authentication_failed")
    assert len(provider.calls) == 1 and result.counts["requests"] == 1
    assert result.counts["not_attempted"] == 3
    rows = [row for event in events for row in artifacts(db, event.id)]
    assert len(rows) == 1 and rows[0].status == "failed"
    assert rows[0].error_code == "request_rejected"
    assert rows[0].artifact_metadata["authentication_failed"] is True


def test_validator_rejection_stays_failed_and_is_never_rendered(
    db, factory, settings, sources, no_trending
):
    event = make_event(
        db,
        sources,
        Domain.GAMING,
        f"Studio {uuid4().hex[:6]} reveals a game",
        70,
        description="The studio revealed its next game at a showcase on Tuesday.",
    )

    def foreign_ref(**request):
        return {"points": [{"text": "Unsupported claim.", "evidence": ["C99"]}]}

    result = stage(settings, factory, 1, Provider(foreign_ref))
    assert result.counts["validation_failed"] == 2 and result.counts["generated"] == 0
    assert {row.status for row in artifacts(db, event.id)} == {"failed"}
    assert available_summaries(db, [event.id]) == {}
    summary = card_summaries(db, [event.id])[event.id]
    assert summary["kind"] == "excerpt"


# --- the Home card summary: persisted AI Summary, else excerpt, else nothing -----------------


def test_card_summary_priority_and_states(db, factory, settings, sources):
    described = make_event(
        db,
        sources,
        Domain.GAMING,
        f"Studio {uuid4().hex[:6]} reveals a game",
        80,
        description="The studio revealed its next game at a showcase on Tuesday. Read more",
    )
    bare = make_event(db, sources, Domain.GAMING, f"Bare {uuid4().hex[:6]} headline", 81)
    excerpt = card_summaries(db, [described.id, bare.id])
    assert excerpt[described.id] == {
        "kind": "excerpt",
        "text": "The studio revealed its next game at a showcase on Tuesday.",
        "language": "en",
        "source": sources[("gaming", "en")].name,
        "content_item_id": str(described.representative_content_id),
    }
    assert bare.id not in excerpt  # no safe summary: the card has none

    asyncio.run(
        ai_enrichment.enrich_events(
            settings, factory, event_ids=[described.id, bare.id], provider=Provider()
        )
    )
    db.expire_all()
    chosen = card_summaries(db, [described.id, bare.id])
    assert chosen[described.id]["kind"] == chosen[bare.id]["kind"] == "ai_summary"
    assert chosen[described.id]["text"] == "The source reports a new studio game."
    assert chosen[described.id]["locale"] == "en"
    state = event_ai(db, described.id)["insights"]["event_summary"]
    assert state["state"] == "available"
    assert chosen[described.id]["artifact_id"] == state["artifact"]["id"]

    summary = next(a for a in artifacts(db, described.id) if a.artifact_type == "event_summary")
    for status, digest in (("stale", "0" * 64), ("rejected", None), ("failed", None)):
        original = (summary.status, summary.input_hash)
        summary.status = "valid" if status == "stale" else status
        summary.input_hash = digest or summary.input_hash
        db.flush()
        # Stale (other evidence), rejected, and failed artifacts are never shown.
        assert available_summaries(db, [described.id]) == {}
        assert card_summaries(db, [described.id])[described.id]["kind"] == "excerpt"
        summary.status, summary.input_hash = original
        db.flush()
    assert available_summaries(db, [described.id])[described.id]["kind"] == "ai_summary"


# --- GET requests read persisted data only ------------------------------------------------------


def test_get_requests_never_call_a_provider_crawler_or_update(
    db, factory, settings, sources, monkeypatch
):
    event = make_event(db, sources, Domain.FINANCE, f"Bank {uuid4().hex[:6]} results", 90)
    asyncio.run(
        ai_enrichment.enrich_events(settings, factory, event_ids=[event.id], provider=Provider())
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a GET request must not reach a provider, crawler, or update")

    from industry_intelligence.ai import providers
    from industry_intelligence.infrastructure.http import HTTPClient
    from industry_intelligence.services import (
        content_ingestion,
        news_ranking_ingestion,
        source_ingestion,
    )

    for module, name in (
        (ai_enrichment, "build_provider"),
        (ai_enrichment, "enrich_events"),
        (ai_enrichment, "generate_with_retry"),
        (providers, "build_provider"),
        (update, "run_update"),
        (update, "execute_update"),
        (update, "ai_stage"),
        (source_ingestion, "ingest"),
        (content_ingestion, "ingest_content"),
        (news_ranking_ingestion, "ingest_news_ranking"),
        (HTTPClient, "get"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    app = create_app(get_settings())
    app.dependency_overrides[get_db_session] = lambda: db
    paths = [
        "/health",
        "/api/v1/sources",
        "/api/v1/today",
        "/api/v1/trending",
        "/api/v1/trending?domain=finance&limit=5",
        "/api/v1/events",
        f"/api/v1/events/{event.id}",
        f"/api/v1/events/{event.id}/content",
        f"/api/v1/events/{event.id}/ai",
        "/api/v1/news?domain=finance&limit=3",
        "/api/v1/search?q=Bank",
        "/api/v1/company-rankings",
        "/api/v1/companies",
    ]
    with TestClient(app) as client:
        for path in paths:
            assert client.get(path).status_code == 200, path
        body = client.get(f"/api/v1/events/{event.id}/ai").json()
    assert body["insights"]["event_summary"]["state"] == "available"
    # Every API route only reads (GET/HEAD); ingestion and AI stay CLI-only.
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/")
        for method in getattr(route, "methods", set())
    }
    assert methods <= {"GET", "HEAD"}
