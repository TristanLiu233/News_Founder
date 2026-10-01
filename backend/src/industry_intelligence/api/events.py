"""Read-only Events and Event Detail; Event building stays CLI-only."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from industry_intelligence.db.session import get_db_session
from industry_intelligence.services.ai_insights import event_ai
from industry_intelligence.services.event_resolution import EVENT_TYPES
from industry_intelligence.services.events import event_content, event_detail, list_events

router = APIRouter(prefix="/api/v1", tags=["events"])

EVENT_TYPE_PATTERN = "^(" + "|".join(EVENT_TYPES) + ")$"


@router.get("/events")
def get_events(
    session: Annotated[Session, Depends(get_db_session)],
    domain: str | None = Query(None, pattern="^(computer|finance|gaming)$"),
    subject_id: UUID | None = None,
    company_id: UUID | None = None,
    event_type: str | None = Query(None, pattern=EVENT_TYPE_PATTERN),
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, min_length=2, max_length=200),
):
    for value in (from_, to):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, "from/to must include a timezone offset")
    if q is not None and len(" ".join(q.split())) < 2:
        raise HTTPException(422, "q must contain at least 2 characters")
    return list_events(
        session, domain, subject_id, company_id, event_type, from_, to, limit, offset, q
    )


@router.get("/events/{event_id}")
def get_event(event_id: UUID, session: Annotated[Session, Depends(get_db_session)]):
    detail = event_detail(session, event_id)
    if detail is None:
        raise HTTPException(404, "Event not found")
    return detail


@router.get("/events/{event_id}/content")
def get_event_content(
    event_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    content = event_content(session, event_id, limit, offset, order)
    if content is None:
        raise HTTPException(404, "Event not found")
    return content


@router.get("/events/{event_id}/ai")
def get_event_ai(
    event_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    locale: str | None = Query(None, pattern="^(en|zh-CN)$"),
):
    """Persisted, validated AI insights only; this endpoint never calls an AI provider.

    Insights are always in the Event's ``primary_locale`` (AI-LP1); ``locale`` is the optional
    UI locale, validated and echoed for compatibility, and never selects the content language.
    """

    insights = event_ai(session, event_id, locale)
    if insights is None:
        raise HTTPException(404, "Event not found")
    return insights
