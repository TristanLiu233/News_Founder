"""Publishers' ranked article lists: native rank kept exactly, nothing synthesized."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from industry_intelligence.adapters.source.base import (
    ContentSourceItem,
    RankedContentItem,
    SourceItem,
)
from industry_intelligence.adapters.source.content_registry import (
    CONTENT_SOURCES,
    NEWS_RANKINGS,
)
from industry_intelligence.adapters.source.news_rankings import (
    ArticleDetails,
    Game3DMHotAdapter,
    GematsuPopularAdapter,
    HackerNewsTopContentAdapter,
    SinaFinanceHotAdapter,
    hn_ranked_entries,
    hn_story_id,
    parse_3dm_hot,
    parse_3dm_published,
    parse_gematsu_article,
    parse_gematsu_popular,
    parse_sina_hot,
)
from industry_intelligence.domain.enums import Domain
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.news_ranking_ingestion import complete_article_details

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 9, 30, 0, 30, tzinfo=UTC)
SINA = (FIXTURES / "sina_finance_hot.js").read_text(encoding="utf-8")
GEMATSU = (FIXTURES / "gematsu_popular.html").read_text(encoding="utf-8")
HOT_3DM = (FIXTURES / "3dm_news_hot.html").read_text(encoding="utf-8")
GEMATSU_ARTICLE = (FIXTURES / "gematsu_article_identity.html").read_text(encoding="utf-8")


def test_sina_click_rank_window_value_and_time_kept_exactly():
    entries, failed, window = parse_sina_hot(SINA, NOW)
    # Row 4 has no URL: it is dropped, and the rows after it keep their own positions.
    assert failed == 1
    assert [entry.rank for entry in entries] == [1, 2, 3, 5]
    first = entries[0]
    assert first.item.title == "A股利好！又一批增持回购来了"
    assert first.item.url == (
        "https://finance.sina.com.cn/roll/2026-09-30/doc-initqeff7175305.shtml"
    )
    assert first.native_value == "3,957"
    assert first.item.metadata == {"media": "21世纪经济报道"}
    # create_date + create_time are Beijing time.
    assert first.item.published_at == datetime(2026, 9, 30, 0, 27, 36, tzinfo=UTC)
    assert first.item.observed_at is NOW and first.item.language == "zh-CN"
    assert window == "day:2026-09-30"


@pytest.mark.parametrize(
    "payload", ["", "not javascript", "var x = {broken", 'var x = {"data": {"a": 1}};']
)
def test_sina_unexpected_response_raises(payload):
    with pytest.raises(ValueError):
        parse_sina_hot(payload, NOW)


def test_sina_missing_window_is_not_invented():
    entries, _, window = parse_sina_hot(
        'var x = {"data": [{"title": "t", "url": "https://finance.sina.com.cn/a.shtml"}]};', NOW
    )
    assert window is None and entries[0].native_value is None
    assert entries[0].item.published_at is None


def test_gematsu_popular_posts_in_publisher_order_not_the_latest_list():
    entries, failed = parse_gematsu_popular(GEMATSU, NOW)
    assert failed == 0
    assert [(entry.rank, entry.item.title) for entry in entries] == [
        (1, "Intergalactic: The Heretic Prophet to be fully revealed in 2027"),
        (2, "Mobile Suit Gundam: Battle Operation 3 Network Test Version appears on SteamDB"),
        (3, "Monster Hunter Outlanders launches October 29"),
        (4, "Satisfactory expansion ‘Core Values’ announced"),
    ]
    assert entries[0].item.url == (
        "https://www.gematsu.com/2026/09/intergalactic-the-heretic-prophet-to-be-fully-revealed-in-2027"
    )
    # The list carries no dates and no figures: none is synthesized.
    assert all(entry.item.published_at is None for entry in entries)
    assert all(entry.native_value is None for entry in entries)
    assert "Latest chronological post" not in {entry.item.title for entry in entries}


def test_gematsu_layout_change_raises():
    with pytest.raises(ValueError, match="Popular Posts"):
        parse_gematsu_popular("<html><section class='latest'></section></html>", NOW)


def test_3dm_recent_hot_block_only_with_article_ids():
    entries, failed = parse_3dm_hot(HOT_3DM, NOW)
    assert failed == 0 and len(entries) == 8
    assert [entry.rank for entry in entries] == list(range(1, 9))
    first = entries[0]
    assert first.item.external_id == "3954507"
    assert first.item.title == "欧美怕吗？中国自研光刻机啥时候到 黄仁勋马斯克惊人一致"
    assert first.item.url == "https://www.3dmgame.com/news/202609/3954507.html"
    assert first.item.published_at is None and first.item.language == "zh-CN"
    # The chronological list before the block is not a ranking.
    assert "3955000" not in {entry.item.external_id for entry in entries}


def test_3dm_foreign_links_are_rejected_without_renumbering():
    html = HOT_3DM.replace(
        "https://www.3dmgame.com/news/202609/3954728.html", "https://ad.example.com/x"
    )
    entries, failed = parse_3dm_hot(html, NOW)
    assert failed == 1 and [entry.rank for entry in entries] == [1, 3, 4, 5, 6, 7, 8]


def test_3dm_missing_block_raises():
    with pytest.raises(ValueError, match="近期热门"):
        parse_3dm_hot("<html><div class='net'><a href='x'>x</a></div></html>", NOW)


def test_3dm_article_pubdate_is_beijing_time():
    html = '<script type="application/ld+json">{"pubDate": "2026-09-29T18:42:58"}</script>'
    assert parse_3dm_published(html) == datetime(2026, 9, 29, 10, 42, 58, tzinfo=UTC)
    assert parse_3dm_published("<html></html>") is None
    assert parse_3dm_published('"pubDate": "not a date"') is None


def test_hacker_news_rank_is_the_top_stories_position_and_time_the_submission():
    rows = [
        SourceItem(
            "42",
            "Show HN: A thing",
            "https://example.com/thing",
            2,
            181,
            NOW,
            {
                "author": "pg",
                "comments": 7,
                "hn_url": "https://news.ycombinator.com/item?id=42",
                "time": 1790726400,
            },
        ),
        SourceItem("43", "Ask HN: Why?", "https://news.ycombinator.com/item?id=43", 5, None, NOW),
    ]
    entries = hn_ranked_entries(rows)
    assert [entry.rank for entry in entries] == [2, 5]
    assert entries[0].native_value == "181" and entries[1].native_value is None
    assert entries[0].item.external_id == "42"
    assert entries[0].item.published_at == datetime.fromtimestamp(1790726400, UTC)
    assert entries[1].item.published_at is None
    assert entries[0].item.metadata == {
        "hn_url": "https://news.ycombinator.com/item?id=42",
        "author": "pg",
        "comments": 7,
    }


def test_ranked_item_requires_a_positive_rank():
    item = ContentSourceItem(None, "t", "https://a.example/x", None, NOW, "en")
    with pytest.raises(ValueError):
        RankedContentItem(rank=0, item=item)


def test_registry_is_a_feed_mode_of_existing_publishers():
    assert list(NEWS_RANKINGS) == [
        "sina_finance_hot",
        "gematsu_popular",
        "game_3dm_hot",
        "hacker_news_top",
    ]
    assert not set(NEWS_RANKINGS) & set(CONTENT_SOURCES)
    names = {definition.name for definition in NEWS_RANKINGS.values()}
    assert names == {"新浪财经", "Gematsu", "3DM", "Hacker News"}
    assert NEWS_RANKINGS["sina_finance_hot"].domain == Domain.FINANCE
    assert NEWS_RANKINGS["gematsu_popular"].domain == Domain.GAMING
    assert NEWS_RANKINGS["game_3dm_hot"].domain == Domain.GAMING
    assert NEWS_RANKINGS["hacker_news_top"].domain == Domain.COMPUTER


def _client(settings, handler):
    return HTTPClient(settings, transport=httpx.MockTransport(handler))


def test_sina_adapter_requests_the_publishers_daily_click_list(settings):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=SINA)

    async def run():
        async with _client(settings, handler) as http:
            adapter = SinaFinanceHotAdapter(http, 30)
            entries = await adapter.fetch()
            return adapter, entries

    adapter, entries = asyncio.run(run())
    params = seen[0].params
    assert seen[0].host == "top.finance.sina.com.cn"
    assert params["top_type"] == "day" and params["top_order"] == "DESC"
    assert params["top_cat"] == "finance_news_0_suda" and params["top_show_num"] == "20"
    assert adapter.window == "day:2026-09-30" and adapter.failed_count == 1
    assert len(entries) == 4


def test_gematsu_and_3dm_adapters_read_their_pages(settings):
    def handler(request):
        if request.url.host == "www.gematsu.com":
            if request.url.path == "/":
                return httpx.Response(200, text=GEMATSU)
            return httpx.Response(200, text=GEMATSU_ARTICLE)
        if request.url.path == "/news/":
            return httpx.Response(200, text=HOT_3DM)
        return httpx.Response(200, text='{"pubDate": "2026-09-29T18:42:58"}')

    async def run():
        async with _client(settings, handler) as http:
            gematsu = GematsuPopularAdapter(http, 30)
            hot = Game3DMHotAdapter(http, 3)
            return (
                await gematsu.fetch(),
                await hot.fetch(),
                await gematsu.article_details("https://www.gematsu.com/2026/09/x"),
                await hot.article_details("https://www.3dmgame.com/news/202609/1.html"),
            )

    gematsu, hot, gematsu_article, hot_article = asyncio.run(run())
    assert len(gematsu) == 4 and [entry.rank for entry in hot] == [1, 2, 3]
    assert gematsu_article == ArticleDetails(
        datetime(2026, 9, 29, 15, 36, 59, tzinfo=UTC), "https://www.gematsu.com/?p=1081377"
    )
    # 3DM rows already carry the article number; the page only adds the time.
    assert hot_article == ArticleDetails(datetime(2026, 9, 29, 10, 42, 58, tzinfo=UTC), None)


def test_gematsu_article_id_is_the_wordpress_post_id_in_the_rss_guid_form():
    details = parse_gematsu_article(GEMATSU_ARTICLE)
    assert details.external_id == "https://www.gematsu.com/?p=1081377"
    assert details.published_at == datetime(2026, 9, 29, 15, 36, 59, tzinfo=UTC)


def test_gematsu_article_id_fails_closed_when_markers_disagree_or_are_missing():
    other_body = GEMATSU_ARTICLE.replace("postid-1081377", "postid-1081378")
    assert parse_gematsu_article(other_body).external_id is None
    no_rest = GEMATSU_ARTICLE.replace("wp-json/wp/v2/posts/1081377", "wp-json/")
    assert parse_gematsu_article(no_rest).external_id is None
    # Another site's REST link is never taken as a Gematsu post.
    foreign = GEMATSU_ARTICLE.replace(
        "https://www.gematsu.com/wp-json/wp/v2/posts/", "https://evil.example/wp-json/wp/v2/posts/"
    )
    assert parse_gematsu_article(foreign).external_id is None
    assert parse_gematsu_article("<html></html>") == ArticleDetails(None, None)


def test_hacker_news_adapter_reuses_top_stories(settings):
    def handler(request):
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[7, 8])
        story = int(request.url.path.rsplit("/", 1)[1].split(".")[0])
        return httpx.Response(
            200,
            json={
                "id": story,
                "title": f"Story {story}",
                "url": f"https://x.example/{story}",
                "score": story * 10,
                "time": 1790726400,
                "by": "u",
            },
        )

    async def run():
        async with _client(settings, handler) as http:
            return await HackerNewsTopContentAdapter(http, 30).fetch()

    entries = asyncio.run(run())
    assert [(entry.rank, entry.item.title, entry.native_value) for entry in entries] == [
        (1, "Story 7", "70"),
        (2, "Story 8", "80"),
    ]
    assert entries[0].item.published_at == datetime.fromtimestamp(1790726400, UTC)


def test_hn_entries_from_the_stored_ranking_snapshot():
    observations = [
        SimpleNamespace(
            rank=1,
            title="Show HN: A thing",
            url="https://example.com/thing",
            raw_metric=181,
            observed_at=NOW,
            item_metadata={
                "hn_url": "https://news.ycombinator.com/item?id=42",
                "author": "pg",
                "comments": 7,
                "metric_label": "points",
                "time": 1790726400,
            },
        ),
        # Stored before Trending-1: no ``time`` — the date stays unknown, never guessed.
        SimpleNamespace(
            rank=3,
            title="Ask HN: Why?",
            url="https://news.ycombinator.com/item?id=43",
            raw_metric=None,
            observed_at=NOW,
            item_metadata={"hn_url": "https://news.ycombinator.com/item?id=43"},
        ),
    ]
    entries = HackerNewsTopContentAdapter.entries_from_observations(observations)
    assert [(e.rank, e.item.external_id, e.native_value) for e in entries] == [
        (1, "42", "181"),
        (3, "43", None),
    ]
    assert entries[0].item.published_at == datetime.fromtimestamp(1790726400, UTC)
    assert entries[1].item.published_at is None
    assert hn_story_id("https://news.ycombinator.com/item?id=x") is None
    assert hn_story_id(None) is None


def test_list_semantics_are_the_publishers_own():
    semantics = {key: (d.list_key, d.list_name, d.ranking_kind) for key, d in NEWS_RANKINGS.items()}
    assert semantics == {
        "sina_finance_hot": ("news_click_day", "热门新闻排行 · 新闻榜 · 点击量排行", "click_count"),
        "gematsu_popular": ("popular_posts", "Popular Posts", "popular"),
        "game_3dm_hot": ("recent_hot", "近期热门", "hot"),
        "hacker_news_top": ("top_stories", "Top Stories", "top"),
    }
    for values in semantics.values():
        text = " ".join(values).lower()
        assert "24" not in text and "rolling" not in text
        assert "today" not in text and "今日" not in text
    # Only Sina states a period: the publisher's daily list for a Beijing calendar date.
    _, _, window = parse_sina_hot(SINA, NOW)
    assert window == "day:2026-09-30"
    assert parse_3dm_hot(HOT_3DM, NOW) and NEWS_RANKINGS["hacker_news_top"].reuse_ranking == (
        "hacker_news"
    )


class _Lookup:
    lookup_delay = 10.0
    lookup_articles = True

    def __init__(self, details):
        self.details, self.calls = details, []

    async def article_details(self, url):
        self.calls.append(url)
        value = self.details.get(url)
        if isinstance(value, Exception):
            raise value
        return value


def _entry(rank, url, published=None, external_id=None):
    return RankedContentItem(rank, ContentSourceItem(external_id, url, url, published, NOW, "en"))


def test_article_lookup_only_for_new_entries_missing_a_date_or_id_with_crawl_delay():
    known = "https://www.gematsu.com/known"
    complete = "https://www.gematsu.com/complete"
    dated = "https://www.gematsu.com/dated"
    new = "https://www.gematsu.com/new"
    broken = "https://www.gematsu.com/broken"
    found = ArticleDetails(datetime(2026, 9, 29, tzinfo=UTC), "https://www.gematsu.com/?p=7")
    adapter = _Lookup(
        {
            new: found,
            dated: ArticleDetails(datetime(2020, 1, 1, tzinfo=UTC), "https://www.gematsu.com/?p=8"),
            broken: RuntimeError(),
        }
    )
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    entries = [
        _entry(1, known),
        _entry(2, complete, datetime(2026, 9, 28, tzinfo=UTC), "https://www.gematsu.com/?p=6"),
        _entry(3, dated, datetime(2026, 9, 28, tzinfo=UTC)),
        _entry(4, new),
        _entry(5, broken),
    ]
    completed, counts = asyncio.run(
        complete_article_details(adapter, entries, {known}, sleep=sleep)
    )
    assert adapter.calls == [dated, new, broken]
    assert sleeps == [10.0, 10.0, 10.0]
    assert counts == {"lookups": 3, "dates_found": 1, "ids_found": 2}
    assert [entry.rank for entry in completed] == [1, 2, 3, 4, 5]
    # The row's own date is kept; only the missing ID is added.
    assert completed[2].item.published_at == datetime(2026, 9, 28, tzinfo=UTC)
    assert completed[2].item.external_id == "https://www.gematsu.com/?p=8"
    assert (completed[3].item.published_at, completed[3].item.external_id) == (
        found.published_at,
        found.external_id,
    )
    # A failed lookup leaves both unknown; the known article is untouched.
    assert (completed[4].item.published_at, completed[4].item.external_id) == (None, None)
    assert completed[0].item.external_id is None


def test_adapters_without_article_pages_are_never_looked_up():
    adapter = _Lookup({})
    adapter.lookup_articles = False
    completed, counts = asyncio.run(
        complete_article_details(adapter, [_entry(1, "https://a.example/x")], set())
    )
    assert adapter.calls == [] and counts["lookups"] == 0 and len(completed) == 1
