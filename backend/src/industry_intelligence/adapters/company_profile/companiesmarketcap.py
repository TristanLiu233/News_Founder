"""CompaniesMarketCap company pages: the publisher's category tags (secondary source).

The same publisher already supplies the Phase 4 market-cap rankings; robots.txt does not restrict
company pages. Only the category badges are kept, verbatim without their emoji (for example
"Semiconductors", "Video games", "Dow jones"): they are the publisher's classification, not an
official industry code. The page must show the target's ticker symbol in its title, so a wrong
slug can never attach another company's categories. The publisher's prose is not stored.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from industry_intelligence.adapters.company_profile.base import (
    ExternalCompanyProfile,
    ProfileRejected,
)
from industry_intelligence.adapters.company_profile.text import clean_text

PAGE_URL = "https://companiesmarketcap.com/{slug}/marketcap/"
_LEADING_SYMBOLS = re.compile(r"^[^\w]+")


def parse_company_page(text, symbol):
    soup = BeautifulSoup(text, "html.parser")
    title = soup.find("meta", property="og:title")
    title_text = (title.get("content") if title else None) or (
        soup.title.string if soup.title else ""
    )
    if f"({symbol})" not in (title_text or ""):
        raise ProfileRejected("Company page does not show the target symbol")
    categories = []
    for badge in soup.select(".categories-box a.category-badge"):
        name = clean_text(_LEADING_SYMBOLS.sub("", badge.get_text(" ", strip=True)))
        if name and name not in categories:
            categories.append(name)
    return categories


class CompaniesMarketCapProfileAdapter:
    def __init__(self, http, definition, now=None):
        self.http = http
        self.definition = definition
        self.now = now or (lambda: datetime.now(UTC))
        self.failed_count = 0

    async def fetch(self, target) -> ExternalCompanyProfile:
        retrieved_at = self.now()
        url = PAGE_URL.format(slug=target.cmc_slug)
        response = await self.http.get(url)
        categories = parse_company_page(response.text, target.symbol)
        return ExternalCompanyProfile(
            source_key=self.definition.key,
            source_url=url,
            retrieved_at=retrieved_at,
            facts={"categories": categories} if categories else {},
            metadata={"slug": target.cmc_slug},
        )
