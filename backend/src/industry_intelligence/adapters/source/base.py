"""Contract for future external trending-information adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from industry_intelligence.domain.enums import Domain


@dataclass(frozen=True, slots=True)
class SourceItem:
    """A source-neutral item observed on an external ranked list."""

    external_id: str | None
    title: str
    url: str | None
    rank: int
    raw_metric: Any | None
    observed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rank < 1:
            msg = "rank must be greater than zero"
            raise ValueError(msg)
        if self.observed_at.tzinfo is None:
            msg = "observed_at must be timezone-aware"
            raise ValueError(msg)


@runtime_checkable
class SourceAdapter(Protocol):
    """Fetch a provider's list and return only normalized SourceItem DTOs.

    Transport, persistence, and downstream ranking logic intentionally remain
    outside this boundary.
    """

    @property
    def source_name(self) -> str:
        """Human-readable source name matching the configured Source record."""

    @property
    def domain(self) -> Domain:
        """The platform domain served by this adapter."""

    async def fetch(self) -> list[SourceItem]:
        """Fetch and normalize the provider's current list."""


@dataclass(frozen=True, slots=True)
class ContentSourceItem:
    """A source-neutral chronological item; its order is never a rank."""

    external_id: str | None
    title: str
    url: str
    published_at: datetime | None
    observed_at: datetime
    language: str
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
