"""companiesmarketcap.com category rankings via the site's public CSV download.

Each category page offers a "Download the list" CSV (``?download=csv``); robots.txt does not
restrict these paths. Rankings are ordered by market capitalization and updated daily, so the
UTC observation day is the snapshot identity.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from industry_intelligence.adapters.company_ranking.base import (
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)

SYMBOL_SCHEME = "companiesmarketcap:symbol"
REQUIRED_COLUMNS = {"Rank", "Name", "Symbol", "marketcap"}


def _decimal(value: str) -> Decimal | None:
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def parse_companiesmarketcap_csv(
    text: str, limit: int
) -> tuple[list[CompanyRankingEntry], int, int]:
    """Return the first ``limit`` valid entries, the source row count, and failed rows."""

    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not REQUIRED_COLUMNS <= set(reader.fieldnames or ()):
        raise ValueError(f"Unexpected companiesmarketcap CSV header: {reader.fieldnames}")
    entries: list[CompanyRankingEntry] = []
    seen_ranks: set[int] = set()
    source_rows = failed = 0
    for row in reader:
        source_rows += 1
        rank_text = (row.get("Rank") or "").strip()
        name = " ".join((row.get("Name") or "").split())
        symbol = (row.get("Symbol") or "").strip()
        raw_value = (row.get("marketcap") or "").strip() or None
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
        metadata = {
            key: value
            for key, value in {
                "symbol": symbol or None,
                "price_usd": (row.get("price (USD)") or "").strip() or None,
                "country": (row.get("country") or "").strip() or None,
            }.items()
            if value
        }
        entries.append(
            CompanyRankingEntry(
                rank=rank,
                name=name,
                raw_value=raw_value,
                metric_value=_decimal(raw_value) if raw_value else None,
                identities=((SYMBOL_SCHEME, symbol),) if symbol else (),
                metadata=metadata,
            )
        )
    entries.sort(key=lambda entry: entry.rank)
    return entries, source_rows, failed


class CompaniesMarketCapAdapter:
    def __init__(self, http, definition, now=None):
        self.http = http
        self.definition = definition
        self.now = now or (lambda: datetime.now(UTC))
        self.failed_count = 0

    async def fetch(self) -> CompanyRankingSnapshot:
        observed_at = self.now()
        response = await self.http.get(self.definition.url, params={"download": "csv"})
        entries, source_rows, self.failed_count = parse_companiesmarketcap_csv(
            response.text, self.definition.entry_limit
        )
        return CompanyRankingSnapshot(
            snapshot_key=f"daily:{observed_at.astimezone(UTC).date().isoformat()}",
            observed_at=observed_at,
            source_url=self.definition.url,
            entries=tuple(entries),
            source_entry_count=source_rows,
            metadata={"format": "csv", "stored_limit": self.definition.entry_limit},
        )
