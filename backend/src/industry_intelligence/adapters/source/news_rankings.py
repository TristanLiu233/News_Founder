"""Publishers' own ranked article lists (native news rankings).

Each adapter reads one list that the publisher itself orders by popularity (clicks, popular
posts, hot, top stories) and returns ``RankedContentItem``s whose ``rank`` is the publisher's
position in that list — never a chronological index. Nothing is scored, merged, or re-ordered
here; the list's window and basis are recorded only when the publisher states them.

An adapter may also define ``published_at(url)``: the publication time from the article page,
used by the ingestion service only for listed articles that are not stored yet and whose list
row has no date (``lookup_delay`` seconds apart). A failed lookup leaves the time unknown.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from industry_intelligence.adapters.source.base import ContentSourceItem, RankedContentItem
from industry_intelligence.adapters.source.finance_news import _clean, _time
from industry_intelligence.adapters.source.hacker_news import HackerNewsAdapter
from industry_intelligence.domain.enums import Domain

BEIJING = ZoneInfo("Asia/Shanghai")


class NewsRankingAdapter:
    """Common shape: ``fetch()`` → ranked entries; ``window`` is set by ``fetch`` when stated."""

    source_name = ""
    domain: Domain
    language = "en"
    lookup_delay = 0.0

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0
        self.window: str | None = None

    async def published_at(self, url) -> datetime | None:
        return None


# --- 新浪财经 热门新闻排行 · 新闻榜 (点击量排行) -----------------------------------------------

SINA_JS_VAR = re.compile(r"=\s*(\{.*\})\s*;?\s*$", re.S)


def parse_sina_hot(payload, observed_at):
    """``var name = {"data": [...]}``: rows in the publisher's click order (``top_order=DESC``).

    Returns ``(entries, failed, window)``; the window is the publisher's own ``top_time`` of the
    daily (``top_type=day``) list, e.g. ``day:2026-09-30``.
    """

    match = SINA_JS_VAR.search(payload.strip())
    if not match:
        raise ValueError("Unexpected Sina ranking response")
    try:
        data = json.loads(match.group(1)).get("data")
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ValueError("Unexpected Sina ranking response") from exc
    if not isinstance(data, list):
        raise ValueError("Unexpected Sina ranking response")
    entries, failed, windows = [], 0, set()
    for rank, row in enumerate(data, 1):
        if not isinstance(row, dict):
            failed += 1
            continue
        title, url = _clean(row.get("title")), _clean(row.get("url"))
        published = None
        date, clock = row.get("create_date"), row.get("create_time")
        if date and clock:
            try:
                published = (
                    datetime.fromisoformat(f"{date}T{clock}")
                    .replace(tzinfo=BEIJING)
                    .astimezone(UTC)
                )
            except ValueError:
                published = None
        if not title or not url.startswith(("http://", "https://")):
            failed += 1
            continue
        if re.fullmatch(r"\d{8}", str(row.get("top_time") or "")):
            value = str(row["top_time"])
            windows.add(f"day:{value[:4]}-{value[4:6]}-{value[6:]}")
        metadata = {"media": _clean(row.get("media"))} if _clean(row.get("media")) else {}
        entries.append(
            RankedContentItem(
                rank=rank,
                item=ContentSourceItem(
                    external_id=None,
                    title=title,
                    url=url,
                    published_at=published,
                    observed_at=observed_at,
                    language="zh-CN",
                    metadata=metadata,
                ),
                native_value=_clean(row.get("top_num")) or None,
            )
        )
    return entries, failed, (windows.pop() if len(windows) == 1 else None)


class SinaFinanceHotAdapter(NewsRankingAdapter):
    source_name = "新浪财经"
    domain = Domain.FINANCE
    language = "zh-CN"
    page_url = "https://finance.sina.com.cn/topnews/"
    endpoint = "https://top.finance.sina.com.cn/ws/GetTopDataList.php"

    async def fetch(self):
        observed_at = datetime.now(UTC)
        # The publisher's page requests today's (Beijing) daily list with these parameters.
        response = await self.http.get(
            self.endpoint,
            params={
                "top_type": "day",
                "top_cat": "finance_news_0_suda",
                "top_time": observed_at.astimezone(BEIJING).strftime("%Y%m%d"),
                "get_new": 1,
                "top_show_num": min(self.limit, 20),
                "top_order": "DESC",
                "js_var": "news_1_data",
            },
        )
        entries, self.failed_count, self.window = parse_sina_hot(response.text, observed_at)
        return entries


# --- Gematsu Popular Posts --------------------------------------------------------------------


def parse_gematsu_popular(html, observed_at):
    """The homepage's ``Popular Posts`` section, in the publisher's order."""

    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("section.gematsu-popular")
    if section is None:
        raise ValueError("Gematsu Popular Posts section not found")
    entries, failed = [], 0
    for rank, post in enumerate(section.select("article"), 1):
        link = post.select_one(".gematsu-post__info a[href]") or post.select_one("h6 a[href]")
        title = _clean(link.get_text(" ", strip=True)) if link else ""
        url = link["href"] if link else ""
        if not title or not url.startswith(("http://", "https://")):
            failed += 1
            continue
        entries.append(
            RankedContentItem(
                rank=rank,
                item=ContentSourceItem(None, title, url, None, observed_at, "en"),
            )
        )
    return entries, failed


PUBLISHED_META = re.compile(
    r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"', re.I
)


class GematsuPopularAdapter(NewsRankingAdapter):
    source_name = "Gematsu"
    domain = Domain.GAMING
    page_url = "https://www.gematsu.com/"
    # robots.txt: Crawl-delay 10.
    lookup_delay = 10.0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(self.page_url)
        entries, self.failed_count = parse_gematsu_popular(response.text, observed_at)
        return entries[: self.limit]

    async def published_at(self, url):
        match = PUBLISHED_META.search((await self.http.get(url)).text)
        return _time(match.group(1)) if match else None


# --- 3DM 近期热门 --------------------------------------------------------------------------------

ARTICLE_3DM = re.compile(r"^https?://www\.3dmgame\.com/news/\d{6}/(\d+)\.html$")
PUBDATE_3DM = re.compile(r'"pubDate"\s*:\s*"([^"]+)"')


def parse_3dm_hot(html, observed_at):
    """The news page's ``近期热门`` block (``div.net`` after its title), in publisher order."""

    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("span", string=lambda value: value and "近期热门" in value)
    heading = heading or next(
        (span for span in soup.find_all("span") if "近期热门" in span.get_text()), None
    )
    block = heading.find_next("div", class_="net") if heading else None
    if block is None:
        raise ValueError("3DM 近期热门 block not found")
    entries, failed = [], 0
    for rank, link in enumerate(block.find_all("a", href=True), 1):
        title = _clean(link.get_text(" ", strip=True))
        match = ARTICLE_3DM.match(link["href"])
        if not title or not match:
            failed += 1
            continue
        entries.append(
            RankedContentItem(
                rank=rank,
                item=ContentSourceItem(
                    match.group(1), title, link["href"], None, observed_at, "zh-CN"
                ),
            )
        )
    return entries, failed


def parse_3dm_published(html):
    """The article's JSON-LD ``pubDate`` (Beijing time, no offset)."""

    match = PUBDATE_3DM.search(html)
    if not match:
        return None
    try:
        parsed = datetime.fromisoformat(match.group(1))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=BEIJING)).astimezone(UTC)


class Game3DMHotAdapter(NewsRankingAdapter):
    source_name = "3DM"
    domain = Domain.GAMING
    language = "zh-CN"
    page_url = "https://www.3dmgame.com/news/"
    lookup_delay = 1.0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(self.page_url)
        entries, self.failed_count = parse_3dm_hot(response.text, observed_at)
        return entries[: self.limit]

    async def published_at(self, url):
        return parse_3dm_published((await self.http.get(url)).text)


# --- Hacker News Top Stories --------------------------------------------------------------------


def hn_ranked_entries(items):
    """Top Stories rows (``SourceItem``) as ranked articles; ``rank`` is HN's own position."""

    entries = []
    for row in items:
        meta = row.metadata or {}
        published = _time(meta.get("time")) if meta.get("time") is not None else None
        entries.append(
            RankedContentItem(
                rank=row.rank,
                item=ContentSourceItem(
                    external_id=row.external_id,
                    title=row.title,
                    url=row.url,
                    published_at=published,
                    observed_at=row.observed_at,
                    language="en",
                    metadata={
                        key: meta[key]
                        for key in ("hn_url", "author", "comments")
                        if meta.get(key) is not None
                    },
                ),
                native_value=str(row.raw_metric) if row.raw_metric is not None else None,
            )
        )
    return entries


class HackerNewsTopContentAdapter(NewsRankingAdapter):
    """The official Firebase ``topstories`` list, read through the existing Top Stories adapter."""

    source_name = "Hacker News"
    domain = Domain.COMPUTER

    async def fetch(self):
        adapter = HackerNewsAdapter(self.http, self.limit)
        items = await adapter.fetch()
        self.failed_count = adapter.failed_count
        return hn_ranked_entries(items)
