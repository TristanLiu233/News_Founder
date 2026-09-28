"""Phase 6.1: content persistence, deterministic linking, orchestration, and Event rebuild.

Everything runs inside one rolled-back connection. Sessions created by the pipeline's session
factory join it through SAVEPOINTs, so their commits never reach the live database. All
synthetic content is published in 2032, far from the live corpus and the 24-hour Event window;
Event building and rebuilds are scoped to the synthetic content or window.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.adapters.source.base import ContentSourceItem
from industry_intelligence.adapters.source.content_registry import ContentSourceDefinition
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    CompanySubjectLink,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventCompanyLink,
    EventContentLink,
    EventRetiredId,
    EventSubjectLink,
    Source,
    Subject,
)
from industry_intelligence.db.models.canonical import SubjectExternalIdentity
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.domain.enums import ContentType, Domain, SubjectType
from industry_intelligence.services import content_ingestion, content_pipeline
from industry_intelligence.services.ai_insights import event_ai
from industry_intelligence.services.company_resolution import resolve_company
from industry_intelligence.services.content_ingestion import (
    link_content_items,
    persist_content,
    relink_all_content,
)
from industry_intelligence.services.event_ingestion import build_events
from industry_intelligence.services.event_merge import merge_events
from industry_intelligence.services.event_rebuild import (
    EventRebuildRefused,
    EventRetirementInvariantError,
    rebuild_events,
    retire_event,
)
from industry_intelligence.services.events import event_content, event_detail

pytestmark = pytest.mark.integration

T0 = datetime(2032, 5, 4, 9, tzinfo=UTC)


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
def tag():
    return uuid4().hex[:8]


def definition(tag, domain=Domain.GAMING, suffix="a"):
    return ContentSourceDefinition(
        f"synthetic_{suffix}",
        f"Synthetic news {tag} {suffix}",
        domain,
        ContentType.NEWS,
        "https://news.example.test/feed",
        object,
        "RSS",
        "en",
    )


def incoming(title, url=None, external_id=None, at=T0, description=None, metadata=None):
    url = url or f"https://news.example.test/{uuid4()}"
    return ContentSourceItem(
        external_id=external_id or url,
        title=title,
        url=url,
        published_at=at,
        observed_at=at + timedelta(minutes=5),
        language="en",
        description=description,
        metadata=metadata or {},
    )


def subject(db, title, domain=Domain.GAMING, kind=SubjectType.GAME):
    row = Subject(domain=domain, subject_type=kind, canonical_name=str(uuid4()), title=title)
    db.add(row)
    db.flush()
    return row


def count(db, model, **filters):
    query = select(func.count()).select_from(model)
    for key, value in filters.items():
        query = query.where(getattr(model, key) == value)
    return db.scalar(query)


def links_of(db, content_id):
    return {
        (link.subject_id, link.strategy, link.relation_type)
        for link in db.scalars(
            select(ContentSubjectLink).where(ContentSubjectLink.content_item_id == content_id)
        )
    }


# --- persistence and replay -----------------------------------------------------------------


def test_persist_counts_replays_by_identity_and_url(db, tag):
    source = definition(tag)
    first = incoming(f"Orbit Forge {tag} delayed to 2033", external_id=f"guid-{tag}-1")
    other = incoming(f"Studio {tag} layoffs")
    result = persist_content(db, source, [first, other, incoming("   ")])
    assert result["persisted_count"] == 2 and result["invalid_count"] == 1
    assert result["replayed_count"] == 0 and len(result["content_ids"]) == 2
    # Same external ID, and a new external ID for an already stored URL, are both replays.
    moved_guid = incoming("Different title", url=first.url, external_id=f"guid-{tag}-2")
    replay = persist_content(db, source, [first, moved_guid])
    assert replay["persisted_count"] == 0 and replay["replayed_count"] == 2
    source_row = db.scalar(select(Source).where(Source.name == source.name))
    assert count(db, ContentItem, source_id=source_row.id) == 2


def test_all_invalid_batch_raises_and_empty_batch_is_a_noop(db, tag):
    with pytest.raises(ValueError):
        persist_content(db, definition(tag), [incoming(" ")])
    assert persist_content(db, definition(tag), [])["persisted_count"] == 0


# --- deterministic linking ------------------------------------------------------------------


def test_new_content_links_by_name_and_same_article(db, tag):
    game = subject(db, f"Orbit Forge {tag}")
    story = subject(db, f"Story {tag}", domain=Domain.COMPUTER, kind=SubjectType.EVENT)
    source_def = definition(tag, Domain.COMPUTER, "c")
    persist_content(db, source_def, [])
    source_row = db.scalar(select(Source).where(Source.name == source_def.name))
    db.add(
        SubjectExternalIdentity(
            subject_id=story.id,
            source_id=source_row.id,
            external_id=f"9{tag[:6]}",
            normalized_name="story",
            resolution_strategy="new_subject",
            confidence=1.0,
        )
    )
    db.flush()
    gaming = persist_content(
        db, definition(tag), [incoming(f"Orbit Forge {tag} gets a release date")]
    )
    computer = persist_content(
        db,
        source_def,
        [incoming(f"Story {tag}", metadata={"source_article_id": f"9{tag[:6]}"})],
    )
    assert gaming["link_count"] == 1 and computer["link_count"] == 1
    (gaming_id,), (computer_id,) = gaming["content_ids"], computer["content_ids"]
    assert links_of(db, gaming_id) == {(game.id, "exact_name", "mentions")}
    assert links_of(db, computer_id) == {(story.id, "same_article", "same_article")}
    # Linking the same items again adds nothing.
    assert link_content_items(db, [gaming_id, computer_id])["links"] == 0


def test_linking_failure_keeps_content(db, tag, monkeypatch):
    subject(db, f"Orbit Forge {tag}")

    def broken(session, content_ids):
        session.execute(select(func.count()).select_from(ContentItem))
        raise RuntimeError("linking broke")

    monkeypatch.setattr(content_ingestion, "link_content_items", broken)
    result = persist_content(db, definition(tag), [incoming(f"Orbit Forge {tag} patch")])
    assert result["persisted_count"] == 1 and result["link_error"] == "RuntimeError"
    (content_id,) = result["content_ids"]
    assert db.get(ContentItem, content_id) is not None and links_of(db, content_id) == set()


def test_relink_is_idempotent_reconciles_and_dry_run_writes_nothing(db, tag):
    game = subject(db, f"Orbit Forge {tag}")
    unrelated = subject(db, f"Other Game {tag}")
    result = persist_content(db, definition(tag), [incoming(f"Orbit Forge {tag} update")])
    (content_id,) = result["content_ids"]
    # A stale link whose evidence no longer holds.
    db.add(
        ContentSubjectLink(
            content_item_id=content_id,
            subject_id=unrelated.id,
            strategy="exact_alias",
            confidence=1,
        )
    )
    db.flush()
    preview = relink_all_content(db, dry_run=True, content_ids=[content_id])
    assert (preview["added"], preview["removed"], preview["unchanged"]) == (0, 1, 1)
    assert preview["links_detail"][0]["evidence"] == {"name": f"Orbit Forge {tag}"}
    assert len(links_of(db, content_id)) == 2
    applied = relink_all_content(db, content_ids=[content_id])
    assert applied["removed"] == 1 and links_of(db, content_id) == {
        (game.id, "exact_name", "mentions")
    }
    again = relink_all_content(db, content_ids=[content_id])
    assert (again["added"], again["removed"], again["unchanged"]) == (0, 0, 1)
    assert count(db, ContentSubjectLink, content_item_id=content_id) == 1


def test_redirected_subject_is_not_a_link_target(db, tag):
    canonical = subject(db, f"Orbit Forge {tag}")
    duplicate = subject(db, f"Orbit Forge Classic {tag}")
    db.add(SubjectRedirect(duplicate_id=duplicate.id, canonical_id=canonical.id))
    db.flush()
    result = persist_content(db, definition(tag), [incoming(f"Orbit Forge Classic {tag} remaster")])
    (content_id,) = result["content_ids"]
    # The merged-away Subject is never a target; the canonical name does not appear verbatim.
    assert links_of(db, content_id) == set()
    control = persist_content(db, definition(tag), [incoming(f"Orbit Forge {tag} remaster")])
    assert links_of(db, control["content_ids"][0]) == {(canonical.id, "exact_name", "mentions")}


def test_ambiguous_and_generic_names_do_not_link(db, tag):
    subject(db, f"Twin Star {tag}")
    subject(db, f"Twin Star {tag}")
    subject(db, "Stellar")
    result = persist_content(
        db, definition(tag), [incoming(f"Twin Star {tag} has a stellar Stellar launch")]
    )
    assert result["link_count"] == 0


# --- Event entity propagation ---------------------------------------------------------------


def test_links_propagate_to_event_subjects_and_companies(db, tag):
    source_a, source_b = definition(tag, suffix="a"), definition(tag, suffix="b")
    game = subject(db, f"Orbit Forge {tag}")
    other = subject(db, f"Nova Drift {tag}")
    publisher = subject(db, f"Forge Works {tag}", kind=SubjectType.COMPANY)
    company = resolve_company(db, f"Forge Works {tag} Holdings", (), "test").company
    db.add(CompanySubjectLink(company_id=company.id, subject_id=publisher.id, strategy="synthetic"))
    headline = f"Forge Works {tag} delays Orbit Forge {tag} to 2033"
    first = persist_content(db, source_a, [incoming(headline)])
    second = persist_content(db, source_b, [incoming(headline, at=T0 + timedelta(minutes=20))])
    lonely = persist_content(db, source_a, [incoming(f"Nova Drift {tag} sells a million")])
    ids = first["content_ids"] + second["content_ids"] + lonely["content_ids"]
    build_events(db, content_ids=ids)
    shared = db.scalar(
        select(EventContentLink.event_id).where(
            EventContentLink.content_item_id == first["content_ids"][0]
        )
    )
    assert count(db, EventContentLink, event_id=shared) == 2
    subject_links = {
        link.subject_id: link.content_count
        for link in db.scalars(select(EventSubjectLink).where(EventSubjectLink.event_id == shared))
    }
    assert subject_links == {game.id: 2, publisher.id: 2}
    company_link = db.scalar(select(EventCompanyLink).where(EventCompanyLink.event_id == shared))
    assert company_link.company_id == company.id
    assert str(publisher.id) in company_link.evidence["subject_ids"]
    solo = db.scalar(
        select(EventContentLink.event_id).where(
            EventContentLink.content_item_id == lonely["content_ids"][0]
        )
    )
    assert {
        link.subject_id
        for link in db.scalars(select(EventSubjectLink).where(EventSubjectLink.event_id == solo))
    } == {other.id}
    assert count(db, EventCompanyLink, event_id=solo) == 0


# --- orchestration --------------------------------------------------------------------------


def _fake_fetch(tag, failing):
    async def fetch(key, settings):
        if key in failing:
            raise ConnectionError("source down")
        return definition(tag, suffix=key), [incoming(f"Orbit Forge {tag} news from {key}")], 1

    return fetch


def _scoped_build(tag):
    def build(session, *args, **kwargs):
        ids = session.scalars(
            select(ContentItem.id).join(Source).where(Source.name.like(f"Synthetic news {tag}%"))
        ).all()
        return build_events(session, content_ids=ids)

    return build


def test_orchestration_isolates_failing_source_and_builds_events(db, factory, tag, monkeypatch):
    subject(db, f"Orbit Forge {tag}")
    db.commit()
    monkeypatch.setattr(content_ingestion, "fetch_content", _fake_fetch(tag, {"down"}))
    monkeypatch.setattr(content_pipeline, "build_events", _scoped_build(tag))
    result = asyncio.run(
        content_pipeline.update_all_content(get_settings(), factory, ["up", "down"])
    )
    up, down = result["sources"]
    assert down == {"source": "down", "error": "ConnectionError"}
    assert up["persisted_count"] == 1 and up["link_count"] == 1 and "content_ids" not in up
    assert result["totals"] == {
        "fetched_count": 1,
        "persisted_count": 1,
        "replayed_count": 0,
        "invalid_count": 0,
        "failed_count": 1,
        "link_count": 1,
        "failed_sources": 1,
    }
    assert result["by_domain"]["gaming"]["persisted_count"] == 1
    assert result["events"]["processed"] == 1 and result["events"]["subject_links"] == 1


def test_event_build_failure_keeps_content(db, factory, tag, monkeypatch):
    monkeypatch.setattr(content_ingestion, "fetch_content", _fake_fetch(tag, set()))

    def broken(session, *args, **kwargs):
        raise RuntimeError("resolver broke")

    monkeypatch.setattr(content_pipeline, "build_events", broken)
    result = asyncio.run(content_pipeline.update_all_content(get_settings(), factory, ["up"]))
    assert result["events"] == {"error": "RuntimeError"}
    source_row = db.scalar(select(Source).where(Source.name == f"Synthetic news {tag} up"))
    assert count(db, ContentItem, source_id=source_row.id) == 1


# --- bounded Event rebuild ------------------------------------------------------------------


def _corpus(db, tag):
    """Two sources report one deal (one 2-item Event); one straddling Event; one far Event."""

    a, b = definition(tag, Domain.FINANCE, "a"), definition(tag, Domain.FINANCE, "b")
    headline = f"{tag}股份：拟11.5亿元收购湖北{tag}45%股权"
    ids = {
        "deal_a": persist_content(db, a, [incoming(headline)])["content_ids"][0],
        "deal_b": persist_content(
            db, b, [incoming(f"【{headline}】公告称。", at=T0 + timedelta(minutes=5))]
        )["content_ids"][0],
        "edge_a": persist_content(
            db, a, [incoming(f"{tag}集团：董事长辞职", at=T0 + timedelta(hours=3))]
        )["content_ids"][0],
        "edge_b": persist_content(
            db, b, [incoming(f"{tag}集团：董事长辞职", at=T0 + timedelta(hours=20))]
        )["content_ids"][0],
        "far": persist_content(db, a, [incoming(f"{tag}远期新闻", at=T0 + timedelta(days=10))])[
            "content_ids"
        ][0],
    }
    return ids


def _event_of(db, content_id):
    return db.scalar(
        select(EventContentLink.event_id).where(EventContentLink.content_item_id == content_id)
    )


def test_rebuild_window_membership_new_ids_and_outside_untouched(db, tag):
    ids = _corpus(db, tag)
    build_events(db, content_ids=list(ids.values()))
    before = {key: _event_of(db, cid) for key, cid in ids.items()}
    assert before["deal_a"] == before["deal_b"] and before["edge_a"] == before["edge_b"]
    # The window holds deal_* and edge_a only; the edge Event straddles it and is rebuilt whole.
    result = rebuild_events(db, T0 - timedelta(hours=1), T0 + timedelta(hours=4))
    assert result["content_items_in_window"] == 3 and result["content_items"] == 4
    assert (result["old_events"], result["new_events"]) == (2, 2)
    assert (result["unchanged_clusters"], result["dissolved_clusters"]) == (2, 0)
    after = {key: _event_of(db, cid) for key, cid in ids.items()}
    assert after["deal_a"] == after["deal_b"] != before["deal_a"]
    assert after["edge_a"] == after["edge_b"] != before["edge_a"]
    assert after["far"] == before["far"]
    assert db.get(Event, before["deal_a"]) is None
    retired = db.get(EventRetiredId, before["deal_a"])
    assert str(retired.representative_content_id) == ids["deal_a"]
    assert retired.content_item_ids[0] == str(ids["deal_a"])
    assert event_detail(db, before["deal_a"])["id"] == str(after["deal_a"])
    assert event_detail(db, before["deal_a"])["redirected"] is True
    assert event_content(db, before["deal_a"])["event_id"] == str(after["deal_a"])
    assert event_ai(db, before["deal_a"], "en")["event_id"] == str(after["deal_a"])
    assert result["content_range"]["last"] == (T0 + timedelta(hours=20)).isoformat()
    again = rebuild_events(db, T0 - timedelta(hours=1), T0 + timedelta(hours=4))
    assert again["partition"] == result["partition"]
    latest = _event_of(db, ids["deal_a"])
    assert event_detail(db, before["deal_a"])["id"] == str(latest)
    assert event_detail(db, after["deal_a"])["id"] == str(latest)


def test_rebuild_dry_run_writes_nothing_and_processes_unbuilt_content(db, tag):
    ids = _corpus(db, tag)
    build_events(db, content_ids=[ids["deal_a"]])
    event_before = _event_of(db, ids["deal_a"])
    nested = db.begin_nested()
    preview = rebuild_events(db, T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    assert preview["previously_unprocessed"] == 1 and preview["new_multi_content_events"] == 1
    nested.rollback()
    assert _event_of(db, ids["deal_a"]) == event_before and _event_of(db, ids["deal_b"]) is None
    assert db.get(EventRetiredId, event_before) is None


def test_retirement_is_idempotent_and_refuses_anchor_overwrite(db, tag):
    ids = _corpus(db, tag)
    build_events(db, content_ids=[ids["deal_a"]])
    event = db.get(Event, _event_of(db, ids["deal_a"]))
    assert retire_event(db, event) is retire_event(db, event)
    db.flush()
    assert db.scalar(select(func.count()).select_from(EventRetiredId)) >= 1
    db.get(EventRetiredId, event.id).representative_content_id = ids["deal_b"]
    with pytest.raises(EventRetirementInvariantError, match="conflicting content anchors"):
        retire_event(db, event)


def test_rebuild_failure_rolls_back_retirement_with_transaction(db, tag, monkeypatch):
    ids = _corpus(db, tag)
    build_events(db, content_ids=[ids["deal_a"]])
    old_id = _event_of(db, ids["deal_a"])

    def fail_build(*_args, **_kwargs):
        raise RuntimeError("synthetic rebuild failure")

    nested = db.begin_nested()
    monkeypatch.setattr("industry_intelligence.services.event_rebuild.build_events", fail_build)
    with pytest.raises(RuntimeError, match="synthetic rebuild failure"):
        rebuild_events(db, T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    nested.rollback()
    assert db.get(EventRetiredId, old_id) is None
    assert db.get(Event, old_id) is not None


def test_rebuild_refuses_reviewed_merges_and_invalid_ranges(db, tag):
    ids = _corpus(db, tag)
    build_events(db, content_ids=list(ids.values()))
    with pytest.raises(EventRebuildRefused):
        rebuild_events(db, T0, T0)
    with pytest.raises(EventRebuildRefused):
        rebuild_events(db, datetime(2032, 5, 4), datetime(2032, 5, 5))
    assert (
        rebuild_events(db, T0 - timedelta(days=400), T0 - timedelta(days=399))["content_items"] == 0
    )
    extra = persist_content(
        db,
        definition(tag, Domain.FINANCE, "c"),
        [incoming(f"{tag}市场综述", at=T0 + timedelta(minutes=30))],
    )["content_ids"]
    build_events(db, content_ids=extra)
    merge_events(db, _event_of(db, ids["deal_a"]), _event_of(db, extra[0]), "reviewed test")
    with pytest.raises(EventRebuildRefused, match="reviewed-merged"):
        rebuild_events(db, T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    # A window without the merged Events is still allowed.
    assert rebuild_events(db, T0 + timedelta(days=9), T0 + timedelta(days=11))["new_events"] == 1
