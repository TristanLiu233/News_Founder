from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from industry_intelligence.config import Settings
from industry_intelligence.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_name="Industry Intelligence Test",
        environment="test",
        database_url="postgresql+psycopg://localhost:5432/industry_intelligence_test",
        log_level="WARNING",
        cors_origins="http://localhost:3000",
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
