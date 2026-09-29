"""Collision-aware repair of the Phase 1 Subject canonical-name invariant.

The service is deliberately narrow: it recognizes only hash-shaped Subjects carrying the
``phase1_backfill`` marker, plans every change before writing, and auto-merges a collision only
when one Subject uniquely owns a shared provider-neutral canonical identity. Unknown collisions
fail closed for apply mode and remain visible in dry-run output.
"""

from __future__ import annotations

import re
from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select

from industry_intelligence.db.models import (
    ContentSubjectLink,
    EventSubjectLink,
    RankObservation,
    Subject,
)
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.db.models.search import SearchDocument, SubjectRedirect
from industry_intelligence.services.resolution import merge_subjects, normalize_subject_name
from industry_intelligence.services.search_index import refresh_subject

LEGACY_HASH = re.compile(r"^[0-9a-f]{64}$")
LEGACY_ALIAS_SOURCE = "phase1_backfill"


class SubjectIdentityRepairRefused(ValueError):
    """Raised before writes when the complete plan contains an unproved collision."""


def _counts(session, subject_id):
    def count(model, column):
        return session.scalar(select(func.count()).select_from(model).where(column == subject_id))

    return {
        "observations": count(RankObservation, RankObservation.subject_id),
        "alias_count": count(SubjectAlias, SubjectAlias.subject_id),
        "identity_count": count(SubjectExternalIdentity, SubjectExternalIdentity.subject_id),
        "content_links": count(ContentSubjectLink, ContentSubjectLink.subject_id),
        "event_links": count(EventSubjectLink, EventSubjectLink.subject_id),
        "search_documents": count(SearchDocument, SearchDocument.subject_id),
    }


def _subject_record(session, subject, aliases, identities):
    return {
        "id": str(subject.id),
        "title": subject.title,
        "canonical_name": subject.canonical_name,
        "aliases": sorted({alias.alias for alias in aliases[subject.id]}),
        "identities": [
            {
                "source_id": str(identity.source_id),
                "external_id": identity.external_id,
                "canonical_id": identity.canonical_id,
                "normalized_name": identity.normalized_name,
            }
            for identity in sorted(
                identities[subject.id], key=lambda row: (str(row.source_id), row.external_id)
            )
        ],
        **_counts(session, subject.id),
    }


def _deterministic_survivor(group, target, legacy_ids, identities):
    """Return (survivor, retired, evidence) or None for a review-required collision."""

    if len(group) != 2 or {subject.id for subject in group} - legacy_ids:
        return None
    if any(
        not identities[subject.id]
        or any(identity.normalized_name != target for identity in identities[subject.id])
        for subject in group
    ):
        return None
    canonical_ids = {
        identity.canonical_id
        for subject in group
        for identity in identities[subject.id]
        if identity.canonical_id
    }
    if len(canonical_ids) != 1:
        return None
    [canonical_id] = canonical_ids
    owners = [
        subject
        for subject in group
        if any(identity.canonical_id == canonical_id for identity in identities[subject.id])
    ]
    if len(owners) != 1:
        return None
    survivor = owners[0]
    retired = next(subject for subject in group if subject.id != survivor.id)
    return (
        survivor,
        retired,
        {
            "rule": "unique_provider_neutral_identity_owner",
            "canonical_id": canonical_id,
            "normalized_identity": target,
        },
    )


def plan_subject_identity_repair(session):
    redirected = select(SubjectRedirect.duplicate_id)
    subjects = list(session.scalars(select(Subject).where(Subject.id.not_in(redirected))))
    subject_ids = [subject.id for subject in subjects]
    aliases = defaultdict(list)
    identities = defaultdict(list)
    for alias in session.scalars(
        select(SubjectAlias).where(SubjectAlias.subject_id.in_(subject_ids))
    ):
        aliases[alias.subject_id].append(alias)
    for identity in session.scalars(
        select(SubjectExternalIdentity).where(SubjectExternalIdentity.subject_id.in_(subject_ids))
    ):
        identities[identity.subject_id].append(identity)

    legacy = [
        subject
        for subject in subjects
        if LEGACY_HASH.fullmatch(subject.canonical_name)
        and any(alias.alias_source == LEGACY_ALIAS_SOURCE for alias in aliases[subject.id])
    ]
    legacy_ids = {subject.id for subject in legacy}
    # Redirected Subjects keep their rows, so their canonical names still occupy the unique key.
    occupied = defaultdict(set)
    for subject_id, domain, subject_type, canonical_name in session.execute(
        select(Subject.id, Subject.domain, Subject.subject_type, Subject.canonical_name)
    ):
        occupied[(domain, subject_type, canonical_name)].add(subject_id)
    by_key = defaultdict(list)
    for subject in subjects:
        by_key[
            (subject.domain, subject.subject_type, normalize_subject_name(subject.title))
        ].append(subject)

    safe_updates = []
    merges = []
    review_required = []
    handled = set()
    for subject in sorted(legacy, key=lambda row: str(row.id)):
        target = normalize_subject_name(subject.title)
        key = (subject.domain, subject.subject_type, target)
        group = by_key[key]
        blockers = occupied[key] - {row.id for row in group}
        if blockers:
            if key not in handled:
                handled.add(key)
                review_required.append(
                    {
                        "target": target,
                        "reason": "canonical_name_occupied",
                        "occupied_by": sorted(str(subject_id) for subject_id in blockers),
                        "subjects": [
                            _subject_record(session, row, aliases, identities) for row in group
                        ],
                    }
                )
            continue
        if len(group) == 1:
            safe_updates.append(
                {
                    "id": str(subject.id),
                    "title": subject.title,
                    "old": subject.canonical_name,
                    "new": target,
                }
            )
            continue
        if key in handled:
            continue
        handled.add(key)
        records = [_subject_record(session, row, aliases, identities) for row in group]
        decision = _deterministic_survivor(group, target, legacy_ids, identities)
        if decision is None:
            review_required.append({"target": target, "subjects": records})
            continue
        survivor, retired, evidence = decision
        merges.append(
            {
                "target": target,
                "survivor": _subject_record(session, survivor, aliases, identities),
                "retired": _subject_record(session, retired, aliases, identities),
                "evidence": evidence,
            }
        )

    reindex = [row["id"] for row in safe_updates] + [row["survivor"]["id"] for row in merges]
    retired = [row["retired"]["id"] for row in merges]
    return {
        "subjects": len(subjects),
        "legacy_hashes": len(legacy),
        "safe_updates": safe_updates,
        "collisions": len(merges) + len(review_required),
        "merges": merges,
        "review_required": review_required,
        "expected_redirects": retired,
        "search_reindex_targets": reindex,
        "search_delete_targets": retired,
    }


def repair_subject_identities(session, *, dry_run=True):
    """Plan or transactionally apply the bounded Subject identity repair."""

    plan = plan_subject_identity_repair(session)
    result = {"dry_run": dry_run, **plan}
    if dry_run:
        return result
    if plan["review_required"]:
        raise SubjectIdentityRepairRefused(
            f"{len(plan['review_required'])} collision group(s) require review"
        )

    subjects = {str(subject.id): subject for subject in session.scalars(select(Subject))}
    for item in plan["merges"]:
        merge_subjects(session, UUID(item["survivor"]["id"]), UUID(item["retired"]["id"]))
        subjects[item["survivor"]["id"]].canonical_name = item["target"]
    for item in plan["safe_updates"]:
        subjects[item["id"]].canonical_name = item["new"]
    session.flush()
    for subject_id in plan["search_reindex_targets"]:
        refresh_subject(session, UUID(subject_id))
    session.flush()
    result["applied"] = {
        "canonical_updates": len(plan["safe_updates"]) + len(plan["merges"]),
        "merges": len(plan["merges"]),
        "redirects": len(plan["expected_redirects"]),
        "search_documents_refreshed": len(plan["search_reindex_targets"]),
    }
    return result
