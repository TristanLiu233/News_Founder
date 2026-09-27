from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from industry_intelligence.db.models import (
    Company,
    Ranking,
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

    company = Company(canonical_name="Example Corp", domain=Domain.FINANCE)
    ranking = Ranking(
        domain=Domain.FINANCE,
        name="Top companies",
        publisher="Example publisher",
        metric_type="market_cap",
        frequency="daily",
    )
    snapshot = RankingSnapshot(
        ranking=ranking,
        company=company,
        rank=1,
        metric_value=Decimal("123.4567"),
        observed_at=observed_at,
    )

    assert rank_list.source is source
    assert observation in source.rank_observations
    assert observation in subject.rank_observations
    assert snapshot in ranking.snapshots
    assert snapshot in company.ranking_snapshots
    assert observation.raw_metric == {"score": 123}
    assert snapshot.metric_value == Decimal("123.4567")


def test_critical_schema_constraints_and_foreign_keys_exist() -> None:
    observation_constraint_sql = {
        str(constraint.sqltext)
        for constraint in RankObservation.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    snapshot_constraint_sql = {
        str(constraint.sqltext)
        for constraint in RankingSnapshot.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    observation_targets = {
        foreign_key.column.table.name for foreign_key in RankObservation.__table__.foreign_keys
    }
    snapshot_targets = {
        foreign_key.column.table.name for foreign_key in RankingSnapshot.__table__.foreign_keys
    }

    assert "rank > 0" in observation_constraint_sql
    assert "rank > 0" in snapshot_constraint_sql
    assert observation_targets == {"subjects", "sources", "rank_lists"}
    assert snapshot_targets == {"rankings", "companies"}
