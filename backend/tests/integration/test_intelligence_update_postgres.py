"""Operations-1: the daily ``intelligence-update`` run end to end on PostgreSQL.

These tests run every real stage (ingestion, composite ranking, relinking, Event building, AI
with a fixture provider, and the data-quality audit), so they only run against the isolated
``*_test`` database (``DATABASE_URL`` override) and still roll everything back. Sources are
synthetic registry entries whose adapters return fixed items; no network request is made.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.adapters.source.base import ContentSourceItem, SourceItem
from industry_intelligence.adapters.source.content_registry import (
    CONTENT_SOURCES,
    ContentSourceDefinition,
)
from industry_intelligence.adapters.source.registry import SOURCES, SourceDefinition
from industry_intelligence.ai.provider import AIRateLimited
from industry_intelligence.ai.providers.fixture import FixtureProvider
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    AIArtifact,
    CompositeRankingRun,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventContentLink,
    EventSubjectLink,
    IngestionBatch,
    RankObservation,
    Subject,
)
from industry_intelligence.db.models.canonical import SubjectAlias
from industry_intelligence.db.models.search import SearchDocument
from industry_intelligence.domain.enums import ContentType, Domain, SubjectType
from industry_intelligence.services import event_ingestion
from industry_intelligence.services.ai_suggestions import reject_artifact
from industry_intelligence.services.intelligence_update import (
    DEGRADED,
    FAILED,
    SKIPPED,
    SUCCESS,
    plan_update,
    run_update,
)

pytestmark = pytest.mark.integration

T0 = datetime(2033, 3, 1, 9, tzinfo=UTC)
DATABASE = make_url(get_settings().database_url).database or ""


@pytest.fixture(autouse=True)
def isolated_database():
    if not DATABASE.endswith("_test"):
        pytest.skip("writes every stage; run with DATABASE_URL pointing at a *_test database")


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
def tag():
    return uuid4().hex[:8]


@pytest.fixture
def registry(monkeypatch, tag):
    """One ranking source, one content source, and one failing content source."""

    game = f"Orbit Forge {tag}"

    class Ranking:
        def __init__(self, http, limit):
            self.failed_count = 0

        async def fetch(self):
            at = datetime.now(UTC)
            return [SourceItem(f"slug-{tag}", game, None, 1, "9.1", at)]

    class News:
        def __init__(self, http, limit):
            self.failed_count = 0

        async def fetch(self):
            return [
                ContentSourceItem(
                    f"{tag}-1",
                    f"{game} studio announces an expansion",
                    f"https://news.example.test/{tag}/1",
                    T0,
                    T0 + timedelta(minutes=5),
                    "en",
                    # Product-1.1: a complete excerpt, so the Event supports both AI tasks.
                    "The studio said the expansion adds a new region and two story chapters.",
                )
            ]

    class Down:
        def __init__(self, http, limit):
            self.failed_count = 0

        async def fetch(self):
            request = httpx.Request("GET", "https://down.example.test/feed")
            raise httpx.HTTPStatusError("503", request=request, response=httpx.Response(503))

    monkeypatch.setitem(
        SOURCES,
        "synthetic_rank",
        SourceDefinition(
            "synthetic_rank",
            f"Synthetic ranking {tag}",
            Domain.GAMING,
            "Top games",
            SubjectType.GAME,
            "https://rank.example.test",
            Ranking,
        ),
    )
    for key, adapter in (("synthetic_news", News), ("synthetic_down", Down)):
        monkeypatch.setitem(
            CONTENT_SOURCES,
            key,
            ContentSourceDefinition(
                key,
                f"Synthetic {key} {tag}",
                Domain.GAMING,
                ContentType.NEWS,
                "https://news.example.test/feed",
                adapter,
                "RSS",
                "en",
            ),
        )
    return game


KEYS = ["synthetic_rank", "synthetic_news", "synthetic_down"]


def insight_responder(**request):
    import json

    payload = json.loads(request["user"].split("\n", 1)[1])
    ref = payload["reports"][0]["ref"]
    if request["schema_name"] == "event_summary":
        chinese = "Simplified Chinese" in request["system"]
        text = "据来源报道，该工作室公布了扩展内容。" if chinese else "The studio announced it."
        return {"points": [{"text": text, "evidence": [ref]}], "insufficient_evidence": False}
    return {"points": [], "insufficient_evidence": True}


def update(settings, factory, **kwargs):
    return asyncio.run(run_update(settings, factory, sources=KEYS, **kwargs))


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def snapshot(db):
    return {
        model.__tablename__: count(db, model)
        for model in (
            Subject,
            SubjectAlias,
            RankObservation,
            IngestionBatch,
            ContentItem,
            ContentSubjectLink,
            Event,
            EventContentLink,
            EventSubjectLink,
            SearchDocument,
            CompositeRankingRun,
            AIArtifact,
        )
    }


def test_full_run_orders_stages_isolates_a_failing_source_and_degrades(
    db, factory, settings, registry
):
    provider = FixtureProvider(insight_responder)
    result = update(settings, factory, run_id="run-a", provider=provider)
    names = [stage.name for stage in result.stages]
    assert names == [
        "precheck",
        "rankings",
        "composite",
        "company_rankings",
        "content",
        "links",
        "events",
        "ai",
        "postcheck",
    ]
    stage = {s.name: s for s in result.stages}
    assert stage["precheck"].status == SUCCESS and stage["precheck"].counts["audit_errors"] == 0
    assert stage["rankings"].counts["observations_persisted"] == 1
    assert (stage["company_rankings"].status, stage["company_rankings"].reason) == (
        SKIPPED,
        "no_sources_selected",
    )
    content = stage["content"]
    assert content.status == DEGRADED and content.counts["persisted_count"] == 1
    assert content.counts["link_count"] == 1
    assert {row["source"]: row.get("status_code") for row in content.sources} == {
        "synthetic_news": None,
        "synthetic_down": 503,
    }
    assert stage["events"].counts["created_events"] == 1
    assert stage["events"].counts["event_subject_links"] == 1
    assert stage["events"].counts["retired_ids_registered"] == 0
    assert stage["ai"].counts["generated"] == 2 and len(provider.calls) == 2
    # AI-LP1: primary locale only (the synthetic report is English), no cross-language task.
    assert {a.locale for a in db.scalars(select(AIArtifact))} == {"en"}
    assert stage["postcheck"].status == SUCCESS and stage["postcheck"].counts["audit_errors"] == 0
    assert (result.status, result.exit_code) == (DEGRADED, 3)
    assert result.deltas["content_items"] == 1 and result.deltas["events"] == 1
    subject = db.scalar(select(Subject).where(Subject.title == registry))
    link = db.scalar(select(ContentSubjectLink).where(ContentSubjectLink.subject_id == subject.id))
    assert link.strategy == "exact_name"
    assert db.get(SearchDocument, subject.id) is not None


def test_repeated_run_is_idempotent_and_replays_cached_ai(db, factory, settings, registry):
    update(settings, factory, run_id="run-b", provider=FixtureProvider(insight_responder))
    before = snapshot(db)
    event_ids = set(db.scalars(select(Event.id)))
    provider = FixtureProvider(insight_responder)
    again = update(settings, factory, run_id="run-b", provider=provider)
    stage = {s.name: s for s in again.stages}
    # Same run ID: the ranking batch replays without a fetch; content replays by identity.
    assert stage["rankings"].counts["replayed_sources"] == 1
    assert stage["rankings"].counts["observations_persisted"] == 0
    assert stage["composite"].counts == {"runs_created": 0, "runs_replayed": 3}
    assert stage["content"].counts["persisted_count"] == 0
    assert stage["content"].counts["replayed_count"] == 1
    assert stage["links"].counts["content_links_added"] == 0
    assert stage["links"].counts["content_links_removed"] == 0
    assert stage["events"].counts["processed"] == 0
    assert stage["ai"].counts["cached"] == 2 and stage["ai"].counts["requests"] == 0
    assert provider.calls == []
    assert snapshot(db) == before and set(db.scalars(select(Event.id))) == event_ids
    assert all(delta == 0 for delta in again.deltas.values())
    # A new run ID is a new, correctly timed ranking snapshot (append-only), nothing else.
    later = update(settings, factory, run_id="run-c", skip_ai=True)
    assert later.deltas["rank_observations"] == 1
    assert {k: v for k, v in later.deltas.items() if k != "rank_observations"} == dict.fromkeys(
        later.deltas.keys() - {"rank_observations"}, 0
    )
    assert count(db, IngestionBatch, IngestionBatch.source_key == "synthetic_rank") == 2


def test_precheck_data_quality_error_blocks_every_write(db, factory, settings, registry):
    # A DQ-1 legacy hash canonical name is an error-level audit finding.
    db.add(
        Subject(
            domain=Domain.GAMING, subject_type=SubjectType.GAME, canonical_name="a" * 64, title="X"
        )
    )
    db.commit()
    before = snapshot(db)
    result = update(settings, factory, provider=FixtureProvider(insight_responder))
    assert [stage.name for stage in result.stages] == ["precheck"]
    assert result.stages[0].reason == "data_quality_errors"
    assert (result.status, result.exit_code) == (FAILED, 1)
    assert snapshot(db) == before


def test_event_failure_keeps_ingested_content_and_a_rerun_recovers(
    db, factory, settings, registry, monkeypatch
):
    real = event_ingestion.build_events

    def broken(session, *args, **kwargs):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(event_ingestion, "build_events", broken)
    provider = FixtureProvider(insight_responder)
    failed = update(settings, factory, run_id="run-d", provider=provider)
    stage = {s.name: s for s in failed.stages}
    assert stage["events"].status == FAILED and stage["events"].errors == [
        {"error": "RuntimeError"}
    ]
    assert (stage["ai"].status, stage["ai"].reason) == (SKIPPED, "blocked_by_events")
    assert stage["postcheck"].status == SUCCESS and failed.status == FAILED
    assert provider.calls == []
    assert failed.deltas["content_items"] == 1 and failed.deltas["events"] == 0
    # Restart: the same command, no manual database step.
    monkeypatch.setattr(event_ingestion, "build_events", real)
    recovered = update(settings, factory, run_id="run-d", provider=provider)
    stage = {s.name: s for s in recovered.stages}
    assert stage["content"].counts["persisted_count"] == 0
    assert stage["events"].counts["processed"] == 1 and recovered.deltas["events"] == 1
    assert stage["ai"].counts["generated"] == 2


def test_ai_provider_failure_keeps_canonical_updates_and_degrades(db, factory, settings, registry):
    def rate_limited(**request):
        raise AIRateLimited()

    result = update(settings, factory, provider=FixtureProvider(rate_limited))
    stage = {s.name: s for s in result.stages}
    assert stage["events"].status == SUCCESS and result.deltas["events"] == 1
    assert stage["ai"].status == DEGRADED and stage["ai"].counts["failed"] == 2
    assert stage["ai"].errors == [{"error": "rate_limited", "count": 2}]
    assert result.status == DEGRADED
    # Transient failures are retried by the next run; the canonical data stays.
    retry = FixtureProvider(insight_responder)
    again = update(settings, factory, provider=retry, run_id="run-e")
    assert {s.name: s for s in again.stages}["ai"].counts["generated"] == 2
    assert len(retry.calls) == 2


def test_non_transient_failures_are_not_regenerated_unattended(db, factory, settings, registry):
    def bad_evidence(**request):
        return {"points": [{"text": "Wrong.", "evidence": ["C9"]}], "insufficient_evidence": False}

    update(settings, factory, provider=FixtureProvider(bad_evidence), run_id="run-f")
    assert count(db, AIArtifact, AIArtifact.error_code == "validation_failed") == 2
    provider = FixtureProvider(insight_responder)
    again = update(settings, factory, provider=provider, run_id="run-f")
    ai = again.stage("ai")
    # Paid, checked answers that failed validation wait for an explicit ``ai-event-enrich``.
    assert ai.counts["skipped_failed"] == 2 and ai.counts["tasks"] == 0
    assert provider.calls == [] and again.status == DEGRADED


def test_reviewer_rejection_is_never_regenerated_unattended(db, factory, settings, registry):
    update(settings, factory, provider=FixtureProvider(insight_responder), run_id="run-g")
    summary = db.scalar(
        select(AIArtifact).where(
            AIArtifact.artifact_type == "event_summary", AIArtifact.locale == "en"
        )
    )
    reject_artifact(db, summary.id, "audited: unsupported detail")
    db.commit()
    provider = FixtureProvider(insight_responder)
    again = update(settings, factory, provider=provider, run_id="run-g")
    ai = again.stage("ai")
    assert ai.counts["skipped_rejected"] == 1 and ai.counts["cached"] == 1
    assert provider.calls == [] and again.status == DEGRADED
    assert db.get(AIArtifact, summary.id).status == "rejected"


def test_skip_ai_succeeds_without_provider_calls(db, factory, settings, registry):
    result = asyncio.run(
        run_update(settings, factory, sources=["synthetic_rank", "synthetic_news"], skip_ai=True)
    )
    assert result.status == SUCCESS and result.exit_code == 0
    assert result.stage("ai").reason == "skip_ai"
    assert count(db, AIArtifact) == 0


def test_plan_writes_nothing_and_sends_nothing(db, factory, settings, registry, monkeypatch):
    before = snapshot(db)

    def no_network(*args, **kwargs):
        raise AssertionError("the plan must not fetch")

    for definition in (SOURCES["synthetic_rank"], CONTENT_SOURCES["synthetic_news"]):
        monkeypatch.setattr(definition.adapter, "fetch", no_network)
    plan = asyncio.run(plan_update(settings, factory, sources=KEYS))
    assert plan["precheck"]["status"] == SUCCESS
    stages = {row["stage"]: row for row in plan["stages"]}
    assert stages["rankings"]["sources"] == ["synthetic_rank"]
    assert stages["content"]["sources"] == ["synthetic_news", "synthetic_down"]
    assert stages["ai"]["status"] in {"planned", SKIPPED}
    assert plan["manual_only_company_rankings"] == [
        "wikipedia_largest_banks_assets",
        "wikipedia_video_game_revenue",
    ]
    assert snapshot(db) == before
