"""Explicit content-source catalogs: chronological feeds and publishers' ranked article lists."""

from dataclasses import dataclass

from industry_intelligence.adapters.source.finance_news import (
    CNBCMarketsAdapter,
    EastmoneyNewsAdapter,
    NasdaqMarketsAdapter,
    SinaFinanceNewsAdapter,
)
from industry_intelligence.adapters.source.news_feeds import (
    ArsTechnicaITAdapter,
    GameLookAdapter,
    GamesIndustryBizAdapter,
    GematsuAdapter,
    ITHomeNewsAdapter,
    TechCrunchAdapter,
)
from industry_intelligence.adapters.source.news_rankings import (
    Game3DMHotAdapter,
    GematsuPopularAdapter,
    HackerNewsTopContentAdapter,
    SinaFinanceHotAdapter,
)
from industry_intelligence.domain.enums import ContentType, Domain


@dataclass(frozen=True)
class ContentSourceDefinition:
    key: str
    name: str
    domain: Domain
    content_type: ContentType
    url: str
    adapter: type
    acquisition: str
    primary_language: str


CONTENT_SOURCES = {
    item.key: item
    for item in (
        ContentSourceDefinition(
            "cnbc_markets",
            "CNBC",
            Domain.FINANCE,
            ContentType.NEWS,
            CNBCMarketsAdapter.feed_url,
            CNBCMarketsAdapter,
            "RSS",
            "en",
        ),
        ContentSourceDefinition(
            "nasdaq_markets",
            "Nasdaq",
            Domain.FINANCE,
            ContentType.NEWS,
            NasdaqMarketsAdapter.feed_url,
            NasdaqMarketsAdapter,
            "RSS",
            "en",
        ),
        ContentSourceDefinition(
            "eastmoney_news",
            "东方财富",
            Domain.FINANCE,
            ContentType.FEED,
            EastmoneyNewsAdapter.endpoint,
            EastmoneyNewsAdapter,
            "API",
            "zh-CN",
        ),
        ContentSourceDefinition(
            "sina_finance_news",
            "新浪财经",
            Domain.FINANCE,
            ContentType.FEED,
            SinaFinanceNewsAdapter.endpoint,
            SinaFinanceNewsAdapter,
            "API",
            "zh-CN",
        ),
        # Phase 6.1: Computer and Gaming news (publisher RSS; see AGENTS.md source audit).
        ContentSourceDefinition(
            "ithome_news",
            "IT之家",
            Domain.COMPUTER,
            ContentType.NEWS,
            ITHomeNewsAdapter.feed_url,
            ITHomeNewsAdapter,
            "RSS",
            "zh-CN",
        ),
        ContentSourceDefinition(
            "techcrunch",
            "TechCrunch",
            Domain.COMPUTER,
            ContentType.NEWS,
            TechCrunchAdapter.feed_url,
            TechCrunchAdapter,
            "RSS",
            "en",
        ),
        ContentSourceDefinition(
            "arstechnica_it",
            "Ars Technica",
            Domain.COMPUTER,
            ContentType.NEWS,
            ArsTechnicaITAdapter.feed_url,
            ArsTechnicaITAdapter,
            "RSS",
            "en",
        ),
        ContentSourceDefinition(
            "gamesindustry_biz",
            "GamesIndustry.biz",
            Domain.GAMING,
            ContentType.NEWS,
            GamesIndustryBizAdapter.feed_url,
            GamesIndustryBizAdapter,
            "RSS",
            "en",
        ),
        ContentSourceDefinition(
            "gematsu",
            "Gematsu",
            Domain.GAMING,
            ContentType.NEWS,
            GematsuAdapter.feed_url,
            GematsuAdapter,
            "RSS",
            "en",
        ),
        ContentSourceDefinition(
            "gamelook",
            "GameLook",
            Domain.GAMING,
            ContentType.NEWS,
            GameLookAdapter.feed_url,
            GameLookAdapter,
            "RSS",
            "zh-CN",
        ),
    )
}


@dataclass(frozen=True)
class NewsRankingDefinition:
    """A publisher's own ranked article list: a second feed mode of an existing publisher.

    ``name`` is the publisher's ``Source`` (shared with its chronological feed or ranking), so
    an article already stored from the feed is the same ContentItem. ``list_name`` and
    ``ranking_kind`` are the publisher's own label and basis; nothing is inferred.
    """

    key: str
    name: str
    domain: Domain
    list_key: str
    list_name: str
    ranking_kind: str
    url: str
    adapter: type
    acquisition: str
    primary_language: str
    content_type: ContentType = ContentType.NEWS
    # A Subject ranking (``SOURCES`` key) that stores this same upstream list in the same run;
    # its snapshot is reused so the list is requested once per run.
    reuse_ranking: str | None = None


NEWS_RANKINGS = {
    item.key: item
    for item in (
        NewsRankingDefinition(
            "sina_finance_hot",
            "新浪财经",
            Domain.FINANCE,
            "news_click_day",
            "热门新闻排行 · 新闻榜 · 点击量排行",
            "click_count",
            SinaFinanceHotAdapter.page_url,
            SinaFinanceHotAdapter,
            "API",
            "zh-CN",
        ),
        NewsRankingDefinition(
            "gematsu_popular",
            "Gematsu",
            Domain.GAMING,
            "popular_posts",
            "Popular Posts",
            "popular",
            GematsuPopularAdapter.page_url,
            GematsuPopularAdapter,
            "HTML",
            "en",
        ),
        NewsRankingDefinition(
            "game_3dm_hot",
            "3DM",
            Domain.GAMING,
            "recent_hot",
            "近期热门",
            "hot",
            Game3DMHotAdapter.page_url,
            Game3DMHotAdapter,
            "HTML",
            "zh-CN",
        ),
        NewsRankingDefinition(
            "hacker_news_top",
            "Hacker News",
            Domain.COMPUTER,
            "top_stories",
            "Top Stories",
            "top",
            "https://news.ycombinator.com",
            HackerNewsTopContentAdapter,
            "API",
            "en",
            reuse_ranking="hacker_news",
        ),
    )
}
