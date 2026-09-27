"""Contract for future external company-ranking adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from industry_intelligence.domain.enums import Domain


@dataclass(frozen=True, slots=True)
class RankingDefinition:
    """Metadata identifying a publisher's ranking list."""

    name: str
    publisher: str
    domain: Domain
    metric_type: str
    frequency: str
    source_url: str | None = None
    methodology: str | None = None


@dataclass(frozen=True, slots=True)
class RankingItem:
    """A normalized company position from an external ranking publisher."""

    company_name: str
    rank: int
    metric_value: Decimal | None
    observed_at: datetime
    source_url: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rank < 1:
            msg = "rank must be greater than zero"
            raise ValueError(msg)
        if self.observed_at.tzinfo is None:
            msg = "observed_at must be timezone-aware"
            raise ValueError(msg)


@runtime_checkable
class RankingAdapter(Protocol):
    """Fetch normalized company-ranking data without storing or interpreting it."""

    @property
    def ranking(self) -> RankingDefinition:
        """Metadata for the publisher ranking this adapter provides."""

    async def fetch(self) -> list[RankingItem]:
        """Fetch and normalize the ranking's current observations."""
