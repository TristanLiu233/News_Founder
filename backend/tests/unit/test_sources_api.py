from fastapi.testclient import TestClient

from industry_intelligence.main import create_app


def test_source_and_today_routes_exist(settings):
    app = create_app(settings)
    paths = set(app.openapi()["paths"])
    assert "/api/v1/sources" in paths
    assert "/api/v1/today" in paths


def test_today_database_error_is_sanitized(settings):
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    response = client.get("/api/v1/today")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
