"""DQ-2.1: 3DM name producer, resolver title healing, and the bounded repair (rolled back)."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from industry_intelligence.adapters.source.base import SourceItem
from industry_intelligence.adapters.source.chinese import is_3dm_row_text, parse_3dm
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import RankList, RankObservation, Source, Subject
from industry_intelligence.db.models.canonical import SubjectAlias, SubjectExternalIdentity
from industry_intelligence.db.models.search import SearchDocument, SubjectRedirect
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.services.gaming_subject_repair import (
    GamingSubjectRepairRefused,
    plan_gaming_subject_repair,
    repair_gaming_subjects,
)
from industry_intelligence.services.resolution import normalize_subject_name, resolve_subject
from industry_intelligence.services.search_index import refresh_subject
from industry_intelligence.services.source_ingestion import persist

pytestmark = pytest.mark.integration
NOW = datetime(2034, 1, 1, tzinfo=UTC)
DEFINITION = SOURCES["game_3dm"]
FIXTURE = Path(__file__).parents[1] / "fixtures" / "3dm_ranking_live.html"


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


def tag():
    return uuid4().hex[:10]


def source_3dm(db):
    found = db.scalar(select(Source).where(Source.name == DEFINITION.name))
    if found:
        return found
    row = Source(
        name=DEFINITION.name,
        domain=DEFINITION.domain,
        source_type="HTML",
        base_url=DEFINITION.url,
        primary_language="zh-CN",
    )
    db.add(row)
    db.flush()
    return row


def item(title, slug):
    return SimpleNamespace(
        external_id=slug, url=f"https://ol.3dmgame.com/{slug}/", title=title, metadata={}
    )


def row_text(name, rank=8):
    return f"{rank} {name} 角色扮演 运营商：测试厂商 公测时间：2009-08-28 8.9 分"


def historical(db, source, name, *, title=None, raw=None):
    """A Subject as the first 3DM run left it: row text as canonical name and source alias."""

    raw = raw or row_text(name)
    subject = Subject(
        domain=Domain.GAMING,
        subject_type=SubjectType.GAME,
        canonical_name=normalize_subject_name(raw),
        title=title or name,
    )
    db.add(subject)
    db.flush()
    slug = f"dq{tag()}"
    db.add_all(
        [
            SubjectAlias(
                subject_id=subject.id,
                alias=raw,
                normalized_alias=normalize_subject_name(raw),
                locale="zh-CN",
                alias_type="source_name",
                alias_source="game_3dm",
            ),
            SubjectExternalIdentity(
                subject_id=subject.id,
                source_id=source.id,
                external_id=slug,
                canonical_url=f"https://ol.3dmgame.com/{slug}",
                normalized_name=normalize_subject_name(raw),
                resolution_strategy="new_subject",
                confidence=1.0,
            ),
        ]
    )
    ranking = RankList(source_id=source.id, name=f"DQ 3DM {tag()}", domain=Domain.GAMING)
    db.add(ranking)
    db.flush()
    for hour, observed in enumerate((raw, title or name)):
        db.add(
            RankObservation(
                subject_id=subject.id,
                source_id=source.id,
                rank_list_id=ranking.id,
                rank=8,
                title=observed,
                observed_at=NOW.replace(hour=hour),
            )
        )
    db.flush()
    refresh_subject(db, subject.id)
    return subject


def state(db, *subjects):
    return [
        (
            row.title,
            row.canonical_name,
            db.scalar(
                select(SearchDocument.canonical_name).where(SearchDocument.subject_id == row.id)
            ),
        )
        for row in (db.get(Subject, subject.id) for subject in subjects)
    ]


def plan_row(plan, subject, key="safe_updates"):
    return next((row for row in plan[key] if row["id"] == str(subject.id)), None)


# Resolver: title healing by exact external ID.


def test_polluted_title_heals_title_and_canonical_name_together(db):
    source, name, slug = source_3dm(db), f"DQ剑网{tag()}", f"dq{tag()}"
    first = resolve_subject(db, DEFINITION, source, item(row_text(name), slug)).subject
    assert first.canonical_name == normalize_subject_name(row_text(name))

    healed = resolve_subject(db, DEFINITION, source, item(name, slug))

    assert healed.subject.id == first.id
    assert healed.strategy == "exact_external_id"
    assert (first.title, first.canonical_name) == (name, normalize_subject_name(name))


def test_healing_keeps_source_alias_and_identity_evidence(db):
    source, name, slug = source_3dm(db), f"DQ魔兽{tag()}", f"dq{tag()}"
    subject = resolve_subject(db, DEFINITION, source, item(row_text(name), slug)).subject
    resolve_subject(db, DEFINITION, source, item(name, slug))
    db.flush()

    aliases = db.scalars(select(SubjectAlias).where(SubjectAlias.subject_id == subject.id)).all()
    identities = db.scalars(
        select(SubjectExternalIdentity).where(SubjectExternalIdentity.subject_id == subject.id)
    ).all()
    assert [(row.alias, row.alias_source) for row in aliases] == [(row_text(name), "game_3dm")]
    assert [(row.external_id, row.normalized_name) for row in identities] == [
        (slug, normalize_subject_name(row_text(name)))
    ]


@pytest.mark.parametrize("retired", [False, True])
def test_occupied_target_fails_closed_without_merge(db, retired):
    source, name, slug = source_3dm(db), f"DQ占用{tag()}", f"dq{tag()}"
    subject = resolve_subject(db, DEFINITION, source, item(row_text(name), slug)).subject
    other = Subject(
        domain=Domain.GAMING,
        subject_type=SubjectType.GAME,
        canonical_name=normalize_subject_name(name),
        title=name,
    )
    db.add(other)
    db.flush()
    if retired:
        keeper = Subject(
            domain=Domain.GAMING, subject_type=SubjectType.GAME, canonical_name=tag(), title=tag()
        )
        db.add(keeper)
        db.flush()
        db.add(SubjectRedirect(duplicate_id=other.id, canonical_id=keeper.id))
        db.flush()
    redirects = db.scalar(select(func.count()).select_from(SubjectRedirect))
    before = (subject.title, subject.canonical_name)

    result = resolve_subject(db, DEFINITION, source, item(name, slug))

    assert result.subject.id == subject.id
    assert (subject.title, subject.canonical_name) == before
    assert db.scalar(select(func.count()).select_from(SubjectRedirect)) == redirects


def test_clean_subject_deliberate_canonical_and_polluted_input_are_left_alone(db):
    source, name, slug = source_3dm(db), f"DQ干净{tag()}", f"dq{tag()}"
    clean = resolve_subject(db, DEFINITION, source, item(name, slug)).subject
    resolve_subject(db, DEFINITION, source, item(row_text(name), slug))
    assert (clean.title, clean.canonical_name) == (name, normalize_subject_name(name))

    other_slug = f"dq{tag()}"
    seeded = resolve_subject(db, DEFINITION, source, item(row_text(name, 3), other_slug)).subject
    seeded.canonical_name = f"deliberate {tag()}"
    before = (seeded.title, seeded.canonical_name)
    resolve_subject(db, DEFINITION, source, item(f"DQ另一个{tag()}", other_slug))
    assert (seeded.title, seeded.canonical_name) == before


def test_repeated_ingestion_is_idempotent(db):
    source, name, slug = source_3dm(db), f"DQ重复{tag()}", f"dq{tag()}"
    resolve_subject(db, DEFINITION, source, item(row_text(name), slug))
    results = [resolve_subject(db, DEFINITION, source, item(name, slug)) for _ in range(3)]
    db.flush()

    subject = results[0].subject
    assert {result.subject.id for result in results} == {subject.id}
    assert (subject.title, subject.canonical_name) == (name, normalize_subject_name(name))
    assert (
        db.scalar(
            select(func.count())
            .select_from(SubjectExternalIdentity)
            .where(SubjectExternalIdentity.subject_id == subject.id)
        )
        == 1
    )


def test_ingesting_the_live_fixture_creates_clean_consistent_subjects(db):
    definition = replace(DEFINITION, name=f"3DM DQ {tag()}", key=f"game_3dm_dq_{tag()}")
    rows, failed = parse_3dm(FIXTURE.read_text())
    items = [SourceItem(**row, observed_at=NOW) for row in rows]

    result = persist(db, definition, items, f"dq-{tag()}", failed)

    assert result["persisted_count"] == len(rows) == 20
    subjects = db.scalars(
        select(Subject)
        .join(RankObservation, RankObservation.subject_id == Subject.id)
        .join(Source, Source.id == RankObservation.source_id)
        .where(Source.name == definition.name)
    ).all()
    assert len(subjects) == 20
    for subject in subjects:
        assert not is_3dm_row_text(subject.title)
        assert subject.canonical_name == normalize_subject_name(subject.title)


# Repair: plan, dry-run, apply, refusal, idempotency.


def test_plan_detects_residue_and_polluted_titles(db):
    source = source_3dm(db)
    residue = historical(db, source, f"DQ残留{tag()}")
    name = f"DQ暗黑{tag()}"
    polluted = historical(
        db, source, name, title=f"{name} 运营商：测试厂商 公测时间：2023-06-06 9.0 分"
    )

    plan = plan_gaming_subject_repair(db)

    first, second = plan_row(plan, residue), plan_row(plan, polluted)
    assert (first["new_title"], first["title_changed"]) == (residue.title, False)
    assert first["new_canonical_name"] == normalize_subject_name(residue.title)
    assert (second["new_title"], second["title_changed"]) == (name, True)
    assert first["external_ids"] and first["source_aliases"]


def test_dry_run_writes_nothing(db):
    subject = historical(db, source_3dm(db), f"DQ只读{tag()}")
    fingerprint = """
        SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM (
            SELECT id::text, title, canonical_name FROM subjects
            UNION ALL SELECT subject_id::text, canonical_name, normalized_name FROM search_documents
        ) t
    """
    before = db.scalar(text(fingerprint))
    db.execute(text("SET TRANSACTION READ ONLY"))

    result = repair_gaming_subjects(db, dry_run=True)

    assert plan_row(result, subject)
    assert "applied" not in result
    assert db.scalar(text(fingerprint)) == before


def test_apply_updates_names_and_refreshes_search(db):
    source = source_3dm(db)
    residue = historical(db, source, f"DQ应用{tag()}")
    name = f"DQ流放{tag()}"
    polluted = historical(db, source, name, title=f"{name} 2018-01-11 运营商：测试 8.2 分")
    aliases = db.scalar(select(func.count()).select_from(SubjectAlias))
    redirects = db.scalar(select(func.count()).select_from(SubjectRedirect))

    result = repair_gaming_subjects(db, dry_run=False)

    assert result["applied"]["canonical_updates"] >= 2
    for subject, title in ((residue, residue.title), (polluted, name)):
        document = db.get(SearchDocument, subject.id)
        db.refresh(document)
        assert (subject.title, subject.canonical_name) == (title, normalize_subject_name(title))
        assert (document.canonical_name, document.normalized_name) == (
            title,
            normalize_subject_name(title),
        )
    assert db.scalar(select(func.count()).select_from(SubjectAlias)) == aliases
    assert db.scalar(select(func.count()).select_from(SubjectRedirect)) == redirects

    again = plan_gaming_subject_repair(db)
    assert not plan_row(again, residue) and not plan_row(again, polluted)
    assert not plan_row(again, residue, "review_required")


def test_occupied_target_is_review_required_and_blocks_apply(db):
    source = source_3dm(db)
    name = f"DQ冲突{tag()}"
    blocked = historical(db, source, name)
    other = Subject(
        domain=Domain.GAMING,
        subject_type=SubjectType.GAME,
        canonical_name=normalize_subject_name(name),
        title=name,
    )
    db.add(other)
    db.flush()
    before = state(db, blocked, other)

    plan = plan_gaming_subject_repair(db)
    row = plan_row(plan, blocked, "review_required")
    assert row["reason"] == "canonical_name_occupied"
    assert row["occupied_by"] == [str(other.id)]
    with pytest.raises(GamingSubjectRepairRefused):
        repair_gaming_subjects(db, dry_run=False)
    assert state(db, blocked, other) == before


def test_disagreeing_evidence_is_review_required(db):
    source = source_3dm(db)
    name = f"DQ证据{tag()}"
    subject = historical(db, source, name, raw=row_text(f"DQ别名{tag()}"))

    row = plan_row(plan_gaming_subject_repair(db), subject, "review_required")

    assert row["reason"] == "no_single_clean_name"
    assert len(row["names"]) == 2
