from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from industry_intelligence.main import create_app


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class SuccessfulConnection:
    def __enter__(self) -> SuccessfulConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, _: object) -> None:
        return None


class SuccessfulEngine:
    def connect(self) -> SuccessfulConnection:
        return SuccessfulConnection()


class FailingEngine:
    def connect(self) -> SuccessfulConnection:
        raise SQLAlchemyError("database is unreachable")


def test_database_health_endpoint_when_connected(app, client: TestClient) -> None:
    app.state.db_engine = SuccessfulEngine()

    response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_api_docs_are_served_outside_production(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 200


def test_production_hides_api_docs_but_keeps_health(settings) -> None:
    production = create_app(settings.model_copy(update={"environment": "production"}))

    with TestClient(production) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        assert client.get("/health").json() == {"status": "ok"}


def test_database_health_endpoint_when_unavailable(app, client: TestClient) -> None:
    app.state.db_engine = FailingEngine()

    response = client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {"detail": {"status": "error", "database": "unavailable"}}
