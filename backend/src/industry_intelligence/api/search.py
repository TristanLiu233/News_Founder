"""Validated, read-only canonical search endpoint."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.session import get_db_session
from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.services.search import normalize_search_query, search

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
def get_search(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    q: str = Query(min_length=2, max_length=200),
    domain: Domain | None = None,
    subject_type: SubjectType | None = None,
    source: str | None = None,
    language: Literal["en", "zh-CN"] | None = None,
    mode: Literal["lexical", "semantic", "hybrid"] = "hybrid",
    locale: Literal["en", "zh-CN"] = "en",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
):
    normalized = normalize_search_query(q)
    if not 2 <= len(normalized) <= 200:
        raise HTTPException(422, "Normalized query must contain 2–200 characters")
    if source:
        names = {definition.name for definition in SOURCES.values()}
        if source in SOURCES:
            source = SOURCES[source].name
        elif source not in names:
            raise HTTPException(422, "Unknown source")
    return search(
        session,
        request.app.state.settings,
        normalized,
        domain=domain,
        subject_type=subject_type,
        source=source,
        language=language,
        mode=mode,
        limit=limit,
        offset=offset,
        locale=locale,
    )
