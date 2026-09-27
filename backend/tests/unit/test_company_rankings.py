"""Company-ranking adapters, parsers, and registry, without network or database."""

import asyncio
import inspect
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from industry_intelligence.adapters.company_ranking import (
    CompanyRankingAdapter,
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)
from industry_intelligence.adapters.company_ranking.companiesmarketcap import (
    SYMBOL_SCHEME,
    CompaniesMarketCapAdapter,
    parse_companiesmarketcap_csv,
)
from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.adapters.company_ranking.wikipedia import (
    ARTICLE_SCHEME,
    WikipediaRankingAdapter,
    parse_billions,
    parse_wikipedia_ranking,
)
from industry_intelligence.config import Settings
from industry_intelligence.domain.enums import Domain
from industry_intelligence.infrastructure.http import HTTPClient, user_agent
from industry_intelligence.main import create_app
from industry_intelligence.services import company_ranking_ingestion as ingestion
from industry_intelligence.services.company_ranking_ingestion import (
    SourceContactRequired,
    content_hash,
    ingest_company_ranking,
)
from industry_intelligence.services.company_rankings import movement

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
CMC_TECH = COMPANY_RANKINGS["cmc_tech_market_cap"]
WIKI_BANKS = COMPANY_RANKINGS["wikipedia_largest_banks_assets"]
WIKI_GAMES = COMPANY_RANKINGS["wikipedia_video_game_revenue"]
CSV_HEADER = '"Rank","Name","Symbol","marketcap","price (USD)","country"\n'


def wiki_html(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["parse"]["text"]


def make_settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_cmc_fixture_parses_top_prefix_with_symbols_and_raw_metric():
    text = (FIXTURES / "companiesmarketcap_tech.csv").read_text()
    entries, source_rows, failed = parse_companiesmarketcap_csv(text, 100)
    # 119 records: one real name spans two physical lines inside quotes.
    assert (len(entries), source_rows, failed) == (100, 119, 0)
    first = entries[0]
    assert (first.rank, first.name) == (1, "NVIDIA")
    assert first.identities == ((SYMBOL_SCHEME, "NVDA"),)
    assert first.raw_value == "5434765213696"
    assert first.metric_value == Decimal("5434765213696")
    assert first.metadata == {"symbol": "NVDA", "price_usd": "225.07", "country": "United States"}
    assert [entry.rank for entry in entries] == list(range(1, 101))
    assert entries[2].name == "Alphabet (Google)"
    assert "Strategy (MicroStrategy)" in {entry.name for entry in entries}


def test_cmc_limit_keeps_source_order_and_counts_all_rows():
    text = (FIXTURES / "companiesmarketcap_tech.csv").read_text()
    entries, source_rows, _ = parse_companiesmarketcap_csv(text, 5)
    assert [entry.rank for entry in entries] == [1, 2, 3, 4, 5]
    assert source_rows == 119


def test_cmc_malformed_rows_are_skipped_and_counted():
    text = CSV_HEADER + "\n".join(
        [
            '"1","Good Co","GOOD","1000","1.5","Japan"',
            '"x","Bad Rank","BAD","10","1","Japan"',
            '"0","Zero Rank","ZERO","10","1","Japan"',
            '"3","   ","EMPTY","10","1","Japan"',
            '"1","Duplicate Rank","DUP","10","1","Japan"',
            '"4","No Symbol Co","","n/a","","China"',
            '"5","Sony 株式会社","SONY","12","","Japan"',
        ]
    )
    entries, source_rows, failed = parse_companiesmarketcap_csv(text, 100)
    assert source_rows == 7 and failed == 4
    assert [entry.name for entry in entries] == ["Good Co", "No Symbol Co", "Sony 株式会社"]
    no_symbol = entries[1]
    assert no_symbol.identities == () and no_symbol.metric_value is None
    assert no_symbol.raw_value == "n/a"
    assert no_symbol.metadata == {"country": "China"}


def test_cmc_changed_header_fails_loudly():
    with pytest.raises(ValueError, match="header"):
        parse_companiesmarketcap_csv('"Position","Company"\n"1","A"\n', 10)


def test_cmc_adapter_requests_csv_and_uses_daily_snapshot_identity():
    requested = []

    def handler(request):
        requested.append(request.url)
        return httpx.Response(200, text=CSV_HEADER + '"1","Good Co","GOOD","1000","1","Japan"\n')

    async def run():
        async with HTTPClient(make_settings(), transport=httpx.MockTransport(handler)) as http:
            adapter = CompaniesMarketCapAdapter(http, CMC_TECH, now=lambda: NOW)
            return adapter, await adapter.fetch()

    adapter, snapshot = asyncio.run(run())
    assert isinstance(adapter, CompanyRankingAdapter)
    assert requested[0].params["download"] == "csv"
    assert str(requested[0]).startswith(CMC_TECH.url)
    assert snapshot.snapshot_key == "daily:2026-09-27"
    assert snapshot.observed_at == NOW and snapshot.published_at is None
    assert snapshot.source_url == CMC_TECH.url
    assert snapshot.source_entry_count == 1 and adapter.failed_count == 0


def test_wikipedia_banks_fixture_parses_rank_name_metric_identity_and_basis():
    entries, source_rows, failed, context = parse_wikipedia_ranking(
        wiki_html("wikipedia_largest_banks"), WIKI_BANKS.params, 200
    )
    assert (len(entries), source_rows, failed) == (100, 100, 0)
    first = entries[0]
    assert (first.rank, first.name, first.raw_value) == (
        1,
        "Industrial and Commercial Bank of China",
        "7,645.80",
    )
    assert first.metric_value == Decimal("7645.80")
    assert first.identities == ((ARTICLE_SCHEME, "Industrial and Commercial Bank of China"),)
    # The flag icon links to the country article; it is metadata, never the identity.
    assert first.metadata["country"] == "China"
    assert context["metric_header"].startswith("Total assets (April 2026)")
    assert "S&P Global Market Intelligence" in context["basis"]
    assert entries[-1].rank == 100


def test_wikipedia_games_fixture_keeps_section_identity_estimates_and_millions():
    entries, source_rows, failed, context = parse_wikipedia_ranking(
        wiki_html("wikipedia_video_game_revenue"), WIKI_GAMES.params, 200
    )
    assert (len(entries), source_rows, failed) == (50, 50, 0)
    tencent = entries[0]
    assert tencent.name == "Tencent Interactive Entertainment"
    # The link targets a section of the parent's article: kept, so it never merges with Tencent.
    assert tencent.identities == ((ARTICLE_SCHEME, "Tencent#Video games"),)
    assert tencent.metadata["headquarters"] == "Shenzhen, China"
    valve = next(entry for entry in entries if entry.name == "Valve Corporation")
    assert valve.raw_value == "$6.5 billion (estimate)" and valve.metadata["estimate"] is True
    assert valve.metric_value == Decimal("6.5")
    gameloft = entries[-1]
    assert (gameloft.raw_value, gameloft.metric_value) == ("$350 million", Decimal("0.35"))
    assert [entry.name for entry in entries if not entry.identities] == [
        "Century Games",
        "Funfly",
        "Papergames",
    ]
    assert context["metric_header"] == "Revenue (USD$)"


MALFORMED_TABLE = """
<p>This list is ranked by revenue.[1]</p>
<table class="wikitable"><tr><th>Rank</th><th>Company</th><th>Revenue (USD)</th></tr>
<tr><td>1</td><td><span class="flagicon"><a href="/wiki/Japan">JP</a></span>
  <a href="/wiki/Alpha_K.K.">Alpha K.K.</a><sup class="reference"><a href="#c">[2]</a></sup></td>
  <td>$1.5 billion</td></tr>
<tr><td>=2</td><td>Tie Co</td><td>$1 billion</td></tr>
<tr><td>3</td><td></td><td>$1 billion</td></tr>
<tr><td>4</td></tr>
<tr><td>5</td><td><a href="/wiki/File:Logo.png">logo</a>
  <a href="/w/index.php?title=Red&amp;action=edit&amp;redlink=1">Red Link Ltd</a></td>
  <td>€3 billion</td></tr>
<tr><td>6</td><td><a href="/wiki/%E8%85%BE%E8%AE%AF">腾讯</a></td><td>n/a</td></tr>
</table>"""


def test_wikipedia_malformed_fragment_is_counted_not_guessed():
    params = {"name_headers": ("Company",), "metric_header_prefix": "Revenue"}
    entries, source_rows, failed, context = parse_wikipedia_ranking(MALFORMED_TABLE, params, 50)
    assert source_rows == 6 and failed == 3
    assert [entry.rank for entry in entries] == [1, 5, 6]
    alpha, red, tencent = entries
    assert alpha.name == "Alpha K.K." and alpha.identities == ((ARTICLE_SCHEME, "Alpha K.K."),)
    assert red.identities == () and red.metric_value is None and red.raw_value == "€3 billion"
    assert tencent.identities == ((ARTICLE_SCHEME, "腾讯"),) and tencent.metric_value is None
    assert context["basis"] == "This list is ranked by revenue."


def test_wikipedia_missing_table_fails_loudly():
    params = {"name_headers": ("Company",), "metric_header_prefix": "Revenue"}
    with pytest.raises(ValueError, match="table not found"):
        parse_wikipedia_ranking(
            "<table class='wikitable'><tr><th>Name</th></tr></table>", params, 5
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$35.7 billion", Decimal("35.7")),
        ("$350 million", Decimal("0.35")),
        ("7,645.80", Decimal("7645.80")),
        ("US$ 2 bn", Decimal("2")),
        ("€3 billion", None),
        ("n/a", None),
        ("$1.2 trillion", None),
    ],
)
def test_parse_billions_is_conservative(raw, expected):
    assert parse_billions(raw) == expected


REST_FIXTURE = (FIXTURES / "wikipedia_rest_video_game_revenue.html").read_text()
REST_PATH = "/api/rest_v1/page/html/List_of_largest_video_game_companies_by_revenue"


def rest_transport(seen, body=REST_FIXTURE, status=200, headers=None):
    def handler(request):
        seen.append(request)
        return httpx.Response(status, text=body, headers=headers or {})

    return httpx.MockTransport(handler)


def fetch_games(transport):
    async def run():
        async with HTTPClient(make_settings(), transport=transport) as http:
            return await WikipediaRankingAdapter(http, WIKI_GAMES, now=lambda: NOW).fetch()

    return asyncio.run(run())


def test_rest_fixture_parses_parsoid_links_and_references():
    entries, source_rows, failed, context = parse_wikipedia_ranking(
        REST_FIXTURE, WIKI_GAMES.params, 200
    )
    assert (len(entries), source_rows, failed) == (50, 50, 0)
    assert entries[0].identities == ((ARTICLE_SCHEME, "Tencent#Video games"),)
    assert entries[2].identities == ((ARTICLE_SCHEME, "Xbox (division)"),)
    assert "[" not in entries[0].name and entries[0].raw_value == "$35.7 billion"
    assert context["basis"].startswith("This is a listing of top 50")


def test_wikipedia_adapter_reads_only_the_current_rest_page():
    seen = []
    snapshot = fetch_games(rest_transport(seen))
    assert len(seen) == 1
    request = seen[0]
    assert (request.method, request.url.host, request.url.path) == (
        "GET",
        "en.wikipedia.org",
        REST_PATH,
    )
    # No Action API HTML and no historical-revision parameters are ever requested.
    assert "api.php" not in str(request.url) and not request.url.params
    assert snapshot.snapshot_key == "revision:1376914809"
    assert snapshot.published_at == datetime(2026, 9, 26, 23, 51, 15, tzinfo=UTC)
    assert snapshot.observed_at == NOW
    assert snapshot.source_url.endswith("&oldid=1376914809")
    assert snapshot.metadata["endpoint"] == "rest_v1/page/html"
    assert len(snapshot.entries) == 50


def test_revision_falls_back_to_etag_and_is_required():
    body = REST_FIXTURE.replace(
        'about="https://en.wikipedia.org/wiki/Special:Redirect/revision/', 'data-x="'
    )
    snapshot = fetch_games(rest_transport([], body, headers={"ETag": 'W/"1400000001/abc-123"'}))
    assert snapshot.snapshot_key == "revision:1400000001"
    with pytest.raises(ValueError, match="revision not reported"):
        fetch_games(rest_transport([], body))


def test_forbidden_response_is_raised_once_without_retry():
    seen = []
    with pytest.raises(httpx.HTTPStatusError):
        fetch_games(rest_transport(seen, "Please respect our robot policy", status=403))
    assert len(seen) == 1


def test_no_historical_revision_option_exists():
    assert "revision" not in inspect.signature(WikipediaRankingAdapter).parameters
    assert "revision" not in inspect.signature(ingest_company_ranking).parameters
    result = subprocess.run(
        [sys.executable, "-m", "industry_intelligence.cli", "company-ranking-ingest"]
        + [WIKI_GAMES.key, "--revision", "1360133679"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
    )
    assert result.returncode == 2 and "unrecognized arguments: --revision" in result.stderr


def test_contact_is_required_before_any_wikipedia_request():
    with pytest.raises(SourceContactRequired, match="SOURCE_CONTACT"):
        asyncio.run(ingest_company_ranking(WIKI_BANKS.key, make_settings(), session_factory=None))
    placeholder = make_settings(source_contact="your-email@example.com")
    with pytest.raises(SourceContactRequired):
        asyncio.run(ingest_company_ranking(WIKI_BANKS.key, placeholder, session_factory=None))


def test_ingestion_sends_contact_only_to_wikimedia(monkeypatch):
    agents = {}

    def handler(request):
        agents[request.url.host] = request.headers["User-Agent"]
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(200, text=REST_FIXTURE)
        return httpx.Response(200, text=CSV_HEADER + '"1","Good Co","GOOD","1","1","Japan"\n')

    class RecordingClient(HTTPClient):
        def __init__(self, settings, contact=None):
            super().__init__(settings, transport=httpx.MockTransport(handler), contact=contact)

    class NoDatabase:
        def begin(self):
            return nullcontext(None)

    monkeypatch.setattr(ingestion, "HTTPClient", RecordingClient)
    monkeypatch.setattr(
        ingestion,
        "persist_company_snapshot",
        lambda session, definition, snapshot, failed: {"persisted_count": 1, "failed_count": 0},
    )
    configured = make_settings(source_contact="ops@research.org")
    asyncio.run(ingest_company_ranking(CMC_TECH.key, configured, NoDatabase()))
    asyncio.run(ingest_company_ranking(WIKI_GAMES.key, configured, NoDatabase()))
    assert "ops@research.org" not in agents["companiesmarketcap.com"]
    assert agents["en.wikipedia.org"].endswith("; ops@research.org)")


def test_user_agent_adds_contact_only_when_configured():
    assert user_agent() == "IndustryIntelligence/0.1 (personal research MVP)"
    assert user_agent("ops@example.test") == (
        "IndustryIntelligence/0.1 (personal research MVP; ops@example.test)"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("  ", None),
        ("your-email@example.com", None),
        ("https://maintainer.invalid/contact", None),
        (" ops@research.org ", "ops@research.org"),
    ],
)
def test_source_contact_placeholders_count_as_unset(value, expected):
    assert make_settings(source_contact=value).source_contact == expected


def test_contact_is_sent_only_by_clients_that_opt_in():
    agents = []

    def handler(request):
        agents.append(request.headers["User-Agent"])
        return httpx.Response(200, text="ok")

    async def run(contact):
        client = HTTPClient(
            make_settings(source_contact="ops@research.org"),
            transport=httpx.MockTransport(handler),
            contact=contact,
        )
        async with client as http:
            await http.get("https://source.test/")

    asyncio.run(run(None))
    asyncio.run(run("ops@research.org"))
    assert "ops@research.org" not in agents[0]
    assert agents[1].endswith("; ops@research.org)")


def test_registry_is_explicit_and_covers_each_domain():
    assert all(key == definition.key for key, definition in COMPANY_RANKINGS.items())
    assert {definition.domain for definition in COMPANY_RANKINGS.values()} == set(Domain)
    assert {definition.metric_type for definition in COMPANY_RANKINGS.values()} == {
        "market_cap",
        "total_assets",
        "revenue",
    }
    for definition in COMPANY_RANKINGS.values():
        assert definition.url.startswith("https://")
        assert definition.requires_contact == (definition.source_key == "wikipedia")


def test_dtos_reject_invalid_rank_name_and_naive_timestamps():
    with pytest.raises(ValueError, match="greater than zero"):
        CompanyRankingEntry(rank=0, name="A")
    with pytest.raises(ValueError, match="empty"):
        CompanyRankingEntry(rank=1, name="  ")
    with pytest.raises(ValueError, match="timezone-aware"):
        CompanyRankingSnapshot("k", datetime(2026, 1, 1), "https://x.test", ())
    with pytest.raises(ValueError, match="timezone-aware"):
        CompanyRankingSnapshot("k", NOW, "https://x.test", (), published_at=datetime(2026, 1, 1))


def test_content_hash_ignores_order_and_name_formatting_but_not_values():
    a = CompanyRankingEntry(rank=1, name="Alpha  Corp", raw_value="10")
    b = CompanyRankingEntry(rank=2, name="Beta", raw_value="5")
    assert content_hash([a, b]) == content_hash([b, CompanyRankingEntry(1, "alpha corp", "10")])
    assert content_hash([a, b]) != content_hash([CompanyRankingEntry(1, "Alpha Corp", "11"), b])


@pytest.mark.parametrize(
    ("previous", "current", "has_previous", "expected"),
    [
        (10, 6, True, (4, "up")),
        (3, 5, True, (-2, "down")),
        (7, 7, True, (0, "unchanged")),
        (None, 9, True, (None, "new")),
        (None, 1, False, (None, None)),
    ],
)
def test_movement_is_previous_minus_current(previous, current, has_previous, expected):
    assert movement(previous, current, has_previous) == expected


def test_company_ranking_routes_validate_without_database(settings):
    app = create_app(settings)
    paths = set(app.openapi()["paths"])
    assert {
        "/api/v1/company-rankings",
        "/api/v1/company-rankings/{ranking_ref}",
        "/api/v1/company-rankings/{ranking_ref}/history",
        "/api/v1/companies/{company_id}/rankings",
    } <= paths
    client = TestClient(app)
    assert client.get("/api/v1/company-rankings?domain=global").status_code == 422
    assert client.get("/api/v1/company-rankings?limit=0").status_code == 422
    assert client.get("/api/v1/company-rankings?limit=101").status_code == 422
    assert client.get("/api/v1/company-rankings/bad%20ref").status_code == 422
    assert client.get("/api/v1/company-rankings/x?limit=201").status_code == 422
    assert client.get("/api/v1/company-rankings/x?snapshot_id=nope").status_code == 422
    assert client.get("/api/v1/company-rankings/x/history?limit=101").status_code == 422
    assert client.get("/api/v1/companies/not-a-uuid/rankings").status_code == 422
    assert client.get(f"/api/v1/companies/{uuid4()}/rankings?limit=0").status_code == 422
