"""Contract for company-profile adapters.

An adapter fetches, parses, and normalizes what one source publishes about one reviewed target
company into an ``ExternalCompanyProfile``. Identity attachment, persistence, idempotency, and
the field-level merge into the current profile belong to the ingestion service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

# Normalized profile fields an adapter may report. Values are source-published facts, only
# cleaned (HTML removed, whitespace collapsed, safely shortened); never generated.
PROFILE_FIELDS = frozenset(
    {
        "legal_name",
        "legal_name_locale",
        "website",
        "country",
        "headquarters",
        "jurisdiction",
        "industry",
        "categories",
        "description",
        "description_language",
        "business_areas",
        "key_products",
    }
)
LIST_FIELDS = frozenset({"categories", "business_areas", "key_products"})


class ProfileRejected(ValueError):
    """The source answered, but its record fails an identity or validity check."""


@dataclass(frozen=True, slots=True)
class LocalizedName:
    """A published company name; ``name_type`` is ``legal_name``, ``localized_name`` ..."""

    name: str
    locale: str | None
    name_type: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty")


@dataclass(frozen=True, slots=True)
class ExternalCompanyProfile:
    source_key: str
    source_url: str
    retrieved_at: datetime
    facts: dict[str, Any]
    # Stable external identifiers as (scheme, value), strongest first.
    identities: tuple[tuple[str, str], ...] = ()
    names: tuple[LocalizedName, ...] = ()
    # The source's own date for this content (a filing date, a registry update), when known.
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        unknown = set(self.facts) - PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Unknown profile fields: {sorted(unknown)}")
        for key in LIST_FIELDS & set(self.facts):
            if not isinstance(self.facts[key], list):
                raise ValueError(f"{key} must be a list")


@runtime_checkable
class CompanyProfileAdapter(Protocol):
    """Constructed as ``Adapter(http, definition)``; counts skipped malformed parts."""

    failed_count: int

    async def fetch(self, target) -> ExternalCompanyProfile:
        """Fetch and normalize the source's current information about one target."""
