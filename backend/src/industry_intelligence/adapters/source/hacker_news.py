"""Hacker News official Firebase Top Stories adapter."""

import asyncio
import logging
from datetime import UTC, datetime
from html import unescape

from industry_intelligence.adapters.source.base import SourceItem
from industry_intelligence.domain.enums import Domain

logger = logging.getLogger(__name__)


class HackerNewsAdapter:
    source_name = "Hacker News"
    domain = Domain.COMPUTER
    base_url = "https://hacker-news.firebaseio.com/v0"

    def __init__(self, http, limit=30):
        self.http = http
        self.limit = limit
        self.failed_count = 0

    async def fetch(self):
        self.failed_count = 0
        observed_at = datetime.now(UTC)
        ids = (await self.http.get(f"{self.base_url}/topstories.json")).json()
        if not isinstance(ids, list):
            raise ValueError("Unexpected HN top stories response")
        semaphore = asyncio.Semaphore(8)

        async def item(rank, story_id):
            try:
                async with semaphore:
                    data = (await self.http.get(f"{self.base_url}/item/{story_id}.json")).json()
                if not isinstance(data, dict) or data.get("dead") or data.get("deleted"):
                    raise ValueError("Unavailable story")
                title = unescape(data["title"]).strip()
                if not title or str(data["id"]) != str(story_id):
                    raise ValueError("Malformed story")
                hn_url = f"https://news.ycombinator.com/item?id={story_id}"
                return SourceItem(
                    str(story_id),
                    title,
                    data.get("url") or hn_url,
                    rank,
                    data.get("score"),
                    observed_at,
                    {
                        "author": data.get("by"),
                        "comments": data.get("descendants"),
                        "hn_url": hn_url,
                        "metric_label": "points",
                    },
                )
            except Exception as exc:
                self.failed_count += 1
                logger.warning(
                    "source=hacker_news event=item_failed rank=%s error=%s",
                    rank,
                    type(exc).__name__,
                )
                return None

        items = await asyncio.gather(
            *(item(i, value) for i, value in enumerate(ids[: self.limit], 1))
        )
        if ids and not any(items):
            raise ValueError("All HN items failed")
        return [entry for entry in items if entry is not None]
