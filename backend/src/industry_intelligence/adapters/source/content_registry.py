"""Explicit chronological content-source catalog."""

from dataclasses import dataclass

from industry_intelligence.adapters.source.finance_news import (
    CNBCMarketsAdapter,
    EastmoneyNewsAdapter,
    NasdaqMarketsAdapter,
    SinaFinanceNewsAdapter,
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
    )
}
