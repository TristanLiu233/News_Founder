"""Event Summary and Why It Matters prompts (displayable, locale-specific artifacts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_intelligence.ai.prompts import (
    GROUNDING_RULES,
    REFS,
    STRING,
    PromptSpec,
    object_schema,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SummaryPoint(_Strict):
    text: str = Field(min_length=1, max_length=400)
    evidence: list[str] = Field(min_length=1, max_length=10)


class EventSummaryOutput(_Strict):
    points: list[SummaryPoint] = Field(min_length=1, max_length=4)
    insufficient_evidence: bool


class ContextPoint(_Strict):
    text: str = Field(min_length=1, max_length=400)
    context: Literal["business", "technology", "industry", "product", "regulatory"]
    evidence: list[str] = Field(min_length=1, max_length=10)


class WhyItMattersOutput(_Strict):
    points: list[ContextPoint] = Field(max_length=3)
    insufficient_evidence: bool

    @model_validator(mode="after")
    def empty_only_when_insufficient(self):
        if not self.points and not self.insufficient_evidence:
            raise ValueError("no points without insufficient_evidence")
        return self


# v2 (Phase 7.1 live audit): v1 rewrote "计划在15个交易日后的3个月内" as
# "将在公告披露15个交易日后的3个月内…实施", adding a time anchor and certainty that the report
# did not state.
EVENT_SUMMARY_V2 = PromptSpec(
    version="event_summary_v2",
    artifact_type="event_summary",
    system=f"""\
You summarize one news Event for an industry intelligence product. An Event is a group of \
reports about one real-world occurrence; the input lists every report with a ref, source, \
language, publication time, title, and (sometimes) a short excerpt.

Write 1 to 4 short, factual points in {{language}} that say what happened, who is involved, and \
the core facts. Merge facts shared by several reports into one point instead of repeating them. \
Do not write a title or a headline and do not restate the whole headline as a point. Set \
insufficient_evidence to true when the reports are too thin for more than a restatement of \
their titles, and still write the single point they support.

Keep each report's own framing: a plan, proposal, intention, or expectation stays one (for \
example 计划/拟/plans to), never a certain or completed action. Do not add reference points, \
time anchors, conditions, or qualifiers that the reports do not state (for example what a \
period is counted from), even when they are usual for this kind of news.

{GROUNDING_RULES}""",
    schema=object_schema(
        {
            "points": {
                "type": "array",
                "items": object_schema({"text": STRING, "evidence": REFS}),
            },
            "insufficient_evidence": {"type": "boolean"},
        }
    ),
    output_model=EventSummaryOutput,
)

EVENT_WHY_IT_MATTERS_V1 = PromptSpec(
    version="event_why_it_matters_v1",
    artifact_type="event_why_it_matters",
    system=f"""\
You explain why one news Event matters, for an industry intelligence product. The input lists \
every report about the Event with a ref, source, language, publication time, title, and \
(sometimes) a short excerpt.

Write 0 to 3 short points in {{language}} that describe the business, technology, industry, \
product, or regulatory context of the Event, and label each with that context. Each point must \
be descriptive and rest on what the reports themselves state (for example a report that says a \
product enters a new market, a rule applies to an industry, or a deal changes ownership). It \
must not predict outcomes, judge which company is better, or comment on share prices. If the \
reports only state the bare fact without context, return no points and set \
insufficient_evidence to true; that is the expected answer for most short news-flash reports.

{GROUNDING_RULES}""",
    schema=object_schema(
        {
            "points": {
                "type": "array",
                "items": object_schema(
                    {
                        "text": STRING,
                        "context": {
                            "type": "string",
                            "enum": ["business", "technology", "industry", "product", "regulatory"],
                        },
                        "evidence": REFS,
                    }
                ),
            },
            "insufficient_evidence": {"type": "boolean"},
        }
    ),
    output_model=WhyItMattersOutput,
)

INSIGHT_PROMPTS = {spec.artifact_type: spec for spec in (EVENT_SUMMARY_V2, EVENT_WHY_IT_MATTERS_V1)}
