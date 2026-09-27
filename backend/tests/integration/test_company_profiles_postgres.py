"""Company profiles, identity, merges, subject links, and API inside rolled-back transactions.

Every company, identity, subject, and source here is synthetic and unique per test (names carry
a random tag; exchange codes use an unassigned 99xxxx range), so live data never matches or
changes. The ``db`` fixture rolls back everything, including the global relink.
"""

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from industry_intelligence.adapters.company_profile import ExternalCompanyProfile, LocalizedName
from industry_intelligence.adapters.company_profile.gleif import LEI_SCHEME
from industry_intelligence.adapters.company_profile.registry import PROFILE_SOURCES
from industry_intelligence.adapters.company_profile.sec_edgar import CIK_SCHEME
from industry_intelligence.adapters.company_ranking import (
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)
from industry_intelligence.adapters.company_ranking.companiesmarketcap import SYMBOL_SCHEME
from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    CompanyAlias,
    CompanyIdentity,
    CompanyProfile,
    CompanyProfileSource,
    CompanyRedirect,
    CompanySubjectLink,
    ContentItem,
    ContentSubjectLink,
    RankingEntry,
    Source,
    Subject,
    SubjectAlias,
)
from industry_intelligence.db.models.canonical import SubjectExternalIdentity
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.main import create_app
from industry_intelligence.services.company_merge import MergeRefused, merge_companies
from industry_intelligence.services.company_profile_ingestion import (
    find_target_company,
    persist_profile_source,
)
from industry_intelligence.services.company_profiles import company_detail, list_companies
from industry_intelligence.services.company_ranking_ingestion import persist_company_snapshot
from industry_intelligence.services.company_resolution import resolve_company
from industry_intelligence.services.company_subject_linking import relink_all_companies
from industry_intelligence.services.resolution import normalize_subject_name

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 27, 1, tzinfo=UTC)


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
    return uuid4().hex[:10]


def company(db, name, symbol=None):
    result = resolve_company(db, name, ((SYMBOL_SCHEME, symbol),) if symbol else (), "test")
    return result.company


def profile(source_key, facts, identities=(), names=(), retrieved_at=T0, published_at=None):
    return ExternalCompanyProfile(
        source_key=source_key,
        source_url=f"https://{source_key}.example.test/record",
        retrieved_at=retrieved_at,
        facts=facts,
        identities=tuple(identities),
        names=tuple(names),
        published_at=published_at,
        metadata={"synthetic": True},
    )


def persist(db, target, source_key, **kwargs):
    return persist_profile_source(
        db, target, PROFILE_SOURCES[source_key], profile(source_key, **kwargs)
    )


def full_profile(db, target, tag):
    persist(
        db,
        target,
        "official_website",
        facts={
            "website": f"https://{tag}.example.test/",
            "description": f"{tag} builds things.",
            "description_language": "en",
        },
        names=[LocalizedName(f"示例{tag}", "zh-CN", "localized_name")],
    )
    persist(
        db,
        target,
        "sec_edgar",
        facts={
            "industry": "Semiconductors & Related Devices",
            "headquarters": "SANTA CLARA, CA",
            "country": "US",
            "business_areas": ["Compute", "Graphics"],
            "key_products": ["Data Center"],
        },
        identities=[(CIK_SCHEME, f"C{tag}")],
        published_at=T0 - timedelta(days=30),
    )
    return persist(
        db,
        target,
        "gleif",
        facts={
            "legal_name": f"{tag.upper()} CORPORATION",
            "legal_name_locale": "en",
            "headquarters": "Santa Clara, US-CA",
            "country": "US",
            "jurisdiction": "US-DE",
        },
        identities=[(LEI_SCHEME, f"L{tag}")],
        names=[LocalizedName(f"{tag.upper()} CORPORATION", "en", "legal_name")],
    )


def count(db, model, **filters):
    query = select(func.count()).select_from(model)
    for key, value in filters.items():
        query = query.where(getattr(model, key) == value)
    return db.scalar(query)


# --- Profile ingestion ------------------------------------------------------------------------


def test_profile_merges_fields_by_priority_with_provenance(db, tag):
    target = company(db, f"{tag} Devices", f"S{tag}")
    result = full_profile(db, target, tag)
    assert result["status"] == "created"
    row = db.get(CompanyProfile, target.id)
    assert row.legal_name == f"{tag.upper()} CORPORATION"
    # GLEIF outranks SEC for the registered headquarters; the official site gives the website.
    assert row.headquarters == "Santa Clara, US-CA"
    assert row.website == f"https://{tag}.example.test/"
    assert row.business_areas == ["Compute", "Graphics"]
    assert row.field_sources == {
        "legal_name": "gleif",
        "website": "official_website",
        "description": "official_website",
        "headquarters": "gleif",
        "country": "gleif",
        "jurisdiction": "gleif",
        "industry": "sec_edgar",
        "business_areas": "sec_edgar",
        "key_products": "sec_edgar",
    }
    assert count(db, CompanyProfileSource, company_id=target.id) == 3
    identities = set(
        db.execute(
            select(CompanyIdentity.scheme, CompanyIdentity.value).where(
                CompanyIdentity.company_id == target.id
            )
        ).all()
    )
    assert {(CIK_SCHEME, f"C{tag}"), (LEI_SCHEME, f"L{tag}")} <= identities
    zh = db.scalar(
        select(CompanyAlias).where(
            CompanyAlias.company_id == target.id, CompanyAlias.alias_type == "localized_name"
        )
    )
    assert (zh.alias, zh.locale, zh.alias_source) == (f"示例{tag}", "zh-CN", "official_website")


def test_replayed_profile_only_refreshes_checked_at(db, tag):
    target = company(db, f"{tag} Replay", f"S{tag}")
    facts = {"industry": "Banks", "key_products": []}
    persist(db, target, "sec_edgar", facts=facts, identities=[(CIK_SCHEME, f"C{tag}")])
    before = db.get(CompanyProfile, target.id).profile_updated_at
    later = T0 + timedelta(days=1)
    again = persist_profile_source(
        db,
        target,
        PROFILE_SOURCES["sec_edgar"],
        profile("sec_edgar", facts, [(CIK_SCHEME, f"C{tag}")], retrieved_at=later),
    )
    row = db.scalar(
        select(CompanyProfileSource).where(CompanyProfileSource.company_id == target.id)
    )
    assert again["status"] == "replayed"
    assert (row.retrieved_at, row.checked_at) == (T0, later)
    assert db.get(CompanyProfile, target.id).profile_updated_at == before
    assert count(db, CompanyProfileSource, company_id=target.id) == 1
    assert count(db, CompanyIdentity, company_id=target.id) == 2


def test_changed_profile_updates_record_and_rebuilds(db, tag):
    target = company(db, f"{tag} Change", f"S{tag}")
    persist(db, target, "companiesmarketcap", facts={"categories": ["Banks"]})
    later = T0 + timedelta(days=2)
    result = persist_profile_source(
        db,
        target,
        PROFILE_SOURCES["companiesmarketcap"],
        profile("companiesmarketcap", {"categories": ["Banks", "Fintech"]}, retrieved_at=later),
    )
    assert result["status"] == "updated"
    row = db.get(CompanyProfile, target.id)
    assert row.categories == ["Banks", "Fintech"] and row.profile_updated_at == later


def test_missing_optional_fields_and_invalid_website_stay_empty(db, tag):
    target = company(db, f"{tag} Sparse", f"S{tag}")
    persist(
        db,
        target,
        "official_website",
        facts={"website": "javascript:alert(1)", "business_areas": [], "key_products": []},
    )
    row = db.get(CompanyProfile, target.id)
    assert row.website is None and row.description is None
    assert row.business_areas == [] and row.key_products == [] and row.field_sources == {}
    detail = company_detail(db, target.id)
    assert detail["profile"]["key_products"] == [] and detail["names"]["zh"] is None
    assert detail["display_names"] == {"en": target.canonical_name, "zh-CN": target.canonical_name}


def test_identity_held_by_another_company_is_refused_not_moved(db, tag):
    owner = company(db, f"{tag} Owner", f"O{tag}")
    other = company(db, f"{tag} Other", f"X{tag}")
    persist(db, owner, "gleif", facts={"legal_name": "Owner"}, identities=[(LEI_SCHEME, f"L{tag}")])
    result = persist(
        db, other, "gleif", facts={"legal_name": "Other"}, identities=[(LEI_SCHEME, f"L{tag}")]
    )
    assert result["identity_conflicts"] == [
        {"scheme": LEI_SCHEME, "value": f"L{tag}", "reason": "held_by_other"}
    ]
    holder = db.scalar(select(CompanyIdentity.company_id).where(CompanyIdentity.value == f"L{tag}"))
    assert holder == owner.id
    # A second value in an exclusive scheme is refused too.
    second = persist(db, owner, "sec_edgar", facts={}, identities=[(LEI_SCHEME, f"M{tag}")])
    assert second["identity_conflicts"][0]["reason"] == "exclusive_scheme"


def test_target_company_is_found_by_ranking_symbol_and_redirects(db, tag):
    assert find_target_company(db, f"missing{tag}") is None
    canonical = company(db, f"{tag} Canonical", f"A{tag}")
    duplicate = company(db, f"{tag} Duplicate", f"B{tag}")
    merge_companies(db, canonical.id, duplicate.id, "reviewed in test")
    assert find_target_company(db, f"B{tag}").id == canonical.id


# --- Identity resolution with profile names --------------------------------------------------


def test_chinese_alias_resolves_future_rows_and_ambiguity_is_not_merged(db, tag):
    first = company(db, f"{tag} First", f"F{tag}")
    persist(
        db,
        first,
        "official_website",
        facts={},
        names=[LocalizedName(f"甲{tag}", "zh-CN", "localized_name")],
    )
    again = resolve_company(db, f"甲{tag}", (), "test")
    assert (again.company.id, again.strategy) == (first.id, "exact_name")
    second = company(db, f"{tag} Second", f"G{tag}")
    persist(
        db,
        second,
        "official_website",
        facts={},
        names=[LocalizedName(f"甲{tag}", "zh-CN", "localized_name")],
    )
    ambiguous = resolve_company(db, f"甲{tag}", (), "test")
    assert ambiguous.strategy == "new_company"
    assert ambiguous.company.id not in {first.id, second.id}


def test_external_identity_wins_over_a_name_match(db, tag):
    named = company(db, f"{tag} Named", f"N{tag}")
    held = company(db, f"{tag} Held", f"H{tag}")
    result = resolve_company(db, f"{tag} Named", ((SYMBOL_SCHEME, f"H{tag}"),), "test")
    assert (result.company.id, result.strategy) == (held.id, "external_identity")
    assert result.company.id != named.id


def test_same_name_with_a_different_cik_or_lei_stays_separate(db, tag):
    existing = company(db, f"{tag} Same", f"S{tag}")
    persist(db, existing, "sec_edgar", facts={}, identities=[(CIK_SCHEME, f"C1{tag}")])
    for scheme, value in ((CIK_SCHEME, f"C2{tag}"), (LEI_SCHEME, f"L2{tag}")):
        persist(db, existing, "gleif", facts={}, identities=[(LEI_SCHEME, f"L1{tag}")])
        result = resolve_company(db, f"{tag} Same", ((scheme, value),), "test")
        if scheme == CIK_SCHEME:
            assert result.strategy == "new_company"
        else:
            assert result.company.id != existing.id


# --- Reviewed merge ---------------------------------------------------------------------------


def ranking_definition(tag):
    return replace(
        COMPANY_RANKINGS["cmc_tech_market_cap"],
        key=f"test_profile_{tag}",
        name=f"Test profile ranking {tag}",
        source_key=f"test_profile_source_{tag}",
    )


def rank(db, tag, rows, key="day1", observed_at=T0):
    snapshot = CompanyRankingSnapshot(
        snapshot_key=key,
        observed_at=observed_at,
        source_url="https://example.test/ranking",
        entries=tuple(
            CompanyRankingEntry(rank=i, name=name, identities=((SYMBOL_SCHEME, symbol),))
            for i, (name, symbol) in enumerate(rows, start=1)
        ),
    )
    return persist_company_snapshot(db, ranking_definition(tag), snapshot)


def test_merge_moves_everything_and_old_ids_redirect(db, tag):
    rank(db, tag, [(f"{tag} Tencent", f"T{tag}")])
    rank(db, tag, [(f"{tag} Tencent Holdings", f"H{tag}")], "day2", T0 + timedelta(days=1))
    canonical = find_target_company(db, f"T{tag}")
    duplicate = find_target_company(db, f"H{tag}")
    assert canonical.id != duplicate.id
    persist(db, canonical, "gleif", facts={"legal_name": "L"}, identities=[(LEI_SCHEME, f"L{tag}")])
    persist(db, duplicate, "gleif", facts={"legal_name": "Other"})
    persist(db, duplicate, "companiesmarketcap", facts={"categories": ["Internet"]})
    result = merge_companies(db, canonical.id, duplicate.id, "Same issuer: reviewed")
    assert result["moved_ranking_entries"] == 1
    assert count(db, RankingEntry, company_id=duplicate.id) == 0
    assert count(db, CompanyAlias, company_id=duplicate.id) == 0
    assert count(db, CompanyProfileSource, company_id=duplicate.id) == 0
    # The canonical company's own GLEIF record wins over the duplicate's.
    sources = dict(
        db.execute(
            select(CompanyProfileSource.source_key, CompanyProfileSource.facts).where(
                CompanyProfileSource.company_id == canonical.id
            )
        ).all()
    )
    assert sources["gleif"] == {"legal_name": "L"} and "companiesmarketcap" in sources
    assert db.get(CompanyProfile, canonical.id).categories == ["Internet"]
    assert db.get(CompanyRedirect, duplicate.id).canonical_id == canonical.id
    detail = company_detail(db, duplicate.id)
    assert detail["redirected"] and detail["id"] == str(canonical.id)
    assert [r["latest_rank"] for r in detail["current_rankings"]] == [1]
    # The duplicate's old name now resolves to the canonical company, never the duplicate row.
    again = resolve_company(db, f"{tag} Tencent Holdings", (), "test")
    assert again.company.id == canonical.id


@pytest.mark.parametrize("case", ["lei", "snapshot", "cycle", "self", "reason", "twice"])
def test_unsafe_or_invalid_merges_are_refused(db, tag, case):
    a = company(db, f"{tag} A", f"A{tag}")
    b = company(db, f"{tag} B", f"B{tag}")
    reason = "reviewed"
    if case == "lei":
        persist(db, a, "gleif", facts={}, identities=[(LEI_SCHEME, f"LA{tag}")])
        persist(db, b, "gleif", facts={}, identities=[(LEI_SCHEME, f"LB{tag}")])
    elif case == "snapshot":
        rank(db, tag, [(f"{tag} A", f"A{tag}"), (f"{tag} B", f"B{tag}")])
    elif case == "cycle":
        merge_companies(db, a.id, b.id, reason)
        a, b = b, a
    elif case == "self":
        b = a
    elif case == "reason":
        reason = "   "
    elif case == "twice":
        c = company(db, f"{tag} C", f"C{tag}")
        merge_companies(db, a.id, b.id, reason)
        a = c
    with pytest.raises(MergeRefused):
        merge_companies(db, a.id, b.id, reason)


# --- Company <-> Subject links ----------------------------------------------------------------


def subject(db, title, subject_type=SubjectType.STOCK, aliases=(), code=None, source=None):
    row = Subject(
        domain=Domain.FINANCE,
        subject_type=subject_type,
        canonical_name=str(uuid4()),
        title=title,
    )
    db.add(row)
    db.flush()
    for alias in (title, *aliases):
        db.add(
            SubjectAlias(
                subject_id=row.id,
                alias=alias,
                normalized_alias=normalize_subject_name(alias),
                locale="zh-CN",
                alias_type="source_name",
                alias_source="synthetic_test",
            )
        )
    if code:
        db.add(
            SubjectExternalIdentity(
                subject_id=row.id,
                source_id=source.id,
                external_id=code,
                canonical_id=code,
                normalized_name=normalize_subject_name(title),
                resolution_strategy="synthetic",
                confidence=1.0,
            )
        )
    db.flush()
    return row


def source_row(db, tag):
    row = Source(
        name=f"Synthetic source {tag}",
        domain=Domain.FINANCE,
        source_type="test",
        base_url="https://example.test",
        primary_language="zh-CN",
    )
    db.add(row)
    db.flush()
    return row


def links(db, company_id):
    return {
        (row.subject_id, row.strategy)
        for row in db.scalars(
            select(CompanySubjectLink).where(CompanySubjectLink.company_id == company_id)
        )
    }


def test_exchange_code_and_exact_name_links_are_deterministic(db, tag):
    code = f"99{random.randint(1000, 9999)}"
    src = source_row(db, tag)
    listed = company(db, f"{tag} Chips", f"{code}.SS")
    stock = subject(db, f"芯片{tag}", code=f"SH:{code}", source=src)
    named = company(db, f"{tag} Bank", f"N{tag}")
    persist(db, named, "gleif", facts={}, names=[LocalizedName(f"银行{tag}", "zh", "legal_name")])
    bank_stock = subject(db, f"银行{tag}")
    repo = company(db, f"{tag}-repo", f"R{tag}")
    subject(db, f"{tag}-repo", subject_type=SubjectType.REPOSITORY)
    lonely = company(db, f"{tag} Lonely", f"L{tag}")
    relink_all_companies(db)
    assert links(db, listed.id) == {(stock.id, "exchange_code")}
    assert links(db, named.id) == {(bank_stock.id, "exact_name")}
    assert links(db, repo.id) == set() and links(db, lonely.id) == set()
    detail = company_detail(db, listed.id)
    assert detail["related_subjects"][0]["evidence"] == {
        "company_symbol": f"{code}.SS",
        "subject_identity": f"SH:{code}",
    }
    # A linked A-share security name is a sourced Chinese display name.
    assert detail["names"] == {
        "en": f"{tag} Chips",
        "zh": f"芯片{tag}",
        "zh_basis": "linked_subject",
    }


def test_ambiguous_names_and_shared_subjects_are_not_linked(db, tag):
    first = company(db, f"{tag} One", f"A{tag}")
    second = company(db, f"{tag} Two", f"B{tag}")
    for target in (first, second):
        persist(
            db,
            target,
            "official_website",
            facts={},
            names=[LocalizedName(f"同名{tag}", "zh-CN", "localized_name")],
        )
    subject(db, f"同名{tag}")
    third = company(db, f"{tag} Three", f"C{tag}")
    subject(db, f"{tag} Three")
    subject(db, f"{tag} Three ", aliases=())  # normalizes to the same name: two subjects
    relink_all_companies(db)
    assert links(db, first.id) == links(db, second.id) == links(db, third.id) == set()


def test_links_resolve_redirected_subjects(db, tag):
    target = company(db, f"{tag} Redirected", f"S{tag}")
    duplicate = subject(db, f"{tag} Redirected")
    canonical = subject(db, f"{tag} Canonical stock")
    db.add(SubjectRedirect(duplicate_id=duplicate.id, canonical_id=canonical.id))
    db.flush()
    relink_all_companies(db)
    assert links(db, target.id) == {(canonical.id, "exact_name")}


# --- API -------------------------------------------------------------------------------------


def news(db, tag, subject_row, count=2):
    src = source_row(db, f"news{tag}")
    for index in range(count):
        item = ContentItem(
            source_id=src.id,
            domain=Domain.FINANCE,
            content_type="news",
            identity_key=str(uuid4()),
            title=f"Synthetic article {tag} {index}",
            url=f"https://news.example.test/{tag}/{index}",
            published_at=T0 - timedelta(minutes=index),
            observed_at=T0,
            language="en",
            item_metadata={},
            content_hash=str(uuid4()),
        )
        db.add(item)
        db.flush()
        db.add(
            ContentSubjectLink(
                content_item_id=item.id,
                subject_id=subject_row.id,
                strategy="synthetic",
                confidence=1.0,
            )
        )
    db.flush()


def test_api_company_list_query_filters_and_pagination(client, db, tag):
    rank(
        db,
        tag,
        [(f"{tag} Alpha", f"A{tag}"), (f"{tag} Beta", f"B{tag}"), (f"{tag} Gamma", f"C{tag}")],
    )
    alpha = find_target_company(db, f"A{tag}")
    full_profile(db, alpha, tag)
    body = client.get("/api/v1/companies", params={"q": tag, "limit": 2}).json()
    assert body["total"] == 3 and len(body["items"]) == 2 and body["limit"] == 2
    assert body["items"][0]["id"] == str(alpha.id) and body["items"][0]["has_profile"]
    page2 = client.get("/api/v1/companies", params={"q": tag, "limit": 2, "offset": 2}).json()
    assert len(page2["items"]) == 1
    ranked = client.get("/api/v1/companies", params={"ranking": f"test_profile_{tag}"}).json()
    assert [item["rankings"][0]["rank"] for item in ranked["items"]] == [1, 2, 3]
    assert client.get("/api/v1/companies", params={"q": f"示例{tag}"}).json()["total"] == 1
    assert client.get("/api/v1/companies", params={"q": f"l{tag}".upper()}).json()["total"] == 1
    profiled = client.get("/api/v1/companies", params={"q": tag, "has_profile": True}).json()
    assert [item["id"] for item in profiled["items"]] == [str(alpha.id)]
    assert (
        client.get("/api/v1/companies", params={"q": tag, "domain": "computer"}).json()["total"]
        == 3
    )
    assert (
        client.get("/api/v1/companies", params={"q": tag, "domain": "gaming"}).json()["total"] == 0
    )
    assert (
        client.get("/api/v1/companies", params={"ranking": "missing_ranking_key"}).json()["items"]
        == []
    )
    for params in ({"limit": 51}, {"domain": "sports"}, {"offset": -1}, {"q": "x" * 101}):
        assert client.get("/api/v1/companies", params=params).status_code == 422


def test_api_company_detail_profile_sources_rankings_and_news(client, db, tag):
    rank(db, tag, [(f"{tag} Listed", f"{tag}")])
    target = find_target_company(db, tag)
    full_profile(db, target, tag)
    empty = client.get(f"/api/v1/companies/{target.id}").json()
    assert empty["related_news_count"] == 0 and empty["related_subjects"] == []
    assert client.get("/api/v1/news", params={"company_id": str(target.id)}).json()["items"] == []
    stock = subject(db, f"{tag} Listed")
    news(db, tag, stock)
    relink_all_companies(db)
    body = client.get(f"/api/v1/companies/{target.id}").json()
    assert body["display_names"] == {"en": f"{tag} Listed", "zh-CN": f"示例{tag}"}
    assert body["profile"]["legal_name"] == f"{tag.upper()} CORPORATION"
    assert [s["source_key"] for s in body["profile_sources"]] == [
        "official_website",
        "sec_edgar",
        "gleif",
    ]
    assert body["profile_sources"][1]["used_fields"] == [
        "business_areas",
        "industry",
        "key_products",
    ]
    assert body["current_rankings"][0]["latest_rank"] == 1
    assert body["domains"] == ["computer"]
    assert body["related_news_count"] == 2
    items = client.get("/api/v1/news", params={"company_id": str(target.id), "limit": 10}).json()
    assert [i["title"] for i in items["items"]] == [
        f"Synthetic article {tag} 0",
        f"Synthetic article {tag} 1",
    ]
    appearances = client.get(f"/api/v1/companies/{target.id}/rankings").json()
    assert appearances["rankings"][0]["latest_rank"] == 1


def test_api_company_not_found_and_malformed(client):
    assert client.get(f"/api/v1/companies/{uuid4()}").status_code == 404
    assert client.get("/api/v1/companies/not-a-uuid").status_code == 422


def test_list_never_includes_merged_duplicates(db, tag):
    a = company(db, f"{tag} Keep", f"A{tag}")
    b = company(db, f"{tag} Gone", f"B{tag}")
    merge_companies(db, a.id, b.id, "reviewed")
    result = list_companies(db, q=tag)
    assert [item["id"] for item in result["items"]] == [str(a.id)]
