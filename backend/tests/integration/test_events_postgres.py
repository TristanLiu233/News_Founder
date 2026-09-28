"""Event building, links, read models, merges, and API inside rolled-back transactions.

Every source, subject, company, and content item here is synthetic, carries a random tag, and is
published in 2031, far outside the live corpus and the 24-hour matching window, so live content
never becomes a candidate and live Events never change. ``build_events`` is always scoped to the
test's own content IDs; the ``db`` fixture rolls everything back.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    CompanySubjectLink,
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
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.main import create_app
from industry_intelligence.services.company_resolution import resolve_company
from industry_intelligence.services.event_ingestion import build_events, relink_all_events
from industry_intelligence.services.event_merge import EventMergeRefused, merge_events
from industry_intelligence.services.event_resolution import RESOLVER_VERSION
from industry_intelligence.services.events import event_content, event_detail, list_events

pytestmark = pytest.mark.integration

T0 = datetime(2031, 3, 1, 9, tzinfo=UTC)
WINDOW = {"from_": T0 - timedelta(days=2), "to": T0 + timedelta(days=30)}


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


@pytest.fixture
def tag():
    return uuid4().hex[:8]


@pytest.fixture
def sources(db, tag):
    rows = []
    for index, language in enumerate(("zh-CN", "zh-CN", "en")):
        row = Source(
            name=f"Synthetic event source {tag} {index}",
            domain=Domain.FINANCE,
            source_type="test",
            base_url="https://example.test",
            primary_language=language,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def content(db, source, title, at=T0, language="zh-CN", domain=Domain.FINANCE, **kwargs):
    item = ContentItem(
        source_id=source.id,
        domain=domain,
        content_type="feed",
        identity_key=str(uuid4()),
        title=title,
        url=f"https://news.example.test/{uuid4()}",
        published_at=kwargs.get("published_at", at),
        observed_at=kwargs.get("observed_at", at + timedelta(minutes=30)),
        language=language,
        description=kwargs.get("description"),
        item_metadata={},
        content_hash=str(uuid4()),
    )
    db.add(item)
    db.flush()
    return item


def subject(db, title):
    row = Subject(
        domain=Domain.FINANCE,
        subject_type=SubjectType.COMPANY,
        canonical_name=str(uuid4()),
        title=title,
    )
    db.add(row)
    db.flush()
    return row


def link_subject(db, item, subject_row):
    db.add(
        ContentSubjectLink(
            content_item_id=item.id,
            subject_id=subject_row.id,
            strategy="synthetic",
            confidence=1.0,
        )
    )
    db.flush()


def build(db, *items):
    return build_events(db, content_ids=[item.id for item in items])


def event_of(db, item):
    link = db.scalar(select(EventContentLink).where(EventContentLink.content_item_id == item.id))
    return db.get(Event, link.event_id), link


def count(db, model, **filters):
    query = select(func.count()).select_from(model)
    for key, value in filters.items():
        query = query.where(getattr(model, key) == value)
    return db.scalar(query)


# --- building ------------------------------------------------------------------------------


def test_single_content_becomes_one_event_with_publication_time_basis(db, sources, tag):
    item = content(
        db,
        sources[1],
        f"【{tag}股份：拟回购公司股份】{tag}股份公告称，公司拟以自有资金回购股份。",
    )
    result = build(db, item)
    event, link = event_of(db, item)
    assert result["processed"] == 1 and result["created_events"] == 1
    assert event.canonical_title == f"{tag}股份：拟回购公司股份"
    assert event.content_count == 1 and event.source_count == 1
    assert event.first_published_at == event.last_published_at == T0
    assert event.first_observed_at == T0 + timedelta(minutes=30)
    assert event.representative_content_id == item.id
    assert event.title_language == "zh-CN" and event.domain == Domain.FINANCE
    assert link.strategy == "seed" and link.confidence == "origin"
    assert link.evidence["reason"] == "no_match"
    assert link.evidence["resolver_version"] == RESOLVER_VERSION


def test_two_sources_reporting_the_same_event_share_one_event(db, sources, tag):
    first = content(db, sources[0], f"{tag}建工：副董事长代行董事长职责")
    second = content(
        db,
        sources[1],
        f"【{tag}建工：副董事长代行董事长职责】公司董事会收到董事长的书面辞职报告。",
        T0 + timedelta(minutes=8),
    )
    result = build(db, second, first)
    event, first_link = event_of(db, first)
    same, second_link = event_of(db, second)
    assert result["created_events"] == 1 and result["joined_existing"] == 1
    assert event.id == same.id
    assert event.content_count == 2 and event.source_count == 2
    assert event.event_type == "leadership"
    assert event.canonical_title == f"{tag}建工：副董事长代行董事长职责"
    assert event.representative_content_id == first.id
    assert (event.first_published_at, event.last_published_at) == (
        T0,
        T0 + timedelta(minutes=8),
    )
    assert first_link.strategy == "seed"
    assert second_link.strategy == "exact_headline" and second_link.confidence == "exact"
    assert second_link.evidence["matched_content_item_id"] == str(first.id)
    assert second_link.evidence["time_delta_seconds"] == 480


def test_unrelated_content_in_the_same_window_stays_separate(db, sources, tag):
    a = content(db, sources[0], f"自然资源部{tag}联合发布黄色地质灾害气象风险预警")
    b = content(
        db, sources[1], f"水利部{tag}联合发布黄色山洪灾害气象预警", T0 + timedelta(minutes=5)
    )
    c = content(db, sources[1], f"{tag}ETF易方达：因交易价格溢价将停牌")
    d = content(db, sources[0], f"{tag}ETF嘉实：因交易价格溢价将停牌")
    result = build(db, a, b)
    assert build(db, c, d)["created_events"] == 2
    assert result["created_events"] == 2
    assert len({event_of(db, item)[0].id for item in (a, b, c, d)}) == 4
    evidence = event_of(db, b)[1].evidence
    assert evidence["reason"] == "no_match"
    assert evidence["nearest"]["reason"] == "no_identity_evidence"


def test_replay_creates_no_duplicate_events_or_links(db, sources, tag):
    items = [
        content(db, sources[0], f"{tag}追加4.2亿元购车补贴"),
        content(db, sources[1], f"【{tag}追加4.2亿元购车补贴】据报道，近日追加资金。"),
    ]
    build(db, *items)
    before = (count(db, Event), count(db, EventContentLink))
    replay = build(db, *items)
    assert replay["processed"] == 0 and replay["created_events"] == 0
    assert (count(db, Event), count(db, EventContentLink)) == before


def test_incremental_content_joins_an_existing_event_and_title_stays_stable(db, sources, tag):
    first = content(db, sources[0], f"尼泊尔{tag}发生雪崩致多人失联")
    build(db, first)
    event, _ = event_of(db, first)
    later = content(
        db,
        sources[1],
        f"【尼泊尔{tag}发生雪崩致多人失联】据当地登山探险公司消息，数名向导失联。",
        T0 + timedelta(hours=2),
    )
    result = build(db, later)
    assert result["created_events"] == 0 and result["joined_existing"] == 1
    db.refresh(event)
    assert event_of(db, later)[0].id == event.id
    assert event.canonical_title == f"尼泊尔{tag}发生雪崩致多人失联"
    assert event.last_published_at == T0 + timedelta(hours=2)


def test_canonical_title_prefers_a_headline_without_feed_boilerplate(db, sources, tag):
    early = content(db, sources[1], f"市场消息：{tag}重启收购Campus Living Villages的谈判。")
    clean = content(
        db, sources[0], f"{tag}重启收购Campus Living Villages的谈判", T0 + timedelta(minutes=9)
    )
    build(db, early, clean)
    event, _ = event_of(db, early)
    assert event_of(db, clean)[0].id == event.id
    assert event.canonical_title == f"{tag}重启收购Campus Living Villages的谈判"
    assert event.representative_content_id == clean.id
    assert event.first_published_at == T0


def test_content_outside_the_window_starts_a_new_event(db, sources, tag):
    first = content(db, sources[0], f"{tag}追加4.2亿元购车补贴")
    late = content(db, sources[1], f"{tag}追加4.2亿元购车补贴", T0 + timedelta(days=2))
    build(db, first, late)
    assert event_of(db, first)[0].id != event_of(db, late)[0].id


def test_content_matching_two_events_is_not_guessed_into_either(db, sources, tag):
    # Two same-source items with one issuer prefix stay separate (same source, not exact).
    one = content(db, sources[0], f"{tag}股份：拟减持不超100万股公司股份")
    two = content(db, sources[0], f"{tag}股份：拟减持不超100万股股份计划")
    candidate = content(
        db, sources[1], f"{tag}股份：拟减持不超100万股公司股份计划", T0 + timedelta(minutes=3)
    )
    result = build(db, one, two, candidate)
    events = {event_of(db, item)[0].id for item in (one, two, candidate)}
    assert len(events) == 3 and result["created_events"] == 3
    evidence = event_of(db, candidate)[1].evidence
    assert evidence["reason"] == "ambiguous"
    assert sorted(evidence["ambiguous_event_ids"]) == sorted(
        str(event_of(db, item)[0].id) for item in (one, two)
    )


def test_since_and_limit_bound_a_run(db, sources, tag):
    old = content(db, sources[0], f"{tag} old item one", T0 - timedelta(days=5), "en")
    new = [
        content(db, sources[2], f"{tag} new item {i}", T0 + timedelta(minutes=i), "en")
        for i in range(3)
    ]
    ids = [old.id, *(item.id for item in new)]
    result = build_events(db, since=T0 - timedelta(days=1), limit=2, content_ids=ids)
    assert result["processed"] == 2
    processed = set(db.scalars(select(EventContentLink.content_item_id)))
    assert new[0].id in processed and new[1].id in processed
    assert old.id not in processed and new[2].id not in processed


# --- Subjects and Companies ----------------------------------------------------------------


def test_event_subjects_and_companies_come_from_content_links(db, sources, tag):
    stock = subject(db, f"Acme{tag}")
    company = resolve_company(db, f"Acme {tag} Holdings", (), "test").company
    db.add(CompanySubjectLink(company_id=company.id, subject_id=stock.id, strategy="synthetic"))
    first = content(
        db, sources[2], f"Acme{tag} recalls warehouse robots after safety review", language="en"
    )
    second = content(
        db,
        sources[0],
        f"Acme{tag} recalls warehouse robots after a safety review",
        T0 + timedelta(hours=1),
        language="en",
    )
    for item in (first, second):
        link_subject(db, item, stock)
    build(db, first, second)
    event, link = event_of(db, second)
    assert link.strategy == "shared_entity_headline" and link.confidence == "strong"
    assert link.evidence["shared_subject_ids"] == [str(stock.id)]
    assert link.evidence["shared_company_ids"] == [str(company.id)]
    subject_link = db.scalar(select(EventSubjectLink).where(EventSubjectLink.event_id == event.id))
    assert subject_link.subject_id == stock.id and subject_link.content_count == 2
    assert subject_link.strategy == "content_subject_link"
    company_link = db.scalar(select(EventCompanyLink).where(EventCompanyLink.event_id == event.id))
    assert company_link.company_id == company.id and company_link.content_count == 2
    assert company_link.evidence["subject_ids"] == [str(stock.id)]


def test_same_company_different_event_is_not_merged(db, sources, tag):
    stock = subject(db, f"Beta{tag}")
    launch = content(db, sources[2], f"Beta{tag} launches new phone lineup", language="en")
    results = content(
        db,
        sources[0],
        f"Beta{tag} reports earnings for the third quarter",
        T0 + timedelta(hours=3),
        language="en",
    )
    for item in (launch, results):
        link_subject(db, item, stock)
    build(db, launch, results)
    assert event_of(db, launch)[0].id != event_of(db, results)[0].id
    nearest = event_of(db, results)[1].evidence["nearest"]
    assert nearest["reason"] == "conflict:event_type"


def test_event_without_entity_links_has_empty_relations(db, sources, tag):
    item = content(db, sources[0], f"印度{tag}北方邦暴雨已致56人死亡")
    build(db, item)
    event, _ = event_of(db, item)
    detail = event_detail(db, event.id)
    assert detail["subjects"] == [] and detail["companies"] == []
    assert detail["subject_count"] == detail["company_count"] == 0


def test_merged_subject_links_resolve_to_the_canonical_subject(db, sources, tag):
    canonical, duplicate = subject(db, f"Gamma{tag}"), subject(db, f"Gamma{tag} dup")
    db.add(SubjectRedirect(duplicate_id=duplicate.id, canonical_id=canonical.id))
    item = content(db, sources[0], f"Gamma{tag} opens new plant", language="en")
    link_subject(db, item, duplicate)
    build(db, item)
    event, _ = event_of(db, item)
    assert (
        db.scalar(select(EventSubjectLink.subject_id).where(EventSubjectLink.event_id == event.id))
        == canonical.id
    )


def test_relink_rebuilds_event_entities_after_content_links_change(db, sources, tag):
    item = content(db, sources[0], f"Delta{tag} opens new plant", language="en")
    build(db, item)
    event, _ = event_of(db, item)
    assert count(db, EventSubjectLink, event_id=event.id) == 0
    link_subject(db, item, stock := subject(db, f"Delta{tag}"))
    result = relink_all_events(db)
    assert result["events"] >= 1
    assert (
        db.scalar(select(EventSubjectLink.subject_id).where(EventSubjectLink.event_id == event.id))
        == stock.id
    )


# --- read models ---------------------------------------------------------------------------


def _three_events(db, sources, tag):
    stock = subject(db, f"Epsilon{tag}")
    company = resolve_company(db, f"Epsilon {tag} Group", (), "test").company
    db.add(CompanySubjectLink(company_id=company.id, subject_id=stock.id, strategy="synthetic"))
    deal = content(db, sources[0], f"{tag}股份：拟11.5亿元收购湖北{tag}45%股权", T0)
    deal_2 = content(
        db,
        sources[1],
        f"【{tag}股份：拟11.5亿元收购湖北{tag}45%股权】公告称。",
        T0 + timedelta(minutes=5),
    )
    for item in (deal, deal_2):
        link_subject(db, item, stock)
    other = content(db, sources[0], f"{tag}数贸会今天闭幕 超5万名客商到会", T0 + timedelta(days=1))
    english = content(
        db, sources[2], f"{tag} appeals court upholds designation", T0 + timedelta(days=2), "en"
    )
    build(db, deal, deal_2, other, english)
    return stock, company, [event_of(db, item)[0] for item in (deal, other, english)]


def test_list_events_orders_recent_first_and_filters(db, sources, tag):
    stock, company, (deal, other, english) = _three_events(db, sources, tag)
    page = list_events(db, **WINDOW)
    ids = [item["id"] for item in page["items"]]
    assert ids[:3] == [str(english.id), str(other.id), str(deal.id)]
    assert page["total"] == 3
    first = page["items"][2]
    assert first["content_count"] == 2 and first["source_count"] == 2
    assert first["event_type"] == "acquisition"
    assert first["time"]["basis"] == "content_publication"
    assert first["subjects"][0]["id"] == str(stock.id)
    assert first["companies"][0]["id"] == str(company.id)
    assert {s["name"] for s in first["sources"]} == {sources[0].name, sources[1].name}
    assert first["languages"] == [{"language": "zh-CN", "content_count": 2}]

    assert [i["id"] for i in list_events(db, subject_id=stock.id)["items"]] == [str(deal.id)]
    assert [i["id"] for i in list_events(db, company_id=company.id)["items"]] == [str(deal.id)]
    assert list_events(db, subject_id=uuid4())["total"] == 0
    typed = list_events(db, event_type="acquisition", **WINDOW)
    assert [i["id"] for i in typed["items"]] == [str(deal.id)]
    ranged = list_events(db, from_=T0 + timedelta(hours=12), to=T0 + timedelta(days=1, hours=1))
    assert [i["id"] for i in ranged["items"]] == [str(other.id)]
    assert list_events(db, domain="gaming", **WINDOW)["total"] == 0
    paged = list_events(db, limit=1, offset=1, **WINDOW)
    assert [i["id"] for i in paged["items"]] == [str(other.id)] and paged["total"] == 3


def test_event_detail_and_content_timeline(db, sources, tag):
    _, _, (deal, _, _) = _three_events(db, sources, tag)
    detail = event_detail(db, deal.id)
    assert detail["id"] == str(deal.id) and detail["redirected"] is False
    assert detail["content_count"] == 2 and len(detail["content_preview"]) == 2
    assert detail["content_preview"][0]["link_strategy"] == "seed"
    assert detail["time"]["first_published_at"] == T0.isoformat()
    timeline = event_content(db, deal.id, limit=1, offset=0)
    assert timeline["total"] == 2 and len(timeline["items"]) == 1
    assert timeline["items"][0]["published_at"] == T0.isoformat()
    newest = event_content(db, deal.id, limit=1, offset=0, order="desc")
    assert newest["items"][0]["link_strategy"] == "exact_headline"
    assert event_detail(db, uuid4()) is None and event_content(db, uuid4()) is None


# --- reviewed merge ------------------------------------------------------------------------


def test_reviewed_merge_moves_content_and_redirects(db, sources, tag):
    a = content(db, sources[0], f"{tag}伊斯兰堡谅解备忘录谈判重启")
    b = content(db, sources[1], f"{tag}美伊应回归伊斯兰堡谅解备忘录", T0 + timedelta(hours=1))
    build(db, a, b)
    canonical, duplicate = event_of(db, a)[0], event_of(db, b)[0]
    assert canonical.id != duplicate.id
    result = merge_events(db, canonical.id, duplicate.id, "Reviewed: same memorandum story")
    assert result["moved_content_links"] == 1
    db.refresh(canonical)
    db.refresh(duplicate)
    assert canonical.content_count == 2 and canonical.source_count == 2
    assert duplicate.content_count == 0 and duplicate.representative_content_id is None
    redirect = db.get(EventRedirect, duplicate.id)
    assert redirect.canonical_id == canonical.id
    detail = event_detail(db, duplicate.id)
    assert detail["id"] == str(canonical.id) and detail["redirected"] is True
    assert str(duplicate.id) not in [i["id"] for i in list_events(db, **WINDOW)["items"]]
    moved = event_of(db, b)[1]
    assert moved.evidence["reviewed_merge"]["from_event_id"] == str(duplicate.id)
    with pytest.raises(EventMergeRefused, match="already merged"):
        merge_events(db, canonical.id, duplicate.id, "again")
    with pytest.raises(EventMergeRefused, match="cycle"):
        merge_events(db, duplicate.id, canonical.id, "reverse")


def test_reviewed_merge_safeguards(db, sources, tag):
    deal = content(db, sources[0], f"{tag}股份：拟收购湖北资产")
    exit_ = content(db, sources[1], f"{tag}集团：董事长辞职", T0 + timedelta(minutes=1))
    far = content(db, sources[0], f"{tag}数贸会闭幕", T0 + timedelta(days=9))
    game = content(
        db, sources[2], f"{tag} studio ships sequel", language="en", domain=Domain.GAMING
    )
    build(db, deal, exit_, far, game)
    e_deal, e_exit, e_far, e_game = (event_of(db, i)[0] for i in (deal, exit_, far, game))
    with pytest.raises(EventMergeRefused, match="reason"):
        merge_events(db, e_deal.id, e_exit.id, "  ")
    with pytest.raises(EventMergeRefused, match="itself"):
        merge_events(db, e_deal.id, e_deal.id, "same")
    with pytest.raises(EventMergeRefused, match="Event types"):
        merge_events(db, e_deal.id, e_exit.id, "types differ")
    with pytest.raises(EventMergeRefused, match="domains"):
        merge_events(db, e_deal.id, e_game.id, "domains differ")
    with pytest.raises(EventMergeRefused, match="apart"):
        merge_events(db, e_deal.id, e_far.id, "too far apart")
    with pytest.raises(EventMergeRefused, match="exist"):
        merge_events(db, e_deal.id, uuid4(), "missing")
    assert count(db, EventRedirect, canonical_id=e_deal.id) == 0


def test_reviewed_merge_refuses_distinct_subjects(db, sources, tag):
    a = content(db, sources[0], f"Zeta{tag} opens plant", language="en")
    b = content(db, sources[1], f"Eta{tag} opens plant", language="en")
    link_subject(db, a, subject(db, f"Zeta{tag}"))
    link_subject(db, b, subject(db, f"Eta{tag}"))
    build(db, a, b)
    with pytest.raises(EventMergeRefused, match="Subjects"):
        merge_events(db, event_of(db, a)[0].id, event_of(db, b)[0].id, "different firms")


# --- API -----------------------------------------------------------------------------------


def test_events_api_list_detail_content_and_redirect(client, db, sources, tag):
    stock, company, (deal, other, _) = _three_events(db, sources, tag)
    params = {"from": WINDOW["from_"].isoformat(), "to": WINDOW["to"].isoformat()}
    listing = client.get("/api/v1/events", params={**params, "limit": 2}).json()
    assert listing["total"] == 3 and len(listing["items"]) == 2 and listing["limit"] == 2
    by_subject = client.get("/api/v1/events", params={"subject_id": str(stock.id)}).json()
    assert [i["id"] for i in by_subject["items"]] == [str(deal.id)]
    by_company = client.get("/api/v1/events", params={"company_id": str(company.id)}).json()
    assert [i["id"] for i in by_company["items"]] == [str(deal.id)]
    detail = client.get(f"/api/v1/events/{deal.id}").json()
    assert detail["canonical_title"] == deal.canonical_title
    assert detail["companies"][0]["display_names"]["en"] == company.canonical_name
    content_page = client.get(f"/api/v1/events/{deal.id}/content", params={"limit": 1}).json()
    assert content_page["total"] == 2 and len(content_page["items"]) == 1
    merge_events(db, deal.id, other.id, "API redirect check")
    redirected = client.get(f"/api/v1/events/{other.id}").json()
    assert redirected["id"] == str(deal.id) and redirected["requested_id"] == str(other.id)
    assert client.get(f"/api/v1/events/{uuid4()}").status_code == 404
    assert client.get(f"/api/v1/events/{uuid4()}/content").status_code == 404
    assert client.get("/api/v1/events/not-a-uuid").status_code == 422
