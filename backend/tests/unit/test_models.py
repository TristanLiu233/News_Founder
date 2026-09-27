from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    Ranking,
    RankingEntry,
    RankingSnapshot,
    RankList,
    RankObservation,
    Source,
    Subject,
)
from industry_intelligence.domain.enums import Domain, SubjectType


def test_models_can_be_instantiated_and_related() -> None:
    observed_at = datetime.now(UTC)
    source = Source(
        name="Example Source",
        domain=Domain.COMPUTER,
        source_type="ranking_feed",
        base_url="https://example.test",
    )
    rank_list = RankList(source=source, name="Top stories", domain=Domain.COMPUTER)
    subject = Subject(
        domain=Domain.COMPUTER,
        subject_type=SubjectType.TECHNOLOGY,
        canonical_name="example-technology",
        title="Example technology",
    )
    observation = RankObservation(
        subject=subject,
        source=source,
        rank_list=rank_list,
        rank=1,
        raw_metric={"score": 123},
        observed_at=observed_at,
    )

    company = Company(canonical_name="Example Corp", normalized_name="example corp")
    CompanyAlias(
        company=company,
        alias="Example",
        normalized_alias="example",
        alias_type="x",
        alias_source="t",
    )
    CompanyIdentity(company=company, scheme="ticker", value="EXC", source_key="t")
    ranking = Ranking(
        key="example_ranking",
        source_key="example",
        domain=Domain.FINANCE,
        name="Top companies",
        publisher="Example publisher",
        metric_type="market_cap",
        metric_label="Market cap",
        frequency="daily",
        acquisition="CSV",
        primary_language="en",
    )
    snapshot = RankingSnapshot(
        ranking=ranking,
        snapshot_key="daily:2026-09-27",
        observed_at=observed_at,
        effective_at=observed_at,
        source_url="https://example.test",
        content_hash="0" * 64,
        status="complete",
        entry_count=1,
    )
    entry = RankingEntry(
        snapshot=snapshot,
        company=company,
        rank=1,
        raw_name="Example Corp",
        metric_value=Decimal("123.4567"),
        resolution_strategy="new_company",
    )

    assert rank_list.source is source
    assert observation in source.rank_observations
    assert observation in subject.rank_observations
    assert snapshot in ranking.snapshots
    assert entry in snapshot.entries
    assert entry in company.ranking_entries
    assert len(company.aliases) == 1 and len(company.identities) == 1
    assert observation.raw_metric == {"score": 123}
    assert entry.metric_value == Decimal("123.4567")


def test_critical_schema_constraints_and_foreign_keys_exist() -> None:
    observation_constraint_sql = {
        str(constraint.sqltext)
        for constraint in RankObservation.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    entry_constraint_sql = {
        str(constraint.sqltext)
        for constraint in RankingEntry.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    observation_targets = {
        foreign_key.column.table.name for foreign_key in RankObservation.__table__.foreign_keys
    }
    entry_targets = {
        foreign_key.column.table.name for foreign_key in RankingEntry.__table__.foreign_keys
    }
    snapshot_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in RankingSnapshot.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert "rank > 0" in observation_constraint_sql
    assert "rank > 0" in entry_constraint_sql
    assert observation_targets == {"subjects", "sources", "rank_lists"}
    assert entry_targets == {"ranking_snapshots", "companies"}
    assert ("ranking_id", "snapshot_key") in snapshot_unique
