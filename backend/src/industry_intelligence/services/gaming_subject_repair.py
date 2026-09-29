"""Deterministic repair of 3DM game Subjects whose names still carry ranking-row text (DQ-2.1).

The first 3DM ingestion stored the whole flattened row ("8 剑网3 角色扮演 运营商：… 8.9 分") as
title and canonical_name; later runs healed only the title. A candidate is an active Subject of
the 3DM definition's domain and type, held by a 3DM external identity, whose canonical_name
differs from its normalized title. The clean name is derived from the Subject's own persisted
3DM evidence (title, observation titles, 3DM source alias) with the current row-text rule, and
every piece of evidence must agree. Anything else — disagreeing evidence, several 3DM identities,
or a target canonical_name held by another Subject — is review-required and blocks ``--apply``.
Nothing is merged; aliases and identity ``normalized_name`` are kept as source provenance.
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy import select

from industry_intelligence.adapters.source.chinese import clean_3dm_row_text, is_3dm_row_text
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.models import RankObservation, Source, Subject
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.services.resolution import is_polluted_title, normalize_subject_name
from industry_intelligence.services.search_index import refresh_subject
from industry_intelligence.services.subject_identity_repair import LEGACY_HASH

SOURCE_KEY = "game_3dm"


class GamingSubjectRepairRefused(ValueError):
    """Raised before writes when the plan contains a review-required candidate."""


def _clean(text):
    text = " ".join(text.split())
    return clean_3dm_row_text(text) if is_3dm_row_text(text) else text


def plan_gaming_subject_repair(session):
    definition = SOURCES[SOURCE_KEY]
    source = session.scalar(select(Source).where(Source.name == definition.name))
    empty = {"source": definition.name, "candidates": 0, "safe_updates": [], "review_required": []}
    if source is None:
        return {**empty, "search_refresh_targets": []}
    redirected = select(SubjectRedirect.duplicate_id)
    held = select(SubjectExternalIdentity.subject_id).where(
        SubjectExternalIdentity.source_id == source.id
    )
    subjects = [
        subject
        for subject in session.scalars(
            select(Subject)
            .where(
                Subject.domain == definition.domain,
                Subject.subject_type == definition.subject_type,
                Subject.id.in_(held),
                Subject.id.not_in(redirected),
            )
            .order_by(Subject.id)
        )
        if subject.canonical_name != normalize_subject_name(subject.title)
        and not LEGACY_HASH.fullmatch(subject.canonical_name)
    ]
    planned = []
    for subject in subjects:
        identities = session.scalars(
            select(SubjectExternalIdentity.external_id).where(
                SubjectExternalIdentity.subject_id == subject.id,
                SubjectExternalIdentity.source_id == source.id,
            )
        ).all()
        observed = session.scalars(
            select(RankObservation.title).where(
                RankObservation.subject_id == subject.id,
                RankObservation.source_id == source.id,
            )
        ).all()
        aliases = session.scalars(
            select(SubjectAlias.alias).where(
                SubjectAlias.subject_id == subject.id, SubjectAlias.alias_source == SOURCE_KEY
            )
        ).all()
        evidence = [subject.title, *observed, *aliases]
        names = sorted({_clean(text) for text in evidence if text})
        record = {
            "id": str(subject.id),
            "external_ids": sorted(identities),
            "old_title": subject.title,
            "old_canonical_name": subject.canonical_name,
            "evidence_texts": len(set(evidence)),
            "observations": len(observed),
            "source_aliases": sorted(aliases),
        }
        if len(identities) != 1:
            planned.append((subject, {**record, "reason": "ambiguous_source_identity"}))
        elif len(names) != 1 or not names[0] or is_polluted_title(names[0]):
            planned.append((subject, {**record, "reason": "no_single_clean_name", "names": names}))
        else:
            target = normalize_subject_name(names[0])
            planned.append(
                (
                    subject,
                    {
                        **record,
                        "new_title": names[0],
                        "new_canonical_name": target,
                        "title_changed": names[0] != subject.title,
                    },
                )
            )

    targets = Counter(row["new_canonical_name"] for _, row in planned if "new_title" in row)
    safe_updates, review_required = [], []
    for subject, row in planned:
        if "reason" in row:
            review_required.append(row)
            continue
        target = row["new_canonical_name"]
        occupied = session.scalars(
            select(Subject.id).where(
                Subject.domain == subject.domain,
                Subject.subject_type == subject.subject_type,
                Subject.canonical_name == target,
                Subject.id != subject.id,
            )
        ).all()
        if occupied:
            review_required.append(
                {
                    **row,
                    "reason": "canonical_name_occupied",
                    "occupied_by": sorted(str(value) for value in occupied),
                }
            )
        elif targets[target] > 1:
            review_required.append({**row, "reason": "target_shared_by_candidates"})
        else:
            safe_updates.append(row)
    return {
        "source": definition.name,
        "candidates": len(planned),
        "safe_updates": safe_updates,
        "review_required": review_required,
        "search_refresh_targets": [row["id"] for row in safe_updates],
    }


def repair_gaming_subjects(session, *, dry_run=True):
    """Plan or transactionally apply the bounded 3DM game-name repair."""

    plan = plan_gaming_subject_repair(session)
    result = {"dry_run": dry_run, **plan}
    if dry_run:
        return result
    if plan["review_required"]:
        raise GamingSubjectRepairRefused(
            f"{len(plan['review_required'])} candidate(s) require review"
        )
    for row in plan["safe_updates"]:
        subject = session.get(Subject, UUID(row["id"]))
        subject.title = row["new_title"]
        subject.canonical_name = row["new_canonical_name"]
    session.flush()
    for subject_id in plan["search_refresh_targets"]:
        refresh_subject(session, UUID(subject_id))
    session.flush()
    result["applied"] = {
        "canonical_updates": len(plan["safe_updates"]),
        "title_updates": sum(1 for row in plan["safe_updates"] if row["title_changed"]),
        "search_documents_refreshed": len(plan["search_refresh_targets"]),
    }
    return result
