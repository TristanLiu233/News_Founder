"""Event API parameter validation; rejected before any database access."""

from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    "params",
    [
        {"domain": "space"},
        {"event_type": "rumour"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"subject_id": "not-a-uuid"},
        {"company_id": "not-a-uuid"},
        {"from": "yesterday"},
        {"from": "2026-09-27T00:00:00"},
        {"to": "2026-09-27T00:00:00"},
    ],
)
def test_event_list_rejects_invalid_filters(client, params):
    assert client.get("/api/v1/events", params=params).status_code == 422


@pytest.mark.parametrize("params", [{"order": "sideways"}, {"limit": 51}, {"limit": 0}])
def test_event_content_rejects_invalid_paging(client, params):
    response = client.get(f"/api/v1/events/{uuid4()}/content", params=params)
    assert response.status_code == 422


def test_event_detail_rejects_malformed_ids(client):
    assert client.get("/api/v1/events/not-a-uuid").status_code == 422


def test_event_routes_are_read_only(client):
    assert client.post("/api/v1/events").status_code == 405
    assert client.post(f"/api/v1/events/{uuid4()}").status_code == 405


def test_event_ai_rejects_invalid_locale_and_ids(client):
    assert client.get(f"/api/v1/events/{uuid4()}/ai", params={"locale": "fr"}).status_code == 422
    assert client.get("/api/v1/events/not-a-uuid/ai").status_code == 422


def test_event_ai_route_is_read_only(client):
    assert client.post(f"/api/v1/events/{uuid4()}/ai").status_code == 405
