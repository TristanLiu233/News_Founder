"""Prompts, input hashes, evidence validation guards, and suggestion rules (pure, no I/O)."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from industry_intelligence.ai.prompts import GROUNDING_RULES
from industry_intelligence.ai.prompts.event_candidates import (
    EVENT_ENTITY_CANDIDATES_V1,
    EVENT_PAIR_V1,
)
from industry_intelligence.ai.prompts.event_insights import (
    CROSS_LANGUAGE,
    CROSS_LANGUAGE_PROMPTS,
    EVENT_SUMMARY_V4,
    EVENT_SUMMARY_XLANG_V1,
    EVENT_WHY_IT_MATTERS_V6,
    EVENT_WHY_IT_MATTERS_XLANG_V1,
    FINAL_LANGUAGE_CHECK,
    INSIGHT_PROMPTS,
    OUTPUT_LANGUAGE_RULES,
    PRIMARY,
    insight_spec,
)
from industry_intelligence.services.ai_enrichment import insight_payload, system_prompt
from industry_intelligence.services.ai_evidence import (
    EventEvidence,
    EvidenceItem,
    generation_mode,
    input_hash,
    ref_map,
    render_event,
    validate_output,
)
from industry_intelligence.services.ai_suggestions import (
    distinctive_names,
    entity_suggestions,
    mention_grounded,
)

T = datetime(2031, 3, 1, 9, 30, tzinfo=UTC)
ALL_PROMPTS = (
    EVENT_SUMMARY_V4,
    EVENT_WHY_IT_MATTERS_V6,
    EVENT_SUMMARY_XLANG_V1,
    EVENT_WHY_IT_MATTERS_XLANG_V1,
    EVENT_ENTITY_CANDIDATES_V1,
    EVENT_PAIR_V1,
)


def item(title, excerpt=None, source="Source A", language="en", at=T):
    return EvidenceItem(str(uuid4()), source, language, at, title, excerpt, "https://x.test/1")


def evidence(*items):
    return EventEvidence(
        event_id=str(uuid4()),
        domain="gaming",
        content_count=len(items),
        source_count=len({i.source for i in items}),
        items=tuple(items),
    )


def validate(spec, data, ev, locale="en"):
    return validate_output(spec, data, ref_map(ev), ev.items, locale, ev.source_count)


def summary(text, refs=("C1",)):
    return {"points": [{"text": text, "evidence": list(refs)}], "insufficient_evidence": False}


# --- prompts -------------------------------------------------------------------------------


def test_prompt_versions_are_unique_and_explicit():
    versions = [spec.version for spec in ALL_PROMPTS]
    assert len(set(versions)) == len(versions)
    assert all(re.fullmatch(r"[a-z_]+_v[1-9][0-9]*", version) for version in versions)
    assert (
        set(INSIGHT_PROMPTS)
        == set(CROSS_LANGUAGE_PROMPTS)
        == {
            "event_summary",
            "event_why_it_matters",
        }
    )
    # Primary-locale prompts stay the Checkpoint D audited versions.
    assert INSIGHT_PROMPTS["event_summary"].version == "event_summary_v4"
    assert INSIGHT_PROMPTS["event_why_it_matters"].version == "event_why_it_matters_v6"
    assert CROSS_LANGUAGE_PROMPTS["event_summary"].version == "event_summary_xlang_v1"
    assert CROSS_LANGUAGE_PROMPTS["event_why_it_matters"].version == (
        "event_why_it_matters_xlang_v1"
    )
    for artifact_type in INSIGHT_PROMPTS:
        assert insight_spec(artifact_type, PRIMARY) is INSIGHT_PROMPTS[artifact_type]
        assert insight_spec(artifact_type, CROSS_LANGUAGE) is CROSS_LANGUAGE_PROMPTS[artifact_type]
        assert not INSIGHT_PROMPTS[artifact_type].cross_language
        assert CROSS_LANGUAGE_PROMPTS[artifact_type].cross_language


def test_summary_prompt_keeps_plan_framing_and_adds_no_anchors():
    # Phase 7.1 live audit: v1 turned a planned share reduction "15个交易日后" into a certain
    # action counted from the announcement date, which no report stated.
    system = system_prompt(EVENT_SUMMARY_V4, "zh-CN")
    assert "never a certain or completed action" in system
    assert "Do not add reference points, time anchors" in system
    assert "当日/on the day" in system
    assert "Keep juxtaposed facts separate" in system
    assert "消息传出后" in system
    assert GROUNDING_RULES in system


def test_summary_prompt_version_change_changes_the_input_hash():
    ev = evidence(item("Studio A plans a sale."))
    assert input_hash("event_summary_v1", "zh-CN", ev) != input_hash(
        EVENT_SUMMARY_V4.version, "zh-CN", ev
    )


def test_why_prompt_forbids_inferred_causation_and_preserves_reported_framing():
    system = system_prompt(EVENT_WHY_IT_MATTERS_V6, "zh-CN")
    assert "Do not infer causation, motivation, or a relationship" in system
    assert "受此影响" in system
    assert "Preserve reported, planned, expected, and alleged framing" in system
    assert "Copy entity, person, company, organization, product, and title names exactly" in system
    assert "Do not correct, normalize, expand, translate" in system
    assert "Do not invent how information was communicated" in system
    assert "采访" in system
    assert "published_at field is when a report was published" in system
    assert "not use it as an event date" in system
    assert "Do not add sequence words" in system


def test_primary_prompts_carry_no_cross_language_rules():
    # Phase 7.1 Checkpoint E: translation rules must not change the audited primary prompts.
    for spec in INSIGHT_PROMPTS.values():
        assert OUTPUT_LANGUAGE_RULES not in spec.system
        assert FINAL_LANGUAGE_CHECK not in spec.system
        assert spec.system.rstrip().endswith(GROUNDING_RULES)


def test_cross_language_prompts_are_the_primary_text_plus_language_rules():
    for artifact_type, spec in CROSS_LANGUAGE_PROMPTS.items():
        primary = INSIGHT_PROMPTS[artifact_type]
        assert spec.schema == primary.schema and spec.output_model is primary.output_model
        assert spec.system == primary.system.replace(
            GROUNDING_RULES,
            f"{OUTPUT_LANGUAGE_RULES}\n\n{GROUNDING_RULES}\n\n{FINAL_LANGUAGE_CHECK}",
        )


def test_cross_language_prompts_keep_names_hedges_and_output_language():
    # Phase 7.1 Checkpoint E: Chinese reports with English output produced name translations
    # (吉宏股份 (Jihong Co.), 日本电产 → Nidec, 广西日报 → Guangxi Daily), points copied in
    # Chinese, an over-long point, "following the report" between juxtaposed facts, and
    # detail added in translation.
    for spec in CROSS_LANGUAGE_PROMPTS.values():
        system = system_prompt(spec, "en")
        assert "Write every point in English, even when the reports are in another" in system
        assert "never copy a whole sentence or clause" in system
        assert 'never "Tencent (腾讯)"' in system
        assert "Everything that is not such a name is written in English" in system
        assert "an attribution (外界认为, analysts said) stays attached to the same claim" in system
        assert 'with nothing added (never "$100万美元")' in system
        assert "check every name in your output" in system
        assert "negations stay negations" in system
        assert "stay side by side" in system
        assert "Translate without adding" in system
        assert "Background knowledge is not evidence" in system
        assert "under 300 characters" in system
        assert "{language}" not in system
        assert system.rstrip().endswith("If a point is not in English, rewrite it in English.")
        assert "Write every point in Simplified Chinese" in system_prompt(spec, "zh-CN")


@pytest.mark.parametrize(
    ("languages", "locale", "mode"),
    [
        (["zh-CN"], "zh-CN", PRIMARY),
        (["zh-CN", "zh-CN"], "en", CROSS_LANGUAGE),
        (["en"], "en", PRIMARY),
        (["en"], "zh-CN", CROSS_LANGUAGE),
        (["zh"], "zh-CN", PRIMARY),
        (["en", "zh-CN"], "en", CROSS_LANGUAGE),
        (["en", "zh-CN"], "zh-CN", CROSS_LANGUAGE),
    ],
)
def test_generation_mode_follows_report_languages(languages, locale, mode):
    ev = evidence(*(item("Studio A", language=language) for language in languages))
    assert generation_mode(ev, locale) == mode


def test_only_cross_language_requests_carry_the_output_language():
    ev = evidence(item("广西追加4.2亿元购车补贴", language="zh-CN"))
    for artifact_type, spec in CROSS_LANGUAGE_PROMPTS.items():
        assert insight_payload(spec, ev, "en")["output_language"] == "English"
        assert insight_payload(spec, ev, "zh-CN")["output_language"] == "Simplified Chinese"
        assert insight_payload(spec, ev, "en")["reports"] == render_event(ev)["reports"]
        # Primary-locale requests stay exactly as audited in Checkpoint D.
        assert insight_payload(INSIGHT_PROMPTS[artifact_type], ev, "zh-CN") == render_event(ev)


def test_lineages_hash_separately():
    ev = evidence(item("广西追加4.2亿元购车补贴", language="zh-CN"))
    for artifact_type, spec in CROSS_LANGUAGE_PROMPTS.items():
        primary = INSIGHT_PROMPTS[artifact_type]
        assert input_hash(spec.version, "en", ev) != input_hash(primary.version, "en", ev)
        assert input_hash(f"{spec.version[:-1]}2", "en", ev) != input_hash(spec.version, "en", ev)


def test_every_prompt_carries_the_grounding_rules():
    for spec in ALL_PROMPTS:
        assert GROUNDING_RULES in spec.system
        for rule in ("Do not browse", "investment advice", "Never describe a single report"):
            assert rule in spec.system


def _closed(schema):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for child in schema["properties"].values():
            _closed(child)
    if schema.get("type") == "array":
        _closed(schema["items"])


def test_schemas_are_closed_objects():
    for spec in ALL_PROMPTS:
        _closed(spec.schema)


def test_localized_prompts_name_the_output_language():
    assert "Simplified Chinese" in system_prompt(EVENT_SUMMARY_V4, "zh-CN")
    assert "in English" in system_prompt(EVENT_SUMMARY_V4, "en")
    assert "{language}" not in system_prompt(EVENT_WHY_IT_MATTERS_V6, "zh-CN")


# --- input hash ----------------------------------------------------------------------------


def test_input_hash_tracks_evidence_prompt_and_locale():
    first, second = item("Studio A announces game"), item("工作室A公布新作", language="zh-CN")
    base = input_hash("event_summary_v1", "en", evidence(first, second))
    assert base == input_hash("event_summary_v1", "en", evidence(second, first))
    assert base != input_hash("event_summary_v2", "en", evidence(first, second))
    assert base != input_hash("event_summary_v1", "zh-CN", evidence(first, second))
    assert base != input_hash("event_summary_v1", "en", evidence(first))
    for changed in (
        replace(first, title="Studio A delays game"),
        replace(first, excerpt="new excerpt"),
        replace(first, published_at=datetime(2031, 3, 2, tzinfo=UTC)),
        replace(first, source="Source B"),
    ):
        assert base != input_hash("event_summary_v1", "en", evidence(changed, second))


def test_rendered_event_marks_single_source_and_uses_refs():
    ev = evidence(item("Studio A announces game"))
    payload = render_event(ev)
    assert payload["single_source"] is True
    assert [report["ref"] for report in payload["reports"]] == ["C1"]
    assert "content_id" not in payload["reports"][0]


# --- evidence validation -------------------------------------------------------------------


def test_valid_summary_maps_refs_to_event_content_ids():
    ev = evidence(item("Studio A raises $40 million", "Round led by Fund B on March 1"))
    outcome = validate(
        EVENT_SUMMARY_V4, summary("According to Source A, Studio A raised $40 million."), ev
    )
    assert outcome.valid, outcome.errors
    assert outcome.output["points"][0]["evidence"] == [ev.items[0].content_id]


@pytest.mark.parametrize(
    "refs",
    [("C2",), ("C1", "C9"), ("A1",)],
)
def test_unknown_evidence_refs_fail(refs):
    ev = evidence(item("Studio A announces game"))
    outcome = validate(EVENT_SUMMARY_V4, summary("Source A reports the game.", refs), ev)
    assert not outcome.valid
    assert any("unknown evidence ref" in error for error in outcome.errors)


def test_foreign_content_id_cannot_be_cited():
    ev = evidence(item("Studio A announces game"))
    foreign = str(uuid4())
    outcome = validate(EVENT_SUMMARY_V4, summary("Source A reports the game.", (foreign,)), ev)
    assert not outcome.valid


def test_points_without_evidence_fail_schema_validation():
    ev = evidence(item("Studio A announces game"))
    outcome = validate(EVENT_SUMMARY_V4, summary("Source A reports the game.", ()), ev)
    assert not outcome.valid and outcome.errors[0].startswith("schema:")
    assert not validate(EVENT_SUMMARY_V4, {"points": [], "insufficient_evidence": True}, ev).valid
    assert not validate(EVENT_SUMMARY_V4, {"points": "x"}, ev).valid


@pytest.mark.parametrize(
    ("text", "locale"),
    [
        ("Analysts set a price target of $200 for Studio A.", "en"),
        ("Investors should buy Studio A shares.", "en"),
        ("Studio A shares will rise after the announcement.", "en"),
        ("This is a buy rating for Studio A.", "en"),
        ("机构给出目标价，建议买入工作室A。", "zh-CN"),
        ("该消息公布后股价将上涨。", "zh-CN"),
    ],
)
def test_investment_advice_and_predictions_fail(text, locale):
    ev = evidence(item("Studio A announces game"))
    outcome = validate(EVENT_SUMMARY_V4, summary(text), ev, locale)
    assert any("prohibited" in error for error in outcome.errors)


@pytest.mark.parametrize(
    "text",
    ["Multiple reports say Studio A announced a game.", "多家媒体报道，工作室A公布新作。"],
)
def test_single_source_event_cannot_claim_multiple_reports(text):
    ev = evidence(item("Studio A announces game"))
    locale = "zh-CN" if "媒体" in text else "en"
    outcome = validate(EVENT_SUMMARY_V4, summary(text), ev, locale)
    assert any("single-source" in error for error in outcome.errors)
    two = evidence(item("Studio A announces game"), item("Studio A game", source="Source B"))
    assert validate(EVENT_SUMMARY_V4, summary(text), two, locale).valid


def test_figures_must_appear_in_the_evidence():
    ev = evidence(item("恩捷股份：拟11.5亿元收购湖北恩捷45%股权", language="zh-CN"))
    ok = validate(
        EVENT_SUMMARY_V4, summary("恩捷股份拟以11.5亿元收购湖北恩捷45%股权。"), ev, "zh-CN"
    )
    assert ok.valid, ok.errors
    wrong = validate(
        EVENT_SUMMARY_V4, summary("恩捷股份拟以12亿元收购湖北恩捷45%股权。"), ev, "zh-CN"
    )
    assert any("figures not in evidence ['12']" in error for error in wrong.errors)
    rounded = validate(EVENT_SUMMARY_V4, summary("Enjie plans a RMB 1.2 billion deal."), ev)
    assert any("figures not in evidence ['1.2']" in error for error in rounded.errors)


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("广西追加4.2亿元购车补贴", "Guangxi added RMB 420 million in car purchase subsidies."),
        ("计提约1万亿日元（合63亿美元）损失", "A loss of about 1 trillion yen (USD 6.3 billion)."),
        ("网易4000万美元赌中爆款", "NetEase bet USD 40 million on the game."),
        ("恩捷股份：拟11.5亿元收购湖北恩捷45%股权", "Enjie plans a RMB 1.15 billion deal for 45%."),
        ("流动量19946.2万人次", "Cross-regional trips reached 199.462 million."),
        ("追加4.2亿元资金", "Guangxi added 4.2亿元 in funds."),
    ],
)
def test_exact_chinese_scale_equivalents_in_english_are_allowed_figures(title, text):
    # Phase 7.1 Checkpoint E: 63亿美元 written as "6.3 billion" was rejected although equal.
    ev = evidence(item(title, language="zh-CN"))
    outcome = validate(EVENT_SUMMARY_V4, summary(text), ev)
    assert outcome.valid, outcome.errors


@pytest.mark.parametrize(
    ("text", "figure"),
    [
        ("Guangxi added RMB 42 million in subsidies.", "42"),  # wrong scale
        ("Guangxi added RMB 420 in subsidies.", "420"),  # no scale word
        ("Guangxi added RMB 420 thousand in subsidies.", "420"),  # wrong scale word
        ("Guangxi added RMB 430 million in subsidies.", "430"),  # fabricated
        ("Guangxi added RMB 420 million and 420 cars.", "420"),  # bare repeat stays checked
    ],
)
def test_wrong_scale_and_invented_figures_still_fail(text, figure):
    ev = evidence(item("广西追加4.2亿元购车补贴", language="zh-CN"))
    outcome = validate(EVENT_SUMMARY_V4, summary(text), ev)
    assert any(f"figures not in evidence ['{figure}']" in error for error in outcome.errors)


@pytest.mark.parametrize(
    "text",
    [
        "据TechCrunch报道，Anthropic承诺在七年内投入116亿美元，可能增长到约200亿美元。",
        "Akamai获得价值11.6 billion美元的合同。",
    ],
)
def test_exact_english_scale_equivalents_in_chinese_are_allowed_figures(text):
    # Phase 7.1 Checkpoint E: $11.6 billion written as 116亿美元 was rejected although equal.
    ev = evidence(item("Anthropic to pay Akamai $11.6 billion; could grow to about $20 billion"))
    outcome = validate(EVENT_SUMMARY_XLANG_V1, summary(text), ev, "zh-CN")
    assert outcome.valid, outcome.errors


@pytest.mark.parametrize(
    ("text", "figure"),
    [
        ("Anthropic投入1160亿美元。", "1160"),  # wrong scale
        ("Anthropic投入117亿美元。", "117"),  # fabricated
        ("Anthropic投入116美元。", "116"),  # missing scale
    ],
)
def test_wrong_english_to_chinese_scales_still_fail(text, figure):
    ev = evidence(item("Anthropic to pay Akamai $11.6 billion over seven years"))
    outcome = validate(EVENT_SUMMARY_XLANG_V1, summary(text), ev, "zh-CN")
    assert any(f"figures not in evidence ['{figure}']" in error for error in outcome.errors)


def test_a_wrong_scale_reusing_the_evidence_digits_is_left_to_manual_audit():
    # Known limit of the literal figure check: "11.6" appears in the evidence, so 11.6亿 (a
    # hundredfold error) is not caught deterministically; the manual audit checks every scale.
    ev = evidence(item("Anthropic to pay Akamai $11.6 billion over seven years"))
    assert validate(EVENT_SUMMARY_XLANG_V1, summary("Anthropic投入11.6亿美元。"), ev, "zh-CN").valid


def test_scale_equivalence_needs_a_chinese_scale_in_the_evidence():
    ev = evidence(item("Studio A raised $420,000,000"))
    assert validate(EVENT_SUMMARY_V4, summary("Studio A raised $420,000,000."), ev).valid
    outcome = validate(EVENT_SUMMARY_V4, summary("Studio A raised $420 million."), ev)
    assert any("figures not in evidence ['420']" in error for error in outcome.errors)


XLANG = EVENT_SUMMARY_XLANG_V1


@pytest.mark.parametrize(
    "text",
    [
        "A report said 日本电产 will book losses, after which its stock price fell 17%.",
        "日本电产's stock price fell 17% following the report.",
        "Guangxi added funds; as a result, sales rose.",
        "The loss led to a management change at 日本电产.",
        "Studio A cut 17 jobs because of the delay.",
        "据报道，工作室A宣布裁员，随后股价下跌17%。",
        "工作室A宣布延期，因此裁员17人。",
    ],
)
def test_cross_language_connectors_need_a_signal_in_the_evidence(text):
    # Phase 7.1 Checkpoint E: "following the report" and "after which" joined juxtaposed facts.
    ev = evidence(item("报道称将计提损失 工作室A股价大跌17%", language="zh-CN"))
    locale = "zh-CN" if "据报道" in text or "宣布延期" in text else "en"
    outcome = validate(XLANG, summary(text), ev, locale)
    assert any("unsupported" in error and "connector" in error for error in outcome.errors)
    # Primary-locale validation is unchanged (Checkpoint D artifacts were audited under it).
    assert not any(
        "connector" in e for e in validate(EVENT_SUMMARY_V4, summary(text), ev, locale).errors
    )


def test_cross_language_connectors_pass_with_a_same_kind_evidence_signal():
    sequence = evidence(item("工作室A宣布裁员，随后股价下跌17%", language="zh-CN"))
    text = "Studio A announced layoffs; subsequently its stock price fell 17%."
    assert validate(XLANG, summary(text), sequence).valid
    causal = evidence(item("工作室A因延期导致裁员17人", language="zh-CN"))
    assert validate(XLANG, summary("The delay led to 17 layoffs at Studio A."), causal).valid
    # A sequence signal does not license a causal connector.
    outcome = validate(XLANG, summary("As a result, its stock price fell 17%."), sequence)
    assert any("unsupported causal connector" in error for error in outcome.errors)


@pytest.mark.parametrize(
    "text",
    [
        "吉宏股份 (Jihong Co.) said it has no such cooperation.",
        "Nidec (日本电产) plans to book a loss.",
        "The studio was founded by Konrad Tomaszkiewicz (康拉德·托马什凯维奇).",
        "吉宏股份（Jihong Co.）said it has no such cooperation.",
    ],
)
def test_cross_language_name_glosses_need_both_forms_in_the_evidence(text):
    # Phase 7.1 Checkpoint E: names were glossed or translated with forms no report gives.
    ev = evidence(
        item("吉宏股份：未有合作", language="zh-CN"),
        item("日本电产将计提损失，康拉德·托马什凯维奇创立工作室", language="zh-CN"),
    )
    outcome = validate(XLANG, summary(text), ev)
    assert any("name gloss not in evidence" in error for error in outcome.errors)


def test_figures_quotes_and_given_glosses_are_not_name_glosses():
    ev = evidence(
        item("广西追加4.2亿元购车补贴；腾讯（Tencent）发布“不加班”", language="zh-CN"),
    )
    for text in (
        "Guangxi added RMB 420 million (4.2亿元) in subsidies.",
        "腾讯 (Tencent) described '不加班' (no overtime).",
        "The subsidy covers the vehicle license (行驶证) date.",
    ):
        outcome = validate(XLANG, summary(text), ev)
        assert outcome.valid, outcome.errors


def test_publication_dates_and_small_counts_are_allowed_figures():
    ev = evidence(item("Studio A announces game"))
    text = "On March 1, 2031, Source A reported that Studio A announced 2 games."
    assert validate(EVENT_SUMMARY_V4, summary(text), ev).valid


def test_output_must_be_in_the_requested_locale():
    ev = evidence(item("Studio A announces game"))
    assert not validate(EVENT_SUMMARY_V4, summary("Source A reports the game."), ev, "zh-CN").valid
    assert not validate(EVENT_SUMMARY_V4, summary("工作室A公布新作。"), ev, "en").valid
    mixed = summary("According to 游民星空, Studio A announced a game.")
    assert validate(EVENT_SUMMARY_V4, mixed, ev, "en").valid


def test_why_it_matters_allows_an_explicit_insufficient_answer_only():
    ev = evidence(item("Studio A announces game"))
    empty = {"points": [], "insufficient_evidence": True}
    assert validate(EVENT_WHY_IT_MATTERS_V6, empty, ev).valid
    assert not validate(
        EVENT_WHY_IT_MATTERS_V6, {**empty, "insufficient_evidence": False}, ev
    ).valid
    bad_context = {
        "points": [{"text": "Source A says it matters.", "context": "stock", "evidence": ["C1"]}],
        "insufficient_evidence": False,
    }
    assert not validate(EVENT_WHY_IT_MATTERS_V6, bad_context, ev).valid


def test_pair_judgement_maps_both_events_refs():
    a, b = (
        evidence(item("Bungie restores Destiny content")),
        evidence(item("Bungie官宣《命运2》复活", language="zh-CN", source="GameLook")),
    )
    mapping = {**ref_map(a, "A"), **ref_map(b, "B")}
    data = {
        "same_event": "yes",
        "reason": "Both report Bungie's plan.",
        "evidence_a": ["A1"],
        "evidence_b": ["B1"],
    }
    outcome = validate_output(EVENT_PAIR_V1, data, mapping, a.items + b.items)
    assert outcome.valid
    assert outcome.output["evidence_b"] == [b.items[0].content_id]
    data["evidence_b"] = ["C1"]
    assert not validate_output(EVENT_PAIR_V1, data, mapping, a.items + b.items).valid


# --- suggestion rules ----------------------------------------------------------------------


def test_mentions_are_grounded_by_word_or_cjk_substring():
    assert mention_grounded("Rust", "A new Rust update ships")
    assert not mention_grounded("Rust", "Players trust the frustrating patch")
    assert mention_grounded("吉宏股份", "吉宏股份：跨境社交电商业务")
    assert mention_grounded("Black Myth: Wukong", "black myth：wukong sells")
    assert not mention_grounded("NVIDIA", "Studio A announces game")


def test_distinctive_names_skip_generic_and_lowercase_words():
    names = distinctive_names("Bungie says it's not done with Destiny. The new PS5 Game Trailer")
    assert {"bungie", "destiny", "ps5"} <= names
    assert not names & {"the", "new", "game", "trailer", "says"}


class FakeCatalog:
    def __init__(self, subjects=None, companies=None):
        self._subjects, self._companies = subjects or {}, companies or {}

    def lookup(self, domain, name):
        return self._subjects.get(name, []), self._companies.get(name, [])


def test_entity_suggestions_resolve_only_through_the_catalog():
    ev = evidence(item("NVIDIA and Studio A sign deal for Warframe", "Rust is not mentioned"))
    cid = ev.items[0].content_id
    subject, company, other = uuid4(), uuid4(), uuid4()
    catalog = FakeCatalog(
        subjects={"Warframe": [subject], "Studio A": [uuid4(), uuid4()]},
        companies={"NVIDIA": [company], "Linked Co": [other]},
    )
    output = {
        "mentions": [
            {"name": name, "kind": kind, "evidence": [cid]}
            for name, kind in (
                ("Warframe", "game"),
                ("NVIDIA", "company"),
                ("Studio A", "company"),
                ("Fund B", "organization"),
                ("Unwritten Corp", "company"),
            )
        ]
    }
    ev_items = (replace(ev.items[0], excerpt="Fund B joins"),)
    rows, skipped = entity_suggestions(uuid4(), "gaming", output, ev_items, catalog, (set(), set()))
    by_text = {(row["candidate_text"], row["suggestion_type"]): row for row in rows}
    assert by_text[("Warframe", "event_subject_candidate")]["candidate_id"] == subject
    assert by_text[("NVIDIA", "event_company_candidate")]["candidate_id"] == company
    ambiguous = by_text[("Studio A", "event_subject_candidate")]
    assert ambiguous["candidate_id"] is None
    assert len(ambiguous["metadata"]["ambiguous_candidates"]) == 2
    assert by_text[("Fund B", "new_entity_candidate")]["candidate_id"] is None
    assert skipped == {"ungrounded": 1}
    rows, skipped = entity_suggestions(
        uuid4(), "gaming", output, ev_items, catalog, ({subject}, {company})
    )
    assert skipped["already_linked"] == 2
