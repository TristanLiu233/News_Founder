"""Event Summary and Why It Matters prompts (displayable, locale-specific artifacts)."""

from __future__ import annotations

from dataclasses import replace
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


# --- Cross-language lineage (Phase 7.1 Checkpoint E) -------------------------------------------
# Output in a language other than the reports' uses its own prompt versions, so translation
# rules never change the primary-locale prompts above (whose artifacts were audited in
# Checkpoint D) and a change here never makes primary-locale artifacts stale. The xlang prompts
# are the primary text plus the rules below; they are chosen by ``insight_spec`` from the
# deterministic generation mode (``ai_evidence.generation_mode``).
#
# The rules come from the Checkpoint E live runs with Chinese reports and English output, which
# produced name glosses and translations (吉宏股份 (Jihong Co.), 日本电产 → Nidec, 广西日报 →
# Guangxi Daily), points copied in Chinese, added qualifiers and background knowledge,
# connectors between juxtaposed facts, places and ordinary words left untranslated, and
# over-long points. Connectors and parenthetical name glosses are also rejected by
# deterministic guards (``ai_evidence``), because the prompt alone did not prevent them.
OUTPUT_LANGUAGE_RULES = """\
Output language and length:
- Write every point in {language}, even when the reports are in another language: restate \
what the reports say in {language}; never copy a whole sentence or clause of a report in \
another language. Only names and short quoted phrases may stay in the report's language.
- Proper names stay exactly as the reports write them, in the reports' own script: names of \
companies, people, media outlets, organizations, games, products, and named programs. Do not \
translate, romanize, or explain such a name, and do not add a version in another language (for \
example in parentheses) unless a report itself gives it. This holds even when you know the \
official name in {language}: a company the reports call 腾讯 stays 腾讯 in English output \
(never Tencent, and never "Tencent (腾讯)"), a newspaper called 人民日报 stays 人民日报, a person \
called 张三 stays 张三, and a company the reports call Tencent stays Tencent in Chinese output \
(never 腾讯). An English sentence containing Chinese-script names, such as "According to \
新浪财经, 腾讯 said …", is the expected form.
- Everything that is not such a name is written in {language}: countries, regions, and cities \
(日本 is Japan), job titles (董事长 is chairman), descriptions of a person or company (创始人 \
is founder), and ordinary words (电动汽车 is electric vehicle).
- Before answering, check every name in your output: if it does not appear character for \
character in a report, replace it with the report's own form.
- Restating in another language must not change meaning: hedges (may, could, reportedly, \
about, 拟, 预计, 约, 有报道称) stay hedged, plans stay plans, negations stay negations, figures \
keep the report's digits and units with nothing added (never "$100万美元"), an attribution \
(外界认为, analysts said) stays attached to the same claim, and facts that a report only places \
side by side stay side by side (never join them with following, after, amid, as a result, or \
此后).
- Translate without adding: no qualifier the report does not use (不收费 is "free of charge", \
not "free of charge for now"), and never make a statement more specific than the report, for \
example by saying what an investment, payment, or bet went to when the report does not say. \
Background knowledge is not evidence, even when you are certain it is true.
- Keep each point under 300 characters."""

FINAL_LANGUAGE_CHECK = """\
Final check before answering: the input's output_language is {language}. Every point must be \
written in {language}; only names and short quoted phrases may be in another language. If a \
point is not in {language}, rewrite it in {language}."""


# AI-X1 (cross-language Summary v2). The Operations-1 AI catch-up (five single-source Chinese
# Events, English output) had 0 of 5 servable Summaries under ``event_summary_xlang_v1``: names
# of a person (内塔尼亚胡 → Netanyahu), a newspaper (《朝日新闻》 → Asahi Shimbun), and
# government bodies (斯洛伐克财政部, 总务省) were translated; a Chinese particle stayed in English
# prose (…的恰尼); "up to" was added to a reported 150 小时, and repeated on regeneration. v2
# makes government bodies explicitly names, states the figure-qualifier rule, and gives
# correct/incorrect pairs.
# The examples deliberately use other entities than the five audited Events, so their live
# re-validation tests the rule rather than memorised strings. These rules belong to the Summary
# only: ``event_why_it_matters_xlang_v1`` keeps ``OUTPUT_LANGUAGE_RULES`` byte for byte.
OUTPUT_LANGUAGE_RULES_SUMMARY_V2 = """\
Output language and length:
- Write every point in {language}, even when the reports are in another language. Translate \
the prose of the reports, not their proper names. Only proper names and short quoted phrases \
may stay in the report's language; never copy a sentence, a clause, or a grammatical word (for \
example 的, 了, 表示, 认为) of a report in another language.
- Proper names stay exactly as the reports write them, in the reports' own script, even when \
you know an official name in {language}. Proper names are the names of: people; companies, \
banks, and brands; media outlets and publications; named organizations and institutions, \
including government bodies such as ministries, agencies, bureaus, commissions, and courts; \
games, products, laws, and named programs. Keep 《》 around a name when the report uses them. \
Use a form in another script only when a report itself gives that form (a report writing \
特尔诺夫（Tel Nof） allows Tel Nof).
- Places are not such names and are written in {language}: countries, regions, provinces, \
prefectures, and cities (日本 is Japan). So are nationalities, job titles (总理 is prime \
minister, 社长 is president), descriptions of a person or company, and ordinary words.
- Never put a translation, romanization, or explanation next to a name, in parentheses or \
otherwise (never "腾讯 (Tencent)" or "Tencent (腾讯)") unless a report itself gives both forms.
- Correct and incorrect English output of Chinese reports:
  马克龙表示… → correct: 马克龙 said … — incorrect: Macron said …
  据《日本经济新闻》报道… → correct: According to 《日本经济新闻》, … — incorrect: According to \
Nikkei, …
  德国财政部下调了预期 → correct: 德国财政部 lowered its forecast — incorrect: Germany's Finance \
Ministry lowered its forecast
  国土交通省对该公司展开调查 → correct: 国土交通省 opened an investigation into the company — \
incorrect: the Ministry of Land, Infrastructure, Transport and Tourism opened …
  丰田汽车的社长表示 → correct: the president of 丰田汽车 said — incorrect: 丰田汽车的社长 said \
(的 is Chinese grammar, not part of a name) and incorrect: Toyota's president said
  In Chinese output of English reports the same holds the other way: a company the reports \
call Tencent stays Tencent, never 腾讯.
- Before answering, check every name in your output: if it does not appear character for \
character in a report, replace it with the report's own form.
- Figures keep exactly the report's digits, units, and precision with nothing added (never \
"$100万美元"), and exactly the report's qualifier or none. Add up to, at least, at most, \
more than, over, less than, under, about, around, approximately, roughly, or nearly only when \
the report states the same qualifier for that figure (约 is about, 最高 or 不超过 is up to, \
超过 or 逾 is more than, 不到 is less than). A report saying 续航30小时 gives "30 hours of \
battery life", never "up to 30 hours".
- Restating in another language must not change meaning: hedges (may, could, reportedly, \
拟, 预计, 有报道称) stay hedged, plans stay plans, negations stay negations, an attribution \
(外界认为, analysts said) stays attached to the same claim, and facts that a report only places \
side by side stay side by side (never join them with following, after, amid, as a result, or \
此后).
- Translate without adding: no qualifier the report does not use (不收费 is "free of charge", \
not "free of charge for now"), and never make a statement more specific than the report. \
Background knowledge is not evidence, even when you are certain it is true.
- Keep each point under 300 characters."""

FINAL_LANGUAGE_CHECK_SUMMARY_V2 = """\
Final check before answering: the input's output_language is {language}. Every point must be \
written in {language}; only proper names and short quoted phrases may be in another language, \
and no grammatical word of another language may remain. Every proper name must appear character \
for character in a report (never translated), and every figure must carry exactly the \
report's qualifier or none. Rewrite any point that breaks one of these rules."""


def _cross_language(spec, version, rules=OUTPUT_LANGUAGE_RULES, final=FINAL_LANGUAGE_CHECK):
    system = spec.system.replace(GROUNDING_RULES, f"{rules}\n\n{GROUNDING_RULES}\n\n{final}")
    assert system != spec.system
    return replace(spec, version=version, system=system, cross_language=True)


# Superseded by v2 (AI-X1); kept for its stored artifacts and tests, never selected.
EVENT_SUMMARY_XLANG_V1 = _cross_language(EVENT_SUMMARY_V4, "event_summary_xlang_v1")
EVENT_SUMMARY_XLANG_V2 = replace(
    _cross_language(
        EVENT_SUMMARY_V4,
        "event_summary_xlang_v2",
        OUTPUT_LANGUAGE_RULES_SUMMARY_V2,
        FINAL_LANGUAGE_CHECK_SUMMARY_V2,
    ),
    english_output_guards=True,
)
EVENT_WHY_IT_MATTERS_XLANG_V1 = _cross_language(
    EVENT_WHY_IT_MATTERS_V6, "event_why_it_matters_xlang_v1"
)

PRIMARY = "primary"
CROSS_LANGUAGE = "cross_language"
CROSS_LANGUAGE_PROMPTS = {
    spec.artifact_type: spec for spec in (EVENT_SUMMARY_XLANG_V2, EVENT_WHY_IT_MATTERS_XLANG_V1)
}


def insight_spec(artifact_type, mode):
    """The prompt of one insight type for a generation mode (``primary`` or ``cross_language``)."""

    prompts = CROSS_LANGUAGE_PROMPTS if mode == CROSS_LANGUAGE else INSIGHT_PROMPTS
    return prompts[artifact_type]
