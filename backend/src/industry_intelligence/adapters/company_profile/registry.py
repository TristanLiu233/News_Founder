"""Explicit company-profile source catalog; no dynamic plugin loading.

Source types, strongest provenance first: the company's own website, its regulatory filings, the
LEI registry, and a secondary publisher. Which source wins for each profile field is decided in
``services.company_profile_ingestion.FIELD_PRIORITY``.
"""

from dataclasses import dataclass

from industry_intelligence.adapters.company_profile.companiesmarketcap import (
    CompaniesMarketCapProfileAdapter,
)
from industry_intelligence.adapters.company_profile.gleif import GleifProfileAdapter
from industry_intelligence.adapters.company_profile.official_website import (
    OfficialWebsiteProfileAdapter,
)
from industry_intelligence.adapters.company_profile.sec_edgar import SecEdgarProfileAdapter


@dataclass(frozen=True)
class CompanyProfileSourceDefinition:
    key: str
    # Source brand; never translated.
    name: str
    source_type: str
    acquisition: str
    url: str
    adapter: type
    # ProfileTarget attribute that must be set for this source to apply.
    target_field: str
    # Seconds to wait between targets, keeping every source low-frequency.
    interval: float
    # SEC fair-access policy: declare SEC_CONTACT in the User-Agent (SEC hosts only).
    requires_sec_contact: bool = False


PROFILE_SOURCES: dict[str, CompanyProfileSourceDefinition] = {
    definition.key: definition
    for definition in (
        CompanyProfileSourceDefinition(
            key="official_website",
            name="Official website",
            source_type="official_company",
            acquisition="public HTML (robots.txt honoured)",
            url="",
            adapter=OfficialWebsiteProfileAdapter,
            target_field="website",
            interval=0.5,
        ),
        CompanyProfileSourceDefinition(
            key="sec_edgar",
            name="SEC EDGAR",
            source_type="regulatory_filing",
            acquisition="SEC EDGAR submissions API and annual-report XBRL",
            url="https://www.sec.gov/edgar/search/",
            adapter=SecEdgarProfileAdapter,
            target_field="cik",
            interval=1.0,
            requires_sec_contact=True,
        ),
        CompanyProfileSourceDefinition(
            key="gleif",
            name="GLEIF",
            source_type="lei_registry",
            acquisition="GLEIF LEI Records API",
            url="https://search.gleif.org/",
            adapter=GleifProfileAdapter,
            target_field="lei",
            interval=1.1,
        ),
        CompanyProfileSourceDefinition(
            key="companiesmarketcap",
            name="CompaniesMarketCap",
            source_type="secondary_publisher",
            acquisition="public HTML company page",
            url="https://companiesmarketcap.com/",
            adapter=CompaniesMarketCapProfileAdapter,
            target_field="cmc_slug",
            interval=1.0,
        ),
    )
}
