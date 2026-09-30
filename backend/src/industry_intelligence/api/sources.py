"""Read-only Phase 1 source and Today APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from industry_intelligence.db.session import get_db_session
from industry_intelligence.services.today import sources, today
from industry_intelligence.services.trending import DEFAULT_LIMIT, SELECTION_VIEWS, trending

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
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=100),
    view: str | None = Query(default=None, pattern="^(aggregate|native)$"),
    selection: str | None = Query(default=None, pattern="^(native|home)$"),
):
    # Events on publishers' own ranked article lists. `view=aggregate` (default): current Events
    # in one presentation order derived from their native ranks (never a returned score);
    # `view=native`: the publisher lists in catalog order. `selection` is the Trending-2 alias.
    resolved = view or SELECTION_VIEWS.get(selection or "", "aggregate")
    return trending(session, domain, limit, resolved)
