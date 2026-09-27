"""Conservative, auditable company identity resolution for external rankings.

Funnel, first match wins:

1. exact external identity (a publisher ticker symbol or a Wikipedia article title);
2. seeded identity equivalence: a reviewed pair of identities known to be one company;
3. unique exact normalized name or alias;
4. a new company.

A name match is rejected when it is ambiguous (several companies) or when the candidate already
holds a different value in an exclusive scheme, for example a different ticker. Wikipedia article
links are not exclusive: editors retarget them (a row once linked to a parent's article may later
link to its own), so a different article alone does not prove a different company. No fuzzy
matching is used: a missed match can be merged later, a false merge corrupts history.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.company_ranking.companiesmarketcap import SYMBOL_SCHEME
from industry_intelligence.adapters.company_ranking.wikipedia import ARTICLE_SCHEME
from industry_intelligence.db.models import Company, CompanyAlias, CompanyIdentity
from industry_intelligence.services.resolution import normalize_subject_name

normalize_company_name = normalize_subject_name

# A company holds at most one value per exclusive scheme.
EXCLUSIVE_SCHEMES = frozenset({SYMBOL_SCHEME})

# Reviewed on 2026-09-27 against the live lists: each pair names the same company under
# different published names (for example "ICBC" and the bank's full name, or "NC", whose
# Wikipedia link targets NCSoft). Pairs are identity-to-identity, never name similarity.
SEED_IDENTITY_GROUPS: tuple[frozenset[tuple[str, str]], ...] = tuple(
    frozenset(group)
    for group in (
        {(SYMBOL_SCHEME, "1398.HK"), (ARTICLE_SCHEME, "Industrial and Commercial Bank of China")},
        {(SYMBOL_SCHEME, "SAN"), (ARTICLE_SCHEME, "Banco Santander")},
        {(SYMBOL_SCHEME, "TD"), (ARTICLE_SCHEME, "Toronto-Dominion Bank")},
        {(SYMBOL_SCHEME, "MUFG"), (ARTICLE_SCHEME, "MUFG")},
        {(SYMBOL_SCHEME, "3968.HK"), (ARTICLE_SCHEME, "China Merchants Bank")},
        {(SYMBOL_SCHEME, "ING"), (ARTICLE_SCHEME, "ING Group")},
        {(SYMBOL_SCHEME, "SMFG"), (ARTICLE_SCHEME, "SMBC Group")},
        {(SYMBOL_SCHEME, "RBLX"), (ARTICLE_SCHEME, "Roblox Corporation")},
        {(SYMBOL_SCHEME, "036570.KS"), (ARTICLE_SCHEME, "NCSoft")},
    )
)


@dataclass(frozen=True)
class CompanyResolution:
    company: Company
    strategy: str
    created: bool = False


def _seed_partners(identities):
    partners = set()
    for group in SEED_IDENTITY_GROUPS:
        if group & set(identities):
            partners |= group - set(identities)
    return partners


def _company_by_identities(session, identities):
    if not identities:
        return None
    clauses = [
        (CompanyIdentity.scheme == scheme) & (CompanyIdentity.value == value)
        for scheme, value in identities
    ]
    rows = session.execute(
        select(CompanyIdentity.scheme, CompanyIdentity.value, CompanyIdentity.company_id).where(
            or_(*clauses)
        )
    ).all()
    by_identity = {(row.scheme, row.value): row.company_id for row in rows}
    # Identities are ordered strongest first by the adapter.
    for identity in identities:
        if identity in by_identity:
            return session.get(Company, by_identity[identity])
    return None


def _conflicts(session, company_id, identities):
    """True when the company already holds a different value in an exclusive scheme."""

    schemes = {scheme for scheme, _ in identities} & EXCLUSIVE_SCHEMES
    if not schemes:
        return False
    held = session.execute(
        select(CompanyIdentity.scheme, CompanyIdentity.value).where(
            CompanyIdentity.company_id == company_id, CompanyIdentity.scheme.in_(schemes)
        )
    ).all()
    return any((row.scheme, row.value) not in identities for row in held)


def _unique_name_match(session, normalized, identities):
    ids = set(session.scalars(select(Company.id).where(Company.normalized_name == normalized)))
    ids |= set(
        session.scalars(
            select(CompanyAlias.company_id).where(CompanyAlias.normalized_alias == normalized)
        )
    )
    if len(ids) != 1:
        return None
    company_id = next(iter(ids))
    if _conflicts(session, company_id, set(identities)):
        return None
    return session.get(Company, company_id)


def attach_identity(session, company, name, identities, source_key):
    for scheme, value in identities:
        session.execute(
            insert(CompanyIdentity)
            .values(company_id=company.id, scheme=scheme, value=value, source_key=source_key)
            .on_conflict_do_nothing(index_elements=["scheme", "value"])
        )
    session.execute(
        insert(CompanyAlias)
        .values(
            company_id=company.id,
            alias=name,
            normalized_alias=normalize_company_name(name),
            alias_type="source_name",
            alias_source=source_key,
        )
        .on_conflict_do_nothing(constraint="company_alias_identity")
    )


def resolve_company(session, name, identities, source_key) -> CompanyResolution:
    """Resolve one published company name inside the caller's transaction."""

    name = " ".join(name.split())
    identities = tuple(identities)
    normalized = normalize_company_name(name)
    resolution = None
    if company := _company_by_identities(session, identities):
        resolution = CompanyResolution(company, "external_identity")
    elif (partners := _seed_partners(identities)) and (
        company := _company_by_identities(session, tuple(sorted(partners)))
    ):
        if not _conflicts(session, company.id, set(identities)):
            resolution = CompanyResolution(company, "seed_identity")
    if resolution is None and (company := _unique_name_match(session, normalized, identities)):
        resolution = CompanyResolution(company, "exact_name")
    if resolution is None:
        company = Company(canonical_name=name[:255], normalized_name=normalized[:255])
        session.add(company)
        session.flush()
        resolution = CompanyResolution(company, "new_company", created=True)
    attach_identity(session, resolution.company, name[:255], identities, source_key)
    return resolution
