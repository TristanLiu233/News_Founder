"""Read models for the Companies index and Company Detail; persisted data only.

Company Detail reuses the Phase 4 ranking read model (``company_rankings``) for external
rankings and the existing news tables (through deterministic company ↔ subject links) for
related news. Nothing here fetches, ingests, scores, or combines rankings.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, func, or_, select

from industry_intelligence.adapters.company_profile.registry import PROFILE_SOURCES
from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    CompanyProfile,
    CompanyProfileSource,
    CompanyRedirect,
    CompanySubjectLink,
    ContentSubjectLink,
    Ranking,
    RankingEntry,
    RankingSnapshot,
)
from industry_intelligence.services.company_rankings import _latest_snapshots, company_rankings
from industry_intelligence.services.company_resolution import (
    normalize_company_name,
    resolve_company_redirect,
)
from industry_intelligence.services.resolution import locale_for
from industry_intelligence.services.subject_detail import resolve_canonical

SOURCE_ORDER = {key: index for index, key in enumerate(PROFILE_SOURCES)}
ALIAS_TYPE_ORDER = {
    "localized_name": 0,
    "legal_name": 1,
    "transliterated_legal_name": 2,
    "source_name": 3,
}
LIST_CATEGORY_LIMIT = 4


def _iso(value):
    return value.isoformat() if value else None


def _is_chinese_locale(locale):
    return bool(locale) and locale.lower().startswith("zh")


def chinese_name(aliases, subject_titles=()):
    """(name, basis) for the zh-CN UI, or (None, None): never translated or invented.

    Preference: a localized name verified on the company's official Chinese page, then the
    Chinese title of a deterministically linked subject (an A-share security name), then a
    Chinese registered legal name.
    """

    for alias in aliases:
        if alias["alias_type"] == "localized_name" and _is_chinese_locale(alias["locale"]):
            return alias["alias"], "localized_name"
    for title in subject_titles:
        if locale_for(title) == "zh-CN":
            return title, "linked_subject"
    for alias in aliases:
        if alias["alias_type"] == "legal_name" and _is_chinese_locale(alias["locale"]):
            return alias["alias"], "legal_name"
    return None, None


def display_names(canonical_name, zh_name):
    return {"en": canonical_name, "zh-CN": zh_name or canonical_name}


def _aliases(session, company_ids):
    rows = session.scalars(
        select(CompanyAlias)
        .where(CompanyAlias.company_id.in_(company_ids))
        .order_by(CompanyAlias.alias)
    ).all()
    by_company = {}
    for row in rows:
        by_company.setdefault(row.company_id, []).append(
            {
                "alias": row.alias,
                "locale": row.locale,
                "alias_type": row.alias_type,
                "alias_source": row.alias_source,
            }
        )
    for aliases in by_company.values():
        aliases.sort(key=lambda a: (ALIAS_TYPE_ORDER.get(a["alias_type"], 9), a["alias"]))
    return by_company


def _linked_subjects(session, company_ids):
    """Canonical linked subjects per company (merged subjects resolve, duplicates collapse)."""

    rows = session.scalars(
        select(CompanySubjectLink)
        .where(CompanySubjectLink.company_id.in_(company_ids))
        .order_by(CompanySubjectLink.created_at)
    ).all()
    by_company = {}
    for link in rows:
        subject = resolve_canonical(session, link.subject_id)
        if subject is None:
            continue
        items = by_company.setdefault(link.company_id, {})
        items.setdefault(
            subject.id,
            {
                "id": str(subject.id),
                "title": subject.title,
                "domain": str(subject.domain),
                "subject_type": str(subject.subject_type),
                "strategy": link.strategy,
                "evidence": link.evidence,
            },
        )
    return {company_id: list(items.values()) for company_id, items in by_company.items()}


def _latest_ranks(session, company_ids, ranking_ids=None):
    rankings = list(
        session.scalars(
            select(Ranking).where(Ranking.id.in_(ranking_ids)) if ranking_ids else select(Ranking)
        )
    )
    latest = _latest_snapshots(session, [r.id for r in rankings])
    if not latest or not company_ids:
        return {}
    by_snapshot = {snapshot.id: snapshot.ranking_id for snapshot in latest.values()}
    by_id = {r.id: r for r in rankings}
    ranks = {}
    for company_id, snapshot_id, rank in session.execute(
        select(RankingEntry.company_id, RankingEntry.snapshot_id, RankingEntry.rank).where(
            RankingEntry.snapshot_id.in_(list(by_snapshot)),
            RankingEntry.company_id.in_(company_ids),
        )
    ):
        ranking = by_id[by_snapshot[snapshot_id]]
        ranks.setdefault(company_id, []).append(
            {"key": ranking.key, "name": ranking.name, "domain": str(ranking.domain), "rank": rank}
        )
    for items in ranks.values():
        items.sort(key=lambda item: (item["domain"], item["key"]))
    return ranks


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_companies(
    session, q=None, domain=None, ranking=None, has_profile=None, limit=30, offset=0
):
    """A bounded page of canonical companies with compact profile and ranking context.

    With ``ranking`` the list is that ranking's members ordered by their latest published rank;
    otherwise companies with a profile come first, then by name. Ranks of different rankings are
    never compared or combined.
    """

    conditions = [Company.id.not_in(select(CompanyRedirect.duplicate_id))]
    exact = None
    if q and q.strip():
        normalized = normalize_company_name(q)
        pattern = f"%{_escape_like(normalized)}%"
        conditions.append(
            or_(
                Company.normalized_name.like(pattern, escape="\\"),
                Company.id.in_(
                    select(CompanyAlias.company_id).where(
                        CompanyAlias.normalized_alias.like(pattern, escape="\\")
                    )
                ),
                # Public identifiers (ticker, CIK, LEI) match exactly, case-insensitively.
                Company.id.in_(
                    select(CompanyIdentity.company_id).where(
                        func.upper(CompanyIdentity.value) == q.strip().upper()
                    )
                ),
            )
        )
        exact = case(
            (
                or_(
                    Company.normalized_name == normalized,
                    Company.id.in_(
                        select(CompanyAlias.company_id).where(
                            CompanyAlias.normalized_alias == normalized
                        )
                    ),
                ),
                0,
            ),
            else_=1,
        )
    membership = select(RankingEntry.company_id).join(
        RankingSnapshot, RankingSnapshot.id == RankingEntry.snapshot_id
    )
    ranking_row = None
    if ranking:
        ranking_row = session.scalar(select(Ranking).where(Ranking.key == ranking))
        if ranking_row is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        conditions.append(
            Company.id.in_(membership.where(RankingSnapshot.ranking_id == ranking_row.id))
        )
    if domain:
        conditions.append(
            Company.id.in_(
                membership.join(Ranking, Ranking.id == RankingSnapshot.ranking_id).where(
                    Ranking.domain == domain
                )
            )
        )
    profiled = select(CompanyProfile.company_id)
    if has_profile is True:
        conditions.append(Company.id.in_(profiled))
    elif has_profile is False:
        conditions.append(Company.id.not_in(profiled))

    total = session.scalar(select(func.count()).select_from(Company).where(*conditions))
    order = [exact] if exact is not None else []
    query = select(Company).where(*conditions)
    if ranking_row is not None:
        latest = _latest_snapshots(session, [ranking_row.id]).get(ranking_row.id)
        current = (
            select(RankingEntry.company_id, RankingEntry.rank)
            .where(RankingEntry.snapshot_id == (latest.id if latest else None))
            .subquery()
        )
        query = query.outerjoin(current, current.c.company_id == Company.id)
        order.append(current.c.rank.asc().nulls_last())
    else:
        order.append(case((Company.id.in_(profiled), 0), else_=1))
    order += [Company.canonical_name, Company.id]
    companies = list(session.scalars(query.order_by(*order).offset(offset).limit(limit)))
    ids = [company.id for company in companies]
    aliases = _aliases(session, ids)
    subjects = _linked_subjects(session, ids)
    profiles = {
        p.company_id: p
        for p in session.scalars(select(CompanyProfile).where(CompanyProfile.company_id.in_(ids)))
    }
    ranks = _latest_ranks(session, ids)
    items = []
    for company in companies:
        zh, basis = chinese_name(
            aliases.get(company.id, []), [s["title"] for s in subjects.get(company.id, [])]
        )
        profile = profiles.get(company.id)
        items.append(
            {
                "id": str(company.id),
                "canonical_name": company.canonical_name,
                "names": {"en": company.canonical_name, "zh": zh, "zh_basis": basis},
                "display_names": display_names(company.canonical_name, zh),
                "has_profile": profile is not None,
                "country": profile.country if profile else None,
                "industry": profile.industry if profile else None,
                "categories": (profile.categories if profile else [])[:LIST_CATEGORY_LIMIT],
                "rankings": ranks.get(company.id, []),
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _profile(profile):
    if profile is None:
        return None
    return {
        "legal_name": profile.legal_name,
        "legal_name_locale": profile.legal_name_locale,
        "website": profile.website,
        "country": profile.country,
        "headquarters": profile.headquarters,
        "jurisdiction": profile.jurisdiction,
        "industry": profile.industry,
        "categories": profile.categories,
        "description": profile.description,
        "description_language": profile.description_language,
        "business_areas": profile.business_areas,
        "key_products": profile.key_products,
        "field_sources": profile.field_sources,
        "profile_updated_at": _iso(profile.profile_updated_at),
    }


def _sources(session, company_id, field_sources):
    rows = session.scalars(
        select(CompanyProfileSource).where(CompanyProfileSource.company_id == company_id)
    ).all()
    rows = sorted(rows, key=lambda row: SOURCE_ORDER.get(row.source_key, len(SOURCE_ORDER)))
    return [
        {
            "source_key": row.source_key,
            "source_type": row.source_type,
            "source_name": row.source_name,
            "source_url": row.source_url,
            "retrieved_at": _iso(row.retrieved_at),
            "checked_at": _iso(row.checked_at),
            "published_at": _iso(row.published_at),
            "fields": sorted(row.facts),
            "used_fields": sorted(f for f, key in field_sources.items() if key == row.source_key),
            "metadata": row.source_metadata,
        }
        for row in rows
    ]


def _current_rankings(appearances):
    items = []
    for appearance in appearances:
        latest = appearance["history"][0] if appearance["history"] else None
        current = latest if latest and latest["rank"] is not None else None
        items.append(
            {
                "ranking": appearance["ranking"],
                "in_latest_snapshot": appearance["in_latest_snapshot"],
                "latest_rank": appearance["latest_rank"],
                "movement": latest["movement"] if latest else None,
                "rank_change": current["rank_change"] if current else None,
                "raw_value": current["raw_value"] if current else None,
                "effective_at": latest["effective_at"] if latest else None,
            }
        )
    return items


def related_news_count(session, subject_ids):
    if not subject_ids:
        return 0
    return session.scalar(
        select(func.count(func.distinct(ContentSubjectLink.content_item_id))).where(
            ContentSubjectLink.subject_id.in_(subject_ids)
        )
    )


def company_detail(session, company_id):
    """One canonical company's identity, sourced profile, rankings, and related context."""

    canonical_id = resolve_company_redirect(session, company_id)
    company = session.get(Company, canonical_id)
    if company is None:
        return None
    aliases = _aliases(session, [company.id]).get(company.id, [])
    subjects = _linked_subjects(session, [company.id]).get(company.id, [])
    zh, basis = chinese_name(aliases, [s["title"] for s in subjects])
    identities = session.scalars(
        select(CompanyIdentity)
        .where(CompanyIdentity.company_id == company.id)
        .order_by(CompanyIdentity.scheme, CompanyIdentity.value)
    ).all()
    profile = session.get(CompanyProfile, company.id)
    rankings = company_rankings(session, company.id, limit=1)["rankings"]
    return {
        "id": str(company.id),
        "requested_id": str(company_id),
        "redirected": company.id != company_id,
        "canonical_name": company.canonical_name,
        "names": {"en": company.canonical_name, "zh": zh, "zh_basis": basis},
        "display_names": display_names(company.canonical_name, zh),
        "aliases": aliases,
        "identities": [
            {"scheme": row.scheme, "value": row.value, "source_key": row.source_key}
            for row in identities
        ],
        "profile": _profile(profile),
        "profile_sources": _sources(session, company.id, profile.field_sources if profile else {}),
        "current_rankings": _current_rankings(rankings),
        "domains": sorted({item["ranking"]["domain"] for item in rankings}),
        "related_subjects": subjects,
        "related_news_count": related_news_count(session, [UUID(s["id"]) for s in subjects]),
    }
