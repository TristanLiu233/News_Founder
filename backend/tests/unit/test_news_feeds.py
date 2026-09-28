"""Phase 6.1 Computer/Gaming news RSS adapters (fixture-backed)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.news_feeds import (
    EXCERPT_LIMIT,
    ArsTechnicaITAdapter,
    GameLookAdapter,
    GamesIndustryBizAdapter,
    GematsuAdapter,
    ITHomeNewsAdapter,
    TechCrunchAdapter,
    excerpt,
    parse_news_rss,
)
from industry_intelligence.config import Settings
from industry_intelligence.domain.enums import ContentType, Domain
from industry_intelligence.infrastructure.http import HTTPClient

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 9, 28, tzinfo=UTC)
ITHOME = (FIXTURES / "ithome_rss.xml").read_text()
WORDPRESS = (FIXTURES / "wordpress_news_rss.xml").read_text()
ADAPTERS = (
    ITHomeNewsAdapter,
    TechCrunchAdapter,
    ArsTechnicaITAdapter,
    GamesIndustryBizAdapter,
    GematsuAdapter,
    GameLookAdapter,
)


def test_ithome_article_id_excerpt_and_failed_rows():
    rows, failed = parse_news_rss(ITHOME, NOW, "zh-CN", ITHomeNewsAdapter.article_id)
    assert failed == 1 and len(rows) == 2
    first = rows[0]
    assert first.external_id == first.url == "https://www.ithome.com/1/007/244.htm"
    assert first.metadata["source_article_id"] == "1007244"
    assert first.published_at == datetime(2026, 9, 20, 2, 15, tzinfo=UTC)
    assert first.observed_at is NOW and first.language == "zh-CN"
    # The feed embeds the full article HTML; only a short plain-text excerpt is kept.
    assert len(first.description) == EXCERPT_LIMIT and first.description.endswith("…")
    assert "<" not in first.description and "img" not in first.description
    assert "source_article_id" not in rows[1].metadata


def test_wordpress_feed_categories_author_guid_and_no_body():
    rows, failed = parse_news_rss(WORDPRESS, NOW, "en")
    assert failed == 2 and len(rows) == 2
    english, chinese = rows
    assert english.external_id == "https://news.example.com/?p=1001"
    assert english.metadata == {
        "categories": ["Acquisitions", "Demo Interactive"],
        "author": "Staff Writer",
    }
    assert english.description == "Example Studio has acquired Sample Games …"
    assert all("FULL ARTICLE BODY" not in (row.description or "") for row in rows)
    assert chinese.title == "《示例游戏》宣布延期至2027年发售"
    assert chinese.published_at.tzinfo is not None


def test_empty_malformed_and_unexpected_feeds():
    assert parse_news_rss("<rss><channel/></rss>", NOW, "en") == ([], 0)
    with pytest.raises(ValueError):
        parse_news_rss("<html><body>blocked</body></html>", NOW, "en")
    with pytest.raises(ValueError):
        parse_news_rss("not xml <", NOW, "en")
    assert excerpt("") is None and excerpt("<p> short </p>") == "short"


def test_ithome_article_id_only_for_article_urls():
    assert ITHomeNewsAdapter.article_id("https://www.ithome.com/0/912/345.htm") == "0912345"
    assert ITHomeNewsAdapter.article_id("https://m.ithome.com/html/1007244.htm") is None
    assert ITHomeNewsAdapter.article_id("https://www.ithome.com/tag/example/") is None


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_adapters_fetch_own_feed_with_limit_and_errors(adapter_type):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, text=WORDPRESS)

    async def run():
        async with HTTPClient(Settings(), httpx.MockTransport(handler)) as http:
            adapter = adapter_type(http, limit=1)
            items = await adapter.fetch()
            assert len(items) == 1 and adapter.failed_count == 2
            assert items[0].language == adapter_type.language
        async with HTTPClient(
            Settings(), httpx.MockTransport(lambda request: httpx.Response(403))
        ) as http:
            with pytest.raises(httpx.HTTPStatusError):
                await adapter_type(http).fetch()

    asyncio.run(run())
    assert requested == [adapter_type.feed_url]


def test_registry_entries_for_computer_and_gaming_news():
    expected = {
        "ithome_news": ("IT之家", Domain.COMPUTER, "zh-CN"),
        "techcrunch": ("TechCrunch", Domain.COMPUTER, "en"),
        "arstechnica_it": ("Ars Technica", Domain.COMPUTER, "en"),
        "gamesindustry_biz": ("GamesIndustry.biz", Domain.GAMING, "en"),
        "gematsu": ("Gematsu", Domain.GAMING, "en"),
        "gamelook": ("GameLook", Domain.GAMING, "zh-CN"),
    }
    for key, (name, domain, language) in expected.items():
        definition = CONTENT_SOURCES[key]
        assert (definition.name, definition.domain, definition.primary_language) == (
            name,
            domain,
            language,
        )
        assert definition.content_type == ContentType.NEWS and definition.acquisition == "RSS"
        assert definition.adapter.source_name == name and definition.adapter.domain == domain
        assert definition.url == definition.adapter.feed_url
    finance = {k for k, d in CONTENT_SOURCES.items() if d.domain == Domain.FINANCE}
    assert finance == {"cnbc_markets", "nasdaq_markets", "eastmoney_news", "sina_finance_news"}
