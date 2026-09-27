"""Idempotent company-profile ingestion with per-field provenance.

For each adopted source and reviewed target, the adapter fetches outside any transaction; then
``persist_profile_source`` runs inside the caller's transaction:

1. attach the source's identities (an identity held by another company, or a second value in an
   exclusive scheme, is refused and reported, never moved);
2. record the published names as aliases with their source, locale, and name type;
3. upsert the (company, source) record: identical content is a replay that only refreshes
   ``checked_at``; changed content replaces the record;
4. rebuild ``CompanyProfile`` from all of the company's source records by ``FIELD_PRIORITY``.

Profiles are never fetched on page load, and nothing is generated or translated.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.company_profile.base import LIST_FIELDS
from industry_intelligence.adapters.company_profile.registry import PROFILE_SOURCES
from industry_intelligence.adapters.company_profile.targets import PROFILE_TARGETS
from industry_intelligence.adapters.company_ranking.companiesmarketcap import SYMBOL_SCHEME
from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    CompanyProfile,
    CompanyProfileSource,
)
from industry_intelligence.infrastructure.http import HTTPClient
from industry_intelligence.services.company_ranking_ingestion import LOCK_KEY
from industry_intelligence.services.company_resolution import (
    EXCLUSIVE_SCHEMES,
    normalize_company_name,
    resolve_company_redirect,
)

logger = logging.getLogger(__name__)

# First source holding a non-empty value wins. The official site describes itself; the LEI
# registry is authoritative for registered legal name and address; SEC filings supply the
# official industry code and the reported segments and product lines; the secondary publisher
# only supplies its own category tags.
FIELD_PRIORITY: dict[str, tuple[str, ...]] = {
    "legal_name": ("gleif", "sec_edgar"),
    "website": ("official_website", "sec_edgar"),
    "description": ("official_website",),
    "headquarters": ("gleif", "sec_edgar"),
    "country": ("gleif", "sec_edgar"),
    "jurisdiction": ("gleif", "sec_edgar"),
    "industry": ("sec_edgar",),
    "categories": ("companiesmarketcap",),
    "business_areas": ("sec_edgar",),
    "key_products": ("sec_edgar",),
}
# A dependent field always comes from the same record as its primary field.
COUPLED_FIELDS = {"legal_name": "legal_name_locale", "description": "description_language"}
PROFILE_COLUMNS = (*FIELD_PRIORITY, *COUPLED_FIELDS.values())


class SecContactRequired(RuntimeError):
    """SEC's fair-access policy requires SEC_CONTACT; nothing was requested."""


def valid_website(value):
    """An absolute http(s) URL with a host, else None."""

    if not isinstance(value, str) or len(value) > 2048:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or " " in value.strip():
        return None
    return value.strip()


def sanitize_facts(facts):
    clean = {}
    for key, value in facts.items():
        if key == "website":
            value = valid_website(value)
        elif key in LIST_FIELDS:
            value = list(dict.fromkeys(item for item in value if isinstance(item, str) and item))
        if value not in (None, "", []):
            clean[key] = value
    return clean


def profile_hash(facts, names, identities, published_at):
    payload = {
        "facts": facts,
        "names": sorted([name.name, name.locale or "", name.name_type] for name in names),
        "identities": sorted([scheme, value] for scheme, value in identities),
        "published_at": published_at.isoformat() if published_at else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def find_target_company(session, symbol):
    """The (redirect-resolved) company holding the target's ranking symbol, if observed."""

    company_id = session.scalar(
        select(CompanyIdentity.company_id).where(
            CompanyIdentity.scheme == SYMBOL_SCHEME, CompanyIdentity.value == symbol
        )
    )
    if company_id is None:
        return None
    return session.get(Company, resolve_company_redirect(session, company_id))


def attach_profile_identities(session, company, identities, source_key):
    """Attach identities; return the ones refused because they would conflict."""

    conflicts = []
    for scheme, value in identities:
        owner = session.scalar(
            select(CompanyIdentity.company_id).where(
                CompanyIdentity.scheme == scheme, CompanyIdentity.value == value
            )
        )
        if owner is not None:
            if owner != company.id:
                conflicts.append({"scheme": scheme, "value": value, "reason": "held_by_other"})
            continue
        if scheme in EXCLUSIVE_SCHEMES and session.scalar(
            select(CompanyIdentity.id).where(
                CompanyIdentity.company_id == company.id, CompanyIdentity.scheme == scheme
            )
        ):
            conflicts.append({"scheme": scheme, "value": value, "reason": "exclusive_scheme"})
            continue
        session.add(
            CompanyIdentity(
                company_id=company.id, scheme=scheme, value=value, source_key=source_key
            )
        )
        session.flush()
    return conflicts


def attach_names(session, company, names, source_key):
    for name in names:
        alias = name.name[:255]
        session.execute(
            insert(CompanyAlias)
            .values(
                company_id=company.id,
                alias=alias,
                normalized_alias=normalize_company_name(alias),
                locale=name.locale,
                alias_type=name.name_type,
                alias_source=source_key,
            )
            .on_conflict_do_nothing(constraint="company_alias_identity")
        )


def rebuild_profile(session, company_id):
    """Recompute the company's profile from its source records; returns it (or None)."""

    rows = {
        row.source_key: row
        for row in session.scalars(
            select(CompanyProfileSource).where(CompanyProfileSource.company_id == company_id)
        )
    }
    profile = session.get(CompanyProfile, company_id)
    if not rows:
        if profile is not None:
            session.delete(profile)
        return None
    values, sources = {}, {}
    for field, keys in FIELD_PRIORITY.items():
        for key in keys:
            row = rows.get(key)
            value = row.facts.get(field) if row else None
            if value not in (None, "", []):
                values[field] = value
                sources[field] = key
                if coupled := COUPLED_FIELDS.get(field):
                    values[coupled] = row.facts.get(coupled)
                break
    contributing = {rows[key] for key in sources.values()} or set(rows.values())
    if profile is None:
        profile = CompanyProfile(company_id=company_id)
        session.add(profile)
    for column in PROFILE_COLUMNS:
        default = [] if column in LIST_FIELDS else None
        setattr(profile, column, values.get(column, default))
    profile.field_sources = sources
    profile.profile_updated_at = max(row.retrieved_at for row in contributing)
    session.flush()
    return profile


def persist_profile_source(session, company, definition, profile):
    """Write one source's profile record for ``company`` inside the caller's transaction."""

    # Shares the ranking lock: both paths create identities and aliases for the same companies.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
    conflicts = attach_profile_identities(session, company, profile.identities, definition.key)
    refused = {(item["scheme"], item["value"]) for item in conflicts}
    identities = tuple(identity for identity in profile.identities if identity not in refused)
    attach_names(session, company, profile.names, definition.key)
    facts = sanitize_facts(profile.facts)
    digest = profile_hash(facts, profile.names, identities, profile.published_at)
    row = session.scalar(
        select(CompanyProfileSource).where(
            CompanyProfileSource.company_id == company.id,
            CompanyProfileSource.source_key == definition.key,
        )
    )
    status = "created"
    metadata = (
        {**profile.metadata, "identity_conflicts": conflicts}
        if conflicts
        else dict(profile.metadata)
    )
    if row is not None and row.content_hash == digest:
        status = "replayed"
        row.checked_at = profile.retrieved_at
        row.source_metadata = metadata
    else:
        if row is None:
            row = CompanyProfileSource(company_id=company.id, source_key=definition.key)
            session.add(row)
        else:
            status = "updated"
        row.source_type = definition.source_type
        row.source_name = definition.name
        row.source_url = profile.source_url
        row.retrieved_at = profile.retrieved_at
        row.checked_at = profile.retrieved_at
        row.published_at = profile.published_at
        row.content_hash = digest
        row.facts = facts
        row.source_metadata = metadata
    session.flush()
    rebuild_profile(session, company.id)
    return {
        "company_id": str(company.id),
        "status": status,
        "fields": sorted(facts),
        "names": len(profile.names),
        "identity_conflicts": conflicts,
    }


def _error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


async def _ingest_target(http, definition, target, session_factory):
    base = {"source": definition.key, "symbol": target.symbol}
    with session_factory() as session:
        company = find_target_company(session, target.symbol)
        company_id = company.id if company else None
    if company_id is None:
        # The target's ranking symbol has not been observed; nothing is requested.
        return {**base, "error": "CompanyNotFound"}
    adapter = definition.adapter(http, definition)
    try:
        profile = await adapter.fetch(target)
        with session_factory.begin() as session:
            result = persist_profile_source(
                session, session.get(Company, company_id), definition, profile
            )
    except Exception as exc:
        logger.warning(
            "source=%s symbol=%s event=company_profile_failed error=%s",
            definition.key,
            target.symbol,
            _error(exc),
        )
        return {**base, "error": _error(exc)}
    logger.info(
        "source=%s symbol=%s event=company_profile_ingested status=%s",
        definition.key,
        target.symbol,
        result["status"],
    )
    return {**base, **result, "failed_count": adapter.failed_count}


async def ingest_company_profiles(
    settings, session_factory, symbols=None, source_keys=None, sleep=asyncio.sleep
):
    """Ingest reviewed targets from every adopted source; failures stay isolated."""

    targets = [t for t in PROFILE_TARGETS if not symbols or t.symbol in symbols]
    results = []
    for definition in PROFILE_SOURCES.values():
        if source_keys and definition.key not in source_keys:
            continue
        applicable = [t for t in targets if getattr(t, definition.target_field)]
        if not applicable:
            continue
        if definition.requires_sec_contact and not settings.sec_contact:
            logger.warning(
                "source=%s event=company_profile_skipped error=SecContactRequired", definition.key
            )
            results.append({"source": definition.key, "error": SecContactRequired.__name__})
            continue
        sec_contact = settings.sec_contact if definition.requires_sec_contact else None
        async with HTTPClient(settings, sec_contact=sec_contact) as http:
            for index, target in enumerate(applicable):
                if index:
                    await sleep(definition.interval)
                results.append(await _ingest_target(http, definition, target, session_factory))
    return results
