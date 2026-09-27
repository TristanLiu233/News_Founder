"""Company-ranking persistence, resolution, history, and API inside rolled-back transactions.

Every ranking key, company name, and identity is synthetic and unique per test, so live
companies and rankings in the development database never match or change.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from industry_intelligence.adapters.company_ranking import (
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)
from industry_intelligence.adapters.company_ranking.companiesmarketcap import SYMBOL_SCHEME
from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.adapters.company_ranking.wikipedia import (
    ARTICLE_SCHEME,
    WikipediaRankingAdapter,
)
from industry_intelligence.config import Settings, get_settings
from industry_intelligence.db.models import (
    Company,
    CompanyIdentity,
    Ranking,
    RankingEntry,
    RankingSnapshot,
)
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.main import create_app
from industry_intelligence.services import company_rankings as rankings_service
from industry_intelligence.services import company_resolution
from industry_intelligence.services.company_ranking_ingestion import persist_company_snapshot
from industry_intelligence.services.company_rankings import (
    company_rankings,
    ranking_detail,
    ranking_history,
)

pytestmark = pytest.mark.integration

DAY1 = datetime(2026, 9, 25, 12, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)


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


def definition(tag, suffix="", domain=Domain.GAMING, source_key=None):
    return replace(
        COMPANY_RANKINGS["cmc_video_games_market_cap"],
        key=f"test_{tag}{suffix}",
        name=f"Test ranking {tag}{suffix}",
        source_key=source_key or f"test_source_{tag}",
        domain=domain,
    )


def entry(rank, name, symbol=None, value=None, article=None):
    identities = []
    if symbol:
        identities.append((SYMBOL_SCHEME, symbol))
    if article:
        identities.append((ARTICLE_SCHEME, article))
    return CompanyRankingEntry(
        rank=rank, name=name, raw_value=value, identities=tuple(identities), metadata={}
    )


def snapshot(key, entries, observed_at=DAY1, published_at=None):
    return CompanyRankingSnapshot(
        snapshot_key=key,
        observed_at=observed_at,
        published_at=published_at,
        source_url="https://example.test/ranking",
        entries=tuple(entries),
        source_entry_count=len(entries),
        metadata={"synthetic": True},
    )


def four(tag):
    return [
        entry(1, f"{tag} Alpha", f"A{tag}", "400"),
        entry(2, f"{tag} Beta", f"B{tag}", "300"),
        entry(3, f"{tag} Gamma", f"C{tag}", "200"),
        entry(4, f"{tag} Delta", f"D{tag}", "100"),
    ]


def second_day(tag):
    return [
        entry(1, f"{tag} Beta", f"B{tag}", "410"),
        entry(2, f"{tag} Alpha", f"A{tag}", "405"),
        entry(3, f"{tag} Gamma", f"C{tag}", "210"),
        entry(4, f"{tag} Epsilon", f"E{tag}", "150"),
    ]


def snapshot_count(db, ranking_key):
    return db.scalar(
        select(func.count())
        .select_from(RankingSnapshot)
        .join(Ranking)
        .where(Ranking.key == ranking_key)
    )


def test_first_snapshot_creates_ranking_companies_and_entries(db, tag):
    ranking = definition(tag)
    result = persist_company_snapshot(db, ranking, snapshot("daily:1", four(tag)))
    assert result["persisted_count"] == 4 and result["status"] == "complete"
    assert result["resolution"] == {"new_company": 4}
    row = db.scalar(select(Ranking).where(Ranking.key == ranking.key))
    assert (row.publisher, row.metric_type, row.metric_unit) == (
        "CompaniesMarketCap",
        "market_cap",
        "USD",
    )
    snap = db.scalar(select(RankingSnapshot).where(RankingSnapshot.ranking_id == row.id))
    assert (snap.entry_count, snap.status, snap.effective_at) == (4, "complete", DAY1)
    alpha = db.scalar(
        select(Company).join(CompanyIdentity).where(CompanyIdentity.value == f"A{tag}")
    )
    assert alpha.canonical_name == f"{tag} Alpha"
    assert [alias.alias for alias in alpha.aliases] == [f"{tag} Alpha"]
    first = db.scalar(
        select(RankingEntry).where(RankingEntry.snapshot_id == snap.id, RankingEntry.rank == 1)
    )
    assert (first.raw_name, first.raw_value, first.resolution_strategy) == (
        f"{tag} Alpha",
        "400",
        "new_company",
    )


def test_same_snapshot_key_is_replayed_and_identical_content_is_not_duplicated(db, tag):
    ranking = definition(tag)
    first = persist_company_snapshot(db, ranking, snapshot("daily:1", four(tag)))
    replay = persist_company_snapshot(db, ranking, snapshot("daily:1", second_day(tag)))
    assert replay["replayed"] is True and replay["snapshot_id"] == first["snapshot_id"]
    unchanged = persist_company_snapshot(db, ranking, snapshot("daily:2", four(tag), DAY2))
    assert unchanged["unchanged"] is True and unchanged["persisted_count"] == 0
    assert snapshot_count(db, ranking.key) == 1
    assert (
        db.scalar(
            select(func.count()).select_from(Company).where(Company.canonical_name.startswith(tag))
        )
        == 4
    )


def test_movement_new_dropped_and_preserved_history(db, tag):
    ranking = definition(tag)
    first = persist_company_snapshot(db, ranking, snapshot("daily:1", four(tag)))
    persist_company_snapshot(db, ranking, snapshot("daily:2", second_day(tag), DAY2))
    detail = ranking_detail(db, ranking.key)
    moves = {
        item["display_name"].split()[-1]: (
            item["previous_rank"],
            item["rank_change"],
            item["movement"],
        )
        for item in detail["entries"]
    }
    assert moves == {
        "Beta": (2, 1, "up"),
        "Alpha": (1, -1, "down"),
        "Gamma": (3, 0, "unchanged"),
        "Epsilon": (None, None, "new"),
    }
    assert detail["dropped_count"] == 1
    assert detail["dropped"][0]["display_name"] == f"{tag} Delta"
    assert detail["dropped"][0]["previous_rank"] == 4
    assert detail["previous_snapshot"]["id"] == first["snapshot_id"]
    # The first snapshot is untouched and still readable as it was published.
    old = ranking_detail(db, ranking.key, snapshot_id=first["snapshot_id"])
    assert [item["display_name"] for item in old["entries"]] == [
        f"{tag} {n}" for n in ("Alpha", "Beta", "Gamma", "Delta")
    ]
    assert {item["movement"] for item in old["entries"]} == {None}
    assert old["entries"][0]["raw_value"] == "400"
    history = ranking_history(db, ranking.key)
    assert history["total"] == 2
    assert [item["leader"]["name"] for item in history["items"]] == [f"{tag} Beta", f"{tag} Alpha"]


def test_identity_reuses_company_across_rankings_and_domains(db, tag):
    persist_company_snapshot(
        db, definition(tag, "a"), snapshot("daily:1", [entry(1, f"{tag} Soft", f"S{tag}")])
    )
    result = persist_company_snapshot(
        db,
        definition(tag, "b", Domain.COMPUTER),
        snapshot("daily:1", [entry(7, f"{tag} Soft Holdings", f"S{tag}")]),
    )
    assert result["resolution"] == {"external_identity": 1}
    companies = db.scalars(
        select(Company).join(CompanyIdentity).where(CompanyIdentity.value == f"S{tag}")
    ).all()
    assert len(companies) == 1
    assert {alias.alias for alias in companies[0].aliases} == {
        f"{tag} Soft",
        f"{tag} Soft Holdings",
    }


def test_exact_name_reuse_but_ambiguous_names_are_not_merged(db, tag):
    persist_company_snapshot(
        db, definition(tag, "a"), snapshot("daily:1", [entry(1, f"{tag} Omega")])
    )
    reused = persist_company_snapshot(
        db,
        definition(tag, "b"),
        snapshot("daily:1", [entry(3, f"{tag.upper()}  OMEGA", article=f"Omega {tag}")]),
    )
    assert reused["resolution"] == {"exact_name": 1}
    persist_company_snapshot(
        db,
        definition(tag, "c"),
        snapshot(
            "daily:1", [entry(1, f"{tag} Twin", f"T1{tag}"), entry(2, f"{tag} Twin", f"T2{tag}")]
        ),
    )
    ambiguous = persist_company_snapshot(
        db, definition(tag, "d"), snapshot("daily:1", [entry(1, f"{tag} Twin")])
    )
    assert ambiguous["resolution"] == {"new_company": 1}
    twins = db.scalar(
        select(func.count()).select_from(Company).where(Company.normalized_name == f"{tag} twin")
    )
    assert twins == 3


def test_conflicting_identity_blocks_a_name_match(db, tag):
    persist_company_snapshot(
        db, definition(tag, "a"), snapshot("daily:1", [entry(1, f"{tag} Meta", f"M1{tag}")])
    )
    result = persist_company_snapshot(
        db, definition(tag, "b"), snapshot("daily:1", [entry(1, f"{tag} Meta", f"M2{tag}")])
    )
    assert result["resolution"] == {"new_company": 1}


def test_retargeted_wikipedia_link_does_not_block_a_name_match(db, tag):
    ranking = definition(tag)
    persist_company_snapshot(
        db,
        ranking,
        snapshot("revision:1", [entry(4, f"{tag} Games", article=f"Parent {tag}")], DAY1),
    )
    result = persist_company_snapshot(
        db,
        ranking,
        snapshot("revision:2", [entry(5, f"{tag} Games", article=f"{tag} Games")], DAY2),
    )
    assert result["resolution"] == {"exact_name": 1}
    moved = ranking_detail(db, ranking.key)["entries"][0]
    assert (moved["previous_rank"], moved["movement"]) == (4, "down")


def test_seeded_identity_pair_links_different_published_names(db, tag, monkeypatch):
    group = frozenset({(SYMBOL_SCHEME, f"ICBC{tag}"), (ARTICLE_SCHEME, f"Industrial Bank {tag}")})
    monkeypatch.setattr(company_resolution, "SEED_IDENTITY_GROUPS", (group,))
    persist_company_snapshot(
        db, definition(tag, "a"), snapshot("daily:1", [entry(6, f"{tag} ICBC", f"ICBC{tag}")])
    )
    result = persist_company_snapshot(
        db,
        definition(tag, "b"),
        snapshot(
            "revision:1",
            [entry(1, f"{tag} Industrial Bank full name", article=f"Industrial Bank {tag}")],
        ),
    )
    assert result["resolution"] == {"seed_identity": 1}
    company = db.scalar(
        select(Company).join(CompanyIdentity).where(CompanyIdentity.value == f"ICBC{tag}")
    )
    assert {identity.scheme for identity in company.identities} == {SYMBOL_SCHEME, ARTICLE_SCHEME}


def test_backfilled_older_revision_is_ordered_by_effective_time(db, tag):
    ranking = definition(tag)
    now = datetime.now(UTC)
    persist_company_snapshot(
        db, ranking, snapshot("revision:2", second_day(tag), now, published_at=DAY2)
    )
    persist_company_snapshot(
        db,
        ranking,
        snapshot("revision:1", four(tag), now + timedelta(minutes=1), published_at=DAY1),
    )
    detail = ranking_detail(db, ranking.key)
    assert detail["snapshot"]["snapshot_key"] == "revision:2"
    assert detail["previous_snapshot"]["snapshot_key"] == "revision:1"
    assert detail["entries"][0]["movement"] == "up"
    assert [item["snapshot_key"] for item in ranking_history(db, ranking.key)["items"]] == [
        "revision:2",
        "revision:1",
    ]


def test_invalid_only_snapshot_raises_without_writing(db, tag):
    ranking = definition(tag)
    with pytest.raises(ValueError, match="No valid entries"):
        persist_company_snapshot(db, ranking, snapshot("daily:1", [entry(1, "x" * 300)]))
    assert snapshot_count(db, ranking.key) == 0


def test_duplicate_company_in_one_snapshot_is_skipped_and_partial(db, tag):
    result = persist_company_snapshot(
        db,
        definition(tag),
        snapshot(
            "daily:1", [entry(1, f"{tag} One", f"O{tag}"), entry(2, f"{tag} One again", f"O{tag}")]
        ),
    )
    assert (result["persisted_count"], result["skipped_count"], result["status"]) == (
        1,
        1,
        "partial",
    )


def test_company_history_reports_movement_and_dropped(db, tag):
    ranking = definition(tag)
    persist_company_snapshot(db, ranking, snapshot("daily:1", four(tag)))
    persist_company_snapshot(db, ranking, snapshot("daily:2", second_day(tag), DAY2))
    alpha = db.scalar(select(CompanyIdentity.company_id).where(CompanyIdentity.value == f"A{tag}"))
    delta = db.scalar(select(CompanyIdentity.company_id).where(CompanyIdentity.value == f"D{tag}"))
    alpha_history = company_rankings(db, alpha)["rankings"][0]
    assert alpha_history["latest_rank"] == 2 and alpha_history["in_latest_snapshot"]
    assert [(p["rank"], p["movement"]) for p in alpha_history["history"]] == [
        (2, "down"),
        (1, None),
    ]
    delta_history = company_rankings(db, delta)["rankings"][0]
    assert delta_history["in_latest_snapshot"] is False and delta_history["latest_rank"] is None
    assert [(p["rank"], p["previous_rank"], p["movement"]) for p in delta_history["history"]] == [
        (None, 4, "dropped"),
        (4, None, None),
    ]
    # With limit=1 the older snapshot only supplies the previous rank.
    limited = company_rankings(db, alpha, limit=1)["rankings"][0]["history"]
    assert [(p["rank"], p["previous_rank"], p["movement"]) for p in limited] == [(2, 1, "down")]


def test_api_list_detail_history_and_company(client, db, tag):
    source_key = f"test_source_{tag}"
    main = definition(tag, "a", source_key=source_key)
    other = definition(tag, "b", Domain.FINANCE, source_key=source_key)
    first = persist_company_snapshot(db, main, snapshot("daily:1", four(tag)))
    second = persist_company_snapshot(db, main, snapshot("daily:2", second_day(tag), DAY2))
    other_snap = persist_company_snapshot(
        db, other, snapshot("daily:1", [entry(1, f"{tag} Bank", f"K{tag}")])
    )

    listing = client.get("/api/v1/company-rankings", params={"source": source_key}).json()
    assert listing["total"] == 2
    item = listing["items"][1]
    assert (item["key"], item["status"], item["snapshot_count"]) == (main.key, "ready", 2)
    assert item["latest_snapshot"]["id"] == second["snapshot_id"]
    assert item["latest_snapshot"]["entry_count"] == 4
    gaming = client.get(
        "/api/v1/company-rankings", params={"source": source_key, "domain": "gaming"}
    ).json()
    assert [i["key"] for i in gaming["items"]] == [main.key]
    paged = client.get(
        "/api/v1/company-rankings", params={"source": source_key, "limit": 1, "offset": 1}
    ).json()
    assert (paged["total"], [i["key"] for i in paged["items"]]) == (2, [main.key])

    detail = client.get(
        f"/api/v1/company-rankings/{main.key}", params={"limit": 2, "offset": 1}
    ).json()
    assert (detail["total"], detail["limit"], detail["offset"]) == (4, 2, 1)
    assert [(e["rank"], e["movement"], e["rank_change"]) for e in detail["entries"]] == [
        (2, "down", -1),
        (3, "unchanged", 0),
    ]
    by_id = client.get(f"/api/v1/company-rankings/{detail['ranking']['id']}").json()
    assert by_id["snapshot"]["id"] == second["snapshot_id"] and by_id["dropped_count"] == 1
    old = client.get(
        f"/api/v1/company-rankings/{main.key}", params={"snapshot_id": first["snapshot_id"]}
    ).json()
    assert old["snapshot"]["snapshot_key"] == "daily:1" and old["previous_snapshot"] is None
    assert (
        client.get(
            f"/api/v1/company-rankings/{main.key}",
            params={"snapshot_id": other_snap["snapshot_id"]},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/company-rankings/{main.key}", params={"snapshot_id": str(uuid4())}
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/company-rankings/test_missing_{tag}").status_code == 404
    assert client.get(f"/api/v1/company-rankings/{uuid4()}").status_code == 404

    history = client.get(f"/api/v1/company-rankings/{main.key}/history", params={"limit": 1}).json()
    assert (history["total"], len(history["items"])) == (2, 1)
    assert history["items"][0]["snapshot_key"] == "daily:2"
    assert client.get(f"/api/v1/company-rankings/test_missing_{tag}/history").status_code == 404

    beta = db.scalar(select(CompanyIdentity.company_id).where(CompanyIdentity.value == f"B{tag}"))
    company = client.get(f"/api/v1/companies/{beta}/rankings").json()
    assert company["canonical_name"] == f"{tag} Beta"
    assert company["identities"] == [
        {"scheme": SYMBOL_SCHEME, "value": f"B{tag}", "source_key": main.key}
    ]
    assert company["rankings"][0]["history"][0]["movement"] == "up"
    assert client.get(f"/api/v1/companies/{uuid4()}/rankings").status_code == 404


def test_registered_but_unobserved_ranking_is_listed_without_snapshot(client, tag, monkeypatch):
    pending = definition(tag, source_key=f"pending_{tag}")
    monkeypatch.setattr(rankings_service, "COMPANY_RANKINGS", {pending.key: pending})
    listing = client.get("/api/v1/company-rankings", params={"source": pending.source_key}).json()
    assert listing["total"] == 1
    assert (listing["items"][0]["id"], listing["items"][0]["status"]) == (None, "no_snapshot")
    detail = client.get(f"/api/v1/company-rankings/{pending.key}")
    assert detail.status_code == 200 and detail.json()["status"] == "no_snapshot"
    assert detail.json()["entries"] == []
    assert client.get(f"/api/v1/company-rankings/{pending.key}/history").json()["items"] == []
    missing_snapshot = client.get(
        f"/api/v1/company-rankings/{pending.key}", params={"snapshot_id": str(uuid4())}
    )
    assert missing_snapshot.status_code == 404


REST_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "wikipedia_rest_video_game_revenue.html"
).read_text()


def fetch_current_page(definition, body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))

    async def run():
        async with HTTPClient(Settings(_env_file=None), transport=transport) as http:
            return await WikipediaRankingAdapter(http, definition).fetch()

    return asyncio.run(run())


def test_current_page_snapshot_is_idempotent_across_repeated_runs(db, tag):
    """Offline fixture verification of the production path, not live ingestion."""

    ranking = replace(
        COMPANY_RANKINGS["wikipedia_video_game_revenue"], key=f"test_{tag}", name=f"Wiki {tag}"
    )
    first = persist_company_snapshot(db, ranking, fetch_current_page(ranking, REST_FIXTURE))
    assert (first["snapshot_key"], first["persisted_count"]) == ("revision:1376914809", 50)
    # The next scheduled run sees the same current revision: a replay, nothing written.
    again = persist_company_snapshot(db, ranking, fetch_current_page(ranking, REST_FIXTURE))
    assert again["replayed"] is True and again["snapshot_id"] == first["snapshot_id"]
    # An unrelated page edit (new revision, identical table) is not a new snapshot.
    edited = REST_FIXTURE.replace("revision/1376914809", "revision/1376999999").replace(
        "2026-09-26T23:51:15", "2026-09-28T08:00:00"
    )
    unchanged = persist_company_snapshot(db, ranking, fetch_current_page(ranking, edited))
    assert unchanged["unchanged"] is True
    assert snapshot_count(db, ranking.key) == 1
