"""Small operational health endpoints."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DatabaseHealthResponse(HealthResponse):
    database: Literal["connected"]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report process health without requiring a database connection."""

    return HealthResponse(status="ok")


@router.get("/health/db", response_model=DatabaseHealthResponse)
def database_health(request: Request) -> DatabaseHealthResponse:
    """Verify that the configured PostgreSQL database accepts a trivial query."""

    engine = request.app.state.db_engine
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Never include the database URL or credentials in an operational response/log line.
        logger.warning("Database health check failed: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "database": "unavailable"},
        ) from exc

    return DatabaseHealthResponse(status="ok", database="connected")
