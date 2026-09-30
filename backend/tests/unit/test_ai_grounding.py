"""Product-1.1: deterministic evidence quality, task eligibility, and grounding guards.

Fixture A (headline-only evidence gaining plan / interpretation framing) and Fixture B (a
truncated excerpt completed by inference) come from the Product-1 live audit; they are general
failure modes, so the fixtures use them as examples, never as special cases.
"""

from datetime import UTC, datetime

import pytest

from industry_intelligence.ai.prompts.event_insights import (
    EVENT_SUMMARY_V4,
    EVENT_SUMMARY_V5,
    EVENT_WHY_IT_MATTERS_V6,
    EVENT_WHY_IT_MATTERS_V7,
    INSIGHT_PROMPTS,
)
from industry_intelligence.services.ai_evidence import (
    EXCERPT_CHARS,
    EventEvidence,
    EvidenceItem,
    clip_excerpt,
    evidence_quality,
    excerpt_state,
    task_eligibility,
    validate_output,
)

AT = datetime(2031, 1, 1, tzinfo=UTC)
HEADLINE_A = "A股利好！又一批增持回购来了"
TITLE_B = "Monster Hunter Outlanders launches October 29"
EXCERPT_B = (
    "Free-to-play open-world survival game Monster Hunter Outlanders will launch for iOS via "
    "App Store and …"
)
COMPLETE = (
    "The studio said the expansion adds a new region, two story chapters, and cross-play "
    "between consoles and PC when it launches."
)
CJK_LONG = (
    "据报道，" + "该公司在发布会上介绍了新产品的技术参数与上市安排，并说明了供货计划。" * 5 + "…"
)


def item(title, excerpt=None, source="Source A", index=1, language="en"):
    return EvidenceItem(
        content_id=f"c{index}",
        source=source,
        language=language,
        published_at=AT,
        title=title,
        excerpt=excerpt,
        url=f"https://example.test/{index}",
    )


def evidence(*items):
    return EventEvidence(
        event_id="e",
        domain="finance",
        content_count=len(items),
        source_count=len({i.source for i in items}),
        items=items,
    )


def validate(spec, text, items, locale="en", context=None):
    point = {"text": text, "evidence": ["C1"]}
    if context:
        point["context"] = context
    data = {"points": [point], "insufficient_evidence": False}
    mapping = {f"C{index}": entry.content_id for index, entry in enumerate(items, 1)}
    return validate_output(spec, data, mapping, items, locale, len({i.source for i in items}))


# --- evidence quality -----------------------------------------------------------------------


def test_excerpt_states_use_explicit_markers_and_cut_tails_only():
    assert excerpt_state(None, "t") == ("none", "")
    assert excerpt_state(EXCERPT_B, TITLE_B)[0] == "truncated"
    assert excerpt_state("A studio expands into Europe. Read more", "t") == (
        "complete",
        "A studio expands into Europe.",
    )
    assert excerpt_state(COMPLETE, "t")[0] == "complete"
    # A complete sentence without a final full stop is not treated as cut.
    assert excerpt_state("Chipmakers see AI demand as an opportunity, but roadblocks remain", "t")[
        0
    ] == ("complete")
    for cut in ("The publisher confirmed the date and", "Players can join on PC, consoles,", "[…]"):
        assert excerpt_state(f"{COMPLETE[:60]} {cut}", "t")[0] == "truncated"
    # Too short, or only repeating the headline: no usable excerpt.
    assert excerpt_state("Short.", "t")[0] == "none"
    assert excerpt_state(TITLE_B, TITLE_B)[0] == "none"


def test_loader_clip_marks_its_own_cut():
    long = "x" * (EXCERPT_CHARS + 50)
    clipped = clip_excerpt(long)
    assert clipped.endswith("…") and len(clipped) <= EXCERPT_CHARS + 1
    assert clip_excerpt("short text") == "short text" and clip_excerpt(None) is None


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ((item(HEADLINE_A, language="zh-CN"),), "headline_only"),
        ((item(TITLE_B, EXCERPT_B),), "headline_plus_truncated_excerpt"),
        ((item("Studio ships an expansion", COMPLETE),), "headline_plus_excerpt"),
        (
            (item("工作室公布了新作的上市安排", CJK_LONG, language="zh-CN"),),
            "headline_plus_excerpt",
        ),
        (
            (item("Bank raises rates", source="A"), item("Bank raises rates", source="B", index=2)),
            "multi_source_headlines",
        ),
        (
            (
                item("Bank raises rates", EXCERPT_B, source="A"),
                item("Bank raises rates", source="B", index=2),
            ),
            "multi_source_headlines",
        ),
        (
            (
                item("Bank raises rates", COMPLETE, source="A"),
                item("Bank raises rates", source="B", index=2),
            ),
            "multi_source_with_excerpt",
        ),
        (
            (
                item("Bank raises rates", COMPLETE, source="A"),
                item("Bank raises rates", COMPLETE + " More.", source="B", index=2),
            ),
            "rich_evidence",
        ),
    ],
)
def test_evidence_quality_classes(items, expected):
    assert evidence_quality(evidence(*items))["class"] == expected


def test_task_eligibility_depends_on_evidence_quality():
    headline_only = task_eligibility(evidence_quality(evidence(item(HEADLINE_A))))
    assert headline_only["event_summary"] == (True, None)
    assert headline_only["event_why_it_matters"] == (False, "insufficient_evidence")
    truncated = task_eligibility(evidence_quality(evidence(item(TITLE_B, EXCERPT_B))))
    assert truncated["event_why_it_matters"] == (False, "insufficient_evidence")
    rich = task_eligibility(evidence_quality(evidence(item("Studio ships an expansion", COMPLETE))))
    assert rich == {"event_summary": (True, None), "event_why_it_matters": (True, None)}
    # A headline too short to state a fact supports nothing.
    bare = task_eligibility(evidence_quality(evidence(item("Dots: Always-on agents"))))
    assert bare["event_summary"] == (False, "headline_too_short")
    assert bare["event_why_it_matters"] == (False, "insufficient_evidence")


# --- prompts --------------------------------------------------------------------------------


def test_new_prompt_versions_replace_the_old_ones_which_stay_unchanged():
    assert [spec.version for spec in INSIGHT_PROMPTS.values()] == [
        "event_summary_v5",
        "event_why_it_matters_v7",
    ]
    assert (EVENT_SUMMARY_V4.version, EVENT_WHY_IT_MATTERS_V6.version) == (
        "event_summary_v4",
        "event_why_it_matters_v6",
    )
    assert not EVENT_SUMMARY_V4.grounding_guards and not EVENT_WHY_IT_MATTERS_V6.grounding_guards
    for spec in (EVENT_SUMMARY_V5, EVENT_WHY_IT_MATTERS_V7):
        assert spec.grounding_guards
        assert "Never complete a truncated" in spec.system
    assert "no excerpt" in EVENT_SUMMARY_V5.system


# --- Fixture A: headline-only evidence -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "据新浪财经报道，A股市场再迎一批公司增持回购计划，被视为利好消息。",
        "据新浪财经报道，又一批增持回购来了，这意味着市场信心回升。",
        "据新浪财经报道，又一批增持回购来了，预计将推动A股上涨。",
        "据新浪财经报道，多家公司为了稳定股价推出增持回购。",
    ],
)
def test_fixture_a_unsupported_framing_is_rejected(text):
    items = (item(HEADLINE_A, source="新浪财经", language="zh-CN"),)
    outcome = validate(EVENT_SUMMARY_V5, text, items, "zh-CN")
    assert not outcome.valid
    assert any("unsupported" in error for error in outcome.errors)


def test_fixture_a_passed_the_v4_validator_which_is_the_root_cause():
    items = (item(HEADLINE_A, source="新浪财经", language="zh-CN"),)
    text = "据新浪财经报道，A股市场再迎一批公司增持回购计划，被视为利好消息。"
    assert validate(EVENT_SUMMARY_V4, text, items, "zh-CN").valid


@pytest.mark.parametrize(
    "text",
    [
        "据新浪财经报道，A股又迎来一批增持回购。",
        "新浪财经报道称，A股又有一批增持回购，标题称其为利好。",
    ],
)
def test_fixture_a_conservative_paraphrase_passes(text):
    items = (item(HEADLINE_A, source="新浪财经", language="zh-CN"),)
    assert validate(EVENT_SUMMARY_V5, text, items, "zh-CN").valid


def test_plan_and_expectation_language_passes_when_the_evidence_states_it():
    items = (
        item(
            "某公司计划回购不超过1亿元股份",
            "公告称，公司预计在未来3个月内完成回购，此举旨在维护股东权益。",
            source="东方财富",
            language="zh-CN",
        ),
    )
    text = (
        "据东方财富报道，某公司计划回购不超过1亿元股份，预计在未来3个月内完成，旨在维护股东权益。"
    )
    assert validate(EVENT_SUMMARY_V5, text, items, "zh-CN").valid
    english = (item("Studio plans a sequel", "The studio said it expects to ship in 2031."),)
    assert validate(
        EVENT_SUMMARY_V5,
        "According to Source A, the studio plans a sequel and expects to ship in 2031.",
        english,
    ).valid


def test_english_interpretation_and_causal_framing_needs_evidence():
    items = (item("Bank raises its deposit rate", COMPLETE),)
    for text in (
        "According to Source A, the bank raised its deposit rate, seen as a sign of tightening.",
        "According to Source A, the bank raised its deposit rate as a result of inflation.",
        "According to Source A, the bank raised its deposit rate in order to attract savers.",
    ):
        assert not validate(EVENT_SUMMARY_V5, text, items).valid, text


# --- Fixture B: truncated excerpt -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "According to Gematsu, Monster Hunter Outlanders will launch for iOS via App Store, "
        "among other platforms.",
        "According to Gematsu, Monster Hunter Outlanders will launch for iOS via App Store and "
        "Google Play.",
        "According to Gematsu, Monster Hunter Outlanders will launch on iOS and other platforms.",
    ],
)
def test_fixture_b_completing_truncated_evidence_is_rejected(text):
    items = (item(TITLE_B, EXCERPT_B, source="Gematsu"),)
    for spec, context in ((EVENT_SUMMARY_V5, None), (EVENT_WHY_IT_MATTERS_V7, "product")):
        outcome = validate(spec, text, items, context=context)
        assert not outcome.valid, (spec.version, text)


def test_fixture_b_visible_text_only_passes():
    items = (item(TITLE_B, EXCERPT_B, source="Gematsu"),)
    for text in (
        "According to Gematsu, free-to-play open-world survival game Monster Hunter Outlanders "
        "launches October 29 and will launch for iOS via App Store.",
        "According to Gematsu, Monster Hunter Outlanders will launch for iOS via App Store on "
        "October 29.",
    ):
        assert validate(EVENT_SUMMARY_V5, text, items).valid, text


def test_list_extension_is_allowed_when_the_evidence_itself_says_so():
    items = (item("Game launches", "The game launches on PC, PlayStation 5, and other platforms."),)
    text = "According to Source A, the game launches on PC, PlayStation 5, and other platforms."
    assert validate(EVENT_SUMMARY_V5, text, items).valid
