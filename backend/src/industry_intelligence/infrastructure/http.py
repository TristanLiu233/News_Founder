"""Shared bounded HTTP transport; parsers and 4xx failures are never retried."""

import asyncio

import httpx

from industry_intelligence.config import Settings


class HTTPClient:
    def __init__(self, settings: Settings, transport=None):
        self.client = httpx.AsyncClient(
            timeout=settings.source_http_timeout,
            headers={"User-Agent": "IndustryIntelligence/0.1 (personal research MVP)"},
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def get(self, url, **kwargs):
        for attempt in range(3):
            try:
                response = await self.client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = (
                    not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
                )
                if not retryable or attempt == 2:
                    raise
                await asyncio.sleep(0.25 * 2**attempt)
        raise AssertionError("Unreachable")
