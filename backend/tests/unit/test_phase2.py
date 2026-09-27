from math import isclose
from pathlib import Path

import pytest

from industry_intelligence.adapters.source.chinese import (
    parse_3dm,
    parse_17173,
    parse_eastmoney,
    parse_gamersky,
    parse_huxiu,
    parse_ithome,
)
from industry_intelligence.services.ranking import contribution, freshness, trend_for
from industry_intelligence.services.resolution import (
    normalize_subject_name,
    normalize_url,
    seeded_canonical,
)


def test_chinese_normalization_preserves_meaningful_characters():
    assert normalize_subject_name(" 黑神话： 悟空 ") == "黑神话:悟空"
    assert normalize_subject_name("ＧＰＴ-5  C++ C# .NET 剑网3") == "gpt-5 c++ c# .net 剑网3"
    assert normalize_subject_name("崩坏・星穹铁道") == "崩坏·星穹铁道"


def test_url_normalization_only_removes_tracking_and_fragment():
    assert (
        normalize_url("HTTPS://Example.COM/a/?id=2&utm_source=x#part")
        == "https://example.com/a?id=2"
    )


def test_cross_language_seed_and_similar_product_safety():
    assert seeded_canonical("黑神话：悟空")[0] == "Black Myth: Wukong"
    assert seeded_canonical("英伟达")[0] == "NVIDIA"
    assert seeded_canonical("DOOM")[0] == "DOOM"
    assert seeded_canonical("DOOM II")[0] == "DOOM II"


def test_rrf_and_freshness_exact_math():
    assert isclose(freshness(0, 24), 1.0)
    assert isclose(freshness(24, 24), 0.5)
    assert isclose(contribution(1, 1, 60, 1), 1 / 61)
    assert contribution(1) + contribution(3) > contribution(2)


def test_ranking_history_labels_and_changes():
    assert trend_for(None, 2) == ("new", None)
    assert trend_for(3, 1) == ("up", 2)
    assert trend_for(1, 3) == ("down", -2)
    assert trend_for(2, 2) == ("unchanged", 0)


def test_ithome_chinese_fixture_and_partial_item():
    html = (Path(__file__).parents[1] / "fixtures/ithome_rank.html").read_text()
    rows, failed = parse_ithome(html)
    assert rows[0]["title"] == "英伟达发布 GPT-5"
    assert rows[0]["rank"] == 1 and rows[0]["raw_metric"] == 88
    assert failed == 1


def test_public_api_parser_keeps_market_ticker_identity():
    rows, failed = parse_eastmoney(
        {"data": {"diff": [{"f12": "600519", "f14": "贵州茅台", "f6": 100}]}}
    )
    assert failed == 0
    assert rows[0]["external_id"] == "SH:600519"
    assert rows[0]["metadata"]["canonical_id"] == "SH:600519"


def test_gamersky_ranking_order_and_chinese_text():
    html = (Path(__file__).parents[1] / "fixtures/gamersky_hot.html").read_text()
    rows, failed = parse_gamersky(html)
    assert failed == 0
    assert [(row["rank"], row["title"]) for row in rows] == [(1, "黑神话：悟空"), (2, "剑网3")]


@pytest.mark.parametrize(
    ("parser", "filename", "title"),
    [
        (parse_huxiu, "huxiu_hot.html", "微软发布新技术"),
        (parse_3dm, "3dm_ranking.html", "剑网3"),
        (parse_17173, "17173_ranking.html", "剑网3"),
    ],
)
def test_other_chinese_html_fixtures(parser, filename, title):
    rows, failed = parser((Path(__file__).parents[1] / "fixtures" / filename).read_text())
    assert failed == 0
    assert rows[0]["title"] == title
    assert rows[0]["rank"] == 1


@pytest.mark.parametrize(("alias", "canonical"), [("微软", "Microsoft"), ("比特币", "Bitcoin")])
def test_additional_synthetic_aliases(alias, canonical):
    assert seeded_canonical(alias)[0] == canonical
