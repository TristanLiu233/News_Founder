"""Publishers' own ranked article lists (native news rankings).

Each adapter reads one list that the publisher itself orders by popularity (clicks, popular
posts, hot, top stories) and returns ``RankedContentItem``s whose ``rank`` is the publisher's
position in that list — never a chronological index. Nothing is scored, merged, or re-ordered
here; the list's window and basis are recorded only when the publisher states them.

An adapter with ``lookup_articles`` also defines ``article_details(url)``: the publication time
and the publisher's stable article ID read from the article page. The ingestion service calls
it only for listed articles whose URL is not stored yet and whose row lacks a date or an ID
(``lookup_delay`` seconds apart); a failed lookup leaves both unknown. The stable ID lets an
article whose URL the publisher edited resolve to the row its feed stored under that ID.

An adapter may also build its entries from a Subject-ranking snapshot this run already stored
(``entries_from_observations``), so the same upstream list is not requested twice.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from industry_intelligence.adapters.source.base import (
    ContentSourceItem,
    RankedContentItem,
    SourceItem,
)
from industry_intelligence.adapters.source.finance_news import _clean, _time
from industry_intelligence.adapters.source.hacker_news import HackerNewsAdapter
from industry_intelligence.domain.enums import Domain

BEIJING = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class ArticleDetails:
    """What an article page states: publication time and the publisher's stable article ID."""

    published_at: datetime | None = None
    external_id: str | None = None


class NewsRankingAdapter:
    """Common shape: ``fetch()`` → ranked entries; ``window`` is set by ``fetch`` when stated."""

    source_name = ""
    domain: Domain
    language = "en"
    lookup_delay = 0.0
    lookup_articles = False

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0
        self.window: str | None = None

    async def article_details(self, url) -> ArticleDetails | None:
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


# --- 东方财富 网友点击排行榜 (Product-1) ---------------------------------------------------------

EASTMONEY_ARTICLE = re.compile(r"^https?://finance\.eastmoney\.com/a/(\d+)\.html$")
EASTMONEY_TIME = re.compile(r"(\d{4})年(\d{2})月(\d{2})日\s*(\d{2}):(\d{2})")


def parse_eastmoney_click_rank(html, observed_at):
    """The ``网友点击排行榜`` (reader click ranking) block, rows in the publisher's own numbering.

    ``rank`` is the number the publisher prints next to each row (``span.no``), not a position
    counted here; a row without a valid number, a foreign link, or a title is dropped and the
    others keep their numbers. The article code in the URL is the publisher's stable ID (the
    same code its 7×24 feed rows carry). The block states no period and no click counts, so
    neither is recorded. Other blocks on the page (dated editorial 专题, columns) are not read.
    """

    soup = BeautifulSoup(html, "html.parser")
    heading = next((h2 for h2 in soup.find_all("h2") if "网友点击排行榜" in h2.get_text()), None)
    block = heading.find_next("ul") if heading else None
    if block is None or not block.find_all("li"):
        raise ValueError("东方财富 网友点击排行榜 block not found")
    entries, failed = [], 0
    for row in block.find_all("li"):
        number = row.select_one("span.no")
        link = row.find("a", href=True)
        text = number.get_text(strip=True) if number else ""
        match = EASTMONEY_ARTICLE.match(link["href"]) if link else None
        title = _clean(link.get_text(" ", strip=True)) if link else ""
        if not text.isdigit() or int(text) < 1 or not match or not title:
            failed += 1
            continue
        entries.append(
            RankedContentItem(
                rank=int(text),
                item=ContentSourceItem(
                    match.group(1), title, link["href"], None, observed_at, "zh-CN"
                ),
            )
        )
    return entries, failed


def parse_eastmoney_published(html):
    """The article page's publication time (``div.infos``, Beijing time), else None."""

    soup = BeautifulSoup(html, "html.parser")
    infos = soup.select_one("div.infos")
    match = EASTMONEY_TIME.search(infos.get_text(" ", strip=True)) if infos else None
    if not match:
        return None
    year, month, day, hour, minute = (int(value) for value in match.groups())
    try:
        local = datetime(year, month, day, hour, minute, tzinfo=BEIJING)
    except ValueError:
        return None
    return local.astimezone(UTC)


class EastmoneyClickRankAdapter(NewsRankingAdapter):
    source_name = "东方财富"
    domain = Domain.FINANCE
    language = "zh-CN"
    # The 热点扫描 column page, which carries the site's 网友点击排行榜 block.
    page_url = "https://finance.eastmoney.com/a/crdsm.html"
    lookup_delay = 1.0
    lookup_articles = True

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(self.page_url)
        entries, self.failed_count = parse_eastmoney_click_rank(response.text, observed_at)
        return entries[: self.limit]

    async def article_details(self, url):
        # The article code is already the row's external ID; the page only adds the time.
        return ArticleDetails(parse_eastmoney_published((await self.http.get(url)).text))


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
# WordPress declares the post's REST resource, whose number is the post ID of the feed GUID.
WP_REST_POST = re.compile(
    r'<link[^>]+type="application/json"[^>]+href="'
    r'https://www\.gematsu\.com/wp-json/wp/v2/posts/(\d+)"',
    re.I,
)
WP_BODY_POST = re.compile(r'<body[^>]+class="[^"]*\bpostid-(\d+)\b', re.I)
GEMATSU_GUID = "https://www.gematsu.com/?p={}"


def parse_gematsu_article(html):
    """Publication time and the WordPress post ID in the RSS GUID form (``…/?p=<id>``).

    The ID comes from the page's REST link; when the body class names a different post, the ID
    is left unknown (fail closed) rather than guessed.
    """

    published = PUBLISHED_META.search(html)
    rest, body = WP_REST_POST.search(html), WP_BODY_POST.search(html)
    post_id = rest.group(1) if rest else None
    if post_id and body and body.group(1) != post_id:
        post_id = None
    return ArticleDetails(
        _time(published.group(1)) if published else None,
        GEMATSU_GUID.format(post_id) if post_id else None,
    )


class GematsuPopularAdapter(NewsRankingAdapter):
    source_name = "Gematsu"
    domain = Domain.GAMING
    page_url = "https://www.gematsu.com/"
    # robots.txt: Crawl-delay 10.
    lookup_delay = 10.0
    lookup_articles = True

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(self.page_url)
        entries, self.failed_count = parse_gematsu_popular(response.text, observed_at)
        return entries[: self.limit]

    async def article_details(self, url):
        return parse_gematsu_article((await self.http.get(url)).text)


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
    lookup_articles = True

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(self.page_url)
        entries, self.failed_count = parse_3dm_hot(response.text, observed_at)
        return entries[: self.limit]

    async def article_details(self, url):
        # The article number is already the list row's external ID.
        return ArticleDetails(parse_3dm_published((await self.http.get(url)).text))


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


def hn_story_id(hn_url):
    """The story ID of ``https://news.ycombinator.com/item?id=<id>``, else None."""

    values = parse_qs(urlsplit(hn_url or "").query).get("id", [])
    return values[0] if len(values) == 1 and values[0].isdigit() else None


class HackerNewsTopContentAdapter(NewsRankingAdapter):
    """The official Firebase ``topstories`` list, read through the existing Top Stories adapter.

    In a daily run the Subject ranking (``hacker_news``) has already stored this run's Top
    Stories snapshot; ``entries_from_observations`` builds the ranked articles from it, so the
    list and its items are requested once per run. ``fetch`` is the fallback when no snapshot of
    this run exists (manual ingestion, or the ranking was not run or stored nothing).
    """

    source_name = "Hacker News"
    domain = Domain.COMPUTER

    @staticmethod
    def entries_from_observations(observations):
        return hn_ranked_entries(
            SourceItem(
                hn_story_id((row.item_metadata or {}).get("hn_url")),
                row.title,
                row.url,
                row.rank,
                row.raw_metric,
                row.observed_at,
                row.item_metadata or {},
            )
            for row in observations
            if row.title and row.url
        )

    async def fetch(self):
        adapter = HackerNewsAdapter(self.http, self.limit)
        items = await adapter.fetch()
        self.failed_count = adapter.failed_count
        return hn_ranked_entries(items)
