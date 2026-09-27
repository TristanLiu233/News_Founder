"""Contract for external company-ranking adapters.

Adapters fetch, parse, and normalize one external ranking into a snapshot DTO. Company
resolution, persistence, idempotency, and history belong to the ingestion service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CompanyRankingEntry:
    """One company position exactly as the publisher lists it."""

    rank: int
    name: str
    raw_value: str | None = None
    metric_value: Decimal | None = None
    # Stable external identifiers as (scheme, value), strongest first.
    identities: tuple[tuple[str, str], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("rank must be greater than zero")
        if not self.name.strip():
            raise ValueError("name must not be empty")


@dataclass(frozen=True, slots=True)
class CompanyRankingSnapshot:
    """One observation of an external ranking.

    ``snapshot_key`` identifies the published version (for example a wiki revision or the
    UTC day of a daily list); ingesting the same key twice is a replay.
    """

    snapshot_key: str
    observed_at: datetime
    source_url: str
    entries: tuple[CompanyRankingEntry, ...]
    published_at: datetime | None = None
    # How many rows the publisher listed, which may exceed the stored entries.
    source_entry_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        if not self.snapshot_key:
            raise ValueError("snapshot_key must not be empty")


@runtime_checkable
class CompanyRankingAdapter(Protocol):
    """Constructed as ``Adapter(http, definition)``; counts skipped malformed rows."""

    failed_count: int

    async def fetch(self) -> CompanyRankingSnapshot:
        """Fetch and normalize the ranking's current published version."""
