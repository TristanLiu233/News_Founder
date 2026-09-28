"""Deterministic, conservative ContentItem → canonical Subject linking (pure rules, no I/O).

Principle: no link is better than a wrong link. A content item is linked to a Subject of its own
domain only on exact evidence, in this order:

1. ``same_article`` — the item *is* an article a ranking already observed: its publisher-native
   article ID equals a ranked-story identity of the same Source (e.g. IT之家 RSS news 1007244 and
   IT之家 日榜 ``data-news-id`` 1007244), or its normalized URL equals a Subject identity's
   canonical URL (e.g. a TechCrunch article submitted to Hacker News). Relation ``same_article``.
2. ``exchange_code`` — an A-share code written in market notation (``寒武纪(688256)``,
   ``688256.SH``, ``SH688256``) equal to a Subject's ``SH:``/``SZ:`` identity.
3. ``cashtag`` — ``$TICKER`` equal to a Subject's ``crypto:`` identity.
4. ``exact_name`` / ``exact_alias`` — a Subject title or alias that passes the eligibility rules
   below appears verbatim, is unique among the domain's eligible names, and is not part of a
   longer matched name.

Name eligibility (reasons are reported, never silently relaxed):

* ranked-story Subjects (``SubjectType.EVENT``) are never matched by name — their "name" is a
  headline; they link only through ``same_article``;
* names longer than ``MAX_NAME_CHARS`` or made of a 64-hex hashed key are skipped;
* names containing CJK need at least ``MIN_CJK_CHARS`` CJK characters; CJK names of creative
  works (games) must appear inside 《》 title marks, the Chinese convention for titles, so
  "英雄联盟" does not match "英雄联盟手游";
* a game name written inside 《》 whose whole extent is the name (``《Warframe》``,
  ``《Wardogs》`` for the Steam title "WARDOGS", ``《原神》``) matches case-insensitively even when
  it is a single word or short: the title marks delimit the complete title;
* Latin names are matched case-sensitively on alphanumeric word boundaries and need at least
  ``MIN_LATIN_CHARS`` letters/digits; a single-word Latin name must look distinctive (an inner
  capital such as ``PlayStation``, letters with digits, capitalized hyphenated parts such as
  ``Counter-Strike``, or a ``/``/``.`` inside such as ``owner/repo``), be a reviewed
  ``manual_seed`` alias, or be listed in ``REVIEWED_SINGLE_WORDS``. Common words that are
  Subject names ("Stellar", "Jupiter", "ANATOMY") therefore never match on their own.

Trademark signs (™ ® © ℠) are removed before NFKC normalization, so "Apex Legends™" is the name
"Apex Legends". Nothing here is fuzzy: no substring guessing across boundaries, edit distance,
embeddings, translation, or LLM extraction.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from industry_intelligence.domain.enums import SubjectType

LINKER_VERSION = "content-linker/2"

MIN_CJK_CHARS = 3
MIN_LATIN_CHARS = 4
MAX_NAME_CHARS = 60

# Reviewed single-word Latin names that are distinctive enough to match alone. Add entries only
# after checking the live Subject and content corpora (AGENTS.md, Phase 6.1 Checkpoint C).
REVIEWED_SINGLE_WORDS: frozenset[str] = frozenset()
# Aliases from the reviewed ``resolution.SEED_GROUPS`` (NVIDIA/英伟达, Microsoft/微软, ...).
REVIEWED_ALIAS_SOURCE = "manual_seed"

TITLE_MARKED_TYPES = frozenset({SubjectType.GAME})
# Reasons a game name may still match, but only inside 《》 title marks.
TITLE_MARK_ONLY_REASONS = frozenset({"single_word", "short_cjk", "short_latin"})
NAME_EXCLUDED_TYPES = frozenset({SubjectType.EVENT})

STRATEGY_CONFIDENCE = {
    "same_article": 1.0,
    "exchange_code": 1.0,
    "cashtag": 0.98,
    "exact_name": 0.97,
    "exact_alias": 0.96,
}
STRATEGY_ORDER = tuple(STRATEGY_CONFIDENCE)

CJK = re.compile(r"[㐀-鿿豈-﫿]")
LATIN_OR_DIGIT = re.compile(r"[A-Za-z0-9]")
HASH_KEY = re.compile(r"^[0-9a-f]{64}$")
INNER_CAPITAL = re.compile(r"[a-z][A-Z]")
QUOTES = str.maketrans({"’": "'", "‘": "'", "＇": "'"})
TRADEMARKS = re.compile(r"[™®©℠]")

CODE_PATTERNS = (
    re.compile(r"[(（]\s*(?:(SH|SZ)[:.]?)?(\d{6})(?:\.(SH|SZ|SS))?\s*[)）]", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])(SH|SZ)[:.]?(\d{6})(?!\d)()", re.IGNORECASE),
    re.compile(r"(?<!\d)()(\d{6})\.(SH|SZ|SS)(?![A-Za-z])", re.IGNORECASE),
)
CASHTAG = re.compile(r"(?<![\w$])\$([A-Z][A-Z0-9]{1,9})(?![\w])")


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", TRADEMARKS.sub("", value or "")).translate(QUOTES)


def name_key(value: str) -> str:
    """Uniqueness key: NFKC, case folded, whitespace removed."""

    return re.sub(r"\s+", "", normalize_text(value).casefold())


def name_rejection(name: str, subject_type, reviewed: bool = False) -> str | None:
    """Why ``name`` may not be matched by text, or ``None`` when it is eligible.

    ``reviewed`` marks a human-reviewed alias (``manual_seed``), which may be a single word.
    """

    name = normalize_text(name).strip()
    if subject_type in NAME_EXCLUDED_TYPES:
        return "ranked_story"
    if not name or HASH_KEY.match(name):
        return "not_a_name"
    if len(name) > MAX_NAME_CHARS:
        return "too_long"
    if CJK.search(name):
        return None if len(CJK.findall(name)) >= MIN_CJK_CHARS else "short_cjk"
    if len(LATIN_OR_DIGIT.findall(name)) < MIN_LATIN_CHARS:
        return "short_latin"
    if len(name.split()) == 1 and not reviewed and name not in REVIEWED_SINGLE_WORDS:
        letters = re.sub(r"[^A-Za-z]", "", name)
        distinctive = (
            INNER_CAPITAL.search(name)
            or (letters and re.search(r"\d", name))
            or re.search(r"\w[/.]\w", name)
            or re.search(r"[A-Z]\w*-[A-Z]", name)
        )
        if not distinctive:
            return "single_word"
    return None


def _script_join(left: str, right: str) -> str:
    """Whitespace allowed between two name parts: optional next to CJK, required otherwise."""

    return r"\s*" if CJK.match(left[-1]) or CJK.match(right[0]) else r"\s+"


def name_pattern(name: str, subject_type, plain: bool = True) -> re.Pattern:
    """Regex for one eligible name; ``plain=False`` allows only the 《》 title-mark form."""

    name = normalize_text(name).strip()
    parts = []
    for token in name.split():
        # Inside a token, allow optional whitespace where CJK meets Latin/digits ("暗黑破坏神 4").
        pieces = re.findall(r"[㐀-鿿豈-﫿]+|[^㐀-鿿豈-﫿]+", token)
        parts.append(r"\s*".join(re.escape(piece) for piece in pieces))
    tokens = name.split()
    body = parts[0]
    for index in range(1, len(parts)):
        body += _script_join(tokens[index - 1], tokens[index]) + parts[index]
    marked = rf"《\s*(?i:{body})\s*》"
    if subject_type in TITLE_MARKED_TYPES and (CJK.search(name) or not plain):
        return re.compile(marked)
    prefix = r"(?<![A-Za-z0-9])" if LATIN_OR_DIGIT.match(name[0]) else ""
    suffix = r"(?![A-Za-z0-9])" if LATIN_OR_DIGIT.match(name[-1]) else ""
    if subject_type in TITLE_MARKED_TYPES:
        return re.compile(rf"(?:{prefix}{body}{suffix})|(?:{marked})")
    return re.compile(prefix + body + suffix)


@dataclass(frozen=True)
class NameEntry:
    subject_id: UUID
    subject_type: str
    name: str
    strategy: str  # exact_name (Subject title) or exact_alias
    pattern: re.Pattern


@dataclass
class SubjectCatalog:
    """Deterministic evidence for one domain, built once per linking run."""

    names: list[NameEntry] = field(default_factory=list)
    ambiguous_names: dict[str, set] = field(default_factory=dict)
    rejected_names: dict[str, int] = field(default_factory=dict)
    codes: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    cashtags: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    urls: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    articles: dict[tuple, set] = field(default_factory=lambda: defaultdict(set))


def build_catalog(subjects, aliases, identities) -> SubjectCatalog:
    """``subjects``: (id, type, title); ``aliases``: (subject_id, alias, alias_source);
    ``identities``: (subject_id, source_id, external_id, canonical_id, canonical_url)."""

    catalog = SubjectCatalog()
    types = {subject_id: subject_type for subject_id, subject_type, _ in subjects}
    candidates = [(sid, title, "exact_name", False) for sid, _, title in subjects]
    candidates += [
        (sid, alias, "exact_alias", alias_source == REVIEWED_ALIAS_SOURCE)
        for sid, alias, alias_source in aliases
        if sid in types
    ]
    by_key: dict[str, set] = defaultdict(set)
    eligible = []
    rejected: dict[str, int] = defaultdict(int)
    for subject_id, name, strategy, reviewed in candidates:
        reason = name_rejection(name, types[subject_id], reviewed)
        plain = reason is None
        if (
            reason in TITLE_MARK_ONLY_REASONS
            and types[subject_id] in TITLE_MARKED_TYPES
            and len(name_key(name)) >= 2
        ):
            rejected[f"{reason}:title_mark_only"] += 1
            reason = None
        if reason:
            rejected[reason] += 1
            continue
        by_key[name_key(name)].add(subject_id)
        eligible.append((subject_id, name, strategy, plain))
    seen = set()
    for subject_id, name, strategy, plain in eligible:
        key = name_key(name)
        if len(by_key[key]) > 1:
            catalog.ambiguous_names[key] = by_key[key]
            continue
        if (subject_id, normalize_text(name)) in seen:
            continue
        seen.add((subject_id, normalize_text(name)))
        catalog.names.append(
            NameEntry(
                subject_id,
                types[subject_id],
                name,
                strategy,
                name_pattern(name, types[subject_id], plain),
            )
        )
    catalog.rejected_names = dict(rejected)
    for subject_id, source_id, external_id, canonical_id, canonical_url in identities:
        if subject_id not in types:
            continue
        if canonical_id:
            scheme, _, value = str(canonical_id).partition(":")
            if scheme.upper() in {"SH", "SZ"} and re.fullmatch(r"\d{6}", value):
                catalog.codes[f"{scheme.upper()}:{value}"].add(subject_id)
                catalog.codes[value].add(subject_id)
            elif scheme == "crypto" and value:
                catalog.cashtags[value.upper()].add(subject_id)
        if canonical_url:
            catalog.urls[canonical_url].add(subject_id)
        if external_id:
            catalog.articles[(source_id, str(external_id))].add(subject_id)
    return catalog


def _unique(subject_ids) -> UUID | None:
    return next(iter(subject_ids)) if len(subject_ids) == 1 else None


def match_content(text, url, source_id, article_id, catalog: SubjectCatalog):
    """subject_id → (strategy, evidence) for one content item's title + excerpt."""

    matches: dict = {}

    def add(subject_id, strategy, evidence):
        if subject_id is None:
            return
        current = matches.get(subject_id)
        if current is None or STRATEGY_ORDER.index(strategy) < STRATEGY_ORDER.index(current[0]):
            matches[subject_id] = (strategy, evidence)

    if article_id:
        add(
            _unique(catalog.articles.get((source_id, str(article_id)), ())),
            "same_article",
            {"source_article_id": str(article_id)},
        )
    if url:
        add(_unique(catalog.urls.get(url, ())), "same_article", {"url": url})
    text = normalize_text(text)
    for pattern in CODE_PATTERNS:
        for match in pattern.finditer(text):
            market = (match.group(1) or match.group(3) or "").upper().replace("SS", "SH")
            code = match.group(2)
            key = f"{market}:{code}" if market else code
            add(_unique(catalog.codes.get(key, ())), "exchange_code", {"code": match.group(0)})
    for match in CASHTAG.finditer(text):
        add(
            _unique(catalog.cashtags.get(match.group(1), ())),
            "cashtag",
            {"cashtag": match.group(0)},
        )
    spans = []
    for entry in catalog.names:
        for match in entry.pattern.finditer(text):
            spans.append((match.start(), match.end(), entry))
    for start, end, entry in spans:
        # A name inside a longer matched name of another Subject is not a separate mention.
        if any(
            other.subject_id != entry.subject_id
            and o_start <= start
            and end <= o_end
            and (o_end - o_start) > (end - start)
            for o_start, o_end, other in spans
        ):
            continue
        add(entry.subject_id, entry.strategy, {"name": entry.name})
    return matches
