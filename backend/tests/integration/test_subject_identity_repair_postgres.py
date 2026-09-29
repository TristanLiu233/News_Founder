"""DQ-1 Subject repairs run inside a rolled-back isolated PostgreSQL transaction."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    ContentItem,
    ContentSubjectLink,
    Event,
    EventContentLink,
    EventSubjectLink,
    RankList,
    RankObservation,
    Source,
    Subject,
    SubjectAlias,
)
from industry_intelligence.db.models.canonical import SubjectExternalIdentity
from industry_intelligence.db.models.search import SearchDocument, SubjectRedirect
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.services.resolution import normalize_subject_name
from industry_intelligence.services.search import search
from industry_intelligence.services.search_index import refresh_subject
from industry_intelligence.services.subject_identity_repair import (
    SubjectIdentityRepairRefused,
    plan_subject_identity_repair,
    repair_subject_identities,
)

pytestmark = pytest.mark.integration
NOW = datetime(2032, 1, 1, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection)
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
    engine.dispose()


def legacy_subject(db, title, *, domain=Domain.FINANCE):
    row = Subject(
        domain=domain,
        subject_type=SubjectType.STOCK,
        canonical_name=hashlib.sha256(f"legacy:{uuid4()}".encode()).hexdigest(),
        title=title,
    )
    db.add(row)
    db.flush()
    db.add(
        SubjectAlias(
            subject_id=row.id,
            alias=title,
            normalized_alias=normalize_subject_name(title),
            locale="en",
            alias_type="source_name",
            alias_source="phase1_backfill",
        )
    )
    db.flush()
    return row


def source(db, name):
    row = Source(
        name=f"{name} {uuid4()}",
        domain=Domain.FINANCE,
        source_type="test",
        base_url="https://example.test",
        primary_language="en",
    )
    db.add(row)
    db.flush()
    return row


def identity(db, subject, provider, external_id, canonical_id=None):
    row = SubjectExternalIdentity(
        subject_id=subject.id,
        source_id=provider.id,
        external_id=external_id,
        normalized_name=normalize_subject_name(subject.title),
        canonical_id=canonical_id,
        resolution_strategy="phase1_backfill",
        confidence=1.0,
    )
    db.add(row)
    db.flush()
    return row


def observation(db, subject, provider, rank):
    ranking = RankList(
        source_id=provider.id,
        name=f"Repair ranking {uuid4()}",
        domain=Domain.FINANCE,
    )
    db.add(ranking)
    db.flush()
    row = RankObservation(
        subject_id=subject.id,
        source_id=provider.id,
        rank_list_id=ranking.id,
        rank=rank,
        title=subject.title,
        observed_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def test_legacy_hash_repair_dry_run_apply_and_idempotency(db):
    subject = legacy_subject(db, f"Invariant Repair {uuid4().hex}")
    provider = source(db, "repair-safe")
    identity(db, subject, provider, f"safe-{uuid4()}")
    observation(db, subject, provider, 1)
    refresh_subject(db, subject.id)
    before = (subject.canonical_name, db.scalar(select(func.count()).select_from(SubjectRedirect)))

    preview = repair_subject_identities(db, dry_run=True)
    assert any(row["id"] == str(subject.id) for row in preview["safe_updates"])
    assert (
        subject.canonical_name,
        db.scalar(select(func.count()).select_from(SubjectRedirect)),
    ) == before

    result = repair_subject_identities(db, dry_run=False)
    assert result["applied"]["canonical_updates"] >= 1
    assert subject.canonical_name == normalize_subject_name(subject.title)
    assert (
        db.scalar(
            select(func.count())
            .select_from(RankObservation)
            .where(RankObservation.subject_id == subject.id)
        )
        == 1
    )
    document = db.get(SearchDocument, subject.id)
    assert document.normalized_name == normalize_subject_name(subject.title)
    assert not plan_subject_identity_repair(db)["safe_updates"]


def test_provider_backed_collision_merges_and_preserves_links_and_search(db):
    tag = uuid4().hex
    title = f"Repair Asset {tag}"
    retired = legacy_subject(db, title)
    survivor = legacy_subject(db, title.upper())
    old_source, current_source = source(db, "repair-old"), source(db, "repair-current")
    identity(db, retired, old_source, f"old-{tag}")
    identity(db, survivor, current_source, f"current-{tag}", f"asset:{tag}")
    observation(db, retired, old_source, 1)
    observation(db, survivor, current_source, 2)

    item = ContentItem(
        source_id=old_source.id,
        domain=Domain.FINANCE,
        content_type="news",
        identity_key=f"repair-{tag}",
        title=f"{title} ships",
        url=f"https://example.test/{tag}",
        published_at=NOW,
        observed_at=NOW,
        language="en",
        item_metadata={},
        content_hash=tag,
    )
    db.add(item)
    db.flush()
    db.add(
        ContentSubjectLink(
            content_item_id=item.id, subject_id=retired.id, strategy="exact_name", confidence=1.0
        )
    )
    event = Event(
        domain=Domain.FINANCE,
        event_type="product_launch",
        canonical_title=item.title,
        normalized_title=normalize_subject_name(item.title),
        title_language="en",
        representative_content_id=item.id,
        first_published_at=NOW,
        last_published_at=NOW,
        first_observed_at=NOW,
        last_observed_at=NOW,
        content_count=1,
        source_count=1,
    )
    db.add(event)
    db.flush()
    db.add(
        EventContentLink(
            event_id=event.id,
            content_item_id=item.id,
            strategy="seed",
            confidence="origin",
            evidence={},
        )
    )
    db.add(
        EventSubjectLink(
            event_id=event.id,
            subject_id=retired.id,
            strategy="content_subject_link",
            content_count=1,
            evidence={"content_item_ids": [str(item.id)]},
        )
    )
    refresh_subject(db, retired.id)
    refresh_subject(db, survivor.id)
    db.flush()

    preview = plan_subject_identity_repair(db)
    merge = next(row for row in preview["merges"] if row["target"] == normalize_subject_name(title))
    assert merge["survivor"]["id"] == str(survivor.id)
    assert merge["retired"]["id"] == str(retired.id)
    assert merge["survivor"]["identities"][0]["canonical_id"] == f"asset:{tag}"
    assert merge["retired"]["aliases"] == [title]

    repair_subject_identities(db, dry_run=False)
    assert db.get(SubjectRedirect, retired.id).canonical_id == survivor.id
    assert (
        db.scalar(
            select(func.count())
            .select_from(RankObservation)
            .where(RankObservation.subject_id == survivor.id)
        )
        == 2
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(SubjectExternalIdentity)
            .where(SubjectExternalIdentity.subject_id == survivor.id)
        )
        == 2
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(ContentSubjectLink)
            .where(ContentSubjectLink.subject_id == survivor.id)
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(EventSubjectLink)
            .where(EventSubjectLink.subject_id == survivor.id)
        )
        == 1
    )
    assert db.get(SearchDocument, retired.id) is None
    assert search(db, get_settings(), title)["items"][0]["subject_id"] == str(survivor.id)
    rerun = plan_subject_identity_repair(db)
    assert rerun["safe_updates"] == [] and rerun["merges"] == []


def test_ambiguous_collision_is_review_only_and_apply_writes_nothing(db):
    title = f"Ambiguous Repair {uuid4().hex}"
    first, second = legacy_subject(db, title), legacy_subject(db, title.upper())
    before = (first.canonical_name, second.canonical_name)
    plan = plan_subject_identity_repair(db)
    assert any(row["target"] == normalize_subject_name(title) for row in plan["review_required"])
    with pytest.raises(SubjectIdentityRepairRefused):
        repair_subject_identities(db, dry_run=False)
    assert (first.canonical_name, second.canonical_name) == before
    assert db.get(SubjectRedirect, first.id) is None
    assert db.get(SubjectRedirect, second.id) is None


def test_target_held_by_another_canonical_name_is_review_only(db):
    title = f"Occupied Repair {uuid4().hex}"
    subject = legacy_subject(db, title)
    holder = Subject(
        domain=Domain.FINANCE,
        subject_type=SubjectType.STOCK,
        canonical_name=normalize_subject_name(title),
        title=f"Different title {uuid4().hex}",
    )
    db.add(holder)
    db.flush()
    before = subject.canonical_name

    plan = plan_subject_identity_repair(db)
    assert not any(row["id"] == str(subject.id) for row in plan["safe_updates"])
    review = next(
        row for row in plan["review_required"] if row["target"] == normalize_subject_name(title)
    )
    assert review["reason"] == "canonical_name_occupied"
    assert review["occupied_by"] == [str(holder.id)]
    with pytest.raises(SubjectIdentityRepairRefused):
        repair_subject_identities(db, dry_run=False)
    assert subject.canonical_name == before
