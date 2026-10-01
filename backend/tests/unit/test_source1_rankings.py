"""Source-1: IT之家 日榜 as a publisher's ranked article list.

Fixture-backed: native rank kept exactly as printed, stable article identity (the IT之家 feed
GUID, never the mobile URL), malformed rows, list metadata, and the article publication time
(Beijing time). Nothing is scored or synthesized.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

from industry_intelligence.adapters.source.chinese import parse_ithome
from industry_intelligence.adapters.source.content_registry import NEWS_RANKINGS
from industry_intelligence.adapters.source.news_feeds import ITHomeNewsAdapter
from industry_intelligence.adapters.source.news_rankings import (
    ArticleDetails,
    ITHomeDailyContentAdapter,
    ithome_guid,
    ithome_ranked_entries,
    parse_ithome_published,
)
from industry_intelligence.domain.enums import Domain
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.news_ranking_ingestion import _needs_lookup

FIXTURES = Path(__file__).parents[1] / "fixtures"
ITHOME_RANK = (FIXTURES / "ithome_daily_live.html").read_text(encoding="utf-8")
ITHOME_ARTICLE = (FIXTURES / "ithome_article_mobile.html").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 1, 0, 10, tzinfo=UTC)


def _client(settings, handler):
    return HTTPClient(settings, transport=httpx.MockTransport(handler))


# --- IT之家 日榜 ----------------------------------------------------------------------------


def test_ithome_guid_is_the_feeds_identity_of_the_news_id():
    assert ithome_guid("1008903") == "https://www.ithome.com/1/008/903.htm"
    assert ithome_guid(1007244) == "https://www.ithome.com/1/007/244.htm"
    # The feed adapter reads the same news ID back from that GUID: one identity both ways.
    assert ITHomeNewsAdapter.article_id(ithome_guid("1008903")) == "1008903"
    assert ithome_guid("12345") is None and ithome_guid("abc1234") is None
    assert ithome_guid("") is None


def test_ithome_daily_entries_are_stored_under_the_feed_guid_with_the_printed_rank():
    rows, failed = parse_ithome(ITHOME_RANK)
    # Only the 日榜 block (the first on the page) is read, not the 周榜 after it.
    assert len(rows) == 12 and rows[0]["external_id"] == "1008903"
    entries, dropped = ithome_ranked_entries(rows, NOW)
    assert failed == dropped == 0 and len(entries) == len(rows)
    assert [entry.rank for entry in entries] == [row["rank"] for row in rows]
    for entry, row in zip(entries, rows, strict=True):
        guid = ithome_guid(row["external_id"])
        # Never the mobile URL: the article is the feed's row, not a second one.
        assert entry.item.external_id == entry.item.url == guid
        assert entry.item.metadata == {"source_article_id": row["external_id"]}
        assert entry.item.published_at is None and entry.item.language == "zh-CN"
        assert entry.native_value is None  # the shown comment count is not the ranking basis


def test_ithome_rows_without_a_readable_news_id_or_title_are_dropped():
    rows = [
        {
            "rank": 1,
            "title": "A",
            "url": "https://m.ithome.com/html/1008903.htm",
            "external_id": None,
        },
        {"rank": 2, "title": "B", "url": "https://m.ithome.com/html/abc.htm", "external_id": None},
        {
            "rank": 3,
            "title": "",
            "url": "https://m.ithome.com/html/1008904.htm",
            "external_id": None,
        },
        {
            "rank": 0,
            "title": "D",
            "url": "https://m.ithome.com/html/1008905.htm",
            "external_id": None,
        },
        {"rank": 5, "title": "E", "url": "", "external_id": "1008906"},
    ]
    entries, dropped = ithome_ranked_entries(rows, NOW)
    assert dropped == 3
    assert [(entry.rank, entry.item.external_id) for entry in entries] == [
        (1, "https://www.ithome.com/1/008/903.htm"),
        (5, "https://www.ithome.com/1/008/906.htm"),
    ]


def test_ithome_entries_from_the_stored_ranking_snapshot():
    observed = datetime(2026, 10, 1, 0, 10, 49, tzinfo=UTC)
    observations = [
        SimpleNamespace(
            rank=rank, title=title, url=url, observed_at=observed, raw_metric=358, item_metadata={}
        )
        for rank, title, url in (
            (1, "余承东预热华为 Mate 90", "https://m.ithome.com/html/1008903.htm"),
            (2, "微信朋友圈贷款广告突然消失", "https://m.ithome.com/html/1008586.htm"),
            (3, None, "https://m.ithome.com/html/1008560.htm"),
        )
    ]
    entries = ITHomeDailyContentAdapter.entries_from_observations(observations)
    assert [(entry.rank, entry.item.url) for entry in entries] == [
        (1, "https://www.ithome.com/1/008/903.htm"),
        (2, "https://www.ithome.com/1/008/586.htm"),
    ]
    assert {entry.item.observed_at for entry in entries} == {observed}
    assert ITHomeDailyContentAdapter.entries_from_observations([]) == []


def test_ithome_article_time_is_beijing_time_and_read_from_the_mobile_page(settings):
    assert parse_ithome_published(ITHOME_ARTICLE) == datetime(2026, 9, 30, 12, 15, tzinfo=UTC)
    assert parse_ithome_published("<span class='news-time'>昨日 20:15</span>") is None
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/rankm/":
            return httpx.Response(200, text=ITHOME_RANK)
        return httpx.Response(200, text=ITHOME_ARTICLE)

    async def run():
        async with _client(settings, handler) as http:
            adapter = ITHomeDailyContentAdapter(http, 3)
            entries = await adapter.fetch()
            details = await adapter.article_details("https://www.ithome.com/1/008/903.htm")
            foreign = await adapter.article_details("https://example.test/1/008/903.htm")
            return adapter, entries, details, foreign

    adapter, entries, details, foreign = asyncio.run(run())
    assert seen == ["https://m.ithome.com/rankm/", "https://m.ithome.com/html/1008903.htm"]
    assert len(entries) == 3 and adapter.window is None
    assert details == ArticleDetails(datetime(2026, 9, 30, 12, 15, tzinfo=UTC), None)
    assert foreign is None


def test_ithome_daily_reuses_the_ithome_subject_ranking_and_looks_up_only_new_dateless_rows():
    definition = NEWS_RANKINGS["ithome_daily"]
    assert definition.reuse_ranking == "ithome" and definition.domain == Domain.COMPUTER
    assert (definition.list_key, definition.list_name) == ("daily", "日榜")
    rows, _ = parse_ithome(ITHOME_RANK)
    entries, _ = ithome_ranked_entries(rows, NOW)
    known = {entries[0].item.url}
    needed = [entry for entry in entries if _needs_lookup(ITHomeDailyContentAdapter, entry, known)]
    # The feed already stored the first article: no lookup; every other row lacks a date.
    assert entries[0] not in needed and len(needed) == len(entries) - 1
