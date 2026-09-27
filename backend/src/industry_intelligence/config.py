"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Settings shared by the API, database migration tooling, and logging."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    search_semantic_candidates: int = Field(default=200, ge=1, le=2000)
    search_semantic_max_distance: float = Field(default=0.45, ge=0, le=2)
    search_recent_titles: int = Field(default=8, ge=1, le=20)
    search_document_chars: int = Field(default=12000, ge=1000, le=50000)
    search_rrf_k: float = Field(default=60, gt=0, allow_inf_nan=False)
    search_lexical_weight: float = Field(default=1, gt=0, allow_inf_nan=False)
    search_semantic_weight: float = Field(default=1, gt=0, allow_inf_nan=False)
    search_trigram_threshold: float = Field(default=0.4, ge=0.3, le=1)
    embedding_url: str | None = None
    embedding_api_key: str | None = Field(default=None, repr=False)
    embedding_model: str | None = None
    embedding_dimension: int = Field(default=1536, ge=1, le=16000)
    embedding_timeout: float = Field(default=8, gt=0, le=60)

    rrf_k: float = Field(default=60, gt=0, allow_inf_nan=False)
    freshness_half_life: float = Field(default=24, gt=0, allow_inf_nan=False)
    rank_list_weights: dict[str, float] = Field(default_factory=dict)

    source_fetch_limit: int = Field(default=30, ge=1, le=100)
    source_http_timeout: float = Field(default=20, gt=0, le=120)
    # Contact (URL or email) of the deployment maintainer, sent in the User-Agent only to
    # publishers whose policy requires it (Wikimedia). Never commit a real value.
    source_contact: str | None = Field(default=None, repr=False)

    app_name: str = "Industry Intelligence & Ranking Platform"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://localhost:5432/industry_intelligence"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    @field_validator("database_url")
    @classmethod
    def database_url_must_target_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            msg = "DATABASE_URL must use a PostgreSQL SQLAlchemy URL"
            raise ValueError(msg)
        return value

    @field_validator("source_contact")
    @classmethod
    def placeholder_contact_is_unset(cls, value: str | None) -> str | None:
        """Blank values and documentation placeholders count as not configured."""

        value = (value or "").strip()
        host = urlsplit(value).hostname if "://" in value else value.rsplit("@", 1)[-1]
        domain = (host or "").lower()
        placeholder = domain in {"example.com", "example.org", "example.net"} or domain.endswith(
            (".example", ".invalid", ".test")
        )
        return None if not value or placeholder else value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.upper()
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in valid_levels:
            msg = "LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG"
            raise ValueError(msg)
        return level

    @property
    def cors_origin_list(self) -> list[str]:
        """Return non-empty origins from the comma-separated CORS_ORIGINS setting."""

        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def wildcard_cors_is_not_allowed_in_production(self) -> Settings:
        if self.environment.lower() == "production" and "*" in self.cors_origin_list:
            msg = "CORS_ORIGINS cannot contain '*' in production"
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    """Create and cache process-wide settings without logging sensitive values."""

    return Settings()
