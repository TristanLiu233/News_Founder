"""Prompts, input hashes, evidence validation guards, and suggestion rules (pure, no I/O)."""

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
    EVENT_SUMMARY_V2,
    EVENT_WHY_IT_MATTERS_V1,
    INSIGHT_PROMPTS,
)
from industry_intelligence.services.ai_enrichment import system_prompt
from industry_intelligence.services.ai_evidence import (
    EventEvidence,
    EvidenceItem,
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
ALL_PROMPTS = (EVENT_SUMMARY_V2, EVENT_WHY_IT_MATTERS_V1, EVENT_ENTITY_CANDIDATES_V1, EVENT_PAIR_V1)


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
    assert all(version.endswith(("_v1", "_v2")) for version in versions)
    assert set(INSIGHT_PROMPTS) == {"event_summary", "event_why_it_matters"}
    assert INSIGHT_PROMPTS["event_summary"].version == "event_summary_v2"


def test_summary_prompt_keeps_plan_framing_and_adds_no_anchors():
    # Phase 7.1 live audit: v1 turned a planned share reduction "15个交易日后" into a certain
    # action counted from the announcement date, which no report stated.
    system = system_prompt(EVENT_SUMMARY_V2, "zh-CN")
    assert "never a certain or completed action" in system
    assert "Do not add reference points, time anchors" in system
    assert GROUNDING_RULES in system


def test_summary_prompt_version_change_changes_the_input_hash():
    ev = evidence(item("Studio A plans a sale."))
    assert input_hash("event_summary_v1", "zh-CN", ev) != input_hash(
        EVENT_SUMMARY_V2.version, "zh-CN", ev
    )


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
    assert "Simplified Chinese" in system_prompt(EVENT_SUMMARY_V2, "zh-CN")
    assert "in English" in system_prompt(EVENT_SUMMARY_V2, "en")
    assert "{language}" not in system_prompt(EVENT_WHY_IT_MATTERS_V1, "zh-CN")


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
        EVENT_SUMMARY_V2, summary("According to Source A, Studio A raised $40 million."), ev
    )
    assert outcome.valid, outcome.errors
    assert outcome.output["points"][0]["evidence"] == [ev.items[0].content_id]


@pytest.mark.parametrize(
    "refs",
    [("C2",), ("C1", "C9"), ("A1",)],
)
def test_unknown_evidence_refs_fail(refs):
    ev = evidence(item("Studio A announces game"))
    outcome = validate(EVENT_SUMMARY_V2, summary("Source A reports the game.", refs), ev)
    assert not outcome.valid
    assert any("unknown evidence ref" in error for error in outcome.errors)


def test_foreign_content_id_cannot_be_cited():
    ev = evidence(item("Studio A announces game"))
    foreign = str(uuid4())
    outcome = validate(EVENT_SUMMARY_V2, summary("Source A reports the game.", (foreign,)), ev)
    assert not outcome.valid


def test_points_without_evidence_fail_schema_validation():
    ev = evidence(item("Studio A announces game"))
    outcome = validate(EVENT_SUMMARY_V2, summary("Source A reports the game.", ()), ev)
    assert not outcome.valid and outcome.errors[0].startswith("schema:")
    assert not validate(EVENT_SUMMARY_V2, {"points": [], "insufficient_evidence": True}, ev).valid
    assert not validate(EVENT_SUMMARY_V2, {"points": "x"}, ev).valid


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
    outcome = validate(EVENT_SUMMARY_V2, summary(text), ev, locale)
    assert any("prohibited" in error for error in outcome.errors)


@pytest.mark.parametrize(
    "text",
    ["Multiple reports say Studio A announced a game.", "多家媒体报道，工作室A公布新作。"],
)
def test_single_source_event_cannot_claim_multiple_reports(text):
    ev = evidence(item("Studio A announces game"))
    locale = "zh-CN" if "媒体" in text else "en"
    outcome = validate(EVENT_SUMMARY_V2, summary(text), ev, locale)
    assert any("single-source" in error for error in outcome.errors)
    two = evidence(item("Studio A announces game"), item("Studio A game", source="Source B"))
    assert validate(EVENT_SUMMARY_V2, summary(text), two, locale).valid


def test_figures_must_appear_in_the_evidence():
    ev = evidence(item("恩捷股份：拟11.5亿元收购湖北恩捷45%股权", language="zh-CN"))
    ok = validate(
        EVENT_SUMMARY_V2, summary("恩捷股份拟以11.5亿元收购湖北恩捷45%股权。"), ev, "zh-CN"
    )
    assert ok.valid, ok.errors
    wrong = validate(
        EVENT_SUMMARY_V2, summary("恩捷股份拟以12亿元收购湖北恩捷45%股权。"), ev, "zh-CN"
    )
    assert any("figures not in evidence ['12']" in error for error in wrong.errors)
    converted = validate(EVENT_SUMMARY_V2, summary("Enjie plans a RMB 1.15 billion deal."), ev)
    assert not converted.valid


def test_publication_dates_and_small_counts_are_allowed_figures():
    ev = evidence(item("Studio A announces game"))
    text = "On March 1, 2031, Source A reported that Studio A announced 2 games."
    assert validate(EVENT_SUMMARY_V2, summary(text), ev).valid


def test_output_must_be_in_the_requested_locale():
    ev = evidence(item("Studio A announces game"))
    assert not validate(EVENT_SUMMARY_V2, summary("Source A reports the game."), ev, "zh-CN").valid
    assert not validate(EVENT_SUMMARY_V2, summary("工作室A公布新作。"), ev, "en").valid
    mixed = summary("According to 游民星空, Studio A announced a game.")
    assert validate(EVENT_SUMMARY_V2, mixed, ev, "en").valid


def test_why_it_matters_allows_an_explicit_insufficient_answer_only():
    ev = evidence(item("Studio A announces game"))
    empty = {"points": [], "insufficient_evidence": True}
    assert validate(EVENT_WHY_IT_MATTERS_V1, empty, ev).valid
    assert not validate(
        EVENT_WHY_IT_MATTERS_V1, {**empty, "insufficient_evidence": False}, ev
    ).valid
    bad_context = {
        "points": [{"text": "Source A says it matters.", "context": "stock", "evidence": ["C1"]}],
        "insufficient_evidence": False,
    }
    assert not validate(EVENT_WHY_IT_MATTERS_V1, bad_context, ev).valid


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
