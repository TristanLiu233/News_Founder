"""Chronological news/feed API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from industry_intelligence.db.session import get_db_session
from industry_intelligence.services.news import list_news

router = APIRouter(prefix="/api/v1", tags=["news intelligence"])


@router.get("/news")
def get_news(
    session: Annotated[Session, Depends(get_db_session)],
    domain: str | None = Query(None, pattern="^(computer|finance|gaming)$"),
    source: str | None = None,
    language: str | None = None,
    subject_id: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return {
        "items": list_news(session, domain, source, language, subject_id, limit, offset),
        "limit": limit,
        "offset": offset,
    }
