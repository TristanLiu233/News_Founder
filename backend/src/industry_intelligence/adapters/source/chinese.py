"""Credential-free Chinese ranking adapters using public HTML or APIs."""

import re
from datetime import UTC, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from industry_intelligence.adapters.source.base import SourceItem
from industry_intelligence.domain.enums import Domain


def _items(rows, observed_at, limit):
    return [SourceItem(**row, observed_at=observed_at) for row in rows[:limit]]


def parse_ithome(html):
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one(".rank-box")
    rows, failed = [], 0
    for card in box.select(":scope > .placeholder") if box else []:
        link, title, rank = (
            card.select_one("a[href]"),
            card.select_one(".plc-title"),
            card.select_one(".rank-num"),
        )
        external_id = card.get("data-news-id")
        if not link or not title or not rank or not external_id:
            failed += 1
            continue
        metric = card.select_one(".review-num")
        rows.append(
            dict(
                external_id=external_id,
                title=title.get_text(" ", strip=True),
                url=link["href"],
                rank=int(rank.get_text(strip=True)),
                raw_metric=int(re.sub(r"\D", "", metric.get_text()) or 0) if metric else None,
                metadata={"metric_label": "comments"},
            )
        )
    if not rows:
        raise ValueError("Unexpected IT Home daily ranking structure")
    return rows, failed


def parse_huxiu(html):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h4", string=lambda value: value and "48小时热文" in value)
    container = heading.parent.parent if heading else None
    rows, failed = [], 0
    for rank, card in enumerate(container.select(".article-wrap") if container else [], 1):
        link = card.select_one("a.title-wrap[href]")
        title = card.select_one(".title-wrap__title")
        match = re.search(r"/article/(\d+)\.html", link.get("href", "") if link else "")
        if not link or not title or not match:
            failed += 1
            continue
        rows.append(
            dict(
                external_id=match[1],
                title=title.get_text(" ", strip=True).removeprefix("原创 "),
                url=link["href"],
                rank=rank,
                raw_metric=None,
                metadata={"metric_label": "48-hour rank"},
            )
        )
    if not rows:
        raise ValueError("Unexpected Huxiu ranking structure")
    return rows, failed


def parse_eastmoney(payload):
    records = ((payload or {}).get("data") or {}).get("diff")
    if not isinstance(records, list):
        raise ValueError("Unexpected Eastmoney response")
    rows, failed = [], 0
    for rank, record in enumerate(records, 1):
        if not isinstance(record, dict) or not record.get("f12") or not record.get("f14"):
            failed += 1
            continue
        code, market = str(record["f12"]), "SH" if str(record["f12"]).startswith("6") else "SZ"
        rows.append(
            dict(
                external_id=f"{market}:{code}",
                title=str(record["f14"]),
                url=f"https://quote.eastmoney.com/{market.lower()}{code}.html",
                rank=rank,
                raw_metric=record.get("f6"),
                metadata={
                    "market": market,
                    "ticker": code,
                    "asset_class": "stock",
                    "canonical_id": f"{market}:{code}",
                    "metric_label": "turnover",
                },
            )
        )
    return rows, failed


def parse_sina(payload):
    if not isinstance(payload, list):
        raise ValueError("Unexpected Sina Finance response")
    rows, failed = [], 0
    for rank, record in enumerate(payload, 1):
        if not isinstance(record, dict) or not record.get("symbol") or not record.get("name"):
            failed += 1
            continue
        symbol = str(record["symbol"])
        market = "SH" if symbol.startswith("sh") else "SZ"
        ticker = re.sub(r"\D", "", symbol)
        rows.append(
            dict(
                external_id=f"{market}:{ticker}",
                title=str(record["name"]),
                url=f"https://finance.sina.com.cn/realstock/company/{symbol}/nc.shtml",
                rank=rank,
                raw_metric=record.get("amount"),
                metadata={
                    "market": market,
                    "ticker": ticker,
                    "asset_class": "stock",
                    "canonical_id": f"{market}:{ticker}",
                    "metric_label": "turnover",
                },
            )
        )
    return rows, failed


def parse_17173(html):
    soup = BeautifulSoup(html, "html.parser")
    rows, failed = [], 0
    for entry in soup.select(".rank-list li, .list-item, .item"):
        link = entry.select_one('a[href*="game-info-"]')
        rank_node = entry.select_one(".num, .rank, .c1")
        match = re.search(r"game-info-(\d+)", link.get("href", "") if link else "")
        rank_text = rank_node.get_text(strip=True) if rank_node else ""
        if not link or not match or not rank_text.isdigit():
            continue
        numbers = re.findall(r"\d+", entry.get_text(" ", strip=True))
        rows.append(
            dict(
                external_id=match[1],
                title=link.get_text(" ", strip=True),
                url=urljoin("https://top.17173.com", link["href"]),
                rank=int(rank_text),
                raw_metric=int(numbers[1]) if len(numbers) > 1 else None,
                metadata={"metric_label": "votes"},
            )
        )
    if not rows:
        # Current markup uses repeated c1/c2/c3 columns rather than list elements.
        for rank_node in soup.select(".c1"):
            rank_text = rank_node.get_text(strip=True)
            parent = rank_node.parent
            link = parent.select_one('a[href*="game-info-"]') if parent else None
            match = re.search(r"game-info-(\d+)", str(parent)) if parent else None
            if rank_text.isdigit() and link and match:
                rows.append(
                    dict(
                        external_id=match[1],
                        title=link.get_text(" ", strip=True),
                        url=urljoin("https://top.17173.com", link["href"]),
                        rank=int(rank_text),
                        raw_metric=None,
                        metadata={"metric_label": "votes"},
                    )
                )
    if not rows:
        raise ValueError("Unexpected 17173 ranking structure")
    return rows, failed


def parse_3dm(html):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(string=lambda value: value and "网络游戏排行榜" in value)
    container = heading.find_parent("div") if heading else None
    scope = container.parent if container else soup
    links = scope.select('a[href*="ol.3dmgame.com/"]')
    rows, seen = [], set()
    for link in links:
        title = link.get_text(" ", strip=True)
        slug = link["href"].rstrip("/").rsplit("/", 1)[-1]
        title = re.sub(r"^\d+\s+", "", title)
        title = re.split(
            r"\s+(?:动作|MOBA|射击|角色扮演|策略|卡牌|竞速|体育|冒险|休闲|内测|公测|限量测试|不限号)\b",
            title,
            maxsplit=1,
        )[0]
        if not title or slug in seen:
            continue
        seen.add(slug)
        rows.append(
            dict(
                external_id=slug,
                title=title,
                url=link["href"],
                rank=len(rows) + 1,
                raw_metric=None,
                metadata={"metric_label": "popularity"},
            )
        )
        if len(rows) >= 30:
            break
    if not rows:
        raise ValueError("Unexpected 3DM ranking structure")
    return rows, 0


def parse_gamersky(html):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h3", string=lambda value: value and "近期热游榜" in value)
    container = heading.find_parent("div", class_="Mid2_L_5") if heading else None
    rows, failed = [], 0
    for rank, item in enumerate(container.select("li.gamelist") if container else [], 1):
        link, title = item.select_one("a[href]"), item.select_one("p")
        external_id = item.get("gameid")
        if not link or not title or not external_id:
            failed += 1
            continue
        rows.append(
            dict(
                external_id=external_id,
                title=title.get_text(" ", strip=True),
                url=link["href"],
                rank=rank,
                raw_metric=None,
                metadata={"metric_label": "recent popularity"},
            )
        )
    if not rows:
        raise ValueError("Unexpected Gamersky ranking structure")
    return rows, failed


class _HtmlAdapter:
    parser = None
    url = ""

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        rows, self.failed_count = self.parser((await self.http.get(self.url)).text)
        return _items(rows, datetime.now(UTC), self.limit)


class ITHomeDailyAdapter(_HtmlAdapter):
    source_name, domain, url, parser = (
        "IT之家",
        Domain.COMPUTER,
        "https://m.ithome.com/rankm/",
        staticmethod(parse_ithome),
    )


class HuxiuHotAdapter(_HtmlAdapter):
    source_name, domain, url, parser = (
        "虎嗅",
        Domain.COMPUTER,
        "https://www.huxiu.com/",
        staticmethod(parse_huxiu),
    )


class Game17173Adapter(_HtmlAdapter):
    source_name, domain = "17173", Domain.GAMING
    url = "https://top.17173.com/list-2-0-0-0-0-0-0-0-0-0-1.html"
    parser = staticmethod(parse_17173)


class Game3DMAdapter(_HtmlAdapter):
    source_name, domain, url, parser = (
        "3DM",
        Domain.GAMING,
        "https://ol.3dmgame.com/",
        staticmethod(parse_3dm),
    )


class GamerskyHotAdapter(_HtmlAdapter):
    source_name, domain = "游民星空", Domain.GAMING
    url, parser = "https://ku.gamersky.com/", staticmethod(parse_gamersky)


class EastmoneyTurnoverAdapter:
    source_name, domain = "东方财富", Domain.FINANCE
    url = "https://push2.eastmoney.com/api/qt/clist/get"

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        params = {
            "pn": 1,
            "pz": self.limit,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f6",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f6",
        }
        rows, self.failed_count = parse_eastmoney(
            (await self.http.get(self.url, params=params)).json()
        )
        return _items(rows, datetime.now(UTC), self.limit)


class SinaTurnoverAdapter:
    source_name, domain = "新浪财经", Domain.FINANCE
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        params = {"page": 1, "num": self.limit, "sort": "amount", "asc": 0, "node": "hs_a"}
        rows, self.failed_count = parse_sina((await self.http.get(self.url, params=params)).json())
        return _items(rows, datetime.now(UTC), self.limit)
