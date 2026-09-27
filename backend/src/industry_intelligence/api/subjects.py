"""Read-only canonical subject detail and composite rank history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from industry_intelligence.db.session import get_db_session
from industry_intelligence.services.subject_detail import rank_history, subject_detail

router = APIRouter(prefix="/api/v1", tags=["subjects"])


@router.get("/subjects/{subject_id}")
def get_subject(subject_id: UUID, session: Annotated[Session, Depends(get_db_session)]):
    detail = subject_detail(session, subject_id)
    if detail is None:
        raise HTTPException(404, "Subject not found")
    return detail


@router.get("/subjects/{subject_id}/history")
def get_subject_history(
    subject_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    limit: int = Query(30, ge=1, le=200),
):
    history = rank_history(session, subject_id, limit)
    if history is None:
        raise HTTPException(404, "Subject not found")
    return history
