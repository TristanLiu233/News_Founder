import asyncio
from pathlib import Path

import httpx
import pytest

from industry_intelligence.adapters.source.github_trending import GitHubTrendingAdapter
from industry_intelligence.adapters.source.github_trending_parser import parse_github_trending
from industry_intelligence.config import Settings
from industry_intelligence.infrastructure.http import HTTPClient

HTML = (Path(__file__).parents[1] / "fixtures/github_trending.html").read_text()


def test_fixture():
    rows, failed = parse_github_trending(HTML)
    assert failed == 1
    assert [x["rank"] for x in rows] == [1, 2]
    assert rows[0]["external_id"] == "owner/project"
    assert rows[0]["raw_metric"] == 123
    assert rows[1]["metadata"]["language"] is None
    assert rows[1]["metadata"]["description"] is None


def test_empty():
    assert parse_github_trending("<h1>Trending</h1>") == ([], 0)


@pytest.mark.parametrize("html", ["<h1>Error</h1>", '<article class="Box-row">broken</article>'])
def test_unexpected(html):
    with pytest.raises(ValueError):
        parse_github_trending(html)


@pytest.mark.parametrize("status", [200, 403])
def test_fetch(status):
    async def run():
        async with HTTPClient(
            Settings(), httpx.MockTransport(lambda request: httpx.Response(status, text=HTML))
        ) as http:
            adapter = GitHubTrendingAdapter(http)
            if status == 403:
                with pytest.raises(httpx.HTTPStatusError):
                    await adapter.fetch()
            else:
                items = await adapter.fetch()
                assert len(items) == 2
                assert items[0].observed_at == items[1].observed_at

    asyncio.run(run())
