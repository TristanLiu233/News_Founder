"""FastAPI application factory for the modular-monolith backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from industry_intelligence.api.company_rankings import router as company_rankings_router
from industry_intelligence.api.health import router as health_router
from industry_intelligence.api.news import router as news_router
from industry_intelligence.api.search import router as search_router
from industry_intelligence.api.sources import router as sources_router
from industry_intelligence.api.subjects import router as subjects_router
from industry_intelligence.config import Settings, get_settings
from industry_intelligence.db.session import create_database_engine, create_session_factory
from industry_intelligence.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an app without opening a database connection during startup."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    engine = create_database_engine(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(search_router)
    app.include_router(news_router)
    app.include_router(health_router)
    app.include_router(sources_router)
    app.include_router(subjects_router)
    app.include_router(company_rankings_router)

    @app.exception_handler(Exception)
    async def internal_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Keep accidental connection strings and provider credentials out of logs.
        logger.error("Unhandled application error: %s", exc.__class__.__name__)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
