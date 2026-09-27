"""Read-only Phase 1 source and Today APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from industry_intelligence.db.session import get_db_session
from industry_intelligence.services.ranking import trending
from industry_intelligence.services.today import sources, today

router = APIRouter(prefix="/api/v1", tags=["information retrieval"])


@router.get("/sources")
def list_sources(session: Annotated[Session, Depends(get_db_session)]):
    return {"sources": sources(session)}


@router.get("/today")
def get_today(session: Annotated[Session, Depends(get_db_session)]):
    return today(session)


@router.get("/trending")
def get_trending(
    session: Annotated[Session, Depends(get_db_session)],
    domain: str | None = Query(default=None, pattern="^(computer|finance|gaming)$"),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    return trending(session, domain, limit)
