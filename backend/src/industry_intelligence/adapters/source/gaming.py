"""Steam and itch.io ranked gaming adapters."""

import re
from datetime import UTC, datetime
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from industry_intelligence.adapters.source.base import SourceItem
from industry_intelligence.domain.enums import Domain


def parse_steam_most_played(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tbody tr")
    if not rows:
        raise ValueError("Unexpected Steam chart structure")
    output, failed = [], 0
    for source_rank, row in enumerate(rows, 1):
        link = next((a for a in row.select('a[href*="/app/"]') if a.get_text(strip=True)), None)
        match = re.search(r"/app/(\d+)/", link.get("href", "") if link else "")
        cells = row.select("td")
        if not link or not match or len(cells) < 4:
            failed += 1
            continue
        numbers = [
            int(value.replace(",", ""))
            for value in re.findall(r"\b[\d,]{4,}\b", row.get_text(" ", strip=True))
        ]
        output.append(
            {
                "external_id": match[1],
                "title": link.get_text(" ", strip=True),
                "url": f"https://store.steampowered.com/app/{match[1]}/",
                "rank": source_rank,
                "raw_metric": numbers[0] if numbers else None,
                "metadata": {
                    "app_id": match[1],
                    "metric_label": "current players",
                    "peak_today": numbers[1] if len(numbers) > 1 else None,
                },
            }
        )
    if not output:
        raise ValueError("No valid Steam entries")
    return output, failed


def parse_itch_top_sellers(xml):
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("Malformed itch.io feed") from exc
    output, failed = [], 0
    for source_rank, item in enumerate(root.findall("./channel/item"), 1):
        title = item.findtext("plainTitle") or item.findtext("title")
        url = item.findtext("link") or item.findtext("guid")
        if not title or not url:
            failed += 1
            continue
        output.append(
            {
                "external_id": url.rstrip("/").rsplit("/", 1)[-1],
                "title": title,
                "url": url,
                "rank": source_rank,
                "raw_metric": item.findtext("price"),
                "metadata": {"price": item.findtext("price"), "metric_label": "listed price"},
            }
        )
    return output, failed


class SteamMostPlayedAdapter:
    source_name, domain = "Steam", Domain.GAMING

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get("https://store.steampowered.com/charts/mostplayed")
        rows, self.failed_count = parse_steam_most_played(response.text)
        return [SourceItem(**row, observed_at=observed_at) for row in rows[: self.limit]]


class ItchTopSellersAdapter:
    source_name, domain = "itch.io", Domain.GAMING

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get("https://itch.io/games/top-sellers.xml")
        rows, self.failed_count = parse_itch_top_sellers(response.text)
        return [SourceItem(**row, observed_at=observed_at) for row in rows[: self.limit]]
