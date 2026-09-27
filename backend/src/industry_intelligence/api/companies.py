"""Read-only Companies index and Company Detail; profile ingestion stays CLI-only."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from industry_intelligence.db.session import get_db_session
from industry_intelligence.services.company_profiles import company_detail, list_companies

router = APIRouter(prefix="/api/v1", tags=["companies"])


@router.get("/companies")
def get_companies(
    session: Annotated[Session, Depends(get_db_session)],
    q: str | None = Query(None, max_length=100),
    domain: str | None = Query(None, pattern="^(computer|finance|gaming)$"),
    ranking: str | None = Query(None, pattern=r"^[A-Za-z0-9_-]{1,100}$"),
    has_profile: bool | None = None,
    limit: int = Query(30, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    return list_companies(session, q, domain, ranking, has_profile, limit, offset)


@router.get("/companies/{company_id}")
def get_company(company_id: UUID, session: Annotated[Session, Depends(get_db_session)]):
    detail = company_detail(session, company_id)
    if detail is None:
        raise HTTPException(404, "Company not found")
    return detail
