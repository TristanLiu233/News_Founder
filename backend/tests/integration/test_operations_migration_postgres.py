"""Operations-2 migration ``20260930_12``: upgrade → downgrade → upgrade on the isolated
``*_test`` database. The downgrade removes only ``update_runs`` and ``source_attempts``; every
other table keeps its columns and contents, and the chain ends with exactly one head."""

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from industry_intelligence.config import get_settings
from industry_intelligence.services.intelligence_update import ALEMBIC_INI

pytestmark = pytest.mark.integration

OPERATIONS = {"update_runs", "source_attempts"}
DATABASE = make_url(get_settings().database_url).database or ""


@pytest.fixture(autouse=True)
def isolated_database():
    if not DATABASE.endswith("_test"):
        pytest.skip("runs DDL; run with DATABASE_URL pointing at a *_test database")


def schema_and_data(engine):
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        state = {}
        for table in sorted(tables):
            columns = [(c["name"], str(c["type"])) for c in inspector.get_columns(table)]
            rows = "string_agg(t::text, ',' ORDER BY t::text)"
            digest = conn.scalar(text(f"SELECT md5(coalesce({rows}, '')) FROM \"{table}\" t"))
            state[table] = (columns, digest)
        revision = MigrationContext.configure(conn).get_current_heads()
    return state, revision


@pytest.mark.filterwarnings("ignore:Did not recognize type 'vector'")
def test_operations_migration_round_trip_touches_only_its_tables():
    config = Config(str(ALEMBIC_INI))
    assert ScriptDirectory.from_config(config).get_heads() == ["20260930_12"]
    engine = create_engine(get_settings().database_url)
    before, revision = schema_and_data(engine)
    assert revision == ("20260930_12",) and OPERATIONS <= set(before)
    try:
        command.downgrade(config, "20260928_11")
        down, revision = schema_and_data(engine)
        assert revision == ("20260928_11",)
        assert set(down) == set(before) - OPERATIONS
        assert down == {table: value for table, value in before.items() if table not in OPERATIONS}
    finally:
        command.upgrade(config, "head")
    after, revision = schema_and_data(engine)
    engine.dispose()
    assert revision == ("20260930_12",)
    assert {t: v for t, v in after.items() if t not in OPERATIONS} == {
        t: v for t, v in before.items() if t not in OPERATIONS
    }
    assert {t: after[t][0] for t in OPERATIONS} == {t: before[t][0] for t in OPERATIONS}
