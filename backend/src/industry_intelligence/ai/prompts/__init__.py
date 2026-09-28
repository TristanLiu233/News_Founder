"""Versioned prompts for Phase 7 AI enrichment.

Each ``PromptSpec`` owns everything that defines one kind of AI output: its ``version`` (stored
with every artifact and part of the input hash, so a prompt change never reuses old output), the
system prompt, the plain JSON schema sent to the provider, and the Pydantic model that validates
the response. Changing a prompt means adding a new version, not editing an old one in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class PromptSpec:
    version: str
    artifact_type: str
    system: str
    schema: dict[str, Any]
    output_model: type[BaseModel]
    # Whether the output is written in the requested UI locale.
    localized: bool = True


def object_schema(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or properties),
        "additionalProperties": False,
    }


STRING = {"type": "string"}
REFS = {"type": "array", "items": STRING}

LANGUAGE_NAMES = {"en": "English", "zh-CN": "Simplified Chinese"}

GROUNDING_RULES = """\
Rules that always apply:
- Use only the reports in the input. Do not browse, do not use background knowledge or memory, \
and do not add any fact that the reports do not state.
- Cite every statement with the refs (for example "C1") of the reports that support it. Use only \
refs that appear in the input.
- No predictions, forecasts, price or market-impact expectations, target prices, ratings, or \
investment advice (no buy/sell/hold language). Do not speculate about causes or consequences \
that the reports do not state.
- Copy every figure with exactly the digits the report uses (amounts, percentages, dates, \
counts). Do not convert units or currencies and do not round.
- Keep company, product, game, and person names exactly as the reports write them; do not \
invent translations of names.
- When the input has one report from one source, attribute facts to that report or source \
(for example "According to Gematsu, …"). Never describe a single report as multiple reports, \
several sources, or a consensus.
- When reports from different sources differ, state each version with its source; do not \
decide which one is right.
- If the reports do not support a statement, leave it out."""
