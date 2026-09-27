from math import isclose

import httpx
import pytest

from industry_intelligence.ai.embeddings import CompatibleEmbeddingProvider, validate_vector
from industry_intelligence.config import Settings
from industry_intelligence.services.search import fuse, normalize_search_query


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  ＮＶＩＤＩＡ  ", "nvidia"),
        ("黑神话： 悟空", "黑神话:悟空"),
        ("C++ C# .NET GPT-5", "c++ c# .net gpt-5"),
        ("Black   Myth 悟空", "black myth 悟空"),
    ],
)
def test_search_normalization(value, expected):
    assert normalize_search_query(value) == expected


def test_rrf_exact_math_and_priority():
    lexical = [dict(subject_id=x, exact=False) for x in ["A", "C", "B"]]
    semantic = [dict(subject_id=x, exact=False) for x in ["B", "D", "E", "F", "A"]]
    result = {x["subject_id"]: x for x in fuse(lexical, semantic, Settings())}
    assert isclose(result["A"]["score"], 1 / 61 + 1 / 65)
    assert isclose(result["B"]["score"], 1 / 63 + 1 / 61)
    lexical[0]["exact"] = True
    assert fuse(lexical, semantic, Settings())[0]["subject_id"] == "A"
    assert fuse(lexical, semantic, Settings()) == fuse(lexical, semantic, Settings())


@pytest.mark.parametrize("vector", [[1], [0, 0], [float("nan"), 1], [float("inf"), 1]])
def test_invalid_embeddings(vector):
    with pytest.raises(ValueError):
        validate_vector(vector, 2)


def test_compatible_provider(monkeypatch):
    settings = Settings(
        _env_file=None,
        embedding_url="http://localhost/embeddings",
        embedding_model="test-multilingual",
        embedding_dimension=2,
    )

    def post(url, **kwargs):
        assert kwargs["json"]["input"] == "英伟达"
        return httpx.Response(
            200, request=httpx.Request("POST", url), json={"data": [{"embedding": [1.0, 0.5]}]}
        )

    monkeypatch.setattr(httpx, "post", post)
    assert CompatibleEmbeddingProvider(settings).embed("英伟达") == [1.0, 0.5]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "x"},
        {"q": "   "},
        {"q": "x" * 201},
        {"q": "ok", "mode": "bad"},
        {"q": "ok", "domain": "bad"},
        {"q": "ok", "language": "bad"},
        {"q": "ok", "source": "bad"},
        {"q": "ok", "subject_type": "crypto"},
        {"q": "ok", "limit": 101},
        {"q": "ok", "offset": -1},
    ],
)
def test_api_validation(client, params):
    assert client.get("/api/v1/search", params=params).status_code == 422
