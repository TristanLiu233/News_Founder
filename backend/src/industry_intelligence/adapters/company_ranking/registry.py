"""Explicit external company-ranking catalog; no dynamic plugin loading.

These are publisher rankings shown as published. They are never combined into a platform
company score, and they are separate from the Today/Trending source rank lists.
"""

from dataclasses import dataclass, field
from typing import Any

from industry_intelligence.adapters.company_ranking.companiesmarketcap import (
    CompaniesMarketCapAdapter,
)
from industry_intelligence.adapters.company_ranking.wikipedia import WikipediaRankingAdapter
from industry_intelligence.domain.enums import Domain


@dataclass(frozen=True)
class CompanyRankingDefinition:
    key: str
    source_key: str
    publisher: str
    # Source-provided ranking title; never translated.
    name: str
    domain: Domain
    metric_type: str
    metric_label: str
    metric_unit: str | None
    frequency: str
    acquisition: str
    url: str
    adapter: type
    entry_limit: int
    primary_language: str = "en"
    # Short publisher-provided description, kept in the source language.
    methodology: str | None = None
    # The publisher's API policy requires a contact in the User-Agent (SOURCE_CONTACT).
    requires_contact: bool = False
    params: dict[str, Any] = field(default_factory=dict)


def _cmc(key, domain, name, path, methodology):
    return CompanyRankingDefinition(
        key=key,
        source_key="companiesmarketcap",
        publisher="CompaniesMarketCap",
        name=name,
        domain=domain,
        metric_type="market_cap",
        metric_label="Market cap",
        metric_unit="USD",
        frequency="daily",
        acquisition="CSV",
        url=f"https://companiesmarketcap.com/{path}/",
        adapter=CompaniesMarketCapAdapter,
        # The CSV lists every company in the category; the tracked prefix is Top 100.
        entry_limit=100,
        methodology=methodology,
    )


def _wikipedia(key, domain, name, page, metric_type, metric_label, params):
    return CompanyRankingDefinition(
        key=key,
        source_key="wikipedia",
        publisher="Wikipedia",
        name=name,
        domain=domain,
        metric_type=metric_type,
        metric_label=metric_label,
        metric_unit="USD billion",
        frequency="on_revision",
        acquisition="Wikimedia REST API",
        url=f"https://en.wikipedia.org/wiki/{page.replace(' ', '_')}",
        adapter=WikipediaRankingAdapter,
        # Wikipedia tables are stored in full (50 and 100 rows at audit time).
        entry_limit=200,
        requires_contact=True,
        params={"page": page, **params},
    )


COMPANY_RANKINGS: dict[str, CompanyRankingDefinition] = {
    definition.key: definition
    for definition in (
        _cmc(
            "cmc_tech_market_cap",
            Domain.COMPUTER,
            "Largest tech companies by market cap",
            "tech/largest-tech-companies-by-market-cap",
            "List of the largest tech companies by market capitalization, "
            "all rankings are updated daily.",
        ),
        _cmc(
            "cmc_banks_market_cap",
            Domain.FINANCE,
            "Largest banks and bank holding companies by market cap",
            "banks/largest-banks-by-market-cap",
            "List of the largest banks by market capitalization.",
        ),
        _wikipedia(
            "wikipedia_largest_banks_assets",
            Domain.FINANCE,
            "List of largest banks — By total assets",
            "List of largest banks",
            "total_assets",
            "Total assets",
            {"name_headers": ("Bank name",), "metric_header_prefix": "Total assets"},
        ),
        _cmc(
            "cmc_video_games_market_cap",
            Domain.GAMING,
            "Largest video game companies by market cap",
            "video-games/largest-video-game-companies-by-market-cap",
            "List of the largest video game companies by market capitalization, "
            "all rankings are updated daily.",
        ),
        _wikipedia(
            "wikipedia_video_game_revenue",
            Domain.GAMING,
            "List of largest video game companies by revenue",
            "List of largest video game companies by revenue",
            "revenue",
            "Revenue",
            {
                "name_headers": ("Company",),
                "metric_header_prefix": "Revenue",
                "location_header": "Headquarters",
            },
        ),
    )
}
