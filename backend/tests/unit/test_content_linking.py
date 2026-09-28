"""Deterministic content → Subject linking rules (Phase 6.1).

Examples are taken from the live Subject corpus (crypto "Stellar"/"Jupiter"/"Official Trump",
games "ANATOMY"/"英雄联盟"/"War Thunder", A-shares 寒武纪/京东方Ａ, two separate "Bitcoin"
Subjects) because those are the names that could produce real false links.
"""

from uuid import uuid4

import pytest

from industry_intelligence.domain.enums import SubjectType
from industry_intelligence.services.content_linking import (
    build_catalog,
    match_content,
    name_rejection,
)

SOURCE = uuid4()


def subject(kind, title):
    return (uuid4(), kind, title)


def link(text, subjects, aliases=(), identities=(), url=None, source_id=SOURCE, article=None):
    catalog = build_catalog(subjects, aliases, identities)
    return {
        sid: strategy
        for sid, (strategy, _) in match_content(text, url, source_id, article, catalog).items()
    }


@pytest.mark.parametrize(
    ("name", "kind", "reason"),
    [
        ("Stellar", SubjectType.STOCK, "single_word"),
        ("Jupiter", SubjectType.STOCK, "single_word"),
        ("ANATOMY", SubjectType.GAME, "single_word"),
        ("AI", SubjectType.TECHNOLOGY, "short_latin"),
        ("BNB", SubjectType.STOCK, "short_latin"),
        ("原神", SubjectType.GAME, "short_cjk"),
        ("a" * 64, SubjectType.STOCK, "not_a_name"),
        ("3c19d4dd525a6e26c950defa3a269fa671fbc4d881832ffdfe3dbe4be7a262e1", "stock", "not_a_name"),
        (
            "暗黑破坏神4 运营商：暴雪战网 公测时间：2023-06-06 9.0 分" * 2,
            SubjectType.GAME,
            "too_long",
        ),
        ("小米 18 Fold 中折叠首销情况曝光", SubjectType.EVENT, "ranked_story"),
        ("War Thunder", SubjectType.GAME, None),
        ("PlayStation", SubjectType.COMPANY, None),
        ("Counter-Strike", SubjectType.GAME, None),
        ("paperclipai/paperclip", SubjectType.REPOSITORY, None),
        ("寒武纪", SubjectType.STOCK, None),
    ],
)
def test_name_eligibility(name, kind, reason):
    assert name_rejection(name, kind) == reason


def test_reviewed_seed_alias_may_be_a_single_word():
    assert name_rejection("NVIDIA", SubjectType.COMPANY) == "single_word"
    assert name_rejection("NVIDIA", SubjectType.COMPANY, reviewed=True) is None


def test_common_words_never_link():
    stellar, jupiter, anatomy = (
        subject(SubjectType.STOCK, "Stellar"),
        subject(SubjectType.STOCK, "Jupiter"),
        subject(SubjectType.GAME, "ANATOMY"),
    )
    text = "Stellar quarter as Jupiter probe data reveals ANATOMY of a chip"
    assert link(text, [stellar, jupiter, anatomy]) == {}


def test_generic_ai_alias_and_ambiguous_alias_never_link():
    openai = subject(SubjectType.COMPANY, "OpenAI")
    other = subject(SubjectType.TECHNOLOGY, "Artificial Intelligence")
    aliases = [(openai[0], "AI", "hacker_news"), (other[0], "AI", "hacker_news")]
    assert link("AI chips ship to the AI lab", [openai, other], aliases) == {}


def test_latin_names_are_case_sensitive_with_word_boundaries():
    trump = subject(SubjectType.STOCK, "Official Trump")
    war = subject(SubjectType.GAME, "War Thunder")
    assert link("An official Trump statement", [trump, war]) == {}
    assert link("War Thunderbolt squadron", [trump, war]) == {}
    assert link("Gaijin patches War Thunder today", [trump, war]) == {war[0]: "exact_name"}


def test_company_name_inside_unrelated_phrase_does_not_link():
    openai_api = subject(SubjectType.TECHNOLOGY, "OpenAI API")
    assert link("OpenAI Codex ships", [openai_api]) == {}


def test_two_subjects_with_one_name_are_ambiguous():
    first, second = subject(SubjectType.STOCK, "Bitcoin"), subject(SubjectType.STOCK, "Bitcoin")
    aliases = [
        (sid, alias, "manual_seed")
        for sid in (first[0], second[0])
        for alias in ("Bitcoin", "比特币")
    ]
    catalog = build_catalog([first, second], aliases, [])
    assert "bitcoin" in catalog.ambiguous_names and "比特币" in catalog.ambiguous_names
    assert link("Bitcoin 与 比特币 ETF", [first, second], aliases) == {}


def test_reviewed_seed_alias_links_exactly():
    nvidia = subject(SubjectType.COMPANY, "NVIDIA")
    aliases = [(nvidia[0], "NVIDIA", "manual_seed"), (nvidia[0], "英伟达", "manual_seed")]
    assert link("Nvidia shares rise", [nvidia], aliases) == {}
    assert link("英伟达发布新芯片", [nvidia], aliases) == {nvidia[0]: "exact_alias"}


def test_cjk_stock_names_and_fullwidth_normalization():
    cambricon = subject(SubjectType.STOCK, "寒武纪")
    boe = subject(SubjectType.STOCK, "京东方Ａ")
    assert link("寒武纪涨超5%，京东方A跟涨", [cambricon, boe]) == {
        cambricon[0]: "exact_name",
        boe[0]: "exact_name",
    }


def test_cjk_game_titles_need_title_marks_and_exact_extent():
    lol = subject(SubjectType.GAME, "英雄联盟")
    diablo = subject(SubjectType.GAME, "暗黑破坏神4")
    assert link("英雄联盟全球总决赛开幕", [lol]) == {}
    assert link("《英雄联盟手游》新版本", [lol]) == {}
    assert link("腾讯宣布《英雄联盟》新赛季", [lol]) == {lol[0]: "exact_name"}
    assert link("《暗黑破坏神 4》资料片", [diablo]) == {diablo[0]: "exact_name"}


def test_longer_name_wins_over_contained_name():
    cs, cs2 = (
        subject(SubjectType.GAME, "Counter-Strike"),
        subject(SubjectType.GAME, "Counter-Strike 2"),
    )
    assert link("Valve updates Counter-Strike 2 maps", [cs, cs2]) == {cs2[0]: "exact_name"}
    assert link("Counter-Strike turns 25", [cs, cs2]) == {cs[0]: "exact_name"}


@pytest.mark.parametrize(
    ("text", "linked"),
    [
        ("寒武纪(688256)公告", True),
        ("寒武纪（688256.SH）公告", True),
        ("688256.SS 收涨", True),
        ("SH688256 成交额", True),
        ("sh:688256", True),
        ("SZ688256", False),
        ("编号688256的文件", False),
        ("1688256", False),
    ],
)
def test_exchange_code_needs_market_notation(text, linked):
    cambricon = subject(SubjectType.STOCK, "寒武纪科技股份")
    identities = [(cambricon[0], SOURCE, "SH:688256", "SH:688256", None)]
    expected = {cambricon[0]: "exchange_code"} if linked else {}
    assert link(text, [cambricon], identities=identities) == expected


def test_cashtag_only_and_unique():
    trump = subject(SubjectType.STOCK, "Official Trump")
    identities = [(trump[0], SOURCE, "official-trump", "crypto:TRUMP", None)]
    assert link("TRUMP rallies after speech", [trump], identities=identities) == {}
    assert link("$TRUMP rallies", [trump], identities=identities) == {trump[0]: "cashtag"}


def test_ranked_story_links_only_by_same_article():
    story = subject(SubjectType.EVENT, "小米 18 Fold 中折叠首销情况曝光")
    identities = [
        (story[0], SOURCE, "1007244", None, "https://m.ithome.com/html/1007244.htm"),
    ]
    assert link("小米 18 Fold 中折叠首销情况曝光", [story], identities=identities) == {}
    assert link("any", [story], identities=identities, article="1007244") == {
        story[0]: "same_article"
    }
    # The same article number from another source is not the same article.
    assert link("any", [story], identities=identities, source_id=uuid4(), article="1007244") == {}
    assert link(
        "any", [story], identities=identities, url="https://m.ithome.com/html/1007244.htm"
    ) == {story[0]: "same_article"}


def test_best_strategy_wins_and_no_match_is_empty():
    cambricon = subject(SubjectType.STOCK, "寒武纪")
    identities = [(cambricon[0], SOURCE, "SH:688256", "SH:688256", None)]
    assert link("寒武纪(688256)", [cambricon], identities=identities) == {
        cambricon[0]: "exchange_code"
    }
    assert link("", [cambricon], identities=identities) == {}


def test_game_titles_inside_title_marks_match_exact_extent_case_insensitively():
    warframe = subject(SubjectType.GAME, "Warframe")
    wardogs = subject(SubjectType.GAME, "WARDOGS")
    genshin = subject(SubjectType.GAME, "原神")
    rust = subject(SubjectType.GAME, "Rust")
    games = [warframe, wardogs, genshin, rust]
    assert link("星约长安：《Warframe》中国首站落幕", games) == {warframe[0]: "exact_name"}
    assert link("大黑马《Wardogs》数据复盘", games) == {wardogs[0]: "exact_name"}
    assert link("《原神》新版本", games) == {genshin[0]: "exact_name"}
    # Outside title marks, or when the marks hold a longer title, single words never link.
    assert link("Warframe update; players trust the frustrated devs", games) == {}
    assert link("《原神手游》与《Warframe 2》", games) == {}
    # A non-game Subject never uses the title-mark form.
    stellar = subject(SubjectType.STOCK, "Stellar")
    assert link("《Stellar》", [stellar]) == {}


def test_trademark_signs_are_not_part_of_names():
    apex = subject(SubjectType.GAME, "Apex Legends™")
    fc = subject(SubjectType.GAME, "EA SPORTS FC™ 27")
    assert link("Apex Legends season launches", [apex, fc]) == {apex[0]: "exact_name"}
    assert link("EA SPORTS FC 27 sales", [apex, fc]) == {fc[0]: "exact_name"}
