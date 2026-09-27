"""External company-ranking adapters, DTOs, and the explicit ranking registry."""

from industry_intelligence.adapters.company_ranking.base import (
    CompanyRankingAdapter,
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)

__all__ = ["CompanyRankingAdapter", "CompanyRankingEntry", "CompanyRankingSnapshot"]
