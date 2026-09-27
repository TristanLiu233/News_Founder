from uuid import uuid4

from fastapi.testclient import TestClient

from industry_intelligence.main import create_app
from industry_intelligence.services.resolution import display_names


def alias(value, locale, alias_type="localized_name"):
    return {"alias": value, "locale": locale, "alias_type": alias_type}


def test_display_names_prefer_localized_alias_and_english_canonical():
    aliases = [alias("BTC", "en"), alias("比特币", "zh-CN"), alias("Bitcoin", "en", "source_name")]
    assert display_names("Bitcoin", aliases) == {"en": "Bitcoin", "zh-CN": "比特币"}
    chinese = [alias("Black Myth: Wukong", "en"), alias("黑神话：悟空", "zh-CN")]
    assert display_names("黑神话：悟空", chinese) == {
        "en": "Black Myth: Wukong",
        "zh-CN": "黑神话：悟空",
    }


def test_display_names_fall_back_to_canonical_and_ignore_source_names():
    assert display_names("Steam Deck", []) == {"en": "Steam Deck", "zh-CN": "Steam Deck"}
    source_only = [alias("寒武纪科技", "zh-CN", "source_name")]
    assert display_names("寒武纪", source_only) == {"en": "寒武纪", "zh-CN": "寒武纪"}


def test_subject_routes_exist_and_validate_without_database(settings):
    app = create_app(settings)
    paths = set(app.openapi()["paths"])
    assert {"/api/v1/subjects/{subject_id}", "/api/v1/subjects/{subject_id}/history"} <= paths
    client = TestClient(app)
    assert client.get("/api/v1/subjects/not-a-uuid").status_code == 422
    assert client.get(f"/api/v1/subjects/{uuid4()}/history?limit=0").status_code == 422
    assert client.get(f"/api/v1/subjects/{uuid4()}/history?limit=201").status_code == 422
    assert client.get("/api/v1/trending?limit=0").status_code == 422
    assert client.get("/api/v1/trending?limit=101").status_code == 422
