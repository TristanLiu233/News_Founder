from datetime import UTC, datetime
from xml.etree.ElementTree import ParseError

import pytest

from industry_intelligence.adapters.source.base import ContentSourceItem
from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.finance_news import (
    parse_eastmoney_news,
    parse_rss,
    parse_sina_news,
)
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.domain.enums import ContentType
from industry_intelligence.services.content_ingestion import (
    _mentions,
    _ticker_mentions,
    deterministic_matches,
)

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def test_rss_normal_partial_unicode_and_shared_timestamp():
    payload = """<rss><channel>
      <item><guid>1</guid><title>Microsoft markets — 测试</title>
      <link>https://example.com/1</link><pubDate>Sun, 27 Sep 2026 10:00:00 GMT</pubDate></item>
      <item><title>missing url</title><pubDate>Sun, 27 Sep 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    rows, failed = parse_rss(payload, NOW, "en")
    assert failed == 1 and len(rows) == 1
    assert rows[0].observed_at is NOW and rows[0].published_at.tzinfo is not None


def test_rss_empty_and_malformed():
    assert parse_rss("<rss><channel/></rss>", NOW, "en") == ([], 0)
    with pytest.raises(ParseError):
        parse_rss("not xml", NOW, "en")


def test_eastmoney_normal_missing_timestamp_and_url():
    rows, failed = parse_eastmoney_news(
        {
            "data": {
                "fastNewsList": [
                    {"code": "abc", "title": "英伟达新闻", "showTime": "2026-09-27 18:00:00"},
                    {"code": "bad", "title": "无时间"},
                ]
            }
        },
        NOW,
    )
    assert len(rows) == 1 and failed == 1
    assert rows[0].url == "https://finance.eastmoney.com/a/abc.html"


def test_sina_nested_feed_and_original_url():
    rows, failed = parse_sina_news(
        {
            "result": {
                "data": {
                    "feed": {
                        "list": [
                            {
                                "id": 7,
                                "rich_text": "微软发布新闻",
                                "create_time": "2026-09-27 18:00:00",
                                "docurl": "https://finance.sina.cn/7x24/original.d.html",
                            }
                        ]
                    }
                }
            }
        },
        NOW,
    )
    assert failed == 0 and rows[0].url.endswith("original.d.html")


def test_content_item_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        ContentSourceItem("1", "title", "https://example.com", NOW, datetime.now(), "en")


def test_finance_source_classification():
    assert {
        SOURCES[key].list_type
        for key in (
            "coingecko_trending",
            "coinpaprika_market",
            "eastmoney_turnover",
            "sina_turnover",
        )
    } == {ContentType.MARKET_PULSE}
    assert {definition.content_type for definition in CONTENT_SOURCES.values()} == {
        ContentType.NEWS,
        ContentType.FEED,
    }


def test_conservative_mentions_prevents_shared_substring_false_positive():
    assert _mentions("Microsoft releases results", "Microsoft")
    assert not _mentions("OpenAI Codex ships", "OpenAI API")
    assert _mentions("OpenAI API ships", "OpenAI")
    assert _ticker_mentions("NVDA rises and $MSFT follows", "NVDA")
    assert not _ticker_mentions("Trump meets officials", "TRUMP")


def test_deterministic_linking_exact_alias_ticker_ambiguous_and_multiple():
    matches = deterministic_matches(
        "Microsoft and 英伟达 discuss NVDA chips",
        [(1, "Microsoft", "Microsoft"), (2, "NVIDIA", "NVIDIA"), (3, "OpenAI", "OpenAI")],
        [(2, "英伟达"), (3, "AI"), (4, "AI")],
        [(2, "stock:NVDA")],
    )
    assert matches[1][0] == "exact_canonical"
    assert matches[2][0] == "exact_alias"
    assert 3 not in matches and 4 not in matches


def test_deterministic_linking_no_match_and_product_safety():
    assert (
        deterministic_matches("OpenAI Codex ships", [(1, "OpenAI API", "OpenAI API")], [], []) == {}
    )
