"""Public Computer and Gaming news RSS adapters (Phase 6.1).

Each feed is the publisher's own public RSS; its item order is chronology, never a rank. Only the
headline, original URL, publication time, source-native categories, and a short excerpt are
kept: some feeds (IT之家, GameLook) embed the full article body in ``description`` or
``content:encoded``, which is never stored.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from xml.etree import ElementTree

from industry_intelligence.adapters.source.base import ContentSourceItem
from industry_intelligence.adapters.source.finance_news import _clean, _time
from industry_intelligence.domain.enums import Domain

EXCERPT_LIMIT = 280
CATEGORY_LIMIT = 12
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


def excerpt(value, limit=EXCERPT_LIMIT) -> str | None:
    """A short plain-text excerpt: HTML removed, cut at ``limit`` characters."""

    text = _clean(value)
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_news_rss(payload, observed_at, language, article_id=None):
    """Parse an RSS 2.0 news feed; malformed XML raises ``ValueError``.

    ``article_id(url)`` optionally returns the publisher's native article number, kept as
    ``metadata["source_article_id"]`` for exact same-article identity.
    """

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ValueError("Malformed RSS feed") from exc
    if root.find("channel") is None:
        raise ValueError("Unexpected RSS structure")
    output, failed = [], 0
    for row in root.findall("./channel/item"):
        title = _clean(row.findtext("title"))
        url = _clean(row.findtext("link"))
        published = _time(row.findtext("pubDate"))
        if not title or not url.startswith(("http://", "https://")) or published is None:
            failed += 1
            continue
        categories = []
        for category in row.findall("category"):
            name = _clean(category.text)
            if name and name not in categories:
                categories.append(name)
        metadata = {"categories": categories[:CATEGORY_LIMIT]}
        author = _clean(row.findtext(DC_CREATOR))
        if author:
            metadata["author"] = author
        native = article_id(url) if article_id else None
        if native:
            metadata["source_article_id"] = native
        output.append(
            ContentSourceItem(
                external_id=_clean(row.findtext("guid")) or url,
                title=title,
                url=url,
                published_at=published,
                observed_at=observed_at,
                language=language,
                description=excerpt(row.findtext("description")),
                metadata=metadata,
            )
        )
    return output, failed


class RSSNewsAdapter:
    source_name = ""
    domain: Domain
    language = "en"
    feed_url = ""

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    @staticmethod
    def article_id(url) -> str | None:
        return None

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(self.feed_url)
        rows, self.failed_count = parse_news_rss(
            response.text, observed_at, self.language, self.article_id
        )
        return rows[: self.limit]


ITHOME_ARTICLE = re.compile(r"^https?://(?:www\.)?ithome\.com/(\d)/(\d{3})/(\d{3})\.htm$")


class ITHomeNewsAdapter(RSSNewsAdapter):
    source_name = "IT之家"
    domain = Domain.COMPUTER
    language = "zh-CN"
    feed_url = "https://www.ithome.com/rss/"

    @staticmethod
    def article_id(url):
        # ``https://www.ithome.com/1/007/244.htm`` is news 1007244, the same ID the IT之家 日榜
        # ranking publishes as ``data-news-id`` (``https://m.ithome.com/html/1007244.htm``).
        match = ITHOME_ARTICLE.match(url)
        return "".join(match.groups()) if match else None


class TechCrunchAdapter(RSSNewsAdapter):
    source_name = "TechCrunch"
    domain = Domain.COMPUTER
    feed_url = "https://techcrunch.com/feed/"


class ArsTechnicaITAdapter(RSSNewsAdapter):
    source_name = "Ars Technica"
    domain = Domain.COMPUTER
    feed_url = "https://feeds.arstechnica.com/arstechnica/technology-lab"


class GamesIndustryBizAdapter(RSSNewsAdapter):
    source_name = "GamesIndustry.biz"
    domain = Domain.GAMING
    feed_url = "https://www.gamesindustry.biz/feed"


class GematsuAdapter(RSSNewsAdapter):
    source_name = "Gematsu"
    domain = Domain.GAMING
    feed_url = "https://www.gematsu.com/feed"


class GameLookAdapter(RSSNewsAdapter):
    source_name = "GameLook"
    domain = Domain.GAMING
    language = "zh-CN"
    # The publisher serves no HTTPS; ``/feed`` answers 301 to ``/feed/``.
    feed_url = "http://www.gamelook.com.cn/feed/"
