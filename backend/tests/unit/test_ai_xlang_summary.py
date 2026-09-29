"""AI-X1: the cross-language Event Summary v2 lineage and its English-output guards.

The regression cases restate the five defect classes of the Operations-1 AI catch-up (Chinese
reports, English Summaries): a translated person name, an added figure qualifier, a translated
government body, a translated newspaper and ministry, and a Chinese particle in English prose.
They use the reports' text, not database IDs. Pure tests, no I/O.
"""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from industry_intelligence.ai.prompts.event_insights import (
    CROSS_LANGUAGE,
    CROSS_LANGUAGE_PROMPTS,
    EVENT_SUMMARY_V4,
    EVENT_SUMMARY_XLANG_V1,
    EVENT_SUMMARY_XLANG_V2,
    EVENT_WHY_IT_MATTERS_V6,
    EVENT_WHY_IT_MATTERS_XLANG_V1,
    FINAL_LANGUAGE_CHECK,
    OUTPUT_LANGUAGE_RULES,
    insight_spec,
)
from industry_intelligence.services.ai_enrichment import system_prompt
from industry_intelligence.services.ai_evidence import (
    EventEvidence,
    EvidenceItem,
    input_hash,
    ref_map,
    validate_output,
)

T = datetime(2031, 3, 1, 9, 30, tzinfo=UTC)
XLANG = EVENT_SUMMARY_XLANG_V2

NETANYAHU = (
    "【内塔尼亚胡称“敌对势力”试图在大选前袭击以色列】以色列总理内塔尼亚胡29日在视察以空军"
    "特尔诺夫（Tel Nof）基地时表示，有情报表明，“敌对势力”正试图在即将到来的10月27日大选前"
    "对以色列发动袭击。（央视新闻）"
)
HEADSET = (
    "iQOO 头戴电竞耳机发布：四模式低延迟连接，售价 699 元",
    "该产品还提供 150 小时超长续航 ，支持多端自定义声效、可插拔指向型麦克风。",
)
SLOVAKIA = "斯洛伐克财政部：在最新预测调整中，将2027年国内生产总值增长预期从1.5%上调至1.8%。"
TOYAMA = (
    "【日本富山市虚报人口普查数据，总务省向警方提出刑事检举，市长鞠躬道歉】据日本《朝日新闻》9月29日报道，"
    "日本富山县富山市被查出虚报2025年人口普查数据，日本总务省向警方提出刑事检举，指控该市多名工作人员"
    "涉嫌违反《统计法》。富山市市长藤井裕久同日就此事鞠躬道歉。（环球网）"
)
OTP = "匈牙利欧迪银行（OTP）的恰尼表示，匈牙利银行业的税负需要逐步取消。"


def evidence(title, excerpt=None, source="新浪财经"):
    item = EvidenceItem(str(uuid4()), source, "zh-CN", T, title, excerpt, "https://x.test/1")
    return EventEvidence(str(uuid4()), "finance", 1, 1, (item,))


def validate(text, ev, spec=XLANG, locale="en"):
    data = {"points": [{"text": text, "evidence": ["C1"]}], "insufficient_evidence": True}
    return validate_output(spec, data, ref_map(ev), ev.items, locale, ev.source_count)


def fails(outcome, kind):
    return not outcome.valid and any(kind in error for error in outcome.errors)


# --- lineage isolation ---------------------------------------------------------------------


def _digest(spec):
    return hashlib.sha256((spec.system + repr(spec.schema)).encode()).hexdigest()[:16]


def test_only_the_cross_language_summary_changed_lineage():
    assert insight_spec("event_summary", CROSS_LANGUAGE) is EVENT_SUMMARY_XLANG_V2
    assert insight_spec("event_why_it_matters", CROSS_LANGUAGE) is EVENT_WHY_IT_MATTERS_XLANG_V1
    assert EVENT_SUMMARY_XLANG_V1 not in CROSS_LANGUAGE_PROMPTS.values()
    # Prompts audited before AI-X1, byte for byte (hash of system prompt and schema).
    assert _digest(EVENT_SUMMARY_V4) == "3502fb7936634849"
    assert _digest(EVENT_WHY_IT_MATTERS_V6) == "3c92a38eb7c2596b"
    assert _digest(EVENT_WHY_IT_MATTERS_XLANG_V1) == "671b1c6cff881f58"
    assert _digest(EVENT_SUMMARY_XLANG_V1) == "c5ab7db972a8f5ef"
    assert OUTPUT_LANGUAGE_RULES in EVENT_WHY_IT_MATTERS_XLANG_V1.system
    assert FINAL_LANGUAGE_CHECK in EVENT_WHY_IT_MATTERS_XLANG_V1.system
    assert OUTPUT_LANGUAGE_RULES not in XLANG.system
    assert XLANG.english_output_guards
    assert not any(
        spec.english_output_guards
        for spec in (EVENT_SUMMARY_V4, EVENT_WHY_IT_MATTERS_V6, EVENT_WHY_IT_MATTERS_XLANG_V1)
    )
    ev = evidence(SLOVAKIA)
    assert input_hash(XLANG.version, "en", ev) != input_hash(
        EVENT_SUMMARY_XLANG_V1.version, "en", ev
    )


def test_v2_prompt_states_the_naming_and_qualifier_policy():
    system = system_prompt(XLANG, "en")
    assert "Translate the prose of the reports, not their proper names" in system
    assert "including government bodies such as ministries, agencies, bureaus" in system
    assert "Places are not such names and are written in English" in system
    assert "马克龙 said" in system and "incorrect: Macron said" in system
    assert "According to 《日本经济新闻》" in system and "incorrect: According to Nikkei" in system
    assert "correct: 德国财政部 lowered its forecast" in system
    assert "the president of 丰田汽车 said" in system and "(的 is Chinese grammar" in system
    assert 'never "up to 30 hours"' in system
    assert 'nothing added (never "$100万美元")' in system
    assert "a report writing 特尔诺夫（Tel Nof） allows Tel Nof" in system
    assert "{language}" not in system
    # The examples are not the five audited Events, so their re-validation tests the rule.
    for audited in ("内塔尼亚胡", "朝日新闻", "斯洛伐克", "总务省", "欧迪银行", "150"):
        assert audited not in system


# --- A. proper names (people, media) ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (
            "Israeli Prime Minister Netanyahu said intelligence shows an attack is planned.",
            "speaker",
        ),
        ("According to Asahi Shimbun, Toyama City falsified 2025 census data.", "attribution"),
        (
            "Toyama City falsified census data, according to Asahi Shimbun as cited by 环球网.",
            "attribution",
        ),
        ("Nidec said it will book a loss.", "speaker"),
    ],
)
def test_translated_names_in_name_positions_are_rejected(text, kind):
    ev = evidence(NETANYAHU + TOYAMA + "日本电产将计提损失")
    assert fails(validate(text, ev), f"{kind} name not in evidence")


@pytest.mark.parametrize(
    "text",
    [
        "Israeli Prime Minister 内塔尼亚胡 said intelligence shows 'hostile forces' plan attacks.",
        "According to 《朝日新闻》 as cited by 环球网, Toyama City falsified 2025 census data.",
        "According to 新浪财经, the prime minister said the election is on October 27.",
        "内塔尼亚胡 visited the Tel Nof air force base.",  # a form the report itself gives
        "日本电产 said it will book a loss.",
    ],
)
def test_preserved_and_report_given_names_pass(text):
    assert validate(text, evidence(NETANYAHU + TOYAMA + "日本电产将计提损失")).valid


# --- organizations and government bodies --------------------------------------------------


@pytest.mark.parametrize(
    ("text", "report"),
    [
        ("Slovakia's Ministry of Finance raised its 2027 GDP growth forecast to 1.8%.", SLOVAKIA),
        ("Japan's Ministry of Internal Affairs and Communications filed a complaint.", TOYAMA),
        ("The staff are accused of violating the Statistics Act.", TOYAMA),
        ("Nidec Corporation will book a loss.", "日本电产将计提损失"),
        ("OTP Bank's 恰尼 called for phasing out the tax.", OTP),
    ],
)
def test_translated_organization_names_are_rejected(text, report):
    assert fails(validate(text, evidence(report)), "institution name not in evidence")


@pytest.mark.parametrize(
    ("text", "report"),
    [
        ("斯洛伐克财政部 raised its 2027 GDP growth forecast from 1.5% to 1.8%.", SLOVAKIA),
        ("总务省 filed a criminal complaint accusing city staff of violating 《统计法》.", TOYAMA),
        ("日本电产 will book a loss, according to 新浪财经.", "日本电产将计提损失"),
        ("恰尼 of 匈牙利欧迪银行（OTP） said the tax should be phased out.", OTP),
        ("OTP's 恰尼 said the bank tax needs to be phased out.", OTP),
    ],
)
def test_preserved_organization_names_pass(text, report):
    outcome = validate(text, evidence(report))
    assert outcome.valid, outcome.errors


# --- B. figure qualifiers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "report", "valid"),
    [
        ("The headset offers 150 hours of battery life.", HEADSET, True),
        ("The headset offers up to 150 hours of battery life.", HEADSET, False),
        ("The headset offers about 150 hours of battery life.", HEADSET, False),
        ("The headset offers more than 150 hours of battery life.", HEADSET, False),
        ("The headset offers up to 150 hours of battery life.", ("续航最高150小时", None), True),
        ("The headset offers about 150 hours of battery life.", ("续航约150小时", None), True),
        ("The headset offers around 150 hours of battery life.", ("续航150小时左右", None), True),
        ("It hired at least 20 people.", ("至少招聘20人", None), True),
        ("It hired at least 20 people.", ("招聘20人", None), False),
        ("It hired more than 1,000 people.", ("招聘超过1000人", None), True),
        ("It sold less than 5% of shares.", ("出售不到5%的股份", None), True),
        ("It sold up to 5% of shares.", ("出售约5%的股份", None), False),  # other kind
        ("The forecast rose from 1.5% to 1.8%.", (SLOVAKIA, None), True),
    ],
)
def test_figure_qualifiers_need_the_same_qualifier_in_the_reports(text, report, valid):
    outcome = validate(text, evidence(*report))
    assert outcome.valid is valid, outcome.errors
    if not valid:
        assert fails(outcome, "figure qualifier not in evidence")


def test_qualifiers_without_a_figure_are_not_checked():
    ev = evidence(*HEADSET)
    assert validate("Over the event, iQOO presented the headset priced at 699 元.", ev).valid


# --- C. mixed-script leakage --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (
            "According to 新浪财经, 匈牙利欧迪银行（OTP）的恰尼 said the tax should go.",
            "grammatical",
        ),
        ("According to 新浪财经, 恰尼表示 the tax should be phased out.", "grammatical"),
        ("OTP 的 chairman said the tax should be phased out.", "grammatical"),
        ("恰尼 said the 银行税 should be phased out.", "not copied"),
    ],
)
def test_chinese_grammar_or_uncopied_chinese_in_english_is_rejected(text, error):
    assert fails(validate(text, evidence(OTP)), error)


@pytest.mark.parametrize(
    ("text", "report"),
    [
        ("内塔尼亚胡 said 'hostile forces' plan an attack.", NETANYAHU),
        ("Guangxi added RMB 420 million (4.2亿元) in subsidies.", "广西追加4.2亿元购车补贴"),
        ("The game 《我的世界》 was updated.", "《我的世界》更新"),
        ("The CEO described the approach as no overtime (不加班).", "工作室CEO透露：不加班"),
        ("Dates run from 2026年5月1日 onward.", "2026年5月1日（含）起"),
    ],
)
def test_copied_names_titles_quotes_and_figures_pass(text, report):
    outcome = validate(text, evidence(report))
    assert outcome.valid, outcome.errors


def test_a_copied_chinese_clause_without_grammar_edges_is_left_to_manual_review():
    # Known limit: without a dictionary a copied clause (需要逐步取消) and a copied name
    # (匈牙利欧迪银行) are both verbatim runs of the report; the prompt forbids the clause and the
    # reviewer rejects it.
    assert validate("恰尼 said the tax on banks 需要逐步取消 soon.", evidence(OTP)).valid


def test_single_character_qualifier_signals_count_only_next_to_the_figure():
    # 超长续航 ("extra-long battery life") is not "more than"; 近日 ("recently") is not "nearly".
    assert fails(
        validate("It offers more than 150 hours.", evidence("近日发布，提供150小时超长续航")),
        "figure qualifier",
    )
    assert validate("It sold more than 100 units.", evidence("售出100多台")).valid
    assert validate("It sold nearly 100 units.", evidence("售出近100台")).valid


# --- scope --------------------------------------------------------------------------------


def test_english_output_guards_apply_only_to_the_v2_summary_in_english():
    ev = evidence(NETANYAHU)
    text = "Israeli Prime Minister Netanyahu said an attack is planned."
    assert not validate(text, ev).valid
    # Unchanged lineages keep their audited validation.
    assert validate(text, ev, EVENT_SUMMARY_XLANG_V1).valid
    assert validate(text, ev, EVENT_WHY_IT_MATTERS_XLANG_V1).valid is False  # schema: context
    assert validate(text, ev, EVENT_SUMMARY_V4).valid
    # Chinese output of English reports is not an English-output task.
    english = EventEvidence(
        str(uuid4()),
        "finance",
        1,
        1,
        (EvidenceItem(str(uuid4()), "CNBC", "en", T, "Tencent up to 5%", None, "https://x.test"),),
    )
    assert validate("据CNBC报道，Tencent股价最高上涨5%。", english, XLANG, "zh-CN").valid


def test_every_audited_defect_is_rejected_and_its_corrected_form_passes():
    cases = [
        (NETANYAHU, "Israeli Prime Minister Netanyahu said on 29th that 'hostile forces' plan an "
         "attack before the October 27 election.",
         "Israeli Prime Minister 内塔尼亚胡 said on the 29th that 'hostile forces' plan an attack "
         "before the October 27 election."),
        (HEADSET[0] + HEADSET[1], "The headset offers up to 150 hours of battery life.",
         "The headset offers 150 hours of battery life."),
        (SLOVAKIA, "According to 新浪财经, Slovakia's Ministry of Finance raised its 2027 GDP "
         "growth forecast from 1.5% to 1.8%.",
         "According to 新浪财经, 斯洛伐克财政部 raised its 2027 GDP growth forecast from 1.5% "
         "to 1.8%."),
        (TOYAMA, "Toyama City falsified 2025 census data, according to Asahi Shimbun as cited by "
         "环球网.",
         "According to 《朝日新闻》 as cited by 环球网, Toyama City falsified 2025 census data."),
        (OTP, "According to 新浪财经, 匈牙利欧迪银行（OTP）的恰尼 said the banking tax burden "
         "needs to be phased out.",
         "According to 新浪财经, 恰尼 of 匈牙利欧迪银行（OTP） said the banking tax burden needs "
         "to be phased out."),
    ]  # fmt: skip
    for report, defective, corrected in cases:
        assert not validate(defective, evidence(report)).valid, defective
        outcome = validate(corrected, evidence(report))
        assert outcome.valid, (corrected, outcome.errors)
