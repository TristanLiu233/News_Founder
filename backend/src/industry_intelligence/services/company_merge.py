"""Reviewed, explicit company merges with redirects; never triggered automatically.

``merge_companies`` moves a duplicate company's ranking entries, identities, aliases, profile
source records, and subject links to the canonical company, records a ``CompanyRedirect``, and
keeps the duplicate row so old IDs keep resolving. It refuses instead of guessing when:

* both companies hold different SEC CIKs or LEIs (distinct legal entities);
* one ranking snapshot lists both companies (moving would put a company twice in one list);
* the merge would create a redirect cycle or re-merge an already merged company.

Different ticker symbols alone do not block a reviewed merge (share classes, ADRs).
"""

from __future__ import annotations

from sqlalchemy import func, select, text, update

from industry_intelligence.adapters.company_profile.gleif import LEI_SCHEME
from industry_intelligence.adapters.company_profile.sec_edgar import CIK_SCHEME
from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    CompanyProfile,
    CompanyProfileSource,
    CompanyRedirect,
    CompanySubjectLink,
    RankingEntry,
)
from industry_intelligence.services.company_profile_ingestion import rebuild_profile
from industry_intelligence.services.company_ranking_ingestion import LOCK_KEY
from industry_intelligence.services.company_resolution import resolve_company_redirect

LEGAL_ENTITY_SCHEMES = (CIK_SCHEME, LEI_SCHEME)


class MergeRefused(ValueError):
    """The merge is unsafe or invalid; nothing was changed."""


def _identity_values(session, company_id):
    rows = session.execute(
        select(CompanyIdentity.scheme, CompanyIdentity.value).where(
            CompanyIdentity.company_id == company_id,
            CompanyIdentity.scheme.in_(LEGAL_ENTITY_SCHEMES),
        )
    ).all()
    values = {}
    for scheme, value in rows:
        values.setdefault(scheme, set()).add(value)
    return values


def merge_companies(session, canonical_id, duplicate_id, reason):
    """Merge ``duplicate_id`` into ``canonical_id`` inside the caller's transaction."""

    reason = " ".join((reason or "").split())
    if not reason:
        raise MergeRefused("A review reason is required")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    canonical_id = resolve_company_redirect(session, canonical_id)
    if canonical_id == duplicate_id:
        raise MergeRefused("Cannot merge a company into itself or create a redirect cycle")
    if session.get(CompanyRedirect, duplicate_id) is not None:
        raise MergeRefused("The duplicate company is already merged")
    canonical = session.get(Company, canonical_id)
    duplicate = session.get(Company, duplicate_id)
    if canonical is None or duplicate is None:
        raise MergeRefused("Both companies must exist")

    held, incoming = (
        _identity_values(session, canonical_id),
        _identity_values(session, duplicate_id),
    )
    for scheme in LEGAL_ENTITY_SCHEMES:
        if held.get(scheme) and incoming.get(scheme) and held[scheme] != incoming[scheme]:
            raise MergeRefused(f"Different {scheme} values: distinct legal entities")
    shared = session.scalar(
        select(func.count())
        .select_from(RankingEntry)
        .where(
            RankingEntry.company_id == duplicate_id,
            RankingEntry.snapshot_id.in_(
                select(RankingEntry.snapshot_id).where(RankingEntry.company_id == canonical_id)
            ),
        )
    )
    if shared:
        raise MergeRefused(f"{shared} snapshot(s) list both companies")

    moved_entries = session.execute(
        update(RankingEntry)
        .where(RankingEntry.company_id == duplicate_id)
        .values(company_id=canonical_id)
    ).rowcount
    moved_identities = session.execute(
        update(CompanyIdentity)
        .where(CompanyIdentity.company_id == duplicate_id)
        .values(company_id=canonical_id)
    ).rowcount
    existing_aliases = set(
        session.scalars(
            select(CompanyAlias.normalized_alias).where(CompanyAlias.company_id == canonical_id)
        )
    )
    for alias in session.scalars(
        select(CompanyAlias).where(CompanyAlias.company_id == duplicate_id)
    ).all():
        if alias.normalized_alias in existing_aliases:
            session.delete(alias)
        else:
            alias.company_id = canonical_id
            existing_aliases.add(alias.normalized_alias)
    kept_sources = set(
        session.scalars(
            select(CompanyProfileSource.source_key).where(
                CompanyProfileSource.company_id == canonical_id
            )
        )
    )
    for record in session.scalars(
        select(CompanyProfileSource).where(CompanyProfileSource.company_id == duplicate_id)
    ).all():
        # The canonical company's own record from the same source wins.
        if record.source_key in kept_sources:
            session.delete(record)
        else:
            record.company_id = canonical_id
    linked = set(
        session.scalars(
            select(CompanySubjectLink.subject_id).where(
                CompanySubjectLink.company_id == canonical_id
            )
        )
    )
    for link in session.scalars(
        select(CompanySubjectLink).where(CompanySubjectLink.company_id == duplicate_id)
    ).all():
        if link.subject_id in linked:
            session.delete(link)
        else:
            link.company_id = canonical_id
    if (profile := session.get(CompanyProfile, duplicate_id)) is not None:
        session.delete(profile)
    session.flush()
    session.execute(
        update(CompanyRedirect)
        .where(CompanyRedirect.canonical_id == duplicate_id)
        .values(canonical_id=canonical_id)
    )
    session.add(
        CompanyRedirect(duplicate_id=duplicate_id, canonical_id=canonical_id, reason=reason)
    )
    session.flush()
    rebuild_profile(session, canonical_id)
    return {
        "canonical_id": str(canonical_id),
        "duplicate_id": str(duplicate_id),
        "moved_ranking_entries": moved_entries,
        "moved_identities": moved_identities,
        "reason": reason,
    }
