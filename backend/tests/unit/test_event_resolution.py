"""Deterministic Event matching rules: normalization, evidence, conflicts, and false merges.

Headlines here mirror the shapes observed in the live 2026-09-27 corpus (东方财富 short feed
titles, 新浪财经 ``【headline】body`` titles, CNBC/Nasdaq English headlines) but are synthetic.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from industry_intelligence.services.event_resolution import (
    ATTRIBUTION_SIMILARITY,
    ENTITY_SIMILARITY,
    EVENT_TYPES,
    TIME_WINDOW,
    aggregate_event_type,
    classify_event_type,
    compare,
    content_features,
    dice,
    extract_headline,
    headline_features,
    match_event,
    normalize_title,
    representative_key,
    split_attribution,
    strip_boilerplate,
)

T0 = datetime(2026, 9, 27, 9, tzinfo=UTC)
SOURCE_A, SOURCE_B, SOURCE_C = uuid4(), uuid4(), uuid4()


def item(title, source=SOURCE_A, at=T0, domain="finance", description=None, **links):
    return content_features(
        uuid4(),
        source,
        domain,
        at,
        title,
        description,
        links.get("subjects", ()),
        links.get("companies", ()),
    )


# --- normalization -------------------------------------------------------------------------


def test_english_normalization_casefolds_and_strips_punctuation_but_keeps_numbers():
    assert normalize_title("Apple Faces $5.7 Billion Verdict — Over iPhone!") == (
        "apple faces 5.7 billion verdict over iphone"
    )
    assert normalize_title("Revenue of 1,250 units") == "revenue of 1250 units"


def test_chinese_normalization_unifies_full_width_punctuation_and_spaces():
    left = normalize_title(
        "集泰股份：液冷硅油2026年上半年未实现任何销售 产品目前处于内部应用验证阶段"
    )
    right = normalize_title(
        "集泰股份:液冷硅油2026年上半年未实现任何销售，产品目前处于内部应用验证阶段。"
    )
    assert left == right
    assert "2026" in left and "：" not in left


def test_full_width_letters_and_digits_are_nfkc_normalized():
    assert normalize_title("ＡＭＤ暗盘突破６４０美元") == normalize_title("AMD暗盘突破640美元")


def test_extract_headline_keeps_only_the_bracketed_feed_headline():
    title = "【安徽建工：副董事长龚健勇代行董事长职责】安徽建工(600502.SH)公告称……"
    assert extract_headline(title) == "安徽建工：副董事长龚健勇代行董事长职责"
    assert extract_headline("No brackets here") == "No brackets here"
    assert extract_headline("前文【不是开头】") == "前文【不是开头】"
    assert extract_headline("【公司：【重磅】拟回购股份】正文") == "公司：【重磅】拟回购股份"
    assert extract_headline("【未闭合的标题") == "【未闭合的标题"
    assert extract_headline("【一】正文") == "【一】正文"


def test_boilerplate_prefixes_are_removed_without_touching_facts():
    assert strip_boilerplate("市场消息：DEXUS重启收购Campus Living Villages的谈判。") == (
        "DEXUS重启收购Campus Living Villages的谈判。"
    )
    # NFKC folds the full-width colon; comparison text only, the canonical title stays verbatim.
    assert strip_boilerplate("2连板集泰股份：液冷硅油") == "集泰股份:液冷硅油"
    assert strip_boilerplate("连板效应分析") == "连板效应分析"


def test_attribution_prefix_counts_only_for_cjk_issuers_and_speakers():
    assert split_attribution("弘宇股份：高管拟减持")[0] == "弘宇股份"
    assert split_attribution("俄外长:美伊应回归备忘录")[0] == "俄外长"
    assert split_attribution("Prediction: A Stock Market Crash Is on the Way")[0] is None
    assert split_attribution("没有冒号的标题")[0] is None


def test_features_use_cjk_bigrams_and_english_words_without_stopwords():
    features = headline_features(normalize_title("NVIDIA 发布 of the chips"))
    assert {"nvidia", "发布", "chips"} <= features
    assert "the" not in features and "of" not in features
    assert dice(set(), {"a"}) == 0.0
    assert dice({"a", "b"}, {"a", "b"}) == 1.0


# --- Event types ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("恩捷股份：拟11.5亿元收购湖北恩捷45%股权", "acquisition"),
        ("安徽建工：副董事长龚健勇代行董事长职责", "leadership"),
        ("CEO who posted photos is no longer with the company", "leadership"),
        ("天奇股份：向特定对象发行股票申请获证监会同意注册批复", "regulatory"),
        ("Apple faces $5.7 billion patent infringement verdict", "regulatory"),
        ("日本NS Solutions将与Anthropic展开合作", "partnership"),
        ("问界新M8于9月30日开启预售", "product_launch"),
        ("Startup raises $40 million in funding round", "funding"),
        ("Retailer reports earnings above estimates", "earnings"),
        ("Hackers claim data breach at exchange", "security_incident"),
        # Loose mentions are not classified.
        ("Broadcom's Earnings Are About 43% Higher. Is It a Buy?", "general"),
        ("Royal Pop collaboration with Swatch is breaking sales records", "general"),
        ("华数传媒：获准注册20亿元超短期融资券", "general"),
        ("印度北方邦暴雨已致56人死亡", "general"),
        # Two families match: not reliable, so general.
        ("公司宣布收购并与对方展开合作", "general"),
    ],
)
def test_event_type_keywords_are_conservative(headline, expected):
    assert classify_event_type(headline, "finance") == expected
    assert expected in EVENT_TYPES


def test_game_release_is_only_assigned_in_gaming_domain():
    assert classify_event_type("Studio delays sequel release date to 2027", "gaming") == (
        "game_release"
    )
    assert classify_event_type("Studio delays sequel release date to 2027", "finance") == "general"


def test_aggregate_event_type_needs_one_specific_type():
    assert aggregate_event_type(["general", "acquisition", "acquisition"]) == "acquisition"
    assert aggregate_event_type(["general"]) == "general"
    assert aggregate_event_type(["acquisition", "leadership"]) == "general"


# --- true merges ---------------------------------------------------------------------------


def test_same_event_from_two_sources_with_feed_formatting_merges_as_exact():
    short = item("安徽建工：副董事长龚健勇代行董事长职责", SOURCE_A)
    long = item(
        "【安徽建工：副董事长龚健勇代行董事长职责】安徽建工公告称，公司董事会收到辞职报告。",
        SOURCE_B,
        T0 + timedelta(minutes=8),
    )
    decision = compare(long, short)
    assert decision.matched and decision.strategy == "exact_headline"
    assert decision.confidence == "exact" and decision.time_delta_seconds == 480


def test_boilerplate_and_punctuation_variants_merge_as_exact():
    left = item("DEXUS重启收购Campus Living Villages的谈判", SOURCE_A)
    right = item("市场消息：DEXUS重启收购Campus Living Villages的谈判。", SOURCE_B)
    assert compare(right, left).strategy == "exact_headline"


def test_same_issuer_small_wording_change_merges_on_attribution_evidence():
    left = item("弘宇股份：高级管理人员刘志鸿拟减持不超100万股公司股份", SOURCE_A)
    right = item(
        "【弘宇股份：高级管理人员刘志鸿拟减持公司不超0.59%股份】持股2.53%的高级管理人员刘志鸿"
        "计划减持公司股份不超过100万股（占公司总股本的0.59%）。",
        SOURCE_B,
        T0 + timedelta(minutes=22),
    )
    decision = compare(right, left)
    assert decision.matched and decision.strategy == "attribution_headline"
    assert decision.confidence == "strong"
    assert decision.similarity >= ATTRIBUTION_SIMILARITY


def test_shared_subject_and_similar_headline_merges_on_entity_evidence():
    subject = uuid4()
    left = item("Acme Robotics recalls warehouse robots after safety review", subjects={subject})
    right = item(
        "Acme Robotics recalls warehouse robots after a safety review",
        SOURCE_B,
        subjects={subject},
    )
    decision = compare(right, left)
    assert decision.matched and decision.strategy == "shared_entity_headline"
    assert decision.shared_subject_ids == (subject,)
    assert decision.similarity >= ENTITY_SIMILARITY


# --- critical false-merge protections ------------------------------------------------------


def test_same_company_same_day_different_event_must_not_merge():
    company = uuid4()
    launch = item("Apple launches new iPhone lineup", companies={company})
    results = item(
        "Apple reports earnings for the fourth quarter",
        SOURCE_B,
        T0 + timedelta(hours=3),
        companies={company},
    )
    decision = compare(results, launch)
    assert not decision.matched
    assert decision.reason == "conflict:event_type"


def test_same_subject_same_type_but_different_headline_must_not_merge():
    subject = uuid4()
    left = item("NVIDIA launches new data center GPU", subjects={subject})
    right = item("NVIDIA launches gaming handheld in China", SOURCE_B, subjects={subject})
    decision = compare(right, left)
    assert not decision.matched and decision.reason == "entity_below_threshold"


def test_same_subject_different_event_type_must_not_merge():
    subject = uuid4()
    left = item("恩捷股份：拟11.5亿元收购湖北恩捷45%股权", subjects={subject})
    right = item("恩捷股份：董事长辞职", SOURCE_B, subjects={subject})
    assert compare(right, left).reason == "conflict:event_type"


def test_similar_headline_different_company_must_not_merge():
    left = item("Acme Bank fined by regulator over reporting failures", companies={uuid4()})
    right = item(
        "Beta Bank fined by regulator over reporting failures", SOURCE_B, companies={uuid4()}
    )
    decision = compare(right, left)
    assert not decision.matched and decision.reason == "conflict:companies"


def test_similar_headline_different_issuer_prefix_must_not_merge():
    left = item("纳指ETF易方达：因交易价格溢价将停牌后复牌", SOURCE_A)
    right = item("纳指ETF嘉实：因交易价格溢价将停牌后复牌", SOURCE_B)
    assert compare(right, left).reason == "conflict:attribution"


def test_similar_headline_without_identity_evidence_must_not_merge():
    # Observed live: two different warnings share much of their wording (Dice 0.565).
    left = item("自然资源部与中国气象局联合发布黄色地质灾害气象风险预警", SOURCE_A)
    right = item("水利部和中国气象局联合发布黄色山洪灾害气象预警", SOURCE_B)
    decision = compare(right, left)
    assert not decision.matched and decision.reason == "no_identity_evidence"
    assert decision.similarity > 0.5


def test_conflicting_numbers_must_not_merge():
    left = item("AMD暗盘突破630美元，日内涨1.1%。", SOURCE_A)
    right = item("AMD暗盘突破640美元，日内涨1.8%。", SOURCE_B, T0 + timedelta(minutes=8))
    decision = compare(right, left)
    assert decision.reason == "conflict:numbers"
    assert decision.similarity == 0.5  # recorded for diagnostics, never decisive


def test_numbers_present_in_the_other_items_body_are_compatible():
    left = item("弘宇股份：高级管理人员刘志鸿拟减持不超100万股公司股份", SOURCE_A)
    right = item(
        "【弘宇股份：高级管理人员刘志鸿拟减持公司不超0.59%股份】减持不超过100万股。", SOURCE_B
    )
    assert compare(right, left).matched


def test_same_time_alone_never_merges():
    left = item("Oil prices fall as talks resume", SOURCE_A)
    right = item("Retail sales beat forecasts in August", SOURCE_B)
    assert compare(right, left).reason == "no_identity_evidence"


def test_outside_time_window_must_not_merge_even_when_exact():
    left = item("广西追加4.2亿元购车补贴", SOURCE_A)
    right = item("广西追加4.2亿元购车补贴", SOURCE_B, T0 + TIME_WINDOW + timedelta(seconds=1))
    assert compare(right, left).reason == "outside_window"


def test_different_domain_must_not_merge():
    left = item("Studio announces sequel", SOURCE_A, domain="gaming")
    right = item("Studio announces sequel", SOURCE_B, domain="finance")
    assert compare(right, left).reason == "conflict:domain"


def test_same_source_distinct_items_merge_only_on_exact_headline():
    left = item("百利天恒：注射用BL-M12D1获临床试验批准通知书", SOURCE_A)
    right = item("百利天恒：宜泽康纳入突破性治疗品种名单", SOURCE_A)
    assert compare(right, left).reason == "same_source"
    repeat = item("百利天恒：注射用BL-M12D1获临床试验批准通知书。", SOURCE_A)
    assert compare(repeat, left).strategy == "exact_headline"


def test_short_exact_headlines_are_not_enough():
    left = item("AMD涨1%", SOURCE_A)
    right = item("AMD涨1%", SOURCE_B)
    assert not compare(right, left).matched


def test_cross_language_headlines_do_not_merge_without_shared_entities():
    left = item("DEXUS resumes talks to buy Campus Living Villages", SOURCE_A)
    right = item("DEXUS重启收购Campus Living Villages的谈判", SOURCE_B)
    assert not compare(right, left).matched


def test_match_event_rejects_when_any_member_conflicts():
    first = item("Acme Corp names new chief executive", SOURCE_A, subjects={s := uuid4()})
    conflicting = item("Acme Corp launches drone", SOURCE_B, subjects={s})
    candidate = item("Acme Corp names new chief executive", SOURCE_C, subjects={s})
    best, rejected = match_event(candidate, [first, conflicting])
    assert best is None
    assert [decision.reason for decision, _ in rejected] == ["conflict:event_type"]
    best, rejected = match_event(candidate, [first])
    assert best[0].strategy == "exact_headline" and best[1] is first and rejected == []


def test_representative_key_prefers_clean_headlines_then_time_source_and_id():
    early = representative_key("新浪标题", T0, "新浪财经", uuid4())
    late = representative_key("东财标题", T0 + timedelta(seconds=1), "东方财富", uuid4())
    assert min(late, early) == early
    tie = representative_key("东财标题", T0, "东方财富", uuid4())
    assert min(early, tie) == tie
    boilerplate = representative_key("市场消息：DEXUS重启收购谈判。", T0, "新浪财经", uuid4())
    clean_later = representative_key(
        "DEXUS重启收购谈判", T0 + timedelta(minutes=9), "东方财富", uuid4()
    )
    assert min(boilerplate, clean_later) == clean_later
    board = representative_key("【2连板康强电子：原材料成本影响较大】正文", T0, "新浪财经", uuid4())
    assert min(board, clean_later) == clean_later
