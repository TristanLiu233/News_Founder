"""Company-profile adapters, DTOs, reviewed targets, and the explicit source registry."""

from industry_intelligence.adapters.company_profile.base import (
    CompanyProfileAdapter,
    ExternalCompanyProfile,
    LocalizedName,
    ProfileRejected,
)

__all__ = ["CompanyProfileAdapter", "ExternalCompanyProfile", "LocalizedName", "ProfileRejected"]
