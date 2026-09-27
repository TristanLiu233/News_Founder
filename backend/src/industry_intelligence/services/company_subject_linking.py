"""Deterministic, conservative Company ↔ canonical Subject links.

A Company (external company rankings) and a Subject (Today/Trending/Search) stay separate
models; a link only says they are the same real-world issuer. Funnel, first match wins:

1. ``exchange_code``: the company's CompaniesMarketCap A-share symbol (``688256.SS``,
   ``002371.SZ``) equals a Subject's exchange-code identity (``SH:688256``, ``SZ:002371``);
2. ``exact_name``: one of the company's names equals, after normalization, an alias of exactly
   one canonical ``company``/``stock`` Subject, and no other company holds that name.

Anything ambiguous stays unlinked; there is no fuzzy or cross-type matching (a GitHub repository
named like a company is never linked). Merged subjects resolve to their canonical subject.
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import delete, select

from industry_intelligence.adapters.company_ranking.companiesmarketcap import SYMBOL_SCHEME
from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    CompanyRedirect,
    CompanySubjectLink,
    Subject,
)
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.domain.enums import SubjectType
from industry_intelligence.services.subject_detail import resolve_canonical

LINKABLE_SUBJECT_TYPES = (SubjectType.COMPANY, SubjectType.STOCK)
_A_SHARE = re.compile(r"^(\d{6})\.(SS|SZ)$")
EXCHANGES = {"SS": "SH", "SZ": "SZ"}


def exchange_code(symbol):
    """``688256.SS`` -> ``SH:688256``; None for any other symbol."""

    match = _A_SHARE.match(symbol or "")
    return f"{EXCHANGES[match.group(2)]}:{match.group(1)}" if match else None


def _canonical_subjects(session, subject_ids):
    subjects = {}
    for subject_id in subject_ids:
        subject = resolve_canonical(session, subject_id)
        if subject is not None and subject.subject_type in LINKABLE_SUBJECT_TYPES:
            subjects[subject.id] = subject
    return subjects


def _exchange_links(session, companies):
    codes = defaultdict(set)
    symbols = {}
    for company_id, value in session.execute(
        select(CompanyIdentity.company_id, CompanyIdentity.value).where(
            CompanyIdentity.scheme == SYMBOL_SCHEME, CompanyIdentity.company_id.in_(companies)
        )
    ):
        if code := exchange_code(value):
            codes[code].add(company_id)
            symbols[company_id] = value
    links = {}
    if not codes:
        return links
    by_code = defaultdict(set)
    for code, subject_id in session.execute(
        select(SubjectExternalIdentity.canonical_id, SubjectExternalIdentity.subject_id).where(
            SubjectExternalIdentity.canonical_id.in_(list(codes))
        )
    ):
        by_code[code].add(subject_id)
    for code, company_ids in codes.items():
        subjects = _canonical_subjects(session, by_code.get(code, ()))
        if len(company_ids) == 1 and len(subjects) == 1:
            subject = next(iter(subjects.values()))
            company_id = next(iter(company_ids))
            links[company_id] = (
                subject.id,
                "exchange_code",
                {"company_symbol": symbols[company_id], "subject_identity": code},
            )
    return links


def _name_links(session, companies, already_linked):
    names = defaultdict(set)
    for company_id, normalized in session.execute(
        select(Company.id, Company.normalized_name).where(Company.id.in_(companies))
    ):
        names[normalized].add(company_id)
    for company_id, normalized in session.execute(
        select(CompanyAlias.company_id, CompanyAlias.normalized_alias).where(
            CompanyAlias.company_id.in_(companies)
        )
    ):
        names[normalized].add(company_id)
    if not names:
        return {}
    by_name = defaultdict(set)
    for normalized, subject_id in session.execute(
        select(SubjectAlias.normalized_alias, SubjectAlias.subject_id)
        .join(Subject, Subject.id == SubjectAlias.subject_id)
        .where(
            SubjectAlias.normalized_alias.in_(list(names)),
            Subject.subject_type.in_(LINKABLE_SUBJECT_TYPES),
        )
    ):
        by_name[normalized].add(subject_id)
    candidates = defaultdict(dict)
    for normalized, subject_ids in by_name.items():
        company_ids = names[normalized]
        subjects = _canonical_subjects(session, subject_ids)
        if len(company_ids) != 1 or len(subjects) != 1:
            continue
        company_id = next(iter(company_ids))
        if company_id in already_linked:
            continue
        subject = next(iter(subjects.values()))
        candidates[company_id][subject.id] = normalized
    # A company whose names point at several different subjects is ambiguous.
    return {
        company_id: (subject_id, "exact_name", {"normalized_name": name})
        for company_id, found in candidates.items()
        if len(found) == 1
        for subject_id, name in found.items()
    }


def relink_all_companies(session):
    """Delete and rebuild every company-subject link inside the caller's transaction."""

    session.execute(delete(CompanySubjectLink))
    companies = list(
        session.scalars(
            select(Company.id).where(Company.id.not_in(select(CompanyRedirect.duplicate_id)))
        )
    )
    links = _exchange_links(session, companies)
    links.update(_name_links(session, companies, set(links)))
    # One subject is linked to at most one company.
    per_subject = defaultdict(list)
    for company_id, (subject_id, _, _) in links.items():
        per_subject[subject_id].append(company_id)
    created = defaultdict(int)
    for company_id, (subject_id, strategy, evidence) in links.items():
        if len(per_subject[subject_id]) != 1:
            continue
        session.add(
            CompanySubjectLink(
                company_id=company_id, subject_id=subject_id, strategy=strategy, evidence=evidence
            )
        )
        created[strategy] += 1
    session.flush()
    return {"companies": len(companies), "links": sum(created.values()), "by_strategy": created}
