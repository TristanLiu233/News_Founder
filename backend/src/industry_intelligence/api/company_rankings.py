"""Read-only external company rankings; ingestion stays CLI-only."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from industry_intelligence.db.session import get_db_session
from industry_intelligence.services.company_rankings import (
    company_rankings,
    list_rankings,
    ranking_detail,
    ranking_history,
)

router = APIRouter(prefix="/api/v1", tags=["company rankings"])

# A ranking is addressed by its UUID or its stable machine key.
RankingRef = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,100}$")]


@router.get("/company-rankings")
def get_company_rankings(
    session: Annotated[Session, Depends(get_db_session)],
    domain: str | None = Query(None, pattern="^(computer|finance|gaming)$"),
    source: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return list_rankings(session, domain, source, limit, offset)


@router.get("/company-rankings/{ranking_ref}")
def get_company_ranking(
    ranking_ref: RankingRef,
    session: Annotated[Session, Depends(get_db_session)],
    snapshot_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    detail = ranking_detail(session, ranking_ref, snapshot_id, limit, offset)
    if detail is None:
        raise HTTPException(404, "Ranking or snapshot not found")
    return detail


@router.get("/company-rankings/{ranking_ref}/history")
def get_company_ranking_history(
    ranking_ref: RankingRef,
    session: Annotated[Session, Depends(get_db_session)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    history = ranking_history(session, ranking_ref, limit, offset)
    if history is None:
        raise HTTPException(404, "Ranking not found")
    return history


@router.get("/companies/{company_id}/rankings")
def get_company_ranking_appearances(
    company_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    limit: int = Query(30, ge=1, le=200),
):
    result = company_rankings(session, company_id, limit)
    if result is None:
        raise HTTPException(404, "Company not found")
    return result
