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


# v4 (Phase 7.1 live audit): v1 rewrote "计划在15个交易日后的3个月内" as
# "将在公告披露15个交易日后的3个月内…实施", adding a time anchor and certainty that the report
# did not state. v2 later added "当日" to a reported share-price move whose evidence gave
# no such time qualifier. v3 then connected juxtaposed facts with "消息传出后", another
# sequence/causation not stated by the report.
EVENT_SUMMARY_V4 = PromptSpec(
    version="event_summary_v4",
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
period is counted from, or 当日/on the day), even when they are usual for this kind of news. \
Do not infer a temporal or causal relationship merely because facts appear together in a \
report. Keep juxtaposed facts separate. Never add connecting phrases such as after the news, \
following the report, as a result, 消息传出后, 受此影响, or 因此 unless the report itself \
explicitly states that connection.

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

# v6 (Phase 7.1 live audit): v1 turned juxtaposed facts into causal claims ("受此影响")
# and linked a management change to an investigation without the report stating either link.
# v2 silently changed the evidence's game name "Arc Raider" to "Arc Raiders". v3 added
# "in an interview" when the evidence did not identify the communication format. v4 combined
# a report's publication year with "September" in its excerpt as an unsupported event date. v5
# added "following earlier cuts" where the report stated cuts but no such sequence.
EVENT_WHY_IT_MATTERS_V6 = PromptSpec(
    version="event_why_it_matters_v6",
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

Do not infer causation, motivation, or a relationship between facts merely because they appear \
in the same report. Use causal language such as because, led to, due to, as a result, 受此影响, \
因此, or 与…有关 only when the report explicitly states that connection. Preserve reported, \
planned, expected, and alleged framing exactly; never strengthen it into a confirmed fact.

Copy entity, person, company, organization, product, and title names exactly as written in the \
reports. Do not correct, normalize, expand, translate, or otherwise change a name based on \
outside knowledge, even when another spelling may be more familiar.

Do not invent how information was communicated. Never add interview, announcement, filing, \
press release, conference, post, 采访, 公告, 发布会, or 帖子 unless that exact \
communication format is stated in the reports.

The published_at field is when a report was published, not when the occurrence happened. Do \
not use it as an event date or combine it with a partial date in report text. Include a date, \
time anchor, period, or sequence only when the title or excerpt explicitly states the complete \
claim you write.
Do not add sequence words such as earlier, later, before, after, following, subsequently, 此前, \
随后, 之后, or 之前 unless that exact sequence is explicit in the title or excerpt.

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

INSIGHT_PROMPTS = {spec.artifact_type: spec for spec in (EVENT_SUMMARY_V4, EVENT_WHY_IT_MATTERS_V6)}
