"""Credential-free public finance ranking adapters."""

from datetime import UTC, datetime

from industry_intelligence.adapters.source.base import SourceItem
from industry_intelligence.domain.enums import Domain


class CoinGeckoTrendingAdapter:
    source_name = "CoinGecko"
    domain = Domain.FINANCE

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        payload = (await self.http.get("https://api.coingecko.com/api/v3/search/trending")).json()
        rows = payload.get("coins") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Unexpected CoinGecko response")
        items = []
        for source_rank, row in enumerate(rows[: self.limit], 1):
            data = row.get("item") if isinstance(row, dict) else None
            if not isinstance(data, dict) or not data.get("id") or not data.get("name"):
                self.failed_count += 1
                continue
            items.append(
                SourceItem(
                    external_id=str(data["id"]),
                    title=str(data["name"]),
                    url=f"https://www.coingecko.com/en/coins/{data['id']}",
                    rank=source_rank,
                    raw_metric=data.get("market_cap_rank"),
                    observed_at=observed_at,
                    metadata={
                        "symbol": data.get("symbol"),
                        "canonical_id": (
                            f"crypto:{str(data['symbol']).upper()}" if data.get("symbol") else None
                        ),
                        "asset_class": "crypto",
                        "market_cap_rank": data.get("market_cap_rank"),
                        "metric_label": "market cap rank",
                    },
                )
            )
        if rows and not items:
            raise ValueError("No valid CoinGecko entries")
        return items


class CoinPaprikaMarketAdapter:
    source_name = "CoinPaprika"
    domain = Domain.FINANCE

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        rows = (await self.http.get("https://api.coinpaprika.com/v1/tickers")).json()
        if not isinstance(rows, list):
            raise ValueError("Unexpected CoinPaprika response")
        items = []
        for source_rank, row in enumerate(rows, 1):
            if len(items) >= self.limit:
                break
            if not isinstance(row, dict) or not row.get("id") or not row.get("name"):
                self.failed_count += 1
                continue
            market_cap = ((row.get("quotes") or {}).get("USD") or {}).get("market_cap")
            items.append(
                SourceItem(
                    external_id=str(row["id"]),
                    title=str(row["name"]),
                    url=f"https://coinpaprika.com/coin/{row['id']}/",
                    rank=source_rank,
                    raw_metric=market_cap,
                    observed_at=observed_at,
                    metadata={
                        "symbol": row.get("symbol"),
                        "canonical_id": (
                            f"crypto:{str(row['symbol']).upper()}" if row.get("symbol") else None
                        ),
                        "asset_class": "crypto",
                        "provider_rank": row.get("rank"),
                        "metric_label": "USD market cap",
                    },
                )
            )
        if rows and not items:
            raise ValueError("No valid CoinPaprika entries")
        return items
