from datetime import UTC, datetime

from industry_intelligence.adapters.source.base import SourceItem
from industry_intelligence.adapters.source.github_trending_parser import parse_github_trending
from industry_intelligence.domain.enums import Domain


class GitHubTrendingAdapter:
    source_name = "GitHub"
    domain = Domain.COMPUTER

    def __init__(self, http, limit=30):
        self.http, self.limit, self.failed_count = http, limit, 0

    async def fetch(self):
        observed_at = datetime.now(UTC)
        response = await self.http.get("https://github.com/trending?since=daily")
        rows, self.failed_count = parse_github_trending(response.text)
        return [SourceItem(**row, observed_at=observed_at) for row in rows[: self.limit]]
