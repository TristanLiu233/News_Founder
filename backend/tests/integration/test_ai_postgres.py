"""Phase 7 AI enrichment, read model, suggestions, and reviewed actions in rolled-back
transactions, with a fixture provider (no real AI service is ever called).

Synthetic sources, subjects, companies, and content are tagged and published in 2031, far from
the live corpus, so live Events, links, and artifacts are never touched; the ``connection``
fixture rolls everything back.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.ai.prompts import event_insights
from industry_intelligence.ai.provider import AITimeout
from industry_intelligence.ai.providers.fixture import FixtureProvider
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    AIArtifact,
    AISuggestion,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventCompanyLink,
    EventContentLink,
    EventRedirect,
    EventSubjectLink,
    Source,
    Subject,
)
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.main import create_app
from industry_intelligence.services import ai_enrichment
from industry_intelligence.services.ai_enrichment import enrich_events
from industry_intelligence.services.ai_insights import event_ai
from industry_intelligence.services.ai_suggestions import (
    SuggestionReviewRefused,
    accept_suggestion,
    list_suggestions,
    reject_artifact,
    reject_suggestion,
    suggest_entities,
    suggest_event_merges,
)
from industry_intelligence.services.company_resolution import resolve_company
from industry_intelligence.services.content_ingestion import relink_all_content
from industry_intelligence.services.event_ingestion import build_events, relink_all_events
from industry_intelligence.services.event_rebuild import EventRebuildRefused, rebuild_events

pytestmark = pytest.mark.integration

T0 = datetime(2031, 5, 1, 9, tzinfo=UTC)


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
def sources(db, tag):
    rows = []
    for index, language in enumerate(("en", "en", "zh-CN")):
        row = Source(
            name=f"Synthetic AI source {tag} {index}",
            domain=Domain.GAMING,
            source_type="test",
            base_url="https://example.test",
            primary_language=language,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def content(db, source, title, at=T0, language="en", description=None):
    row = ContentItem(
        source_id=source.id,
        domain=Domain.GAMING,
        content_type="news",
        identity_key=str(uuid4()),
        title=title,
        url=f"https://news.example.test/{uuid4()}",
        published_at=at,
        observed_at=at + timedelta(minutes=5),
        language=language,
        description=description,
        item_metadata={},
        content_hash=str(uuid4()),
    )
    db.add(row)
    db.flush()
    return row


def event_of(db, item):
    link = db.scalar(select(EventContentLink).where(EventContentLink.content_item_id == item.id))
    return db.get(Event, link.event_id)


def make_event(db, source, title, **kwargs):
    item = content(db, source, title, **kwargs)
    build_events(db, content_ids=[item.id])
    db.commit()
    return event_of(db, item), item


def refs(user):
    import json

    payload = json.loads(user.split("\n", 1)[1])
    reports = payload.get("reports") or payload["event_a"]["reports"]
    return [report["ref"] for report in reports]


def insight_responder(**request):
    """A valid summary / why-it-matters answer citing the first report."""

    ref = refs(request["user"])[0]
    chinese = "Simplified Chinese" in request["system"]
    if request["schema_name"] == "event_summary":
        text = (
            "据来源报道，该工作室公布了新作。"
            if chinese
            else "According to the source, the studio announced a game."
        )
        return {"points": [{"text": text, "evidence": [ref]}], "insufficient_evidence": False}
    return {"points": [], "insufficient_evidence": True}


def run(coro):
    return asyncio.run(coro)


def artifacts(db, event_id, **filters):
    query = select(AIArtifact).where(AIArtifact.entity_key == str(event_id))
    for key, value in filters.items():
        query = query.where(getattr(AIArtifact, key) == value)
    return db.scalars(query).all()


# --- enrichment, cache, locale, staleness --------------------------------------------------


def test_enrichment_generates_only_primary_locale_tasks(db, factory, settings, sources, tag):
    # AI-LP1: each Event gets its Summary and Why It Matters in its primary locale only; an
    # English Event stays English and a Chinese Event stays Chinese (no cross-language task).
    english, en_item = make_event(db, sources[0], f"Studio {tag} announces new game")
    chinese, _ = make_event(
        db, sources[2], f"工作室{tag}公布新作", language="zh-CN", at=T0 + timedelta(days=3)
    )
    provider = FixtureProvider(insight_responder)
    result = run(
        enrich_events(settings, factory, event_ids=[english.id, chinese.id], provider=provider)
    )
    assert result["requests"] == 4 and result["generated"] == 4 and result["failed"] == 0
    assert {row["event_id"]: row["primary_locale"] for row in result["events"]} == {
        str(english.id): "en",
        str(chinese.id): "zh-CN",
    }
    for event, locale in ((english, "en"), (chinese, "zh-CN")):
        rows = artifacts(db, event.id)
        assert {(a.artifact_type, a.locale, a.status) for a in rows} == {
            ("event_summary", locale, "valid"),
            ("event_why_it_matters", locale, "valid"),
        }
        assert {a.prompt_version for a in rows} == {"event_summary_v4", "event_why_it_matters_v6"}
        assert len({a.input_hash for a in rows}) == 2
    assert not db.scalars(
        select(AIArtifact).where(
            AIArtifact.entity_key.in_([str(english.id), str(chinese.id)]),
            AIArtifact.prompt_version.like("%xlang%"),
        )
    ).all()
    summary = next(a for a in artifacts(db, english.id) if a.artifact_type == "event_summary")
    assert summary.provider == "fixture" and summary.model == "fixture-model"
    assert summary.source_content_ids == [str(en_item.id)]
    assert summary.output_json["points"][0]["evidence"] == [str(en_item.id)]
    # The Chinese Event was asked for Chinese output with the primary prompt.
    chinese_requests = [c for c in provider.calls if "工作室" in c["user"]]
    assert len(chinese_requests) == 2
    assert all("Simplified Chinese" in c["system"] for c in chinese_requests)


def test_second_run_is_a_cached_replay_without_requests(db, factory, settings, sources, tag):
    event, _ = make_event(db, sources[0], f"Studio {tag} announces new game")
    run(
        enrich_events(
            settings, factory, event_ids=[event.id], provider=FixtureProvider(insight_responder)
        )
    )
    provider = FixtureProvider(insight_responder)
    result = run(enrich_events(settings, factory, event_ids=[event.id], provider=provider))
    assert provider.calls == [] and result["replayed"] == 2 and result["requests"] == 0
    assert len(artifacts(db, event.id)) == 2


def test_dry_run_sends_nothing_and_writes_nothing(db, factory, settings, sources, tag):
    event, _ = make_event(db, sources[0], f"Studio {tag} announces new game")
    result = run(
        enrich_events(
            settings.model_copy(update={"ai_api_key": None}),
            factory,
            event_ids=[event.id],
            dry_run=True,
        )
    )
    assert result["would_generate"] == 2 and result["estimated_requests"] == 2
    assert result["events"][0]["source_count"] == 1
    assert result["events"][0]["primary_locale"] == "en"
    assert artifacts(db, event.id) == []


def test_explicit_cross_language_locale_is_refused_without_requests_or_writes(
    db, factory, settings, sources, tag
):
    english, _ = make_event(db, sources[0], f"Studio {tag} announces new game")
    chinese, _ = make_event(
        db, sources[2], f"工作室{tag}公布新作", language="zh-CN", at=T0 + timedelta(days=3)
    )
    before = db.scalar(select(func.count()).select_from(AIArtifact))
    for dry_run in (True, False):
        provider = FixtureProvider(insight_responder)
        with pytest.raises(ai_enrichment.CrossLanguageGenerationDisabled) as refused:
            run(
                enrich_events(
                    settings,
                    factory,
                    event_ids=[english.id, chinese.id],
                    locales=("en",),
                    provider=provider,
                    dry_run=dry_run,
                )
            )
        assert "disabled by product policy" in str(refused.value)
        assert refused.value.refused == [
            {"event_id": str(chinese.id), "locale": "en", "primary_locale": "zh-CN"}
        ]
        assert provider.calls == []
    assert db.scalar(select(func.count()).select_from(AIArtifact)) == before
    # Naming the primary locale explicitly is allowed and generates only that locale.
    provider = FixtureProvider(insight_responder)
    result = run(
        enrich_events(
            settings, factory, event_ids=[chinese.id], locales=("zh-CN",), provider=provider
        )
    )
    assert result["generated"] == 2 and {a.locale for a in artifacts(db, chinese.id)} == {"zh-CN"}


def test_mixed_language_event_has_no_primary_locale_and_gets_no_ai(
    db, factory, settings, sources, tag
):
    from industry_intelligence.services.event_merge import merge_events

    event, _ = make_event(db, sources[0], f"Studio {tag} announces new game")
    duplicate, _ = make_event(
        db, sources[2], f"工作室{tag}公布新作", language="zh-CN", at=T0 + timedelta(hours=1)
    )
    merge_events(db, event.id, duplicate.id, "reviewed: same announcement in two languages")
    db.commit()
    provider = FixtureProvider(insight_responder)
    result = run(enrich_events(settings, factory, event_ids=[event.id], provider=provider))
    assert result["tasks"] == 0 and result["skipped_no_primary_locale"] == 1
    assert provider.calls == [] and artifacts(db, event.id) == []
    read = event_ai(db, event.id, "en")
    assert read["primary_locale"] is None
    assert read["coverage"] == {"required": 0, "available": 0, "complete": False}
    assert {v["state"] for v in read["insights"].values()} == {"not_generated"}


def test_missing_provider_configuration_refuses_before_any_write(
    db, factory, settings, sources, tag
):
    from industry_intelligence.ai.provider import AIProviderNotConfigured

    event, _ = make_event(db, sources[0], f"Studio {tag} announces new game")
    unconfigured = settings.model_copy(update={"ai_provider": None, "ai_api_key": None})
    with pytest.raises(AIProviderNotConfigured):
        run(enrich_events(unconfigured, factory, event_ids=[event.id]))
    assert artifacts(db, event.id) == []


def test_changed_event_evidence_makes_old_artifacts_stale(db, factory, settings, sources, tag):
    event, first = make_event(db, sources[0], f"Studio {tag} announces new game")
    run(
        enrich_events(
            settings,
            factory,
            event_ids=[event.id],
            locales=("en",),
            provider=FixtureProvider(insight_responder),
        )
    )
    assert event_ai(db, event.id, "en")["insights"]["event_summary"]["state"] == "available"
    # A second report joins the Event (same headline, other source).
    second = content(
        db, sources[1], f"Studio {tag} announces new game", at=T0 + timedelta(minutes=20)
    )
    build_events(db, content_ids=[second.id])
    db.commit()
    assert event_of(db, second).id == event.id
    read = event_ai(db, event.id, "en")
    assert read["insights"]["event_summary"] == {"state": "stale", "artifact": None}
    assert read["source_count"] == 2 and read["single_source"] is False
    result = run(
        enrich_events(
            settings,
            factory,
            event_ids=[event.id],
            locales=("en",),
            provider=FixtureProvider(insight_responder),
        )
    )
    assert result["stale"] == 2 and result["stale_marked"] == 2 and result["generated"] == 2
    assert len(artifacts(db, event.id, status="stale")) == 2
    assert event_ai(db, event.id, "en")["insights"]["event_summary"]["state"] == "available"


def legacy_xlang(db, event, item, spec, locale, status, text):
    """A historical cross-language artifact row as stored before AI-LP1."""

    from industry_intelligence.services.ai_evidence import input_hash, load_event_evidence

    row = AIArtifact(
        entity_type="event",
        entity_key=str(event.id),
        artifact_type=spec.artifact_type,
        locale=locale,
        provider="fixture",
        model="fixture-model",
        prompt_version=spec.version,
        input_hash=input_hash(spec.version, locale, load_event_evidence(db, event.id)),
        source_content_ids=[str(item.id)],
        output_json={"points": [{"text": text, "evidence": [str(item.id)]}]},
        status=status,
        artifact_metadata={},
    )
    db.add(row)
    db.flush()
    return row


def test_historical_cross_language_artifacts_are_kept_but_never_read_or_regenerated(
    db, factory, settings, sources, tag, monkeypatch
):
    # AI-LP1: an English Event with a valid xlang v2 zh-CN Summary and a rejected xlang zh-CN
    # Why from before the policy. Both rows stay; neither is served, neither creates work, and
    # neither affects coverage, whatever the UI locale is.
    event, item = make_event(db, sources[0], f"Studio {tag} announces new game")
    kept = [
        legacy_xlang(
            db, event, item, event_insights.EVENT_SUMMARY_XLANG_V2, "zh-CN", "valid", "旧译文。"
        ),
        legacy_xlang(
            db,
            event,
            item,
            event_insights.EVENT_WHY_IT_MATTERS_XLANG_V1,
            "zh-CN",
            "rejected",
            "被撤下的译文。",
        ),
    ]
    db.commit()
    result = run(
        enrich_events(
            settings, factory, event_ids=[event.id], provider=FixtureProvider(insight_responder)
        )
    )
    assert result["generated"] == 2 and result["stale_marked"] == 0

    def read(locale):
        return event_ai(db, event.id, locale)

    for locale in ("en", "zh-CN", None):
        body = read(locale)
        assert body["locale"] == locale and body["primary_locale"] == "en"
        assert body["coverage"] == {"required": 2, "available": 2, "complete": True}
        for insight in body["insights"].values():
            assert insight["state"] == "available" and insight["artifact"]["locale"] == "en"
        assert "旧译文" not in str(body) and "被撤下" not in str(body)
    plan = run(
        enrich_events(settings, factory, event_ids=[event.id], dry_run=True, skip_settled=True)
    )
    assert (plan["tasks"], plan["cached"], plan["would_generate"]) == (2, 2, 0)
    assert (plan["skipped_rejected"], plan["skipped_failed"]) == (0, 0)
    assert [db.get(AIArtifact, row.id).status for row in kept] == ["valid", "rejected"]
    # A change of the legacy lineage changes nothing; a primary prompt change makes only the
    # primary artifact stale.
    monkeypatch.setitem(
        event_insights.CROSS_LANGUAGE_PROMPTS,
        "event_summary",
        replace(event_insights.EVENT_SUMMARY_XLANG_V2, version="event_summary_xlang_v3"),
    )
    assert read("zh-CN")["coverage"]["complete"] is True
    primary = event_insights.INSIGHT_PROMPTS["event_summary"]
    monkeypatch.setitem(
        event_insights.INSIGHT_PROMPTS,
        "event_summary",
        replace(primary, version="event_summary_v99"),
    )
    body = read("zh-CN")
    assert body["insights"]["event_summary"] == {"state": "stale", "artifact": None}
    assert body["coverage"] == {"required": 2, "available": 1, "complete": False}
    monkeypatch.setitem(event_insights.INSIGHT_PROMPTS, "event_summary", primary)
    # Phase 7.1 Checkpoint F: rejecting the current artifact reads "rejected", never "stale".
    current = read("en")["insights"]["event_summary"]["artifact"]["id"]
    reject_artifact(db, UUID(current), "manual audit: unsupported wording")
    assert read("en")["insights"]["event_summary"] == {"state": "rejected", "artifact": None}
    assert read("en")["coverage"]["available"] == 1
    assert [db.get(AIArtifact, row.id).status for row in kept] == ["valid", "rejected"]


def test_rejected_legacy_xlang_summary_creates_no_work_for_a_chinese_event(
    db, factory, settings, sources, tag
):
    # The AI-X1 case under AI-LP1: a Chinese Event whose English Summary v1 was rejected. The
    # English task no longer exists; the Event is complete with its two Chinese insights.
    event, item = make_event(db, sources[2], f"工作室{tag}公布新作", language="zh-CN")
    rejected = legacy_xlang(
        db, event, item, event_insights.EVENT_SUMMARY_XLANG_V1, "en", "rejected", "Rejected v1."
    )
    db.commit()
    run(
        enrich_events(
            settings, factory, event_ids=[event.id], provider=FixtureProvider(insight_responder)
        )
    )
    plan = run(
        enrich_events(settings, factory, event_ids=[event.id], dry_run=True, skip_settled=True)
    )
    assert (plan["tasks"], plan["cached"], plan["would_generate"]) == (2, 2, 0)
    body = event_ai(db, event.id, "en")
    assert body["primary_locale"] == "zh-CN" and body["coverage"]["complete"] is True
    assert body["insights"]["event_summary"]["artifact"]["locale"] == "zh-CN"
    assert "Rejected v1." not in str(body)
    again = FixtureProvider(insight_responder)
    assert (
        run(enrich_events(settings, factory, event_ids=[event.id], provider=again))["requests"] == 0
        and again.calls == []
    )
    assert db.get(AIArtifact, rejected.id).status == "rejected"


def test_rejected_artifact_is_never_served_and_regenerates_once(
    db, factory, settings, sources, tag
):
    event, _ = make_event(db, sources[2], f"工作室{tag}公布新作", language="zh-CN")

    def enrich():
        return run(
            enrich_events(
                settings,
                factory,
                event_ids=[event.id],
                locales=("zh-CN",),
                artifact_types=("event_summary",),
                provider=FixtureProvider(insight_responder),
            )
        )

    enrich()
    first = event_ai(db, event.id, "zh-CN")["insights"]["event_summary"]["artifact"]["id"]
    reject_artifact(db, UUID(first), "manual audit: unsupported wording")
    db.commit()
    app = create_app(settings)
    app.dependency_overrides[get_db_session] = lambda: db
    with TestClient(app) as client:
        for params in ({"locale": "zh-CN"}, {"locale": "en"}):
            body = client.get(f"/api/v1/events/{event.id}/ai", params=params).json()
            assert body["insights"]["event_summary"] == {"state": "rejected", "artifact": None}
        result = enrich()
        assert (result["requests"], result["generated"]) == (1, 1)
        body = client.get(f"/api/v1/events/{event.id}/ai", params={"locale": "zh-CN"}).json()
        summary = body["insights"]["event_summary"]
        assert summary["state"] == "available" and summary["artifact"]["id"] != first
    assert db.get(AIArtifact, UUID(first)).status == "rejected"


def test_one_failure_does_not_roll_back_other_events(db, factory, settings, sources, tag):
    good, _ = make_event(db, sources[0], f"Studio {tag} announces new game")
    bad, _ = make_event(
        db, sources[1], f"Publisher {tag} delays sequel", at=T0 + timedelta(hours=2)
    )

    def responder(**request):
        if "delays" in request["user"]:
            raise AITimeout()
        return insight_responder(**request)

    result = run(
        enrich_events(
            settings,
            factory,
            event_ids=[good.id, bad.id],
            locales=("en",),
            artifact_types=("event_summary",),
            provider=FixtureProvider(responder),
            sleep=lambda _: asyncio.sleep(0),
        )
    )
    assert result["generated"] == 1 and result["failed"] == 1 and result["errors"] == {"timeout": 1}
    failed = artifacts(db, bad.id)[0]
    assert (failed.status, failed.error_code, failed.output_json) == ("failed", "timeout", None)
    assert failed.artifact_metadata["attempts"] == 2
    assert artifacts(db, good.id)[0].status == "valid"
    assert event_ai(db, bad.id, "en")["insights"]["event_summary"]["state"] == "failed"


def test_foreign_evidence_ids_fail_validation_and_are_not_displayed(
    db, factory, settings, sources, tag
):
    event, _ = make_event(db, sources[0], f"Studio {tag} announces new game")
    other, other_item = make_event(
        db, sources[1], f"Other {tag} studio closes", at=T0 + timedelta(days=3)
    )

    def responder(**request):
        return {
            "points": [{"text": "The studio announced a game.", "evidence": [str(other_item.id)]}],
            "insufficient_evidence": False,
        }

    run(
        enrich_events(
            settings,
            factory,
            event_ids=[event.id],
            locales=("en",),
            artifact_types=("event_summary",),
            provider=FixtureProvider(responder),
        )
    )
    row = artifacts(db, event.id)[0]
    assert row.status == "failed" and row.error_code == "validation_failed"
    assert "unknown evidence ref" in row.artifact_metadata["validation_errors"][0]
    assert event_ai(db, event.id, "en")["insights"]["event_summary"]["state"] == "failed"


# --- read model and API --------------------------------------------------------------------


def test_read_model_states_evidence_metadata_and_rejection(db, factory, settings, sources, tag):
    event, item = make_event(db, sources[0], f"Studio {tag} announces new game")
    read = event_ai(db, event.id, "zh-CN")
    assert read["single_source"] is True and read["primary_locale"] == "en"
    assert {k: v["state"] for k, v in read["insights"].items()} == {
        "event_summary": "not_generated",
        "event_why_it_matters": "not_generated",
    }
    assert read["coverage"] == {"required": 2, "available": 0, "complete": False}
    run(
        enrich_events(
            settings, factory, event_ids=[event.id], provider=FixtureProvider(insight_responder)
        )
    )
    # A Chinese UI reads the English Event's English insights.
    read = event_ai(db, event.id, "zh-CN")
    summary = read["insights"]["event_summary"]
    assert summary["state"] == "available" and summary["artifact"]["locale"] == "en"
    evidence = summary["artifact"]["points"][0]["evidence"][0]
    assert evidence["content_id"] == str(item.id) and evidence["url"] == item.url
    assert evidence["position"] == 1 and evidence["source"] == sources[0].name
    why = read["insights"]["event_why_it_matters"]["artifact"]
    assert why["insufficient_evidence"] is True and why["points"] == []
    assert event_ai(db, event.id, "en")["insights"] == read["insights"]
    reject_artifact(db, UUID(summary["artifact"]["id"]), "reviewer found an inaccuracy")
    after = event_ai(db, event.id, "zh-CN")
    assert after["insights"]["event_summary"] == {"state": "rejected", "artifact": None}
    assert after["coverage"] == {"required": 2, "available": 1, "complete": False}
    assert event_ai(db, uuid4(), "en") is None


def test_coverage_counts_only_the_two_primary_insights(db, factory, settings, sources, tag):
    event, _ = make_event(db, sources[0], f"Studio {tag} announces new game")

    def responder(**request):
        if request["schema_name"] == "event_why_it_matters":
            raise AITimeout()
        return insight_responder(**request)

    run(
        enrich_events(
            settings,
            factory,
            event_ids=[event.id],
            provider=FixtureProvider(responder),
            sleep=lambda _: asyncio.sleep(0),
        )
    )
    read = event_ai(db, event.id)
    assert read["insights"]["event_summary"]["state"] == "available"
    assert read["insights"]["event_why_it_matters"]["state"] == "failed"
    assert read["coverage"] == {"required": 2, "available": 1, "complete": False}


def test_ai_endpoint_reads_persisted_data_and_never_builds_a_provider(
    db, factory, settings, sources, tag, monkeypatch
):
    event, _ = make_event(db, sources[2], f"工作室{tag}公布新作", language="zh-CN")
    run(
        enrich_events(
            settings, factory, event_ids=[event.id], provider=FixtureProvider(insight_responder)
        )
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a page request must not build an AI provider")

    monkeypatch.setattr(ai_enrichment, "build_provider", forbidden)
    app = create_app(get_settings())
    app.dependency_overrides[get_db_session] = lambda: db
    with TestClient(app) as client:
        bodies = []
        for params in ({"locale": "zh-CN"}, {"locale": "en"}, {}):
            response = client.get(f"/api/v1/events/{event.id}/ai", params=params)
            assert response.status_code == 200
            body = response.json()
            assert body["locale"] == params.get("locale") and body["primary_locale"] == "zh-CN"
            summary = body["insights"]["event_summary"]
            assert summary["state"] == "available" and summary["artifact"]["locale"] == "zh-CN"
            bodies.append(body["insights"])
        # The UI locale never changes the content (English UI + Chinese Event = Chinese AI).
        assert bodies[0] == bodies[1] == bodies[2]
        assert (
            client.get(f"/api/v1/events/{event.id}/ai", params={"locale": "fr"}).status_code == 422
        )
        assert client.get(f"/api/v1/events/{uuid4()}/ai").status_code == 404


# --- entity suggestions and reviewed actions -----------------------------------------------


def game_subject(db, title):
    row = Subject(
        domain=Domain.GAMING,
        subject_type=SubjectType.GAME,
        canonical_name=str(uuid4()),
        title=title,
    )
    db.add(row)
    db.flush()
    return row


def mentions_responder(names):
    def responder(**request):
        ref = refs(request["user"])[0]
        return {"mentions": [{"name": n, "kind": k, "evidence": [ref]} for n, k in names]}

    return responder


def test_entity_suggestions_review_and_reviewed_links(db, factory, settings, sources, tag):
    game_name, company_name, unknown = (
        f"Skyforge{tag}",
        f"Nimbus{tag} Interactive",
        f"Orbit{tag} Labs",
    )
    game = game_subject(db, game_name)
    company = resolve_company(db, company_name, (), f"test-{tag}").company
    event, item = make_event(
        db,
        sources[0],
        f"{company_name} and {unknown} bring {game_name} to consoles",
    )
    provider = FixtureProvider(
        mentions_responder(
            [
                (game_name, "game"),
                (company_name, "company"),
                (unknown, "company"),
                ("Absent Co", "company"),
            ]
        )
    )
    result = run(suggest_entities(settings, factory, event_ids=[event.id], provider=provider))
    assert result["new_suggestions"] == 3 and result["skipped"] == {"ungrounded": 1}
    rows = {
        row["suggestion_type"]: row
        for row in list_suggestions(db, "pending")
        if row["event_id"] == str(event.id)
    }
    assert rows["event_subject_candidate"]["candidate_id"] == str(game.id)
    assert rows["event_company_candidate"]["candidate_id"] == str(company.id)
    assert rows["new_entity_candidate"]["candidate_text"] == unknown
    # No canonical write happened yet.
    assert (
        db.scalar(
            select(func.count())
            .select_from(EventSubjectLink)
            .where(EventSubjectLink.event_id == event.id)
        )
        == 0
    )

    # Duplicate run: cached artifact, no request, no duplicate suggestion.
    again = FixtureProvider(mentions_responder([(game_name, "game")]))
    replay = run(suggest_entities(settings, factory, event_ids=[event.id], provider=again))
    assert again.calls == [] and replay["new_suggestions"] == 0

    with pytest.raises(SuggestionReviewRefused, match="never create"):
        accept_suggestion(db, UUID(rows["new_entity_candidate"]["id"]), "looks real")
    with pytest.raises(SuggestionReviewRefused, match="reason"):
        accept_suggestion(db, UUID(rows["event_subject_candidate"]["id"]), " ")

    subject_result = accept_suggestion(
        db, UUID(rows["event_subject_candidate"]["id"]), "headline names the game"
    )
    assert subject_result["links_written"] == 1
    link = db.scalar(
        select(ContentSubjectLink).where(ContentSubjectLink.content_item_id == item.id)
    )
    assert (link.subject_id, link.strategy) == (game.id, "reviewed")
    assert (
        db.scalar(select(EventSubjectLink.subject_id).where(EventSubjectLink.event_id == event.id))
        == game.id
    )

    accept_suggestion(db, UUID(rows["event_company_candidate"]["id"]), "headline names the company")
    company_link = db.scalar(select(EventCompanyLink).where(EventCompanyLink.event_id == event.id))
    assert (company_link.company_id, company_link.strategy) == (company.id, "reviewed")
    reject_suggestion(db, UUID(rows["new_entity_candidate"]["id"]), "not in catalog")
    with pytest.raises(SuggestionReviewRefused, match="already accepted"):
        accept_suggestion(db, UUID(rows["event_subject_candidate"]["id"]), "again")
    db.commit()

    # Deterministic relinks keep reviewed decisions.
    relink = relink_all_content(db, content_ids=[item.id])
    assert relink["reviewed_kept"] == 1 and relink["removed"] == 0
    assert db.get(ContentSubjectLink, link.id) is not None
    relink_all_events(db)
    assert (
        db.scalar(select(EventCompanyLink.strategy).where(EventCompanyLink.event_id == event.id))
        == "reviewed"
    )
    assert (
        db.scalar(select(EventSubjectLink.subject_id).where(EventSubjectLink.event_id == event.id))
        == game.id
    )
    # A rebuild would discard the reviewed Company link, so it is refused.
    with pytest.raises(EventRebuildRefused, match="reviewed Company links"):
        rebuild_events(db, T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))


def test_ambiguous_suggestion_needs_a_listed_candidate(db, factory, settings, sources, tag):
    name = f"Twin{tag}"
    first, second = game_subject(db, name), game_subject(db, name)
    event, item = make_event(db, sources[0], f"{name} launches on PC")
    run(
        suggest_entities(
            settings,
            factory,
            event_ids=[event.id],
            provider=FixtureProvider(mentions_responder([(name, "game")])),
        )
    )
    suggestion = db.scalar(select(AISuggestion).where(AISuggestion.entity_key == str(event.id)))
    assert suggestion.candidate_id is None
    with pytest.raises(SuggestionReviewRefused, match="choose one"):
        accept_suggestion(db, suggestion.id, "ambiguous")
    with pytest.raises(SuggestionReviewRefused, match="choose one"):
        accept_suggestion(db, suggestion.id, "not listed", candidate_id=uuid4())
    accept_suggestion(db, suggestion.id, "store page matches", candidate_id=second.id)
    assert (
        db.scalar(
            select(ContentSubjectLink.subject_id).where(
                ContentSubjectLink.content_item_id == item.id
            )
        )
        == second.id
    )
    assert first.id != second.id


# --- cross-language merge suggestions ------------------------------------------------------


def test_cross_language_merge_suggestion_uses_reviewed_merge(db, factory, settings, sources, tag):
    name = f"Quasar{tag}"
    english, _ = make_event(db, sources[0], f"{name} Studios restores archived campaign content")
    chinese, _ = make_event(
        db,
        sources[2],
        f"{name}官宣恢复已下架的战役内容",
        at=T0 + timedelta(hours=5),
        language="zh-CN",
    )

    def responder(**request):
        return {
            "same_event": "yes",
            "reason": "Both report the restored campaign.",
            "evidence_a": ["A1"],
            "evidence_b": ["B1"],
        }

    since = T0 - timedelta(hours=1)
    preview = run(
        suggest_event_merges(settings, factory, since=since, domain="gaming", dry_run=True)
    )
    pair = preview["candidate_pairs"][0]
    assert {pair["event_a"], pair["event_b"]} == {str(english.id), str(chinese.id)}
    assert name.casefold() in pair["distinctive_names"]
    result = run(
        suggest_event_merges(
            settings, factory, since=since, domain="gaming", provider=FixtureProvider(responder)
        )
    )
    assert result["judgements"] == {"yes": 1} and result["new_suggestions"] == 1
    # Events stay separate until a reviewer accepts.
    assert db.get(EventRedirect, chinese.id) is None
    suggestion = db.scalar(
        select(AISuggestion).where(
            AISuggestion.suggestion_type == "event_merge_candidate",
            AISuggestion.entity_key == str(english.id),
        )
    )
    outcome = accept_suggestion(db, suggestion.id, "same announcement in both languages")
    assert outcome["applied"] == "reviewed_event_merge"
    assert db.get(EventRedirect, chinese.id).canonical_id == english.id
    rerun = run(suggest_event_merges(settings, factory, since=since, domain="gaming", dry_run=True))
    assert rerun["candidate_pairs"] == []


def test_merge_safeguards_are_not_bypassed(db, factory, settings, sources, tag):
    left, _ = make_event(db, sources[0], f"Rival{tag} acquires Studio {tag}")
    right, _ = make_event(
        db, sources[2], f"Rival{tag}正式发布新游戏", at=T0 + timedelta(hours=1), language="zh-CN"
    )
    assert {left.event_type, right.event_type} == {"acquisition", "product_launch"}
    suggestion = AISuggestion(
        suggestion_type="event_merge_candidate",
        entity_type="event",
        entity_key=str(left.id),
        candidate_type="event",
        candidate_id=right.id,
        candidate_text=right.canonical_title,
        candidate_key=str(right.id),
        evidence_content_ids=[],
        provider="fixture",
        model="fixture-model",
        prompt_version="event_pair_same_event_v1",
        status="pending",
        suggestion_metadata={},
    )
    db.add(suggestion)
    db.flush()
    with pytest.raises(SuggestionReviewRefused, match="incompatible Event types"):
        accept_suggestion(db, suggestion.id, "reviewer wants it")
    db.refresh(suggestion)
    assert suggestion.status == "pending"
    assert db.get(EventRedirect, right.id) is None


def test_reviewed_company_link_follows_a_reviewed_merge(db, sources, tag):
    from industry_intelligence.services.event_merge import merge_events

    company = resolve_company(db, f"Merge{tag} Holdings", (), f"test-{tag}").company
    canonical, _ = make_event(db, sources[0], f"Merge{tag} Holdings opens studio")
    duplicate, _ = make_event(
        db, sources[2], f"Merge{tag}开设新工作室", at=T0 + timedelta(hours=2), language="zh-CN"
    )
    db.add(
        EventCompanyLink(
            event_id=duplicate.id,
            company_id=company.id,
            strategy="reviewed",
            content_count=1,
            evidence={},
        )
    )
    db.flush()
    merge_events(db, canonical.id, duplicate.id, "same opening")
    moved = db.scalars(
        select(EventCompanyLink).where(EventCompanyLink.company_id == company.id)
    ).all()
    assert [(link.event_id, link.strategy) for link in moved] == [(canonical.id, "reviewed")]
