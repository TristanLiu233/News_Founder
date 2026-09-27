from __future__ import annotations

import pytest
from pydantic import ValidationError

from industry_intelligence.config import Settings


def test_settings_load() -> None:
    settings = Settings(
        _env_file=None,
        app_name="Test Platform",
        environment="development",
        database_url="postgresql+psycopg://localhost:5432/test_platform",
        log_level="debug",
        cors_origins="http://localhost:3000, http://127.0.0.1:3000",
    )

    assert settings.app_name == "Test Platform"
    assert settings.log_level == "DEBUG"
    assert settings.cors_origin_list == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_production_settings_reject_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="cannot contain"):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://localhost:5432/test_platform",
            cors_origins="*",
        )
