import asyncio

import httpx
import pytest

from industry_intelligence.adapters.source.hacker_news import HackerNewsAdapter
from industry_intelligence.config import Settings
from industry_intelligence.infrastructure.http import HTTPClient


@pytest.mark.parametrize("mode", ["normal", "empty", "malformed", "failure"])
def test_hn(mode):
    def handle(request):
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(
                403 if mode == "failure" else 200, json=[] if mode == "empty" else [3, 2, 1]
            )
        number = int(request.url.path.split("/")[-1].split(".")[0])
        return httpx.Response(
            200,
            json=None
            if mode == "malformed" and number == 2
            else {"id": number, "title": " A &amp; B ", "score": 42},
        )

    async def run():
        async with HTTPClient(Settings(), httpx.MockTransport(handle)) as http:
            adapter = HackerNewsAdapter(http, 3)
            if mode == "failure":
                with pytest.raises(httpx.HTTPStatusError):
                    await adapter.fetch()
                return
            items = await adapter.fetch()
            assert [x.rank for x in items] == (
                [] if mode == "empty" else [1, 3] if mode == "malformed" else [1, 2, 3]
            )
            assert len({x.observed_at for x in items}) <= 1
            assert all(x.title == "A & B" for x in items)
            assert adapter.failed_count == (1 if mode == "malformed" else 0)

    asyncio.run(run())
