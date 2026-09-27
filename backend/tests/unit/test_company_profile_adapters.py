"""Company-profile adapters, contact scoping, and ingestion orchestration without a database."""

import asyncio
import json
import subprocess
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from industry_intelligence.adapters.company_profile import (
    CompanyProfileAdapter,
    ExternalCompanyProfile,
    LocalizedName,
    ProfileRejected,
)
from industry_intelligence.adapters.company_profile import official_website as website_module
from industry_intelligence.adapters.company_profile.companiesmarketcap import (
    CompaniesMarketCapProfileAdapter,
    parse_company_page,
)
from industry_intelligence.adapters.company_profile.gleif import (
    LEI_SCHEME,
    GleifProfileAdapter,
    parse_lei_record,
)
from industry_intelligence.adapters.company_profile.official_website import (
    OfficialWebsiteProfileAdapter,
    RobotsDisallowed,
    page_contains,
    parse_homepage,
)
from industry_intelligence.adapters.company_profile.registry import PROFILE_SOURCES
from industry_intelligence.adapters.company_profile.sec_edgar import (
    CIK_SCHEME,
    SecEdgarProfileAdapter,
    _xbrl_files,
    latest_annual_filing,
    parse_submissions,
    xbrl_lists,
)
from industry_intelligence.adapters.company_profile.targets import (
    PROFILE_TARGETS,
    TARGETS_BY_SYMBOL,
    ProfileTarget,
)
from industry_intelligence.adapters.company_profile.text import clean_text, detect_language
from industry_intelligence.config import Settings
from industry_intelligence.infrastructure.http import HTTPClient, host_in, sec_user_agent
from industry_intelligence.services import company_profile_ingestion as ingestion
from industry_intelligence.services.company_profile_ingestion import (
    ingest_company_profiles,
    profile_hash,
    sanitize_facts,
    valid_website,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 9, 27, 3, tzinfo=UTC)
SEC_CONTACT = "ops@research.org"
WIKI_CONTACT = "wikimedia@maintainer.org"


def read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_settings(**values):
    return Settings(_env_file=None, database_url="postgresql+psycopg://localhost/x", **values)


def definition(key):
    return PROFILE_SOURCES[key]


def run(coro):
    return asyncio.run(coro)


def client(handler, **kwargs):
    return HTTPClient(make_settings(), transport=httpx.MockTransport(handler), **kwargs)


# --- SEC EDGAR -------------------------------------------------------------------------------


def test_sec_submissions_normalize_registrant_facts():
    facts, metadata = parse_submissions(json.loads(read("sec_submissions_nvidia.json")))
    assert facts == {
        "industry": "Semiconductors & Related Devices",
        "headquarters": "SANTA CLARA, CA",
        "country": "US",
        "jurisdiction": "US-DE",
    }
    assert metadata["tickers"] == ["NVDA"] and metadata["sic"] == "3674"
    assert metadata["former_names"] == ["NVIDIA CORP/CA"]


def test_sec_foreign_registrant_gets_no_guessed_country_or_jurisdiction():
    facts, _ = parse_submissions(
        {
            "sicDescription": "Semiconductors",
            "stateOfIncorporation": "F5",
            "addresses": {
                "business": {
                    "city": "HSINCHU",
                    "stateOrCountry": "F5",
                    "stateOrCountryDescription": "Taiwan",
                    "isForeignLocation": 1,
                }
            },
        }
    )
    assert facts == {"industry": "Semiconductors", "headquarters": "HSINCHU, Taiwan"}


def test_latest_annual_filing_skips_other_forms_and_amendments():
    submissions = json.loads(read("sec_submissions_nvidia.json"))
    filing = latest_annual_filing(submissions)
    assert filing["form"] == "10-K" and filing["report_date"] == "2026-01-25"
    submissions["filings"]["recent"]["form"] = ["4", "10-K/A", "10-Q"]
    assert latest_annual_filing(submissions) is None


def test_xbrl_lists_use_current_period_labels_only():
    period_end, lists, truncated = xbrl_lists(
        read("sec_xbrl_instance_sample.xml"), read("sec_xbrl_labels_sample.xml")
    )
    assert period_end == "2026-01-31"
    # Terse labels win; " [Member]" is stripped; the prior-period, structural, geographic,
    # and unlabelled members are excluded; a standard member keeps the filer's label.
    assert lists == {
        "business_areas": ["Compute & Networking", "Graphics"],
        "key_products": ["Gaming", "Services"],
    }
    assert truncated == {}


def test_member_suffix_is_stripped_in_us_gaap_and_ifrs_casing():
    from industry_intelligence.adapters.company_profile.sec_edgar import _MEMBER_SUFFIX

    for label in ("Music [member]", "Music [Member]", "Music Member", "Music"):
        assert _MEMBER_SUFFIX.sub("", label) == "Music"
    assert _MEMBER_SUFFIX.sub("", "Club membership") == "Club membership"


def test_xbrl_labels_fall_back_to_the_schema_when_no_label_file_exists():
    assert _xbrl_files(["a_htm.xml", "a.xsd", "a_lab.xml"]) == ("a_htm.xml", "a_lab.xml")
    assert _xbrl_files(["d1_htm.xml", "FilingSummary.xml", "sony-1.xsd"]) == (
        "d1_htm.xml",
        "sony-1.xsd",
    )
    assert _xbrl_files(["x.xsd", "x.xml", "x_lab.xml"]) == ("x.xml", "x_lab.xml")
    assert _xbrl_files(["report.htm"]) == (None, None)


def sec_handler(agents, index_items=None):
    submissions = read("sec_submissions_nvidia.json")
    items = index_items or ["nvda-20260125.xsd", "nvda-20260125_htm.xml", "nvda-20260125_lab.xml"]

    def handler(request):
        agents.append((request.url.host, request.headers["User-Agent"]))
        path = request.url.path
        if path.endswith("CIK0001045810.json"):
            return httpx.Response(200, text=submissions)
        if path.endswith("index.json"):
            return httpx.Response(200, json={"directory": {"item": [{"name": n} for n in items]}})
        if path.endswith("_htm.xml"):
            return httpx.Response(200, text=read("sec_xbrl_instance_sample.xml"))
        if path.endswith("_lab.xml"):
            return httpx.Response(200, text=read("sec_xbrl_labels_sample.xml"))
        return httpx.Response(404)

    return handler


def test_sec_adapter_fetches_submissions_and_annual_report_xbrl():
    agents = []

    async def go():
        async with client(sec_handler(agents), sec_contact=SEC_CONTACT) as http:
            adapter = SecEdgarProfileAdapter(http, definition("sec_edgar"), lambda: NOW, 0)
            return adapter, await adapter.fetch(ProfileTarget("NVDA", cik="1045810"))

    adapter, profile = run(go())
    assert isinstance(adapter, CompanyProfileAdapter) and adapter.failed_count == 0
    assert profile.identities == ((CIK_SCHEME, "0001045810"),)
    assert profile.facts["business_areas"] == ["Compute & Networking", "Graphics"]
    assert profile.facts["industry"] == "Semiconductors & Related Devices"
    assert profile.published_at == datetime(2026, 2, 25, tzinfo=UTC)
    assert profile.metadata["annual_report"]["url"].endswith("/nvda-20260125.htm")
    assert profile.metadata["xbrl"]["status"] == "parsed"
    assert len(agents) == 4
    assert all(agent == sec_user_agent(SEC_CONTACT) for _, agent in agents)


def test_sec_adapter_keeps_registrant_facts_when_xbrl_is_unavailable():
    agents = []

    async def go():
        async with client(sec_handler(agents, ["report.htm"]), sec_contact=SEC_CONTACT) as http:
            adapter = SecEdgarProfileAdapter(http, definition("sec_edgar"), lambda: NOW, 0)
            return adapter, await adapter.fetch(ProfileTarget("NVDA", cik="0001045810"))

    adapter, profile = run(go())
    assert adapter.failed_count == 1 and profile.metadata["xbrl"] == {"status": "unavailable"}
    assert "business_areas" not in profile.facts and profile.facts["country"] == "US"


def test_sec_adapter_rejects_a_mismatched_cik():
    def handler(request):
        return httpx.Response(200, json={"cik": "0000000002", "filings": {"recent": {}}})

    async def go():
        async with client(handler, sec_contact=SEC_CONTACT) as http:
            adapter = SecEdgarProfileAdapter(http, definition("sec_edgar"), lambda: NOW, 0)
            await adapter.fetch(ProfileTarget("X", cik="0000000001"))

    with pytest.raises(ValueError, match="different CIK"):
        run(go())


# --- GLEIF -------------------------------------------------------------------------------------


def test_gleif_record_supplies_legal_name_headquarters_and_jurisdiction():
    payload = json.loads(read("gleif_lei_record_nvidia.json"))
    facts, names, published_at, metadata = parse_lei_record(payload, "549300S4KLFTLO7GSQ80")
    assert facts == {
        "legal_name": "NVIDIA CORPORATION",
        "legal_name_locale": "en",
        "headquarters": "SANTA CLARA, US-CA",
        "country": "US",
        "jurisdiction": "US-DE",
    }
    assert names == (LocalizedName("NVIDIA CORPORATION", "en", "legal_name"),)
    assert published_at.tzinfo is not None and metadata["entity_category"] == "GENERAL"


def gleif_payload(lei="L1", **entity):
    base = {
        "legalName": {"name": "騰訊控股有限公司", "language": "zh"},
        "otherNames": [],
        "transliteratedOtherNames": [
            {
                "name": "Tencent Holdings Limited",
                "language": "zh",
                "type": "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME",
            },
            {
                "name": "teng xun kong gu",
                "language": "zh",
                "type": "AUTO_ASCII_TRANSLITERATED_LEGAL_NAME",
            },
        ],
        "headquartersAddress": {"city": "Hong Kong", "region": None, "country": "HK"},
        "jurisdiction": "KY",
        "category": "GENERAL",
        "status": "ACTIVE",
    }
    base.update(entity)
    return {"data": {"id": lei, "attributes": {"entity": base, "registration": {}}}}


def test_gleif_keeps_native_and_preferred_transliterated_names_but_not_automatic_ones():
    facts, names, _, _ = parse_lei_record(gleif_payload(), "L1")
    assert facts["legal_name"] == "騰訊控股有限公司" and facts["legal_name_locale"] == "zh"
    assert names == (
        LocalizedName("騰訊控股有限公司", "zh", "legal_name"),
        LocalizedName("Tencent Holdings Limited", None, "transliterated_legal_name"),
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (gleif_payload(lei="OTHER"), "different LEI"),
        (gleif_payload(category="BRANCH"), "branch"),
        (gleif_payload(status="INACTIVE"), "INACTIVE"),
        (gleif_payload(legalName={"name": " ", "language": "en"}), "no legal name"),
    ],
)
def test_gleif_rejects_wrong_branch_inactive_or_nameless_records(payload, message):
    with pytest.raises(ProfileRejected, match=message):
        parse_lei_record(payload, "L1")


def test_gleif_adapter_reads_only_the_reviewed_lei_record():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, text=read("gleif_lei_record_nvidia.json"))

    async def go():
        async with client(handler) as http:
            adapter = GleifProfileAdapter(http, definition("gleif"), lambda: NOW)
            return await adapter.fetch(ProfileTarget("NVDA", lei="549300S4KLFTLO7GSQ80"))

    profile = run(go())
    assert urls == ["https://api.gleif.org/api/v1/lei-records/549300S4KLFTLO7GSQ80"]
    assert profile.identities == ((LEI_SCHEME, "549300S4KLFTLO7GSQ80"),)
    assert profile.source_url.endswith("549300S4KLFTLO7GSQ80")


# --- Official website ----------------------------------------------------------------------


def test_homepage_meta_description_wins_and_is_cleaned():
    description, field, language = parse_homepage(read("official_homepage_sample.html"))
    assert description == "Example Devices designs & builds accelerated computing platforms."
    assert field == "meta:description" and language == "en"


def test_homepage_falls_back_to_json_ld_and_reports_nothing_when_absent():
    json_ld = (
        '<html><script type="application/ld+json">[{"@type":["Organization"],'
        '"description":"组织描述"}]</script></html>'
    )
    assert parse_homepage(json_ld) == ("组织描述", "json-ld:description", "zh")
    assert parse_homepage("<html><head><title>x</title></head></html>") == (None, None, None)


def test_page_contains_ignores_scripts():
    page = read("official_homepage_sample.html")
    assert page_contains(page, "Example Devices") and not page_contains(page, "示例设备")


def official_handler(pages, robots=None, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            status, body = robots or (404, "")
            return httpx.Response(status, text=body)
        return httpx.Response(200, text=pages.get(str(request.url), ""))

    return handler


def test_official_adapter_verifies_localized_names_on_the_cited_page():
    pages = {
        "https://www.example.test/": read("official_homepage_sample.html"),
        "https://www.example.test/zh/": "<html><body>示例设备官方网站</body></html>",
        "https://www.example.test/cn/": "<html><body>No Chinese name here</body></html>",
    }
    target = ProfileTarget(
        "EXD",
        website="https://www.example.test/",
        localized_names=(
            ("zh-CN", "示例设备", "https://www.example.test/zh/"),
            ("zh-CN", "伪造名称", "https://www.example.test/cn/"),
        ),
    )

    async def go():
        async with client(official_handler(pages)) as http:
            adapter = OfficialWebsiteProfileAdapter(
                http, definition("official_website"), lambda: NOW
            )
            return adapter, await adapter.fetch(target)

    adapter, profile = run(go())
    assert profile.names == (LocalizedName("示例设备", "zh-CN", "localized_name"),)
    assert adapter.failed_count == 1
    assert profile.metadata["unverified_names"] == [
        {"name": "伪造名称", "url": "https://www.example.test/cn/", "reason": "not_found"}
    ]
    assert profile.facts["website"] == "https://www.example.test/"


def test_official_adapter_honours_robots_and_server_errors():
    target = ProfileTarget("EXD", website="https://www.example.test/private/")
    for robots in ((200, "User-agent: *\nDisallow: /private/\n"), (503, "")):
        calls = []

        async def go(robots=robots, calls=calls):
            async with client(official_handler({}, robots, calls)) as http:
                adapter = OfficialWebsiteProfileAdapter(http, definition("official_website"))
                await adapter.fetch(target)

        with pytest.raises(RobotsDisallowed):
            run(go())
        # Only robots.txt was requested (retried on 5xx), never the page itself.
        assert all(url.endswith("/robots.txt") for url in calls)


def test_official_adapter_treats_a_missing_robots_file_as_allowed(monkeypatch):
    monkeypatch.setattr(website_module, "ROBOTS_AGENT", "IndustryIntelligence")
    pages = {"https://www.example.test/": "<html><head></head></html>"}

    async def go():
        async with client(official_handler(pages, (404, ""))) as http:
            adapter = OfficialWebsiteProfileAdapter(
                http, definition("official_website"), lambda: NOW
            )
            return await adapter.fetch(ProfileTarget("EXD", website="https://www.example.test/"))

    profile = run(go())
    assert profile.facts == {"website": "https://www.example.test/"}


# --- CompaniesMarketCap ----------------------------------------------------------------------


def test_companiesmarketcap_categories_are_verbatim_without_emoji_or_prose():
    assert parse_company_page(read("companiesmarketcap_company_page.html"), "NVDA") == [
        "Semiconductors",
        "Tech",
        "Dow jones",
        "AI",
    ]


def test_companiesmarketcap_page_for_another_symbol_is_rejected():
    with pytest.raises(ProfileRejected):
        parse_company_page(read("companiesmarketcap_company_page.html"), "AMD")


def test_companiesmarketcap_adapter_uses_the_reviewed_slug():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, text=read("companiesmarketcap_company_page.html"))

    async def go():
        async with client(handler) as http:
            adapter = CompaniesMarketCapProfileAdapter(http, definition("companiesmarketcap"))
            return await adapter.fetch(ProfileTarget("NVDA", cmc_slug="nvidia"))

    profile = run(go())
    assert urls == ["https://companiesmarketcap.com/nvidia/marketcap/"]
    assert "description" not in profile.facts and len(profile.facts["categories"]) == 4


# --- Text and DTO -----------------------------------------------------------------------------


def test_clean_text_normalizes_and_shortens_safely():
    assert clean_text("<p>A&amp;B\n\n  C</p>") == "A&B C"
    assert clean_text("   ") is None and clean_text(None) is None
    assert clean_text("one two three four five six", 16) == "one two three…"
    assert clean_text("中" * 30, 10) == "中" * 9 + "…"


@pytest.mark.parametrize(
    ("text", "declared", "expected"),
    [
        ("x", "en-AU", "en"),
        ("x", "zh-TW", "zh-TW"),
        ("任天堂です", None, "ja"),
        ("腾讯", None, "zh"),
    ],
)
def test_detect_language(text, declared, expected):
    assert detect_language(text, declared) == expected


def test_profile_dto_validates_fields_and_timestamps():
    with pytest.raises(ValueError, match="Unknown profile fields"):
        ExternalCompanyProfile("s", "https://x.test", NOW, {"moat": "wide"})
    with pytest.raises(ValueError, match="must be a list"):
        ExternalCompanyProfile("s", "https://x.test", NOW, {"key_products": "GPU"})
    with pytest.raises(ValueError, match="timezone-aware"):
        ExternalCompanyProfile("s", "https://x.test", datetime(2026, 1, 1), {})
    with pytest.raises(ValueError, match="empty"):
        LocalizedName(" ", "zh-CN", "localized_name")


def test_invalid_websites_and_empty_lists_are_dropped():
    assert valid_website("https://www.example.test/") == "https://www.example.test/"
    for bad in ("javascript:alert(1)", "www.example.test", "https://", "ftp://x.test", 3):
        assert valid_website(bad) is None
    assert sanitize_facts(
        {"website": "notaurl", "key_products": [], "business_areas": ["A", "A", ""]}
    ) == {"business_areas": ["A"]}


def test_profile_hash_ignores_name_order_but_not_content():
    names = (LocalizedName("A", "en", "legal_name"), LocalizedName("甲", "zh", "legal_name"))
    one = profile_hash({"industry": "X"}, names, (("sec:cik", "1"),), None)
    assert one == profile_hash({"industry": "X"}, names[::-1], (("sec:cik", "1"),), None)
    assert one != profile_hash({"industry": "Y"}, names, (("sec:cik", "1"),), None)
    assert one != profile_hash({"industry": "X"}, names, (("sec:cik", "1"),), NOW)


# --- Contacts ----------------------------------------------------------------------------------


def test_contacts_are_scoped_to_their_publishers_hosts_including_redirects():
    agents = {}

    def handler(request):
        agents[str(request.url)] = request.headers["User-Agent"]
        if request.url.host == "www.sec.gov" and request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://elsewhere.test/landing"})
        return httpx.Response(200, text="ok")

    async def go():
        async with client(handler, contact=WIKI_CONTACT, sec_contact=SEC_CONTACT) as http:
            for url in (
                "https://data.sec.gov/submissions/CIK1.json",
                "https://www.sec.gov/redirect",
                "https://en.wikipedia.org/wiki/X",
                "https://api.gleif.org/api/v1/lei-records/L",
                "https://companiesmarketcap.com/x/",
                "https://notsec.gov.example.test/",
            ):
                await http.get(url)

    run(go())
    sec = sec_user_agent(SEC_CONTACT)
    assert sec == f"IndustryIntelligenceRankingPlatform/0.1 {SEC_CONTACT}"
    assert agents["https://data.sec.gov/submissions/CIK1.json"] == sec
    assert agents["https://www.sec.gov/redirect"] == sec
    assert agents["https://en.wikipedia.org/wiki/X"].endswith(f"; {WIKI_CONTACT})")
    assert SEC_CONTACT not in agents["https://en.wikipedia.org/wiki/X"]
    for url in (
        "https://elsewhere.test/landing",
        "https://api.gleif.org/api/v1/lei-records/L",
        "https://companiesmarketcap.com/x/",
        "https://notsec.gov.example.test/",
    ):
        assert SEC_CONTACT not in agents[url] and WIKI_CONTACT not in agents[url]


def test_host_matching_is_exact_or_subdomain():
    assert host_in("data.sec.gov", ("sec.gov",)) and host_in("SEC.GOV.", ("sec.gov",))
    assert not host_in("notsec.gov", ("sec.gov",)) and not host_in(None, ("sec.gov",))


@pytest.mark.parametrize(
    "value", [None, "  ", "your-email@example.com", "ops@maintainer.invalid", "a@b.test"]
)
def test_sec_contact_placeholders_count_as_unset(value):
    assert make_settings(sec_contact=value).sec_contact is None
    assert make_settings(sec_contact=" ops@research.org ").sec_contact == "ops@research.org"


def test_example_env_keeps_placeholders_and_tracked_files_hold_no_configured_contact():
    root = Path(__file__).parents[3]
    example = (root / ".env.example").read_text()
    assert "SEC_CONTACT=your-email@example.com" in example
    assert "SOURCE_CONTACT=your-email@example.com" in example
    local = Settings()
    contacts = [value for value in (local.sec_contact, local.source_contact) if value]
    if not contacts:
        pytest.skip("no local contact configured")
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout.split(b"\0")
    offenders = []
    for name in filter(None, tracked):
        path = root / name.decode()
        if path.is_file():
            data = path.read_bytes()
            if any(value.encode() in data for value in contacts):
                offenders.append(name.decode())
    # Only paths are reported, never the configured value itself.
    assert offenders == []


# --- Registry, targets, orchestration -------------------------------------------------------


def test_registry_and_reviewed_targets_are_explicit():
    assert list(PROFILE_SOURCES) == ["official_website", "sec_edgar", "gleif", "companiesmarketcap"]
    assert [d.key for d in PROFILE_SOURCES.values() if d.requires_sec_contact] == ["sec_edgar"]
    assert len(TARGETS_BY_SYMBOL) == len(PROFILE_TARGETS) == 15
    for target in PROFILE_TARGETS:
        assert target.lei and target.cmc_slug
        if target.website:
            assert target.website.startswith("https://")
        for locale, name, url in target.localized_names:
            assert locale == "zh-CN" and name and url.startswith("https://")
    # Sites that refused the application client are not configured, so never re-requested.
    assert all(TARGETS_BY_SYMBOL[s].website is None for s in ("GOOG", "TSM", "601939.SS"))


class StubSession:
    def get(self, model, key):
        return type("Row", (), {"id": key})()


class NoDatabase:
    def __call__(self):
        return nullcontext(StubSession())

    def begin(self):
        return nullcontext(StubSession())


def test_sec_source_is_skipped_without_contact_and_makes_no_request(monkeypatch):
    requests = []

    class RecordingClient(HTTPClient):
        def __init__(self, settings, sec_contact=None):
            super().__init__(
                settings, transport=httpx.MockTransport(requests.append), sec_contact=sec_contact
            )

    monkeypatch.setattr(ingestion, "HTTPClient", RecordingClient)
    results = run(ingest_company_profiles(make_settings(), NoDatabase(), ["NVDA"], ["sec_edgar"]))
    assert results == [{"source": "sec_edgar", "error": "SecContactRequired"}] and not requests


def test_unobserved_company_is_not_fetched_and_failures_stay_isolated(monkeypatch):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if "apple" in str(request.url):
            return httpx.Response(403)
        return httpx.Response(200, text=read("companiesmarketcap_company_page.html"))

    class RecordingClient(HTTPClient):
        def __init__(self, settings, sec_contact=None):
            super().__init__(settings, transport=httpx.MockTransport(handler))

    companies = {"NVDA": "nvidia-id", "AAPL": "apple-id"}
    monkeypatch.setattr(ingestion, "HTTPClient", RecordingClient)
    monkeypatch.setattr(
        ingestion,
        "find_target_company",
        lambda session, symbol: (
            type("C", (), {"id": companies[symbol]})() if symbol in companies else None
        ),
    )
    monkeypatch.setattr(
        ingestion,
        "persist_profile_source",
        lambda session, company, definition, profile: {"status": "created"},
    )

    async def no_sleep(_):
        return None

    results = run(
        ingest_company_profiles(
            make_settings(),
            NoDatabase(),
            ["AAPL", "NVDA", "GOOG"],
            ["companiesmarketcap"],
            sleep=no_sleep,
        )
    )
    by_symbol = {result["symbol"]: result for result in results}
    assert by_symbol["AAPL"]["error"] == "HTTP 403"
    assert by_symbol["NVDA"]["status"] == "created"
    assert by_symbol["GOOG"]["error"] == "CompanyNotFound"
    assert not any("alphabet" in url for url in requests)
