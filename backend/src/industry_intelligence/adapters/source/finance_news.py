"""Public finance news/feed adapters with no ranking semantics."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from industry_intelligence.adapters.source.base import ContentSourceItem
from industry_intelligence.domain.enums import Domain


def _clean(value) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def _time(value, default_tz=UTC) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = int(value)
            if number > 10_000_000_000:
                number //= 1000
            return datetime.fromtimestamp(number, UTC)
        parsed = parsedate_to_datetime(str(value))
        return parsed.replace(tzinfo=parsed.tzinfo or default_tz).astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or default_tz).astimezone(UTC)
        except ValueError:
            return None


def parse_rss(payload: str, observed_at: datetime, language: str):
    root = ElementTree.fromstring(payload)
    output, failed = [], 0
    for row in root.findall(".//item"):
        title = _clean(row.findtext("title"))
        url = _clean(row.findtext("link"))
        published = _time(row.findtext("pubDate") or row.findtext("{*}date"))
        external_id = _clean(row.findtext("guid")) or url
        if not title or not url or published is None:
            failed += 1
            continue
        output.append(
            ContentSourceItem(
                external_id=external_id,
                title=title,
                url=url,
                published_at=published,
                observed_at=observed_at,
                language=language,
                description=_clean(row.findtext("description")) or None,
            )
        )
    return output, failed


class RSSFinanceNewsAdapter:
    domain = Domain.FINANCE
    language = "en"
    feed_url = ""

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(self.feed_url)
        rows, self.failed_count = parse_rss(response.text, observed_at, self.language)
        return rows[: self.limit]


class CNBCMarketsAdapter(RSSFinanceNewsAdapter):
    source_name = "CNBC"
    feed_url = "https://www.cnbc.com/id/100003114/device/rss/rss.html"


class NasdaqMarketsAdapter(RSSFinanceNewsAdapter):
    source_name = "Nasdaq"
    feed_url = "https://www.nasdaq.com/feed/rssoutbound?category=Markets"


def parse_eastmoney_news(payload, observed_at):
    container = payload.get("data") if isinstance(payload, dict) else None
    rows = (container or {}).get("fastNewsList") if isinstance(container, dict) else None
    if not isinstance(rows, list):
        rows = (container or {}).get("list") if isinstance(container, dict) else None
    if not isinstance(rows, list):
        rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Unexpected Eastmoney news response")
    output, failed = [], 0
    for row in rows:
        if not isinstance(row, dict):
            failed += 1
            continue
        external_id = row.get("code") or row.get("art_code") or row.get("id")
        title = _clean(row.get("title") or row.get("summary") or row.get("content"))
        url = row.get("url") or row.get("share_url")
        if not url and external_id:
            url = f"https://finance.eastmoney.com/a/{external_id}.html"
        published = _time(
            row.get("display_time") or row.get("showTime") or row.get("time"),
            ZoneInfo("Asia/Shanghai"),
        )
        if not title or not url or published is None:
            failed += 1
            continue
        output.append(
            ContentSourceItem(str(external_id or url), title, url, published, observed_at, "zh-CN")
        )
    return output, failed


class EastmoneyNewsAdapter:
    source_name = "东方财富"
    domain = Domain.FINANCE
    endpoint = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(
            self.endpoint,
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": 102,
                "sortEnd": "",
                "pageSize": self.limit,
                "req_trace": "industry-intelligence",
            },
        )
        rows, self.failed_count = parse_eastmoney_news(response.json(), observed_at)
        return rows[: self.limit]


def parse_sina_news(payload, observed_at):
    result = payload.get("result") if isinstance(payload, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    feed = data.get("feed") if isinstance(data, dict) else None
    rows = feed.get("list") if isinstance(feed, dict) else feed
    if not isinstance(rows, list):
        raise ValueError("Unexpected Sina news response")
    output, failed = [], 0
    for row in rows:
        external_id = row.get("id") if isinstance(row, dict) else None
        title = _clean((row or {}).get("rich_text") or (row or {}).get("content"))
        published = _time(
            (row or {}).get("create_time") or (row or {}).get("ctime"),
            ZoneInfo("Asia/Shanghai"),
        )
        url = (
            (row or {}).get("docurl")
            or (row or {}).get("url")
            or (f"https://finance.sina.com.cn/7x24/{external_id}.shtml" if external_id else None)
        )
        if not title or not url or published is None:
            failed += 1
            continue
        output.append(
            ContentSourceItem(str(external_id or url), title, url, published, observed_at, "zh-CN")
        )
    return output, failed


class SinaFinanceNewsAdapter:
    source_name = "新浪财经"
    domain = Domain.FINANCE
    endpoint = "https://zhibo.sina.com.cn/api/zhibo/feed"

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get(
            self.endpoint, params={"page": 1, "page_size": self.limit, "zhibo_id": 152}
        )
        rows, self.failed_count = parse_sina_news(response.json(), observed_at)
        return rows[: self.limit]
