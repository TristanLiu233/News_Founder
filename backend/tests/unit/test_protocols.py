from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from industry_intelligence.adapters.company_ranking import (
    CompanyRankingAdapter,
    CompanyRankingEntry,
    CompanyRankingSnapshot,
)
from industry_intelligence.adapters.source import SourceAdapter, SourceItem
from industry_intelligence.ai import AIGateway
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


@dataclass
class StructuredAnswer:
    label: str


class FakeAIGateway:
    async def generate(self, *, prompt: str, system_prompt: str | None = None) -> str:
        return f"generated: {prompt}"

    async def structured_generate(
        self,
        *,
        prompt: str,
        schema: type[StructuredAnswer],
        system_prompt: str | None = None,
    ) -> StructuredAnswer:
        return schema(label=prompt)

    async def embed(self, *, texts: Sequence[str]) -> list[list[float]]:
        return [[float(index)] for index, _ in enumerate(texts)]

    async def classify(self, *, text: str, labels: Sequence[str]) -> str:
        return labels[0]


def test_source_adapter_protocol_has_a_working_fake() -> None:
    adapter = FakeSourceAdapter()

    assert isinstance(adapter, SourceAdapter)
    assert asyncio.run(adapter.fetch())[0].raw_metric == {"score": 42}


def test_company_ranking_adapter_protocol_has_a_working_fake() -> None:
    adapter = FakeCompanyRankingAdapter()

    assert isinstance(adapter, CompanyRankingAdapter)
    assert asyncio.run(adapter.fetch()).entries[0].name == "Example Corp"


def test_ai_gateway_protocol_has_a_working_fake() -> None:
    gateway = FakeAIGateway()

    assert isinstance(gateway, AIGateway)
    assert asyncio.run(gateway.generate(prompt="hello")) == "generated: hello"
    assert (
        asyncio.run(gateway.structured_generate(prompt="finance", schema=StructuredAnswer)).label
        == "finance"
    )
    assert asyncio.run(gateway.embed(texts=["one", "two"])) == [[0.0], [1.0]]
    assert asyncio.run(gateway.classify(text="x", labels=["keep", "drop"])) == "keep"


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
