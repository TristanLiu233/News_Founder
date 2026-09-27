"""Ranked company tables from the current English Wikipedia page via the Wikimedia REST API.

Wikimedia's Robot policy asks clients to read page HTML from ``/api/rest_v1/page/html/{title}``
rather than the Action API, and to avoid requesting non-current revisions. Production ingestion
therefore reads only the current page. The page revision it reports is the snapshot identity, so
re-reading an unchanged page is a replay; history accumulates from this project's own periodic
snapshots. Only rank, name, the ranked metric, and short metadata are kept, never article text.
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup, Tag

from industry_intelligence.adapters.company_ranking.base import (
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)

REST_HTML_URL = "https://en.wikipedia.org/api/rest_v1/page/html/{title}"
_REVISION_ABOUT = re.compile(r"/Special:Redirect/revision/(\d+)$")
_ETAG_REVISION = re.compile(r'^(?:W/)?"(\d+)/')
ARTICLE_SCHEME = "wikipedia:en"
_ESTIMATE = re.compile(r"\s*\(estimated?\)\s*$", re.IGNORECASE)
_AMOUNT = re.compile(r"^(?:US)?\$?\s*([\d,]+(?:\.\d+)?)\s*(billion|bn|million|mn)?$", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+([,.;:)])", r"\1", " ".join(text.split()))


def _cell_text(cell: Tag) -> str:
    cell = copy.copy(cell)
    for noise in cell.select("sup.reference, .flagicon, style, .sortkey"):
        noise.decompose()
    return _clean(cell.get_text(" ", strip=True))


def _header_text(cell: Tag) -> str:
    return re.sub(r"\[\s*\d+\s*\]", "", _cell_text(cell)).strip()


def article_title(cell: Tag) -> str | None:
    """Title (with any section fragment) of the first article link that is not a flag icon."""

    for anchor in cell.find_all("a", href=True):
        if anchor.find_parent(class_="flagicon") or anchor.find_parent("sup"):
            continue
        href = anchor["href"]
        # Action API HTML links as /wiki/Title; REST (Parsoid) HTML links as ./Title.
        prefix = next((p for p in ("/wiki/", "./") if href.startswith(p)), None)
        # Red links carry a query (?action=edit&redlink=1) and name no existing article.
        if prefix is None or "?" in href or "new" in (anchor.get("class") or []):
            continue
        title = unquote(href.removeprefix(prefix)).replace("_", " ")
        # Namespaced pages (File:, Help:, ...) are never company articles.
        if ":" in title.split("#", 1)[0]:
            continue
        return title
    return None


def parse_billions(raw_value: str) -> Decimal | None:
    """Parse ``$35.7 billion``, ``$350 million`` or ``7,645.80`` (a column already in
    billions) into billions; anything else stays unparsed rather than guessed."""

    match = _AMOUNT.match(raw_value.strip())
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    unit = (match.group(2) or "").lower()
    return amount / 1000 if unit in {"million", "mn"} else amount


def _find_table(soup: BeautifulSoup, name_headers, metric_prefix):
    for table in soup.select("table.wikitable"):
        header_row = table.find("tr")
        if header_row is None:
            continue
        headers = [_header_text(cell) for cell in header_row.find_all(["th", "td"])]
        rank_col = next((i for i, h in enumerate(headers) if h.lower() == "rank"), None)
        name_col = next((i for i, h in enumerate(headers) if h in name_headers), None)
        metric_col = next((i for i, h in enumerate(headers) if h.startswith(metric_prefix)), None)
        if None not in (rank_col, name_col, metric_col):
            return table, rank_col, name_col, metric_col, headers
    raise ValueError("Ranked company table not found; the page structure may have changed")


def ranking_basis(soup: BeautifulSoup) -> str | None:
    """The lead sentence stating what the table is based on, as a short attributed quote."""

    for paragraph in soup.select("p"):
        text = re.sub(r"\[\s*\d+\s*\]", "", _cell_text(paragraph))
        for sentence in re.split(r"(?<=\.)\s+", text):
            if re.search(r"\b(based (up)?on|ranked by)\b", sentence) and len(sentence) <= 300:
                return sentence
    return None


def parse_wikipedia_ranking(
    html: str, params: dict, limit: int
) -> tuple[list[CompanyRankingEntry], int, int, dict]:
    """Return entries, source row count, failed rows, and table context (header, basis)."""

    soup = BeautifulSoup(html, "html.parser")
    table, rank_col, name_col, metric_col, headers = _find_table(
        soup, params["name_headers"], params["metric_header_prefix"]
    )
    location_col = next(
        (i for i, h in enumerate(headers) if h == params.get("location_header")), None
    )
    entries: list[CompanyRankingEntry] = []
    seen_ranks: set[int] = set()
    source_rows = failed = 0
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(rank_col, name_col, metric_col):
            if cells:
                source_rows += 1
                failed += 1
            continue
        source_rows += 1
        rank_text = _cell_text(cells[rank_col])
        name = _cell_text(cells[name_col])
        if not rank_text.isdigit() or int(rank_text) < 1 or not name:
            failed += 1
            continue
        rank = int(rank_text)
        if rank in seen_ranks:
            failed += 1
            continue
        seen_ranks.add(rank)
        if len(entries) >= limit:
            continue
        raw_value = _cell_text(cells[metric_col]) or None
        title = article_title(cells[name_col])
        metadata = {}
        flag = cells[name_col].select_one(".flagicon img[alt]")
        if flag:
            metadata["country"] = flag["alt"]
        if location_col is not None:
            location_cell = cells[location_col]
            flag = location_cell.select_one(".flagicon img[alt]")
            if flag:
                metadata["country"] = flag["alt"]
            if location := _cell_text(location_cell):
                metadata["headquarters"] = location
        if title:
            metadata["article"] = title
        amount = raw_value
        if amount and _ESTIMATE.search(amount):
            metadata["estimate"] = True
            amount = _ESTIMATE.sub("", amount)
        entries.append(
            CompanyRankingEntry(
                rank=rank,
                name=name,
                raw_value=raw_value,
                metric_value=parse_billions(amount) if amount else None,
                identities=((ARTICLE_SCHEME, title),) if title else (),
                metadata=metadata,
            )
        )
    entries.sort(key=lambda entry: entry.rank)
    return (
        entries,
        source_rows,
        failed,
        {
            "metric_header": headers[metric_col],
            "basis": ranking_basis(soup),
        },
    )


def revision_url(page: str, revision_id: int) -> str:
    """Human-readable permanent link to the exact revision a snapshot was taken from."""

    return f"https://en.wikipedia.org/w/index.php?title={quote(page)}&oldid={revision_id}"


def page_revision(soup: BeautifulSoup, etag: str | None) -> tuple[int, datetime | None]:
    """Revision id and modification time that the REST HTML reports for the current page."""

    revision = None
    html = soup.find("html")
    if html is not None and (match := _REVISION_ABOUT.search(html.get("about", ""))):
        revision = int(match.group(1))
    elif etag and (match := _ETAG_REVISION.match(etag)):
        revision = int(match.group(1))
    if revision is None:
        raise ValueError("Page revision not reported; cannot identify the snapshot")
    modified = soup.find("meta", attrs={"property": "dc:modified"})
    published_at = None
    if modified is not None and modified.get("content"):
        published_at = datetime.fromisoformat(modified["content"].replace("Z", "+00:00"))
    return revision, published_at


class WikipediaRankingAdapter:
    """Reads the current page only; there is deliberately no historical-revision option."""

    def __init__(self, http, definition, now=None):
        self.http = http
        self.definition = definition
        self.now = now or (lambda: datetime.now(UTC))
        self.failed_count = 0

    async def fetch(self) -> CompanyRankingSnapshot:
        observed_at = self.now()
        page = self.definition.params["page"]
        url = REST_HTML_URL.format(title=quote(page.replace(" ", "_"), safe=""))
        response = await self.http.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        revision_id, published_at = page_revision(soup, response.headers.get("etag"))
        entries, source_rows, self.failed_count, context = parse_wikipedia_ranking(
            response.text, self.definition.params, self.definition.entry_limit
        )
        return CompanyRankingSnapshot(
            snapshot_key=f"revision:{revision_id}",
            observed_at=observed_at,
            published_at=published_at,
            source_url=revision_url(page, revision_id),
            entries=tuple(entries),
            source_entry_count=source_rows,
            metadata={
                "page": page,
                "revision_id": revision_id,
                "endpoint": "rest_v1/page/html",
                **context,
                "license": "CC BY-SA 4.0",
            },
        )
