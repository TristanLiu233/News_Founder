"""GLEIF LEI records: the Global LEI System's registry data for one reviewed LEI.

``api.gleif.org`` is GLEIF's documented public API (no key; data under CC0). Only the record of
the reviewed LEI is read, never a name search: full-text search returns ETFs, branches, and
subsidiaries, so it is not identity evidence. The record must be an active, non-branch legal
entity. It supplies the registered legal name (in its own language, e.g. 騰訊控股有限公司),
alternative-language legal names, the headquarters address, and the legal jurisdiction.
"""

from __future__ import annotations

from datetime import UTC, datetime

from industry_intelligence.adapters.company_profile.base import (
    ExternalCompanyProfile,
    LocalizedName,
    ProfileRejected,
)
from industry_intelligence.adapters.company_profile.text import clean_text

LEI_SCHEME = "gleif:lei"
RECORD_URL = "https://api.gleif.org/api/v1/lei-records/{lei}"
PUBLIC_URL = "https://search.gleif.org/#/record/{lei}"
# Other-name types that are legal names in another language or script; automatic
# transliterations (for example pinyin) are not published names and are skipped.
NAME_TYPES = {
    "ALTERNATIVE_LANGUAGE_LEGAL_NAME": "legal_name",
    "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME": "transliterated_legal_name",
}


def _datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _headquarters(address):
    if not address:
        return None
    parts = [clean_text(address.get("city")), clean_text(address.get("region"))]
    return ", ".join(part for part in parts if part) or None


def parse_lei_record(payload, lei):
    """Return (facts, names, published_at, metadata) or raise ``ProfileRejected``."""

    record = payload.get("data") or {}
    if record.get("id") != lei:
        raise ProfileRejected("GLEIF returned a different LEI")
    attributes = record.get("attributes") or {}
    entity = attributes.get("entity") or {}
    registration = attributes.get("registration") or {}
    if entity.get("category") == "BRANCH":
        raise ProfileRejected("LEI belongs to a branch, not the legal entity")
    if entity.get("status") != "ACTIVE":
        raise ProfileRejected(f"LEI entity status is {entity.get('status')}")
    legal = entity.get("legalName") or {}
    legal_name = clean_text(legal.get("name"))
    if not legal_name:
        raise ProfileRejected("LEI record has no legal name")
    headquarters = entity.get("headquartersAddress") or {}
    names = [LocalizedName(legal_name, legal.get("language"), "legal_name")]
    for other in (entity.get("otherNames") or []) + (entity.get("transliteratedOtherNames") or []):
        name_type = NAME_TYPES.get(other.get("type"))
        name = clean_text(other.get("name"))
        if name_type and name:
            locale = None if name_type == "transliterated_legal_name" else other.get("language")
            names.append(LocalizedName(name, locale, name_type))
    facts = {
        "legal_name": legal_name,
        "legal_name_locale": legal.get("language"),
        "headquarters": _headquarters(headquarters),
        "country": (headquarters.get("country") or "").upper()[:2] or None,
        "jurisdiction": entity.get("jurisdiction"),
    }
    metadata = {
        "lei": lei,
        "legal_form": (entity.get("legalForm") or {}).get("id"),
        "entity_category": entity.get("category"),
        "registration_status": registration.get("status"),
        "next_renewal_date": registration.get("nextRenewalDate"),
        "legal_address_country": (entity.get("legalAddress") or {}).get("country"),
    }
    published_at = _datetime(registration.get("lastUpdateDate"))
    return {k: v for k, v in facts.items() if v}, tuple(names), published_at, metadata


class GleifProfileAdapter:
    def __init__(self, http, definition, now=None):
        self.http = http
        self.definition = definition
        self.now = now or (lambda: datetime.now(UTC))
        self.failed_count = 0

    async def fetch(self, target) -> ExternalCompanyProfile:
        retrieved_at = self.now()
        response = await self.http.get(RECORD_URL.format(lei=target.lei))
        facts, names, published_at, metadata = parse_lei_record(response.json(), target.lei)
        return ExternalCompanyProfile(
            source_key=self.definition.key,
            source_url=PUBLIC_URL.format(lei=target.lei),
            retrieved_at=retrieved_at,
            facts=facts,
            identities=((LEI_SCHEME, target.lei),),
            names=names,
            published_at=published_at,
            metadata=metadata,
        )
