from pathlib import Path

import pytest

from industry_intelligence.adapters.source.chinese import (
    clean_3dm_row_text,
    is_3dm_row_text,
    parse_3dm,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
LIVE_TITLES = [
    "失落的方舟",
    "英雄联盟",
    "地下城与勇士",
    "魔兽世界",
    "绝地求生",
    "无畏",
    "生死狙击2",
    "剑网3",
    "炉石传说",
    "守望先锋",
    "DOTA2",
    "流放之路",
    "无畏契约",
    "命运方舟",
    "超激斗梦境",
    "云顶之弈",
    "魔兽世界TBC怀旧服",
    "堡垒之夜",
    "荒野行动Plus",
    "暗黑破坏神4",
]
# Row text stored by the first 3DM ingestion (2026-09-26), as flattened from the row link.
HISTORICAL_ROWS = [
    ("10 守望先锋 射击 运营商：暴雪战网 公测时间：2016-05-24 8.8 分", "守望先锋"),
    ("10 暗黑破坏神4 运营商：暴雪战网 公测时间：2023-06-06 9.0 分", "暗黑破坏神4"),
    ("10 流放之路 2018-01-11 运营商：腾讯游戏 公测时间：2018-01-11 8.2 分", "流放之路"),
    ("1 失落的方舟 动作 运营商：腾讯游戏 公测时间：2018-11-07 8.4 分", "失落的方舟"),
    ("1 无畏契约 内测 运营商：腾讯游戏 公测时间：2020-07-01 9.0 分", "无畏契约"),
    ("2 命运方舟 内测 运营商：腾讯游戏 公测时间：2023-04-12 8.0 分", "命运方舟"),
    ("2 英雄联盟 MOBA 运营商：腾讯游戏 公测时间：2011-09-22 8.9 分", "英雄联盟"),
    ("3 地下城与勇士 动作 运营商：腾讯游戏 公测时间：2005-08-16 9.1 分", "地下城与勇士"),
    ("3 超激斗梦境 公测 运营商：网易游戏 公测时间：2021-06-30 8.4 分", "超激斗梦境"),
    ("4 云顶之弈 公测 运营商：腾讯游戏 公测时间：2019-06-27 9.0 分", "云顶之弈"),
    ("4 魔兽世界 角色扮演 运营商：暴雪战网 公测时间：2005-4-26 8.7 分", "魔兽世界"),
    ("5 绝地求生 射击 运营商：腾讯游戏 公测时间：2018-01-16 9.0 分", "绝地求生"),
    ("5 魔兽世界TBC怀旧服 公测 运营商：网易游戏 公测时间：2021-07-01 9.0 分", "魔兽世界TBC怀旧服"),
    ("6 无畏 角色扮演 运营商：其他厂商 公测时间：2019-05-25 8.6 分", "无畏"),
    ("7 堡垒之夜 限量测试 运营商：腾讯游戏 公测时间：2018-08-17 8.7 分", "堡垒之夜"),
    ("7 生死狙击2 射击 运营商：无端科技 公测时间：未知 8.0 分", "生死狙击2"),
    ("8 剑网3 角色扮演 运营商：金山游戏 公测时间：2009-08-28 8.9 分", "剑网3"),
    ("9 DOTA2 2013-7-9 运营商：完美世界 公测时间：2013-7-9 8.9 分", "DOTA2"),
    ("9 炉石传说 卡牌 运营商：暴雪战网 公测时间：2013-8-17 8.3 分", "炉石传说"),
    ("9 荒野行动Plus 不限号 运营商：网易游戏 公测时间：暂无 9.2 分", "荒野行动Plus"),
    # The later title-healed form (rank already removed) of the three rows parsed wrongly.
    ("暗黑破坏神4 运营商：暴雪战网 公测时间：2023-06-06 9.0 分", "暗黑破坏神4"),
    ("DOTA2 2013-7-9 运营商：完美世界 公测时间：2013-7-9 8.9 分", "DOTA2"),
]


def row(slug, name, slot, rank=1):
    return (
        f'<a href="https://ol.3dmgame.com/{slug}/"><p><i class="i1"><span>{rank}</span></i>'
        f'<i class="i2">{name}</i><i class="i3">{slot}</i></p><div class="info">'
        '<div class="infor"><p>运营商：某厂商</p><p>公测时间：2020-01-01</p>'
        '<div class="score">8.0<u>分</u></div></div></div></a>'
    )


def page(*rows):
    return f'<div class="index_a"><div><p>网络游戏排行榜</p></div>{"".join(rows)}</div>'


def test_live_fixture_parses_every_game_name_without_metadata():
    rows, failed = parse_3dm((FIXTURES / "3dm_ranking_live.html").read_text())

    assert failed == 0
    assert [item["title"] for item in rows] == LIVE_TITLES
    assert [item["rank"] for item in rows] == list(range(1, 21))
    assert {item["external_id"] for item in rows} >= {"dota2", "poe", "ah4", "jianw3"}
    assert not any(is_3dm_row_text(item["title"]) for item in rows)


@pytest.mark.parametrize(
    ("slug", "title"), [("dota2", "DOTA2"), ("poe", "流放之路"), ("ah4", "暗黑破坏神4")]
)
def test_rows_without_a_known_genre_keep_only_the_name(slug, title):
    rows, _ = parse_3dm((FIXTURES / "3dm_ranking_live.html").read_text())

    assert {item["external_id"]: item["title"] for item in rows}[slug] == title


def test_legacy_flattened_fixture_still_parses():
    rows, failed = parse_3dm((FIXTURES / "3dm_ranking.html").read_text())

    assert failed == 0
    assert (rows[0]["rank"], rows[0]["title"]) == (1, "剑网3")


def test_unknown_or_missing_genre_uses_the_name_element():
    rows, failed = parse_3dm(
        page(row("sandbox", "某沙盒游戏", "沙盒", 1), row("blank", "无类型游戏", "", 2))
    )

    assert failed == 0
    assert [item["title"] for item in rows] == ["某沙盒游戏", "无类型游戏"]


@pytest.mark.parametrize(
    "name",
    ["黑神话：悟空", "最终幻想XIV：水晶世界", "Counter-Strike 2", "NBA 2K26", "魔兽世界·经典"],
)
def test_legitimate_name_punctuation_is_not_truncated(name):
    rows, _ = parse_3dm(page(row("game", name, "动作")))

    assert rows[0]["title"] == name
    assert clean_3dm_row_text(f"5 {name} 角色扮演 运营商：某厂商 公测时间：暂无 8.0 分") == name


def test_empty_name_element_is_counted_as_failed():
    rows, failed = parse_3dm(page(row("empty", "", "动作", 1), row("ok", "正常游戏", "动作", 2)))

    assert failed == 1
    assert [item["title"] for item in rows] == ["正常游戏"]


@pytest.mark.parametrize(("text", "title"), HISTORICAL_ROWS)
def test_historical_row_text_reduces_to_the_game_name(text, title):
    assert is_3dm_row_text(text)
    assert clean_3dm_row_text(text) == title


def test_row_text_without_genre_is_cut_at_the_metadata_boundary():
    assert clean_3dm_row_text("3 新游戏 运营商：某厂商 公测时间：暂无 8.0 分") == "新游戏"
    assert not is_3dm_row_text("剑网3")
