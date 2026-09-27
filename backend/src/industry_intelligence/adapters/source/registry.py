"""Explicit source catalog; no dynamic plugin loading."""

from dataclasses import dataclass

from industry_intelligence.adapters.source.chinese import (
    EastmoneyTurnoverAdapter,
    Game3DMAdapter,
    GamerskyHotAdapter,
    HuxiuHotAdapter,
    ITHomeDailyAdapter,
    SinaTurnoverAdapter,
)
from industry_intelligence.adapters.source.finance import (
    CoinGeckoTrendingAdapter,
    CoinPaprikaMarketAdapter,
)
from industry_intelligence.adapters.source.gaming import (
    ItchTopSellersAdapter,
    SteamMostPlayedAdapter,
)
from industry_intelligence.adapters.source.github_trending import GitHubTrendingAdapter
from industry_intelligence.adapters.source.hacker_news import HackerNewsAdapter
from industry_intelligence.domain.enums import ContentType, Domain, SubjectType


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    name: str
    domain: Domain
    list_name: str
    subject_type: SubjectType
    url: str
    adapter: type
    acquisition: str = "API"
    metric_label: str = "points"
    primary_language: str = "en"
    list_type: ContentType = ContentType.RANKING


SOURCES = {
    "hacker_news": SourceDefinition(
        "hacker_news",
        "Hacker News",
        Domain.COMPUTER,
        "Top Stories",
        SubjectType.EVENT,
        "https://news.ycombinator.com",
        HackerNewsAdapter,
    ),
}

SOURCES["github_trending"] = SourceDefinition(
    "github_trending",
    "GitHub",
    Domain.COMPUTER,
    "Trending · Daily",
    SubjectType.REPOSITORY,
    "https://github.com/trending",
    GitHubTrendingAdapter,
    "HTML",
    "stars today",
)
SOURCES["coingecko_trending"] = SourceDefinition(
    "coingecko_trending",
    "CoinGecko",
    Domain.FINANCE,
    "Trending Searches",
    SubjectType.STOCK,
    "https://www.coingecko.com/en/coins/trending",
    CoinGeckoTrendingAdapter,
    "API",
    "market cap rank",
    "en",
    ContentType.MARKET_PULSE,
)
SOURCES["coinpaprika_market"] = SourceDefinition(
    "coinpaprika_market",
    "CoinPaprika",
    Domain.FINANCE,
    "Market Cap Ranking",
    SubjectType.STOCK,
    "https://coinpaprika.com/",
    CoinPaprikaMarketAdapter,
    "API",
    "USD market cap",
    "en",
    ContentType.MARKET_PULSE,
)
SOURCES["steam_most_played"] = SourceDefinition(
    "steam_most_played",
    "Steam",
    Domain.GAMING,
    "Most Played",
    SubjectType.GAME,
    "https://store.steampowered.com/charts/mostplayed",
    SteamMostPlayedAdapter,
    "HTML",
    "current players",
)
SOURCES["itch_top_sellers"] = SourceDefinition(
    "itch_top_sellers",
    "itch.io",
    Domain.GAMING,
    "Top Sellers",
    SubjectType.GAME,
    "https://itch.io/games/top-sellers",
    ItchTopSellersAdapter,
    "RSS",
    "listed price",
)

for definition in (
    SourceDefinition(
        "ithome",
        "IT之家",
        Domain.COMPUTER,
        "日榜",
        SubjectType.EVENT,
        "https://m.ithome.com/rankm/",
        ITHomeDailyAdapter,
        "HTML",
        "comments",
        "zh-CN",
        ContentType.RANKING,
    ),
    SourceDefinition(
        "huxiu_hot",
        "虎嗅",
        Domain.COMPUTER,
        "48小时热文",
        SubjectType.EVENT,
        "https://www.huxiu.com/",
        HuxiuHotAdapter,
        "HTML",
        "48-hour rank",
        "zh-CN",
        ContentType.RANKING,
    ),
    SourceDefinition(
        "eastmoney_turnover",
        "东方财富",
        Domain.FINANCE,
        "A股成交额榜",
        SubjectType.STOCK,
        "https://quote.eastmoney.com/",
        EastmoneyTurnoverAdapter,
        "API",
        "turnover",
        "zh-CN",
        ContentType.MARKET_PULSE,
    ),
    SourceDefinition(
        "sina_turnover",
        "新浪财经",
        Domain.FINANCE,
        "A股成交额榜",
        SubjectType.STOCK,
        "https://finance.sina.com.cn/",
        SinaTurnoverAdapter,
        "API",
        "turnover",
        "zh-CN",
        ContentType.MARKET_PULSE,
    ),
    SourceDefinition(
        "gamersky_hot",
        "游民星空",
        Domain.GAMING,
        "近期热游榜",
        SubjectType.GAME,
        "https://ku.gamersky.com/",
        GamerskyHotAdapter,
        "HTML",
        "recent popularity",
        "zh-CN",
    ),
    SourceDefinition(
        "game_3dm",
        "3DM",
        Domain.GAMING,
        "网络游戏排行榜",
        SubjectType.GAME,
        "https://ol.3dmgame.com/",
        Game3DMAdapter,
        "HTML",
        "popularity",
        "zh-CN",
    ),
):
    SOURCES[definition.key] = definition
