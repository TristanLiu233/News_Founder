"""Deterministic and conservative canonical subject resolution."""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, text, update

from industry_intelligence.db.models import (
    ContentSubjectLink,
    EventSubjectLink,
    RankObservation,
    Subject,
)
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.db.models.search import SubjectRedirect

TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}
SEED_GROUPS = (
    ("Black Myth: Wukong", "黑神话：悟空", "黑神话悟空", "Black Myth Wukong"),
    ("NVIDIA", "Nvidia", "英伟达"),
    ("Microsoft", "微软"),
    ("Bitcoin", "BTC", "比特币"),
)


def normalize_subject_name(value):
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    value = value.translate(str.maketrans({"：": ":", "﹕": ":", "·": "·", "•": "·", "・": "·"}))
    value = re.sub(r"\s*([:·])\s*", r"\1", value)
    return re.sub(r"\s+", " ", value)


def normalize_url(value):
    if not value:
        return None
    parts = urlsplit(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_PARAMETERS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), "")
    )


def locale_for(value):
    return "zh-CN" if re.search(r"[\u3400-\u9fff]", value) else "en"


DISPLAY_ALIAS_TYPES = {"official_name", "localized_name"}
DISPLAY_LOCALES = ("en", "zh-CN")


def display_names(canonical_name, aliases):
    """Per-locale display names shared by Search, Trending and Subject Detail.

    ``aliases`` are dicts ordered by (locale, alias). Localized/official aliases win,
    except that a non-Chinese canonical name beats ticker-like English aliases.
    """
    names = {
        alias["locale"]: alias["alias"]
        for alias in aliases
        if alias["alias_type"] in DISPLAY_ALIAS_TYPES
    }
    if locale_for(canonical_name) == "en":
        names["en"] = canonical_name
    return {locale: names.get(locale, canonical_name) for locale in DISPLAY_LOCALES}


def seeded_canonical(value):
    normalized = normalize_subject_name(value)
    for group in SEED_GROUPS:
        if normalized in {normalize_subject_name(alias) for alias in group}:
            return group[0], group
    return value, (value,)


def is_polluted_title(value):
    """Scraped row text stored as a name: over-long, or carrying 3DM's operator label."""

    return len(value) > 80 or "运营商：" in value or "运营商:" in value


def heal_polluted_title(session, subject, incoming):
    """Replace a polluted title found by exact external ID, keeping canonical_name in step.

    The incoming title must itself be clean. canonical_name follows the new title only when it
    was derived from polluted text (polluted itself or the old title's normal form); a
    deliberate canonical name, or a target held by any other Subject row (redirected ones
    included, as they keep the unique key), leaves both fields unchanged: never a merge.
    Returns True when the Subject changed.
    """

    if not is_polluted_title(subject.title) or is_polluted_title(incoming) or not incoming:
        return False
    target = normalize_subject_name(incoming)
    if subject.canonical_name != target:
        derived = is_polluted_title(subject.canonical_name) or (
            subject.canonical_name == normalize_subject_name(subject.title)
        )
        occupied = session.scalar(
            select(Subject.id).where(
                Subject.domain == subject.domain,
                Subject.subject_type == subject.subject_type,
                Subject.canonical_name == target,
                Subject.id != subject.id,
            )
        )
        if not derived or occupied is not None:
            return False
    subject.title = incoming
    subject.canonical_name = target
    return True


@dataclass(frozen=True)
class ResolutionResult:
    subject: Subject
    strategy: str
    confidence: float
    matched_alias: str | None = None


def resolve_subject(session, definition, source, item):
    external_id = item.external_id or normalize_url(item.url) or normalize_subject_name(item.title)
    canonical_id = (item.metadata or {}).get("canonical_id")
    asset_class = (item.metadata or {}).get("asset_class")
    identity = session.scalar(
        select(SubjectExternalIdentity).where(
            SubjectExternalIdentity.source_id == source.id,
            SubjectExternalIdentity.external_id == external_id,
        )
    )
    if identity:
        # Enrich Phase 1 identities with provider-neutral IDs. When another
        # provider already knows one, future observations share its subject.
        if canonical_id:
            shared = session.scalar(
                select(SubjectExternalIdentity)
                .join(Subject)
                .where(
                    Subject.domain == definition.domain,
                    Subject.subject_type == definition.subject_type,
                    SubjectExternalIdentity.canonical_id == str(canonical_id),
                    SubjectExternalIdentity.id != identity.id,
                )
            )
            identity.canonical_id = str(canonical_id)
            identity.asset_class = asset_class
            if shared:
                identity.subject_id = shared.subject_id
                identity.resolution_strategy = "exact_canonical_id"
                identity.confidence = 1.0
        subject = session.get(Subject, identity.subject_id)
        heal_polluted_title(session, subject, item.title)
        return ResolutionResult(subject, "exact_external_id", 1.0)
    normalized = normalize_subject_name(item.title)
    canonical_url = normalize_url(item.url)
    candidate = None
    strategy, confidence, matched = "new_subject", 1.0, None
    if canonical_id:
        found = session.scalar(
            select(SubjectExternalIdentity)
            .join(Subject)
            .where(
                Subject.domain == definition.domain,
                Subject.subject_type == definition.subject_type,
                SubjectExternalIdentity.canonical_id == str(canonical_id),
            )
        )
        if found:
            candidate, strategy = session.get(Subject, found.subject_id), "exact_canonical_id"
    if candidate is None and canonical_url:
        found = session.scalar(
            select(SubjectExternalIdentity)
            .join(Subject)
            .where(
                Subject.domain == definition.domain,
                Subject.subject_type == definition.subject_type,
                SubjectExternalIdentity.canonical_url == canonical_url,
            )
        )
        if found:
            candidate, strategy = session.get(Subject, found.subject_id), "exact_url"
    canonical_title, seed_group = seeded_canonical(item.title)
    seed_norms = {normalize_subject_name(alias) for alias in seed_group}
    if candidate is None:
        found = session.scalar(
            select(SubjectAlias)
            .join(Subject)
            .where(
                Subject.domain == definition.domain,
                Subject.subject_type == definition.subject_type,
                SubjectAlias.normalized_alias.in_(seed_norms),
            )
        )
        if found:
            candidate, strategy, matched = (
                session.get(Subject, found.subject_id),
                (
                    "cross_language_alias"
                    if locale_for(found.alias) != locale_for(item.title)
                    else "exact_alias"
                ),
                found.alias,
            )
    if candidate is None:
        candidate = session.scalar(
            select(Subject).where(
                Subject.domain == definition.domain,
                Subject.subject_type == definition.subject_type,
                Subject.canonical_name == normalize_subject_name(canonical_title),
                ~Subject.id.in_(select(SubjectRedirect.duplicate_id)),
            )
        )
        if candidate:
            strategy = "exact_normalized_name"
    if candidate is None and len(normalized) >= 8:
        # Blocking is always domain + type; require same script and a very high threshold.
        pool = session.scalars(
            select(Subject)
            .where(
                Subject.domain == definition.domain,
                Subject.subject_type == definition.subject_type,
                ~Subject.id.in_(select(SubjectRedirect.duplicate_id)),
            )
            .limit(200)
        ).all()
        same_script = locale_for(item.title)
        matches = [
            (SequenceMatcher(None, normalized, subject.canonical_name).ratio(), subject)
            for subject in pool
            if locale_for(subject.title) == same_script
        ]
        if matches and max(matches, key=lambda pair: pair[0])[0] >= 0.96:
            confidence, candidate = max(matches, key=lambda pair: pair[0])
            strategy = "fuzzy_name"
    if candidate is None:
        candidate = Subject(
            domain=definition.domain,
            subject_type=definition.subject_type,
            canonical_name=normalize_subject_name(canonical_title),
            title=canonical_title,
        )
        session.add(candidate)
        session.flush()
    for alias in seed_group:
        existing = session.scalar(
            select(SubjectAlias).where(
                SubjectAlias.subject_id == candidate.id,
                SubjectAlias.alias == alias,
                SubjectAlias.locale == locale_for(alias),
            )
        )
        if not existing:
            session.add(
                SubjectAlias(
                    subject_id=candidate.id,
                    alias=alias,
                    normalized_alias=normalize_subject_name(alias),
                    locale=locale_for(alias),
                    alias_type="localized_name" if len(seed_group) > 1 else "source_name",
                    alias_source="manual_seed" if len(seed_group) > 1 else definition.key,
                )
            )
    session.add(
        SubjectExternalIdentity(
            subject_id=candidate.id,
            source_id=source.id,
            external_id=external_id,
            canonical_url=canonical_url,
            canonical_id=str(canonical_id) if canonical_id else None,
            normalized_name=normalized,
            asset_class=asset_class,
            resolution_strategy=strategy,
            confidence=confidence,
            matched_alias=matched,
        )
    )
    return ResolutionResult(candidate, strategy, confidence, matched)


def merge_subjects(session, canonical_id, duplicate_id):
    """Move current identities while retaining immutable composite history."""
    if canonical_id == duplicate_id:
        return
    # Serialize merges so redirect chains cannot race into a cycle.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('subject_merge'))"))
    redirect = session.scalar(
        text("SELECT canonical_id FROM subject_redirects WHERE duplicate_id=:id"),
        {"id": canonical_id},
    )
    canonical_id = redirect or canonical_id
    if canonical_id == duplicate_id:
        raise ValueError("Cannot create a subject redirect cycle")
    if session.get(Subject, canonical_id) is None or session.get(Subject, duplicate_id) is None:
        raise ValueError("Both merge subjects must exist")
    session.execute(
        update(RankObservation)
        .where(RankObservation.subject_id == duplicate_id)
        .values(subject_id=canonical_id)
    )
    for alias in session.scalars(
        select(SubjectAlias).where(SubjectAlias.subject_id == duplicate_id)
    ).all():
        exists = session.scalar(
            select(SubjectAlias.id).where(
                SubjectAlias.subject_id == canonical_id,
                SubjectAlias.alias == alias.alias,
                SubjectAlias.locale == alias.locale,
            )
        )
        if exists:
            session.delete(alias)
        else:
            alias.subject_id = canonical_id
    session.execute(
        update(SubjectExternalIdentity)
        .where(SubjectExternalIdentity.subject_id == duplicate_id)
        .values(subject_id=canonical_id)
    )
    for link in session.scalars(
        select(ContentSubjectLink).where(ContentSubjectLink.subject_id == duplicate_id)
    ).all():
        exists = session.scalar(
            select(ContentSubjectLink.id).where(
                ContentSubjectLink.subject_id == canonical_id,
                ContentSubjectLink.content_item_id == link.content_item_id,
            )
        )
        if exists:
            session.delete(link)
        else:
            link.subject_id = canonical_id
    for link in session.scalars(
        select(EventSubjectLink).where(EventSubjectLink.subject_id == duplicate_id)
    ).all():
        existing = session.scalar(
            select(EventSubjectLink).where(
                EventSubjectLink.subject_id == canonical_id,
                EventSubjectLink.event_id == link.event_id,
            )
        )
        if existing:
            content_ids = sorted(
                {
                    *(existing.evidence or {}).get("content_item_ids", []),
                    *(link.evidence or {}).get("content_item_ids", []),
                }
            )
            existing.content_count = len(content_ids)
            existing.evidence = {**(existing.evidence or {}), "content_item_ids": content_ids}
            session.delete(link)
        else:
            link.subject_id = canonical_id
    session.execute(
        text("UPDATE subject_redirects SET canonical_id=:canonical WHERE canonical_id=:duplicate"),
        {"canonical": canonical_id, "duplicate": duplicate_id},
    )
    session.execute(
        text(
            "INSERT INTO subject_redirects VALUES (:duplicate, :canonical) "
            "ON CONFLICT (duplicate_id) DO UPDATE "
            "SET canonical_id=EXCLUDED.canonical_id"
        ),
        {"canonical": canonical_id, "duplicate": duplicate_id},
    )
    session.execute(text("DELETE FROM search_documents WHERE subject_id=:id"), {"id": duplicate_id})
    # Keep Subject and CompositeRankingEntry rows as historical records.
    from industry_intelligence.services.search_index import refresh_subject

    refresh_subject(session, canonical_id)


def backfill_subject_identities(session):
    """Preserve Phase 1 subjects while adding deterministic audit records."""
    aliases = identities = 0
    subjects = session.scalars(
        select(Subject).where(~Subject.id.in_(select(SubjectRedirect.duplicate_id)))
    ).all()
    known_identities = {
        (identity.source_id, identity.external_id)
        for identity in session.scalars(select(SubjectExternalIdentity)).all()
    }
    for subject in subjects:
        normalized = normalize_subject_name(subject.title)
        if not session.scalar(
            select(SubjectAlias).where(
                SubjectAlias.subject_id == subject.id,
                SubjectAlias.alias == subject.title,
                SubjectAlias.locale == locale_for(subject.title),
            )
        ):
            session.add(
                SubjectAlias(
                    subject_id=subject.id,
                    alias=subject.title,
                    normalized_alias=normalized,
                    locale=locale_for(subject.title),
                    alias_type="source_name",
                    alias_source="phase1_backfill",
                )
            )
            aliases += 1
        observations = session.scalars(
            select(RankObservation).where(RankObservation.subject_id == subject.id)
        ).all()
        for observation in observations:
            external_id = normalize_url(observation.url) or normalized
            key = (observation.source_id, external_id)
            if key in known_identities:
                continue
            session.add(
                SubjectExternalIdentity(
                    subject_id=subject.id,
                    source_id=observation.source_id,
                    external_id=external_id,
                    canonical_url=normalize_url(observation.url),
                    normalized_name=normalized,
                    resolution_strategy="phase1_backfill",
                    confidence=1.0,
                )
            )
            identities += 1
            known_identities.add(key)
    return {
        "subjects": len(subjects),
        "aliases_created": aliases,
        "external_identities_created": identities,
    }
