"""Framework-independent value objects for the shared domain."""

from __future__ import annotations

from dataclasses import dataclass

from industry_intelligence.domain.enums import Domain, SubjectType


@dataclass(frozen=True, slots=True)
class SubjectReference:
    """A lightweight reference used before subject resolution is implemented."""

    domain: Domain
    subject_type: SubjectType
    canonical_name: str
