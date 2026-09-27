import asyncio

import httpx
import pytest

from industry_intelligence.adapters.source.finance import (
    CoinGeckoTrendingAdapter,
    CoinPaprikaMarketAdapter,
)
from industry_intelligence.adapters.source.gaming import (
    ItchTopSellersAdapter,
    SteamMostPlayedAdapter,
    parse_itch_top_sellers,
    parse_steam_most_played,
)
from industry_intelligence.config import Settings
from industry_intelligence.infrastructure.http import HTTPClient

COINGECKO = {
    "coins": [
        {"item": {"id": "btc", "name": "Bitcoin", "symbol": "BTC", "market_cap_rank": 1}},
        {"bad": True},
    ]
}
PAPRIKA = [
    {
        "id": "btc-bitcoin",
        "name": "Bitcoin",
        "symbol": "BTC",
        "rank": 1,
        "quotes": {"USD": {"market_cap": 123}},
    },
    {"bad": True},
]
STEAM = """<table><tbody>
<tr><td>1</td><td><a href="https://store.steampowered.com/app/730/x">Counter-Strike 2</a></td>
<td>Free</td><td>1,024,230</td><td>1,308,112</td></tr>
<tr><td>bad</td></tr></tbody></table>"""
ITCH = """<rss><channel><item><guid>https://a.itch.io/game</guid><plainTitle>Game</plainTitle>
<price>$5</price><link>https://a.itch.io/game</link></item><item /></channel></rss>"""


@pytest.mark.parametrize(
    ("adapter_type", "payload", "expected_metric"),
    [(CoinGeckoTrendingAdapter, COINGECKO, 1), (CoinPaprikaMarketAdapter, PAPRIKA, 123)],
)
def test_finance_adapters(adapter_type, payload, expected_metric):
    async def run():
        async with HTTPClient(
            Settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        ) as http:
            adapter = adapter_type(http)
            items = await adapter.fetch()
            assert [item.rank for item in items] == [1]
            assert items[0].raw_metric == expected_metric
            assert adapter.failed_count == 1
            assert items[0].observed_at.tzinfo is not None

    asyncio.run(run())


@pytest.mark.parametrize("adapter_type", [CoinGeckoTrendingAdapter, CoinPaprikaMarketAdapter])
def test_finance_empty_and_failure(adapter_type):
    async def run(status, payload):
        async with HTTPClient(
            Settings(), httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
        ) as http:
            adapter = adapter_type(http)
            if status >= 400:
                with pytest.raises(httpx.HTTPStatusError):
                    await adapter.fetch()
            else:
                assert await adapter.fetch() == []

    empty = {"coins": []} if adapter_type is CoinGeckoTrendingAdapter else []
    asyncio.run(run(200, empty))
    asyncio.run(run(403, {}))


def test_gaming_parsers():
    steam, steam_failed = parse_steam_most_played(STEAM)
    itch, itch_failed = parse_itch_top_sellers(ITCH)
    assert steam_failed == 1 and steam[0]["external_id"] == "730"
    assert steam[0]["raw_metric"] == 1024230
    assert itch_failed == 1 and itch[0]["title"] == "Game"
    assert parse_itch_top_sellers("<rss><channel /></rss>") == ([], 0)
    with pytest.raises(ValueError):
        parse_steam_most_played("<html />")
    with pytest.raises(ValueError):
        parse_itch_top_sellers("<rss")


@pytest.mark.parametrize(
    ("adapter_type", "body"),
    [(SteamMostPlayedAdapter, STEAM), (ItchTopSellersAdapter, ITCH)],
)
def test_gaming_adapter_shared_timestamp_and_failure(adapter_type, body):
    async def run(status):
        async with HTTPClient(
            Settings(), httpx.MockTransport(lambda request: httpx.Response(status, text=body))
        ) as http:
            adapter = adapter_type(http)
            if status >= 400:
                with pytest.raises(httpx.HTTPStatusError):
                    await adapter.fetch()
            else:
                items = await adapter.fetch()
                assert len(items) == 1 and adapter.failed_count == 1
                assert len({item.observed_at for item in items}) == 1

    asyncio.run(run(200))
    asyncio.run(run(403))
