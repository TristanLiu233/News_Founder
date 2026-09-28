"""Suggestion prompts: entity mentions of one Event and same-event judgement of an Event pair.

Their outputs are never displayed as facts. Entity mentions are checked against the evidence
text and resolved to catalog IDs deterministically; pair judgements only create pending
suggestions for a reviewed merge.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from industry_intelligence.ai.prompts import (
    GROUNDING_RULES,
    REFS,
    STRING,
    PromptSpec,
    object_schema,
)

ENTITY_KINDS = (
    "company",
    "organization",
    "product",
    "game",
    "person",
    "cryptocurrency",
    "other",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EntityMention(_Strict):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal[ENTITY_KINDS]
    evidence: list[str] = Field(min_length=1, max_length=10)


class EntityMentionsOutput(_Strict):
    mentions: list[EntityMention] = Field(max_length=12)


class EventPairOutput(_Strict):
    same_event: Literal["yes", "no", "uncertain"]
    reason: str = Field(min_length=1, max_length=600)
    evidence_a: list[str] = Field(max_length=10)
    evidence_b: list[str] = Field(max_length=10)


EVENT_ENTITY_CANDIDATES_V1 = PromptSpec(
    version="event_entity_candidates_v1",
    artifact_type="event_entity_candidates",
    system=f"""\
You list the named entities that the reports of one news Event are about. The input lists every \
report with a ref, source, language, publication time, title, and (sometimes) a short excerpt.

Return up to 12 mentions: companies, organizations, products, games, people, or \
cryptocurrencies that the reports explicitly name as taking part in or being the subject of the \
Event. Copy each name exactly as it is written in a report (same script, spelling, and case), \
choose its kind, and cite the refs where it appears. Skip generic words, places, and entities \
mentioned only in passing. Return an empty list when no entity is named.

{GROUNDING_RULES}""",
    schema=object_schema(
        {
            "mentions": {
                "type": "array",
                "items": object_schema(
                    {
                        "name": STRING,
                        "kind": {"type": "string", "enum": list(ENTITY_KINDS)},
                        "evidence": REFS,
                    }
                ),
            }
        }
    ),
    output_model=EntityMentionsOutput,
    localized=False,
)

EVENT_PAIR_V1 = PromptSpec(
    version="event_pair_same_event_v1",
    artifact_type="event_pair_judgement",
    system=f"""\
You compare two news Events, A and B, that may be written in different languages, and decide \
whether they report the same concrete real-world occurrence (the same announcement, deal, \
release, incident, or decision at the same time), not merely the same company or topic. The \
input lists the reports of each Event with refs A1, A2, … and B1, B2, ….

Answer "yes" only when the reports clearly describe the same occurrence and nothing in them \
conflicts (different amounts, dates, products, or parties mean "no"). Answer "uncertain" when \
the reports are compatible but too thin to be sure. Give a short reason in English and cite the \
refs of each Event that support it.

{GROUNDING_RULES}""",
    schema=object_schema(
        {
            "same_event": {"type": "string", "enum": ["yes", "no", "uncertain"]},
            "reason": STRING,
            "evidence_a": REFS,
            "evidence_b": REFS,
        }
    ),
    output_model=EventPairOutput,
    localized=False,
)
