from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from industry_intelligence.adapters.source.base import SourceItem
from industry_intelligence.adapters.source.registry import SourceDefinition
from industry_intelligence.config import get_settings
from industry_intelligence.db.models import RankObservation
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.services.source_ingestion import persist
from industry_intelligence.services.today import current_snapshot, today


@pytest.mark.integration
def test_postgres_history_idempotency_and_latest_snapshot(monkeypatch):
    engine = create_engine(get_settings().database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    definition = SourceDefinition(
        "integration_test",
        "Integration Test Source",
        Domain.COMPUTER,
        "Integration Ranking",
        SubjectType.EVENT,
        "https://example.com",
        object,
    )
    from industry_intelligence.services import today as today_module

    monkeypatch.setattr(today_module, "SOURCES", {definition.key: definition})
    first_at = datetime.now(UTC) - timedelta(minutes=1)
    second_at = datetime.now(UTC)
    first = [SourceItem("a", "First", "https://example.com/a", 1, 10, first_at)]
    second = [SourceItem("b", "Second", "https://example.com/b", 1, 20, second_at)]
    try:
        result = persist(session, definition, first, "same-batch")
        replay = persist(session, definition, first, "same-batch")
        persist(session, definition, second, "new-batch")
        session.flush()
        assert result["persisted_count"] == 1
        assert replay["persisted_count"] == 0 and replay["replayed"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(RankObservation)
                .where(RankObservation.observed_at.in_([first_at, second_at]))
            )
            == 2
        )
        batch, rows = current_snapshot(session, definition.key)
        assert batch.batch_key == "new-batch"
        assert [row.title for row, _ in rows] == ["Second"]
        grouped = today(session)
        assert [domain["domain"] for domain in grouped["domains"]] == list(Domain)
        assert len(grouped["domains"][0]["lists"]) == 1
        assert grouped["domains"][0]["lists"][0]["items"][0]["title"] == "Second"
        assert grouped["domains"][1]["lists"] == []
        assert grouped["domains"][2]["lists"] == []
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
