from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from industry_intelligence.adapters.company_ranking import (
    CompanyRankingAdapter,
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)
from industry_intelligence.adapters.source import SourceAdapter, SourceItem
from industry_intelligence.ai import AIProvider
from industry_intelligence.ai.providers.fixture import FixtureProvider
from industry_intelligence.domain.enums import Domain


class FakeSourceAdapter:
    source_name = "Example source"
    domain = Domain.COMPUTER

    async def fetch(self) -> list[SourceItem]:
        return [
            SourceItem(
                external_id="example-1",
                title="Example item",
                url="https://example.test/items/1",
                rank=1,
                raw_metric={"score": 42},
                observed_at=datetime.now(UTC),
            )
        ]


class FakeCompanyRankingAdapter:
    failed_count = 0

    async def fetch(self) -> CompanyRankingSnapshot:
        return CompanyRankingSnapshot(
            snapshot_key="daily:2026-09-27",
            observed_at=datetime.now(UTC),
            source_url="https://example.test/rankings",
            entries=(
                CompanyRankingEntry(rank=1, name="Example Corp", metric_value=Decimal("100.00")),
            ),
        )


def test_source_adapter_protocol_has_a_working_fake() -> None:
    adapter = FakeSourceAdapter()

    assert isinstance(adapter, SourceAdapter)
    assert asyncio.run(adapter.fetch())[0].raw_metric == {"score": 42}


def test_company_ranking_adapter_protocol_has_a_working_fake() -> None:
    adapter = FakeCompanyRankingAdapter()

    assert isinstance(adapter, CompanyRankingAdapter)
    assert asyncio.run(adapter.fetch()).entries[0].name == "Example Corp"


def test_ai_provider_protocol_has_a_working_fixture() -> None:
    provider = FixtureProvider(lambda **request: {"label": request["schema_name"]})

    assert isinstance(provider, AIProvider)
    response = asyncio.run(
        provider.generate_structured(system="s", user="u", schema={}, schema_name="finance")
    )
    assert response.data == {"label": "finance"}
    assert (response.provider, response.model) == ("fixture", "fixture-model")


def test_source_item_rejects_invalid_rank_or_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        SourceItem(
            external_id=None,
            title="invalid",
            url=None,
            rank=0,
            raw_metric=None,
            observed_at=datetime.now(UTC),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        SourceItem(
            external_id=None,
            title="invalid",
            url=None,
            rank=1,
            raw_metric=None,
            observed_at=datetime.now(),
        )
