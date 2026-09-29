"""DQ-2 invariant audit checks run inside a rolled-back PostgreSQL transaction."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from industry_intelligence.config import get_settings
from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    CompanyRedirect,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventCompanyLink,
    EventSubjectLink,
    Source,
    Subject,
    SubjectAlias,
)
from industry_intelligence.db.models.canonical import SubjectExternalIdentity
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.services.data_quality_audit import audit_data_quality, render_text
from industry_intelligence.services.resolution import normalize_subject_name
from industry_intelligence.services.search_index import refresh_subject

pytestmark = pytest.mark.integration
NOW = datetime(2033, 1, 1, tzinfo=UTC)


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
    return uuid4().hex[:12]


def subject(db, title, canonical_name=None, *, subject_type=SubjectType.GAME, index=True):
    row = Subject(
        domain=Domain.GAMING,
        subject_type=subject_type,
        canonical_name=canonical_name or normalize_subject_name(title),
        title=title,
    )
    db.add(row)
    db.flush()
    if index:
        refresh_subject(db, row.id)
    return row


def source(db):
    row = Source(
        name=f"DQ audit {uuid4()}",
        domain=Domain.GAMING,
        source_type="test",
        base_url="https://example.test",
        primary_language="en",
    )
    db.add(row)
    db.flush()
    return row


def identity(db, owner, provider, canonical_id=None):
    db.add(
        SubjectExternalIdentity(
            subject_id=owner.id,
            source_id=provider.id,
            external_id=f"dq-{uuid4()}",
            normalized_name=normalize_subject_name(owner.title),
            canonical_id=canonical_id,
            resolution_strategy="new_subject",
            confidence=1.0,
        )
    )
    db.flush()


def alias(db, owner, name):
    db.add(
        SubjectAlias(
            subject_id=owner.id,
            alias=name,
            normalized_alias=normalize_subject_name(name),
            locale="en",
            alias_type="source_name",
            alias_source="dq_test",
        )
    )
    db.flush()


def redirect(db, duplicate, canonical):
    db.add(SubjectRedirect(duplicate_id=duplicate.id, canonical_id=canonical.id))
    db.flush()


def findings(report, severity, check=None, **match):
    rows = report[{"error": "errors", "warning": "warnings", "info": "info"}[severity]]
    return [
        row
        for row in rows
        if (check is None or row["check"] == check)
        and all(
            row.get(key) == value or (isinstance(row.get(key), list) and value in row[key])
            for key, value in match.items()
        )
    ]


def mentions(report, subject_id):
    text_report = json.dumps(report["errors"] + report["warnings"])
    return str(subject_id) in text_report


def test_clean_state_has_no_errors_and_no_findings_for_clean_rows(db):
    clean = subject(db, f"DQ Clean {tag()}")
    identity(db, clean, source(db), canonical_id=f"dq:clean:{tag()}")
    alias(db, clean, clean.title)

    report = audit_data_quality(db)

    assert report["summary"]["result"]["errors"] == 0
    assert report["summary"]["result"]["passed"] is True
    assert not mentions(report, clean.id)


def test_legacy_hash_is_an_error(db):
    legacy = subject(db, f"DQ Legacy {tag()}", hashlib.sha256(uuid4().bytes).hexdigest())

    report = audit_data_quality(db)

    [row] = findings(report, "error", "subject.legacy_hash", subject_id=str(legacy.id))
    assert row["classification"] == "legacy_hash"
    assert report["summary"]["result"]["passed"] is False


def test_non_hash_canonical_mismatch_is_a_warning_with_provenance(db):
    title = f"DQ Game {tag()}"
    row = subject(db, title, f"8 {normalize_subject_name(title)} 运营商:测试 9.0 分")

    report = audit_data_quality(db)

    [finding] = findings(report, "warning", "subject.canonical_mismatch", subject_id=str(row.id))
    assert finding["classification"] == "non_hash_mismatch"
    assert finding["normalized_title"] == normalize_subject_name(title)
    assert {"sources", "alias_sources", "identity_sources", "observations"} <= set(finding)
    assert not findings(report, "error", subject_id=str(row.id))


def test_canonical_occupancy_active_collision_and_retired_occupancy(db):
    title = f"DQ Shared {tag()}"
    first = subject(db, title)
    second = subject(db, title, f"{normalize_subject_name(title)} extra")
    retired_title = f"DQ Retired Target {tag()}"
    survivor = subject(db, f"DQ Survivor {tag()}")
    retired = subject(db, retired_title, index=False)
    redirect(db, retired, survivor)
    blocked = subject(db, retired_title, f"{normalize_subject_name(retired_title)} row text")

    report = audit_data_quality(db)

    [collision] = findings(
        report, "warning", "subject.canonical_occupancy", target=normalize_subject_name(title)
    )
    assert collision["classification"] == "active_collision"
    assert set(collision["subject_ids"]) == {str(first.id), str(second.id)}
    [occupied] = findings(
        report, "warning", "subject.canonical_occupancy", subject_ids=str(blocked.id)
    )
    assert occupied["classification"] == "retired_occupancy"
    assert occupied["occupied_by_retired"] == [str(retired.id)]


def test_strong_identity_collision_is_an_error_and_cross_scope_a_warning(db):
    provider = source(db)
    canonical_id = f"dq:crypto:{tag()}"
    first, second = subject(db, f"DQ Coin {tag()}"), subject(db, f"DQ Coin {tag()}")
    identity(db, first, provider, canonical_id)
    identity(db, second, provider, canonical_id)
    other_scope = f"dq:scope:{tag()}"
    game = subject(db, f"DQ Scope {tag()}")
    repository = subject(db, f"DQ Scope {tag()}", subject_type=SubjectType.REPOSITORY)
    identity(db, game, provider, other_scope)
    identity(db, repository, provider, other_scope)

    report = audit_data_quality(db)

    [error] = findings(report, "error", "subject.canonical_id_collision", canonical_id=canonical_id)
    assert set(error["subject_ids"]) == {str(first.id), str(second.id)}
    [warning] = findings(
        report, "warning", "subject.canonical_id_cross_scope", canonical_id=other_scope
    )
    assert warning["classification"] == "cross_scope_identity"


def test_alias_collision_is_reported_and_never_merged(db):
    name = f"DQ Alias {tag()}"
    first, second = subject(db, f"DQ A {tag()}"), subject(db, f"DQ B {tag()}")
    alias(db, first, name)
    alias(db, second, name.upper())
    redirects_before = db.scalar(text("SELECT count(*) FROM subject_redirects"))

    report = audit_data_quality(db)

    [row] = findings(
        report, "warning", "subject.alias_collision", normalized_alias=normalize_subject_name(name)
    )
    assert row["classification"] == "review_required"
    assert set(row["subject_ids"]) == {str(first.id), str(second.id)}
    assert db.scalar(text("SELECT count(*) FROM subject_redirects")) == redirects_before
    assert db.get(Subject, first.id).canonical_name == first.canonical_name


def test_redirect_loop_chain_and_known_good(db):
    a, b = subject(db, f"DQ Loop A {tag()}"), subject(db, f"DQ Loop B {tag()}")
    redirect(db, a, b)
    redirect(db, b, a)
    c, d, e = (subject(db, f"DQ Chain {n} {tag()}") for n in "CDE")
    redirect(db, c, d)
    redirect(db, d, e)
    old, current = subject(db, f"DQ Old {tag()}"), subject(db, f"DQ Current {tag()}")
    redirect(db, old, current)
    db.execute(text("DELETE FROM search_documents WHERE subject_id = :id"), {"id": old.id})

    report = audit_data_quality(db)

    loops = findings(report, "error", "subject.redirect_integrity")
    assert {row["subject_id"] for row in loops} >= {str(a.id), str(b.id)}
    assert all(row["classification"] == "redirect_loop" for row in loops)
    [chain] = findings(report, "warning", "subject.redirect_chain", subject_id=str(c.id))
    assert chain["resolves_to"] == str(e.id)
    [good] = findings(report, "info", "subject.redirect_resolves", subject_id=str(old.id))
    assert good["target_id"] == str(current.id)
    assert report["summary"]["subject_redirects"]["loops"] >= 2


def test_retired_subject_with_search_document_is_an_error(db):
    survivor = subject(db, f"DQ Search Survivor {tag()}")
    stale = subject(db, f"DQ Search Stale {tag()}")
    redirect(db, stale, survivor)

    report = audit_data_quality(db)

    [row] = findings(report, "error", "search.retired_document", subject_id=str(stale.id))
    assert row["classification"] == "retired_searchable"
    missing = subject(db, f"DQ Search Missing {tag()}", index=False)
    report = audit_data_quality(db)
    assert findings(report, "warning", "search.missing_active_document", subject_id=str(missing.id))


def test_current_relations_on_retired_subject_and_event_company_structure(db):
    survivor = subject(db, f"DQ Rel Survivor {tag()}")
    retired = subject(db, f"DQ Rel Retired {tag()}", index=False)
    redirect(db, retired, survivor)
    provider = source(db)
    item = ContentItem(
        source_id=provider.id,
        domain=Domain.GAMING,
        content_type="news",
        identity_key=f"dq-{tag()}",
        title="DQ report",
        url=f"https://example.test/{tag()}",
        published_at=NOW,
        observed_at=NOW,
        language="en",
        item_metadata={},
        content_hash=tag(),
    )
    event = Event(
        domain=Domain.GAMING,
        event_type="general",
        canonical_title="DQ report",
        normalized_title="dq report",
        title_language="en",
        first_published_at=NOW,
        last_published_at=NOW,
        first_observed_at=NOW,
        last_observed_at=NOW,
        content_count=1,
        source_count=1,
    )
    company = Company(canonical_name=f"DQ Co {tag()}", normalized_name=f"dq co {tag()}")
    db.add_all([item, event, company])
    db.flush()
    db.add_all(
        [
            ContentSubjectLink(
                content_item_id=item.id, subject_id=retired.id, strategy="exact_name", confidence=1
            ),
            EventSubjectLink(
                event_id=event.id,
                subject_id=retired.id,
                strategy="content_subject_link",
                content_count=1,
                evidence={"content_item_ids": [str(item.id)]},
            ),
            EventCompanyLink(
                event_id=event.id,
                company_id=company.id,
                strategy="content_subject_company",
                content_count=1,
                evidence={"content_item_ids": [str(uuid4())], "subject_ids": [str(survivor.id)]},
            ),
        ]
    )
    db.flush()

    report = audit_data_quality(db)

    relations = findings(
        report, "error", "relation.retired_subject_reference", subject_id=str(retired.id)
    )
    assert {row["classification"] for row in relations} == {
        "content_subject_link_on_retired",
        "event_subject_link_on_retired",
    }
    [structure] = findings(report, "error", "event_company.structure")
    assert structure["problems"] == ["missing_evidence_content"]


def test_company_identity_checks(db):
    first = Company(canonical_name=f"DQ Bank {tag()}", normalized_name=f"dq bank {tag()}")
    second = Company(canonical_name=first.canonical_name, normalized_name=first.normalized_name)
    looped = Company(canonical_name=f"DQ Loop {tag()}", normalized_name=f"dq loop {tag()}")
    other = Company(canonical_name=f"DQ Loop2 {tag()}", normalized_name=f"dq loop2 {tag()}")
    db.add_all([first, second, looped, other])
    db.flush()
    db.add_all(
        [
            CompanyIdentity(company_id=first.id, scheme="sec:cik", value=tag(), source_key="t"),
            CompanyIdentity(company_id=first.id, scheme="sec:cik", value=tag(), source_key="t"),
            CompanyIdentity(company_id=second.id, scheme="gleif:lei", value=tag(), source_key="t"),
            CompanyAlias(
                company_id=second.id,
                alias=f"DQ Alias {tag()}",
                normalized_alias=f"dq alias {tag()}",
                alias_type="source_name",
                alias_source="t",
            ),
            CompanyRedirect(duplicate_id=looped.id, canonical_id=other.id, reason="dq"),
            CompanyRedirect(duplicate_id=other.id, canonical_id=looped.id, reason="dq"),
        ]
    )
    db.flush()

    report = audit_data_quality(db)

    [legal] = findings(
        report, "error", "company.multiple_legal_identities", company_id=str(first.id)
    )
    assert legal["scheme"] == "sec:cik"
    [shared] = findings(
        report, "warning", "company.alias_collision", normalized_name=first.normalized_name
    )
    assert set(shared["company_ids"]) == {str(first.id), str(second.id)}
    loops = findings(report, "error", "company.redirect_integrity")
    assert {row["company_id"] for row in loops} >= {str(looped.id), str(other.id)}


def fingerprint(db):
    tables = db.scalars(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    ).all()
    return {
        table: db.scalar(
            text(
                f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
                f'FROM "{table}" t'
            )
        )
        for table in tables
    }


def test_audit_is_read_only(db):
    title = f"DQ Readonly {tag()}"
    subject(db, title, f"{normalize_subject_name(title)} extra")
    legacy = subject(db, f"DQ RO Legacy {tag()}", hashlib.sha256(uuid4().bytes).hexdigest())
    survivor = subject(db, f"DQ RO Survivor {tag()}")
    redirect(db, legacy, survivor)
    before = fingerprint(db)
    db.execute(text("SET TRANSACTION READ ONLY"))

    report = audit_data_quality(db)

    assert db.scalar(text("SHOW transaction_read_only")) == "on"
    assert fingerprint(db) == before
    assert report["summary"]["result"]["errors"] >= 1


def test_json_report_structure_is_stable(db):
    subject(db, f"DQ Json {tag()}", "dq json mismatch")

    report = json.loads(json.dumps(audit_data_quality(db), ensure_ascii=False, default=str))

    assert {"audit_version", "summary", "checks", "errors", "warnings", "info"} <= set(report)
    assert {"subjects", "subject_redirects", "search", "relations", "companies", "result"} <= set(
        report["summary"]
    )
    ids = [check["id"] for check in report["checks"]]
    assert len(ids) == len(set(ids))
    for check in report["checks"]:
        assert set(check) == {"id", "category", "description", "severity", "status", "count"}
        assert check["status"] in {"ok", check["severity"]}
    for severity, key in (("error", "errors"), ("warning", "warnings"), ("info", "info")):
        assert all(row["severity"] == severity and row["classification"] for row in report[key])
    rendered = render_text(report)
    assert rendered.startswith("Data Quality Audit")
    assert f"{report['summary']['result']['warnings']} warnings" in rendered
