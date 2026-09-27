"""SQLAlchemy engine and request-session helpers."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from industry_intelligence.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    """Create a lazy PostgreSQL engine; connecting is deferred until it is used."""

    return create_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Yield a database session for future API/service dependencies."""

    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session
