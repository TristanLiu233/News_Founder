"""Read models for chronological content and related news."""

from sqlalchemy import func, select

from industry_intelligence.db.models import (
    CompanySubjectLink,
    ContentItem,
    ContentSubjectLink,
    Source,
    Subject,
)


def serialize_content(row, linked=()):
    item, source = row
    return {
        "id": item.id,
        "title": item.title,
        "url": item.url,
        "source": source.name,
        "domain": item.domain,
        "content_type": item.content_type,
        "language": item.language,
        "published_at": item.published_at,
        "observed_at": item.observed_at,
        "linked_subjects": list(linked),
    }


def list_news(
    session,
    domain=None,
    source=None,
    language=None,
    subject_id=None,
    limit=20,
    offset=0,
    company_id=None,
):
    query = select(ContentItem, Source).join(Source)
    if subject_id:
        query = query.join(ContentSubjectLink).where(ContentSubjectLink.subject_id == subject_id)
    if company_id:
        # A company's related news is the news of its deterministically linked subjects.
        linked = select(CompanySubjectLink.subject_id).where(
            CompanySubjectLink.company_id == company_id
        )
        query = query.where(
            ContentItem.id.in_(
                select(ContentSubjectLink.content_item_id).where(
                    ContentSubjectLink.subject_id.in_(linked)
                )
            )
        )
    if domain:
        query = query.where(ContentItem.domain == domain)
    if source:
        query = query.where(Source.name == source)
    if language:
        query = query.where(ContentItem.language == language)
    query = (
        query.order_by(func.coalesce(ContentItem.published_at, ContentItem.observed_at).desc())
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(query).all()
    ids = [item.id for item, _ in rows]
    subjects = {}
    if ids:
        for content_id, sid, name in session.execute(
            select(ContentSubjectLink.content_item_id, Subject.id, Subject.canonical_name)
            .join(Subject)
            .where(ContentSubjectLink.content_item_id.in_(ids))
        ):
            subjects.setdefault(content_id, []).append({"id": sid, "canonical_name": name})
    return [serialize_content(row, subjects.get(row[0].id, ())) for row in rows]


def related_counts(session, subject_ids):
    """Linked content-item counts per subject, in one grouped query."""
    if not subject_ids:
        return {}
    return dict(
        session.execute(
            select(ContentSubjectLink.subject_id, func.count(ContentSubjectLink.content_item_id))
            .where(ContentSubjectLink.subject_id.in_(subject_ids))
            .group_by(ContentSubjectLink.subject_id)
        ).all()
    )


def related_preview(session, subject_ids, limit=3):
    return {
        subject_id: list_news(session, subject_id=subject_id, limit=limit)
        for subject_id in subject_ids
    }
