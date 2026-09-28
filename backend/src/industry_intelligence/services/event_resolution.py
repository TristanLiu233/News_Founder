"""Deterministic, conservative content → Event matching rules (pure functions, no I/O).

Principle: a wrong merge is worse than a duplicate Event. Two content items describe the same
Event only when, inside one domain and a bounded time window, they share *identity evidence*
(an identical normalized headline, an identical attribution prefix, or a shared deterministically
linked Subject/Company) **and** headline evidence, and no conflict rule fires. Title similarity
alone never merges anything; time alone never merges anything.

Headline handling:

* ``extract_headline`` keeps the bracketed headline of ``【headline】body`` feed titles.
* ``strip_boilerplate`` removes a short, documented list of feed prefixes (``市场消息：``,
  ``N连板``) that are not part of the reported fact.
* ``normalize_title`` applies NFKC, case folding, and punctuation/whitespace normalization; it
  keeps every word and number (decimal points inside numbers are preserved).

Similarity is the Dice coefficient over headline features: character bigrams for CJK runs and
word tokens for Latin/digit runs. It is only consulted after identity evidence is established.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

RESOLVER_VERSION = "event-resolver/2"

# Calibrated on the live 2026-09-27 corpus (see AGENTS.md Phase 6 Checkpoint C/E).
TIME_WINDOW = timedelta(hours=24)
MIN_EXACT_CHARS = 8
ATTRIBUTION_SIMILARITY = 0.6
ENTITY_SIMILARITY = 0.7

BOILERPLATE_PREFIXES = (
    re.compile(r"^(?:市场消息|快讯|突发)\s*[:：]\s*"),
    re.compile(r"^\d+连板\s*"),
)
ATTRIBUTION = re.compile(r"^([^:：]{2,24})[:：]\s*(\S.*)$")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
CJK = re.compile(r"[㐀-鿿豈-﫿]")
FEATURE_RUNS = re.compile(r"[㐀-鿿豈-﫿]+|[^\W_]+(?:\.\d+)?", re.UNICODE)
EN_STOPWORDS = frozenset(
    "a an the and or of to in on for at by with from as is are was were be its it this that "
    "after over into".split()
)

EVENT_TYPES = (
    "earnings",
    "product_launch",
    "acquisition",
    "funding",
    "regulatory",
    "leadership",
    "partnership",
    "security_incident",
    "game_release",
    "general",
)

_TYPE_RULES = {
    "earnings": (
        r"\b(quarterly results|reports? earnings|earnings (beat|miss|report)|"
        r"(q[1-4]|first|second|third|fourth)[- ]quarter (results|earnings|profit|revenue))\b",
        r"财报|业绩预告|业绩快报|季度报告|半年报|年报",
    ),
    "acquisition": (
        r"\b(acquires?|acquired|acquisition|takeover|buyout|merger)\b",
        r"收购|并购|吸收合并",
    ),
    "funding": (
        r"\b(funding round|series [a-f] round|raises? [\d.]+ ?(million|billion|m|bn))\b",
        r"[a-f天使]+轮融资|轮融资",
    ),
    "regulatory": (
        r"\b(regulators?|regulatory|court|verdict|lawsuit|sues|sued|antitrust|ruling|"
        r"sanctions?|probe)\b",
        r"证监会|监管|处罚|罚款|立案|反垄断|法院|判决|诉讼|注册批复",
    ),
    "partnership": (
        r"\b(partnership|partners with|teams up|strategic alliance)\b",
        r"合作|携手",
    ),
    "security_incident": (
        r"\b(hack|hacked|hackers|data breach|cyberattack|ransomware|data leak)\b",
        r"黑客|数据泄露|网络攻击|勒索软件",
    ),
    "product_launch": (
        r"\b(launch|launches|launched|unveils?|unveiled)\b",
        r"开启预售|正式发布|发布会|推出",
    ),
}
_LEADERSHIP_ROLE = (
    r"\b(ceo|cfo|coo|chairman|chairwoman|chief executive|president)\b",
    r"董事长|总经理|首席执行官|总裁|行长",
)
_LEADERSHIP_ACTION = (
    r"\b(appoints?|appointed|names|named|steps? down|resigns?|resigned|no longer|replaces?|"
    r"replaced|successor)\b",
    r"辞职|辞去|代行|聘任|任命|离任|接任",
)
_GAME_RELEASE = (
    r"\b(release date|releases?|released|launch(es|ed)?|delay(s|ed)?|early access)\b",
    r"上线|公测|发售|定档|延期",
)
# A keyword does not count when a negation cue precedes it inside the same clause ("未有…合作",
# "denies merger talks"). Cues are whole negating words or phrases, never a bare character
# that also forms ordinary words (未来, 未披露, 未成年, 无人机, 不断), so ambiguous wording keeps
# its match.
# Applied on normalized text, where "doesn't" reads "doesn t".
_NEGATION = (
    r"\b(not|never|nor|cannot|deny|denies|denied|rules? out|ruled out|[a-z]+n t)\b",
    r"未有|未曾|未与|未就|并未|尚未|从未|没有|并无|暂无|无意|不会|不再|不涉及|不存在|不参与|"
    r"否认|辟谣",
)
_CLAUSE_BREAK = re.compile(r"[,;!?，。；！？:：]|\.(?!\d)")


def _nfkc(value):
    return unicodedata.normalize("NFKC", value or "")


def extract_headline(title):
    """The bracketed headline of a ``【headline】body`` title, else the whole title.

    Brackets are matched by depth, so a nested ``【…】`` inside the headline is kept whole; an
    unbalanced or out-of-range bracket leaves the whole title.
    """

    title = title or ""
    stripped = title.lstrip()
    if stripped.startswith("【"):
        depth = 0
        for index, char in enumerate(stripped):
            depth += {"【": 1, "】": -1}.get(char, 0)
            if depth == 0:
                headline = stripped[1:index].strip()
                if 2 <= len(headline) <= 300:
                    return headline
                break
    return title.strip()


def strip_boilerplate(headline):
    value = _nfkc(headline).strip()
    for pattern in BOILERPLATE_PREFIXES:
        value = pattern.sub("", value, count=1)
    return value.strip()


def normalize_title(value):
    """NFKC, casefold, punctuation → space (decimal points in numbers kept), collapsed spaces."""

    value = _nfkc(value).casefold()
    value = re.sub(r"(?<=\d),(?=\d{3}\b)", "", value)
    value = re.sub(r"(?<=\d)\.(?=\d)", "\x00", value)
    value = "".join(
        " " if unicodedata.category(char)[0] in "PSZC" and char != "\x00" else char
        for char in value
    )
    return re.sub(r"\s+", " ", value.replace("\x00", ".")).strip()


def headline_features(normalized):
    """Character bigrams of CJK runs and word tokens of Latin/digit runs."""

    features = set()
    for run in FEATURE_RUNS.findall(normalized):
        if CJK.match(run):
            if len(run) == 1:
                features.add(run)
            features.update(run[index : index + 2] for index in range(len(run) - 1))
        elif run not in EN_STOPWORDS:
            features.add(run)
    return features


def dice(left, right):
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def numbers(value):
    return set(NUMBER.findall(_nfkc(value)))


def split_attribution(headline):
    """(prefix, statement) for ``主体：陈述`` style headlines, else (None, headline).

    Only prefixes containing CJK characters count: the ``公司名：`` announcement convention names
    the issuer or speaker, while English colon prefixes are usually column labels
    ("Prediction:", "Exclusive:") rather than identities.
    """

    match = ATTRIBUTION.match(_nfkc(headline).strip())
    if not match:
        return None, headline
    prefix = normalize_title(match.group(1))
    if not prefix or not CJK.search(prefix):
        return None, headline
    return prefix, match.group(2)


def _any(patterns, text):
    english, chinese = patterns
    return bool(re.search(english, text) or re.search(chinese, text))


def _clauses(headline):
    """Normalized clauses of a headline, split at clause punctuation (not at decimal points)."""

    raw = _nfkc(headline).replace("’", "'")
    return [clause for part in _CLAUSE_BREAK.split(raw) if (clause := normalize_title(part))]


def _asserted(patterns, clauses):
    """True when some keyword match has no negation cue before it in its clause."""

    for clause in clauses:
        for pattern in patterns:
            for match in re.finditer(pattern, clause):
                if not _any(_NEGATION, clause[: match.start()]):
                    return True
    return False


def classify_event_type(headline, domain=None):
    """A type only when exactly one keyword family is asserted; otherwise ``general``.

    A keyword preceded by a negation cue in its own clause is not asserted, so a denial
    ("…未有与某品牌开展合作", "denies merger talks") is not typed as the denied event.
    """

    clauses = _clauses(headline)
    text = " ".join(clauses)
    matched = {name for name, patterns in _TYPE_RULES.items() if _asserted(patterns, clauses)}
    if _any(_LEADERSHIP_ROLE, text) and _asserted(_LEADERSHIP_ACTION, clauses):
        matched.add("leadership")
    if str(domain) == "gaming" and _asserted(_GAME_RELEASE, clauses):
        matched.discard("product_launch")
        matched.add("game_release")
    return matched.pop() if len(matched) == 1 else "general"


@dataclass(frozen=True)
class ContentFeatures:
    """Everything the matcher needs from one content item and its deterministic links."""

    content_id: UUID
    source_id: UUID
    domain: str
    effective_at: datetime
    title: str
    full_text: str
    headline: str
    normalized: str
    attribution: str | None
    statement_features: frozenset
    features: frozenset
    headline_numbers: frozenset
    event_type: str
    subject_ids: frozenset = field(default_factory=frozenset)
    company_ids: frozenset = field(default_factory=frozenset)


def content_features(
    content_id,
    source_id,
    domain,
    effective_at,
    title,
    description=None,
    subject_ids=(),
    company_ids=(),
):
    headline = extract_headline(title)
    core = strip_boilerplate(headline)
    attribution, statement = split_attribution(core)
    normalized = normalize_title(core)
    return ContentFeatures(
        content_id=content_id,
        source_id=source_id,
        domain=str(domain),
        effective_at=effective_at,
        title=title,
        full_text=_nfkc(f"{title}\n{description or ''}"),
        headline=headline,
        normalized=normalized,
        attribution=attribution,
        statement_features=frozenset(headline_features(normalize_title(statement))),
        features=frozenset(headline_features(normalized)),
        headline_numbers=frozenset(numbers(core)),
        event_type=classify_event_type(core, domain),
        subject_ids=frozenset(subject_ids),
        company_ids=frozenset(company_ids),
    )


@dataclass(frozen=True)
class PairDecision:
    """Outcome of comparing one content item with one Event member."""

    matched: bool
    reason: str
    strategy: str | None = None
    confidence: str | None = None
    similarity: float = 0.0
    time_delta_seconds: int = 0
    shared_subject_ids: tuple = ()
    shared_company_ids: tuple = ()

    @property
    def conflict(self):
        return self.reason.startswith("conflict:")


def _numbers_compatible(left, right):
    if not left.headline_numbers or not right.headline_numbers:
        return True
    in_right = all(number in numbers(right.full_text) for number in left.headline_numbers)
    in_left = all(number in numbers(left.full_text) for number in right.headline_numbers)
    return in_right or in_left


def compare(candidate, member, window=TIME_WINDOW):
    """Decide whether ``candidate`` and an existing Event ``member`` report the same Event."""

    delta = int(abs((candidate.effective_at - member.effective_at).total_seconds()))
    # Similarity is recorded for every decision (diagnostics) but never decides on its own.
    similarity = round(dice(candidate.features, member.features), 3)
    base = {"time_delta_seconds": delta, "similarity": similarity}
    if candidate.domain != member.domain:
        return PairDecision(False, "conflict:domain", **base)
    if delta > window.total_seconds():
        return PairDecision(False, "outside_window", **base)
    if (
        candidate.event_type != "general"
        and member.event_type != "general"
        and candidate.event_type != member.event_type
    ):
        return PairDecision(False, "conflict:event_type", **base)
    if (
        candidate.subject_ids
        and member.subject_ids
        and not (candidate.subject_ids & member.subject_ids)
    ):
        return PairDecision(False, "conflict:subjects", **base)
    if (
        candidate.company_ids
        and member.company_ids
        and not (candidate.company_ids & member.company_ids)
    ):
        return PairDecision(False, "conflict:companies", **base)
    if candidate.attribution and member.attribution and candidate.attribution != member.attribution:
        return PairDecision(False, "conflict:attribution", **base)
    if not _numbers_compatible(candidate, member):
        return PairDecision(False, "conflict:numbers", **base)

    if (
        candidate.normalized == member.normalized
        and len(candidate.normalized.replace(" ", "")) >= MIN_EXACT_CHARS
    ):
        return PairDecision(True, "exact_headline", "exact_headline", "exact", **base)
    if candidate.source_id == member.source_id:
        # Two distinct items from one source are distinct reports unless the headline is exact.
        return PairDecision(False, "same_source", **base)
    if candidate.attribution and candidate.attribution == member.attribution:
        statement = round(dice(candidate.statement_features, member.statement_features), 3)
        base["similarity"] = statement
        if statement >= ATTRIBUTION_SIMILARITY:
            return PairDecision(
                True, "attribution_headline", "attribution_headline", "strong", **base
            )
        return PairDecision(False, "attribution_below_threshold", **base)
    shared_subjects = tuple(sorted(candidate.subject_ids & member.subject_ids, key=str))
    shared_companies = tuple(sorted(candidate.company_ids & member.company_ids, key=str))
    if shared_subjects or shared_companies:
        entity = {
            "shared_subject_ids": shared_subjects,
            "shared_company_ids": shared_companies,
        }
        if similarity >= ENTITY_SIMILARITY:
            return PairDecision(
                True, "shared_entity_headline", "shared_entity_headline", "strong", **base, **entity
            )
        return PairDecision(False, "entity_below_threshold", **base, **entity)
    return PairDecision(False, "no_identity_evidence", **base)


def match_event(candidate, members, window=TIME_WINDOW):
    """((decision, member), []) for the best match, or (None, rejected decisions).

    The candidate joins only if it matches at least one member and conflicts with none of the
    Event's members inside the window. Ties keep the earliest member (members arrive ordered).
    """

    decisions = [(compare(candidate, member, window), member) for member in members]
    conflicts = [pair for pair in decisions if pair[0].conflict]
    if conflicts:
        return None, conflicts
    matched = [pair for pair in decisions if pair[0].matched]
    if not matched:
        return None, decisions
    return max(matched, key=lambda pair: (pair[0].confidence == "exact", pair[0].similarity)), []


def has_boilerplate(headline):
    return strip_boilerplate(headline) != _nfkc(headline).strip()


def representative_key(title, effective_at, source_name, content_id):
    """Canonical-title order: a headline without feed boilerplate first, then the earliest
    publication, then source name, then content ID. The title itself stays verbatim."""

    return (has_boilerplate(extract_headline(title)), effective_at, source_name, str(content_id))


def aggregate_event_type(types):
    specific = {value for value in types if value != "general"}
    return specific.pop() if len(specific) == 1 else "general"
