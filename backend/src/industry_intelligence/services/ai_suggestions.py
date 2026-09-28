"""AI-assisted suggestions with human review (Phase 7): entity candidates and Event merges.

AI never writes canonical data here. The flow is:

1. **Entity candidates** (``suggest_entities``): the provider lists names that an Event's
   reports mention (``event_entity_candidates_v1``, cached as an artifact). Each name must occur
   literally in the cited evidence (word-bounded for Latin script); it is then resolved by
   deterministic exact lookup — a normalized Subject title/alias in the Event's domain (ranked
   stories excluded, merged-away Subjects excluded), a normalized Company name/alias, or an exact
   CompaniesMarketCap ticker. One match → an ``event_subject_candidate`` /
   ``event_company_candidate`` with that ID; several → the same type without an ID and the
   candidates listed for the reviewer; none → ``new_entity_candidate`` (never inserted as a
   Subject or Company). Entities the Event is already linked to are skipped.
2. **Cross-language merge candidates** (``suggest_event_merges``): a deterministic prefilter
   (same domain, disjoint report languages, publication ranges within ``PAIR_WINDOW``,
   compatible Event types, no disjoint entity links, and a shared entity link or a shared
   Latin-script name that at most ``MAX_NAME_EVENTS`` Events of the domain mention) selects a
   bounded number of pairs; the provider judges each pair
   (``event_pair_same_event_v1``) and only "yes" becomes an ``event_merge_candidate``.
3. **Review** (CLI): ``accept_suggestion`` applies an explicit reviewed action through the
   existing services — a ``reviewed`` ContentSubjectLink on the cited content (then the Event's
   derived links are refreshed), a ``reviewed`` EventCompanyLink, or ``merge_events`` with all
   Phase 6 safeguards (a refused merge changes nothing and the suggestion stays pending).
   ``reject_suggestion`` records the decision. New-entity candidates cannot be accepted.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from industry_intelligence.adapters.company_ranking.companiesmarketcap import SYMBOL_SCHEME
from industry_intelligence.ai.prompts.event_candidates import (
    EVENT_ENTITY_CANDIDATES_V1,
    EVENT_PAIR_V1,
)
from industry_intelligence.ai.providers import build_provider
from industry_intelligence.db.models import (
    AIArtifact,
    AISuggestion,
    Company,
    CompanyAlias,
    CompanyIdentity,
    CompanyRedirect,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventCompanyLink,
    EventContentLink,
    EventRedirect,
    EventSubjectLink,
    Subject,
    SubjectAlias,
)
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.domain.enums import SubjectType
from industry_intelligence.services.ai_enrichment import (
    ENTITY_EVENT,
    find_cached,
    generate_artifact,
    select_events,
)
from industry_intelligence.services.ai_evidence import (
    input_hash,
    load_event_evidence,
    ref_map,
    render_event,
)
from industry_intelligence.services.content_linking import REVIEWED_STRATEGY
from industry_intelligence.services.event_ingestion import refresh_event_entities
from industry_intelligence.services.event_merge import merge_events
from industry_intelligence.services.resolution import normalize_subject_name

NEUTRAL_LOCALE = "und"
ENTITY_PAIR = "event_pair"
PAIR_WINDOW = timedelta(hours=72)
# A Latin-script name shared by two Events counts as distinctive only when at most this many
# Events of the domain mention it ("Steam" or "OpenAI" appear in many unrelated stories).
MAX_NAME_EVENTS = 3
DEFAULT_PAIR_LIMIT = 10
MAX_PAIR_LIMIT = 50
_LATIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9+.'-]*[A-Za-z0-9+]")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
# Capitalized words that name no specific entity (headline case, months, generic nouns).
_GENERIC = frozenset(
    """the a an and or of to in on for at by with from as is are was were be its it this that
    after over into new news report reports says said update updates trailer gameplay game games
    company companies inc ltd corp co group ceo cfo year years day days week weeks month months
    january february march april may june july august september october november december
    monday tuesday wednesday thursday friday saturday sunday how why what who when where
    will can could would should first second third last next more most than up down out off
    china chinese us usa uk japan japanese europe european global world market markets stock
    stocks shares share price prices billion million percent official launch launches release
    releases released studio studios developer developers publisher players player""".split()
)


class SuggestionReviewRefused(ValueError):
    """The review action is invalid or unsafe; nothing was changed."""


def _nfkc(value):
    return unicodedata.normalize("NFKC", value or "")


def mention_grounded(name, text):
    """The name occurs in the text: word-bounded (case-insensitive) for Latin, substring for CJK."""

    name = normalize_subject_name(name)
    text = normalize_subject_name(text)
    if not name:
        return False
    if _CJK.search(name):
        return name in text
    return re.search(rf"(?<![0-9a-z]){re.escape(name)}(?![0-9a-z])", text) is not None


class EntityCatalog:
    """Exact normalized names of eligible Subjects (per domain) and Companies."""

    def __init__(self, session):
        self.session = session
        self._subjects = {}
        self._companies = None

    def subjects(self, domain):
        if domain not in self._subjects:
            merged = select(SubjectRedirect.duplicate_id)
            eligible = (
                Subject.domain == domain,
                Subject.subject_type != SubjectType.EVENT,
                Subject.id.not_in(merged),
            )
            names = defaultdict(set)
            for subject_id, title in self.session.execute(
                select(Subject.id, Subject.title).where(*eligible)
            ):
                names[normalize_subject_name(title)].add(subject_id)
            for subject_id, alias in self.session.execute(
                select(SubjectAlias.subject_id, SubjectAlias.alias).join(Subject).where(*eligible)
            ):
                names[normalize_subject_name(alias)].add(subject_id)
            self._subjects[domain] = names
        return self._subjects[domain]

    def companies(self):
        if self._companies is None:
            merged = select(CompanyRedirect.duplicate_id)
            names, symbols = defaultdict(set), defaultdict(set)
            for company_id, name in self.session.execute(
                select(Company.id, Company.canonical_name).where(Company.id.not_in(merged))
            ):
                names[normalize_subject_name(name)].add(company_id)
            for company_id, alias in self.session.execute(
                select(CompanyAlias.company_id, CompanyAlias.alias).where(
                    CompanyAlias.company_id.not_in(merged)
                )
            ):
                names[normalize_subject_name(alias)].add(company_id)
            for company_id, value in self.session.execute(
                select(CompanyIdentity.company_id, CompanyIdentity.value).where(
                    CompanyIdentity.scheme == SYMBOL_SCHEME,
                    CompanyIdentity.company_id.not_in(merged),
                )
            ):
                symbols[value].add(company_id)
            self._companies = (names, symbols)
        return self._companies

    def lookup(self, domain, name):
        normalized = normalize_subject_name(name)
        subjects = self.subjects(domain).get(normalized, set())
        names, symbols = self.companies()
        companies = names.get(normalized, set()) | symbols.get(name.strip(), set())
        return sorted(subjects, key=str), sorted(companies, key=str)


def _linked(session, event_id):
    subjects = set(
        session.scalars(
            select(EventSubjectLink.subject_id).where(EventSubjectLink.event_id == event_id)
        )
    )
    companies = set(
        session.scalars(
            select(EventCompanyLink.company_id).where(EventCompanyLink.event_id == event_id)
        )
    )
    return subjects, companies


def entity_suggestions(event_id, domain, output, items, catalog, linked):
    """Suggestion rows (dicts) for validated mentions; pure apart from catalog lookups."""

    texts = {item.content_id: item.text() for item in items}
    linked_subjects, linked_companies = linked
    rows, skipped = [], Counter()
    for mention in output.get("mentions", []):
        name, evidence = mention["name"], mention["evidence"]
        cited = "\n".join(texts[content_id] for content_id in evidence if content_id in texts)
        if not mention_grounded(name, cited):
            skipped["ungrounded"] += 1
            continue
        subjects, companies = catalog.lookup(domain, name)
        normalized = normalize_subject_name(name)
        base = {
            "entity_type": ENTITY_EVENT,
            "entity_key": str(event_id),
            "candidate_text": name[:500],
            "evidence_content_ids": evidence,
        }
        found = False
        for kind, matches, held in (
            ("subject", subjects, linked_subjects),
            ("company", companies, linked_companies),
        ):
            if not matches:
                continue
            found = True
            if len(matches) == 1 and matches[0] in held:
                skipped["already_linked"] += 1
                continue
            unique = len(matches) == 1
            rows.append(
                {
                    **base,
                    "suggestion_type": f"event_{kind}_candidate",
                    "candidate_type": kind,
                    "candidate_id": matches[0] if unique else None,
                    "candidate_key": (str(matches[0]) if unique else f"ambiguous:{normalized}")[
                        :200
                    ],
                    "metadata": {
                        "mention_kind": mention["kind"],
                        "match": "exact_catalog_name",
                        **({} if unique else {"ambiguous_candidates": [str(m) for m in matches]}),
                    },
                }
            )
        if not found:
            rows.append(
                {
                    **base,
                    "suggestion_type": "new_entity_candidate",
                    "candidate_type": "none",
                    "candidate_id": None,
                    "candidate_key": f"text:{mention['kind']}:{normalized}"[:200],
                    "metadata": {"mention_kind": mention["kind"], "match": "not_in_catalog"},
                }
            )
    return rows, skipped


def _insert_suggestions(session, rows, artifact):
    inserted = 0
    for row in rows:
        values = {
            **{key: value for key, value in row.items() if key != "metadata"},
            "suggestion_metadata": row.get("metadata", {}),
            "artifact_id": artifact.id,
            "provider": artifact.provider,
            "model": artifact.model,
            "prompt_version": artifact.prompt_version,
            "status": "pending",
        }
        created = session.execute(
            insert(AISuggestion)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(AISuggestion.id)
        ).scalar()
        inserted += created is not None
    return inserted


async def _generate_or_replay(session_factory, settings, provider, spec, task, sleep):
    """(artifact_id, output, status); reuses a cached valid artifact without a request."""

    with session_factory() as session:
        cached = find_cached(
            session,
            task["entity_type"],
            task["entity_key"],
            spec,
            NEUTRAL_LOCALE,
            task["digest"],
            provider.name,
            provider.model,
        )
        if cached is not None:
            return cached.id, cached.output_json, "cached"
    artifact = await generate_artifact(
        provider,
        settings,
        spec,
        entity_type=task["entity_type"],
        entity_key=task["entity_key"],
        locale=NEUTRAL_LOCALE,
        digest=task["digest"],
        payload=task["payload"],
        mapping=task["mapping"],
        items=task["items"],
        sleep=sleep,
    )
    status, output, artifact_id = artifact.status, artifact.output_json, artifact.id
    with session_factory.begin() as session:
        session.add(artifact)
    return artifact_id, output, status


async def suggest_entities(
    settings,
    session_factory,
    *,
    event_ids=None,
    since=None,
    domain=None,
    limit=10,
    dry_run=False,
    provider=None,
    sleep=None,
):
    """Entity candidate suggestions for a bounded set of Events (pending review)."""

    spec = EVENT_ENTITY_CANDIDATES_V1
    if provider is None and not dry_run:
        provider = build_provider(settings)
    tasks = []
    with session_factory() as session:
        for event in select_events(session, event_ids, since, domain, limit):
            evidence = load_event_evidence(session, event.id)
            if evidence is None or not evidence.items:
                continue
            digest = input_hash(spec.version, NEUTRAL_LOCALE, evidence)
            cached = find_cached(
                session,
                ENTITY_EVENT,
                str(event.id),
                spec,
                NEUTRAL_LOCALE,
                digest,
                provider.name if provider else None,
                provider.model if provider else None,
            )
            tasks.append(
                {
                    "event_id": event.id,
                    "domain": str(event.domain),
                    "title": event.canonical_title[:120],
                    "entity_type": ENTITY_EVENT,
                    "entity_key": str(event.id),
                    "digest": digest,
                    "cached": cached is not None,
                    "payload": render_event(evidence),
                    "mapping": ref_map(evidence),
                    "items": evidence.items,
                }
            )
    result = {
        "dry_run": dry_run,
        "prompt_version": spec.version,
        "events": [
            {"event_id": str(t["event_id"]), "domain": t["domain"], "title": t["title"]}
            for t in tasks
        ],
        "cached": sum(t["cached"] for t in tasks),
        "would_generate": sum(not t["cached"] for t in tasks),
        "estimated_requests": sum(not t["cached"] for t in tasks),
    }
    if dry_run:
        return result
    totals, skipped, per_event = Counter(), Counter(), []
    for task in tasks:
        if totals["requests"] and not task["cached"] and settings.ai_request_interval:
            await (sleep or asyncio.sleep)(settings.ai_request_interval)
        totals["requests"] += not task["cached"]
        artifact_id, output, status = await _generate_or_replay(
            session_factory, settings, provider, spec, task, sleep
        )
        totals[status] += 1
        inserted = 0
        if output is not None:
            with session_factory.begin() as session:
                artifact = session.get(AIArtifact, artifact_id)
                rows, skip = entity_suggestions(
                    task["event_id"],
                    task["domain"],
                    output,
                    task["items"],
                    EntityCatalog(session),
                    _linked(session, task["event_id"]),
                )
                skipped.update(skip)
                inserted = _insert_suggestions(session, rows, artifact)
                totals["suggestions"] += inserted
        per_event.append(
            {"event_id": str(task["event_id"]), "artifact": status, "new_suggestions": inserted}
        )
    return {
        **result,
        "requests": totals["requests"],
        "generated": totals["valid"],
        "replayed": totals["cached"],
        "failed": totals["failed"],
        "new_suggestions": totals["suggestions"],
        "skipped": dict(skipped),
        "results": per_event,
    }


# --- Cross-language Event merge candidates -------------------------------------------------


def distinctive_names(text):
    """Casefolded Latin-script names that can identify an entity across languages."""

    names = set()
    for match in _LATIN_NAME.finditer(_nfkc(text)):
        token = match.group(0).strip(".'-")
        folded = token.casefold()
        if len(folded) < 3 or folded in _GENERIC:
            continue
        if token[0].isupper() or any(char.isdigit() for char in token):
            names.add(folded)
    return names


def _pair_gap(left, right):
    return max(
        left.first_published_at - right.last_published_at,
        right.first_published_at - left.last_published_at,
        timedelta(0),
    )


def merge_candidates(session, since=None, domain=None, limit=DEFAULT_PAIR_LIMIT):
    """Deterministically prefiltered cross-language Event pairs, strongest signals first."""

    events = select_events(session, since=since, domain=domain, limit=5000, max_limit=5000)
    info = {}
    for event in events:
        evidence = load_event_evidence(session, event.id)
        if evidence is None or not evidence.items:
            continue
        subjects, companies = _linked(session, event.id)
        info[event.id] = {
            "event": event,
            "evidence": evidence,
            "languages": {item.language for item in evidence.items},
            "names": set().union(*(distinctive_names(i.text()) for i in evidence.items)),
            "subjects": subjects,
            "companies": companies,
        }
    suggested = set(
        session.execute(
            select(AISuggestion.entity_key, AISuggestion.candidate_key).where(
                AISuggestion.suggestion_type == "event_merge_candidate"
            )
        ).all()
    )
    frequency = defaultdict(Counter)
    for row in info.values():
        frequency[row["event"].domain].update(row["names"])
    pairs = []
    ordered = sorted(
        info.values(), key=lambda row: (row["event"].first_published_at, str(row["event"].id))
    )
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            a, b = left["event"], right["event"]
            if a.domain != b.domain or left["languages"] & right["languages"]:
                continue
            if _pair_gap(a, b) > PAIR_WINDOW:
                continue
            if "general" not in {a.event_type, b.event_type} and a.event_type != b.event_type:
                continue
            if any(
                left[key] and right[key] and not left[key] & right[key]
                for key in ("subjects", "companies")
            ):
                continue
            if (str(a.id), str(b.id)) in suggested:
                continue
            shared_entities = (left["subjects"] & right["subjects"]) | (
                left["companies"] & right["companies"]
            )
            shared_names = left["names"] & right["names"]
            distinctive = sorted(
                name for name in shared_names if frequency[a.domain][name] <= MAX_NAME_EVENTS
            )
            if not (shared_entities or distinctive):
                continue
            pairs.append(
                {
                    "a": left,
                    "b": right,
                    "shared_entity_ids": sorted(str(e) for e in shared_entities),
                    "shared_names": sorted(shared_names),
                    "distinctive_names": distinctive,
                    "score": 3 * len(shared_entities) + 2 * len(distinctive) + len(shared_names),
                    "gap_seconds": int(_pair_gap(a, b).total_seconds()),
                }
            )
    pairs.sort(key=lambda p: (-p["score"], p["gap_seconds"], str(p["a"]["event"].id)))
    return pairs[: max(1, min(limit or DEFAULT_PAIR_LIMIT, MAX_PAIR_LIMIT))]


def _pair_task(pair):
    a, b = pair["a"]["evidence"], pair["b"]["evidence"]
    return {
        "entity_type": ENTITY_PAIR,
        "entity_key": f"{a.event_id}:{b.event_id}",
        "digest": input_hash(EVENT_PAIR_V1.version, NEUTRAL_LOCALE, a, b),
        "payload": {"event_a": render_event(a, "A"), "event_b": render_event(b, "B")},
        "mapping": {**ref_map(a, "A"), **ref_map(b, "B")},
        "items": a.items + b.items,
    }


def _pair_row(pair):
    return {
        "event_a": str(pair["a"]["event"].id),
        "title_a": pair["a"]["event"].canonical_title[:100],
        "event_b": str(pair["b"]["event"].id),
        "title_b": pair["b"]["event"].canonical_title[:100],
        "domain": str(pair["a"]["event"].domain),
        "shared_names": pair["shared_names"],
        "distinctive_names": pair["distinctive_names"],
        "shared_entity_ids": pair["shared_entity_ids"],
        "gap_hours": round(pair["gap_seconds"] / 3600, 1),
    }


async def suggest_event_merges(
    settings,
    session_factory,
    *,
    since=None,
    domain=None,
    limit=DEFAULT_PAIR_LIMIT,
    dry_run=False,
    provider=None,
    sleep=None,
):
    """Judge prefiltered cross-language pairs; a "yes" becomes a pending merge suggestion."""

    spec = EVENT_PAIR_V1
    if provider is None and not dry_run:
        provider = build_provider(settings)
    with session_factory() as session:
        pairs = merge_candidates(session, since, domain, limit)
        tasks = [(pair, _pair_task(pair)) for pair in pairs]
        rows = [_pair_row(pair) for pair in pairs]
    result = {
        "dry_run": dry_run,
        "prompt_version": spec.version,
        "candidate_pairs": rows,
        "estimated_requests": len(tasks),
    }
    if dry_run:
        return result
    totals, judgements, per_pair = Counter(), Counter(), []
    for index, (pair, task) in enumerate(tasks):
        if index and settings.ai_request_interval:
            await (sleep or asyncio.sleep)(settings.ai_request_interval)
        artifact_id, output, status = await _generate_or_replay(
            session_factory, settings, provider, spec, task, sleep
        )
        totals[status] += 1
        totals["requests"] += status != "cached"
        judgement = (output or {}).get("same_event")
        if judgement:
            judgements[judgement] += 1
        created = 0
        if judgement == "yes":
            with session_factory.begin() as session:
                artifact = session.get(AIArtifact, artifact_id)
                a, b = pair["a"]["event"], pair["b"]["event"]
                created = _insert_suggestions(
                    session,
                    [
                        {
                            "suggestion_type": "event_merge_candidate",
                            "entity_type": ENTITY_EVENT,
                            "entity_key": str(a.id),
                            "candidate_type": "event",
                            "candidate_id": b.id,
                            "candidate_key": str(b.id),
                            "candidate_text": b.canonical_title[:500],
                            "evidence_content_ids": output["evidence_a"] + output["evidence_b"],
                            "rationale": output["reason"],
                            "metadata": {
                                "judgement": judgement,
                                "shared_names": pair["shared_names"],
                                "shared_entity_ids": pair["shared_entity_ids"],
                                "gap_hours": round(pair["gap_seconds"] / 3600, 1),
                            },
                        }
                    ],
                    artifact,
                )
        totals["suggestions"] += created
        per_pair.append({**rows[index], "artifact": status, "judgement": judgement})
    return {
        **result,
        "requests": totals["requests"],
        "generated": totals["valid"],
        "replayed": totals["cached"],
        "failed": totals["failed"],
        "judgements": dict(judgements),
        "new_suggestions": totals["suggestions"],
        "results": per_pair,
    }


# --- Review ---------------------------------------------------------------------------------


def _candidate_label(session, suggestion):
    if suggestion.candidate_id is None:
        return None
    if suggestion.candidate_type == "subject":
        row = session.get(Subject, suggestion.candidate_id)
        return row.title if row else None
    if suggestion.candidate_type == "company":
        row = session.get(Company, suggestion.candidate_id)
        return row.canonical_name if row else None
    if suggestion.candidate_type == "event":
        row = session.get(Event, suggestion.candidate_id)
        return row.canonical_title if row else None
    return None


def list_suggestions(session, status="pending", suggestion_type=None, limit=50):
    query = select(AISuggestion)
    if status:
        query = query.where(AISuggestion.status == status)
    if suggestion_type:
        query = query.where(AISuggestion.suggestion_type == suggestion_type)
    rows = session.scalars(
        query.order_by(AISuggestion.created_at, AISuggestion.id).limit(limit)
    ).all()
    output = []
    for row in rows:
        event = (
            session.get(Event, UUID(row.entity_key)) if row.entity_type == ENTITY_EVENT else None
        )
        titles = {
            str(item.id): item.title[:120]
            for item in session.scalars(
                select(ContentItem).where(
                    ContentItem.id.in_([UUID(cid) for cid in row.evidence_content_ids])
                )
            )
        }
        output.append(
            {
                "id": str(row.id),
                "suggestion_type": row.suggestion_type,
                "status": row.status,
                "event_id": row.entity_key,
                "event_title": event.canonical_title[:120] if event else None,
                "event_exists": event is not None,
                "candidate_type": row.candidate_type,
                "candidate_id": str(row.candidate_id) if row.candidate_id else None,
                "candidate_label": _candidate_label(session, row),
                "candidate_text": row.candidate_text,
                "metadata": row.suggestion_metadata,
                "rationale": row.rationale,
                "evidence": [
                    {"content_id": cid, "title": titles.get(cid)}
                    for cid in row.evidence_content_ids
                ],
                "provider": row.provider,
                "model": row.model,
                "prompt_version": row.prompt_version,
                "created_at": row.created_at.isoformat(),
                "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
                "review_reason": row.review_reason,
            }
        )
    return output


def _pending(session, suggestion_id):
    suggestion = session.get(AISuggestion, suggestion_id, with_for_update=True)
    if suggestion is None:
        raise SuggestionReviewRefused("Suggestion not found")
    if suggestion.status != "pending":
        raise SuggestionReviewRefused(f"Suggestion is already {suggestion.status}")
    return suggestion


def _reason(reason):
    reason = " ".join((reason or "").split())
    if not reason:
        raise SuggestionReviewRefused("A review reason is required")
    return reason


def _event_members(session, suggestion):
    event_id = UUID(suggestion.entity_key)
    if session.get(Event, event_id) is None or session.get(EventRedirect, event_id) is not None:
        raise SuggestionReviewRefused(
            "The Event no longer exists as a canonical Event (rebuilt or merged); reject it"
        )
    members = set(
        session.scalars(
            select(EventContentLink.content_item_id).where(EventContentLink.event_id == event_id)
        )
    )
    evidence = [UUID(cid) for cid in suggestion.evidence_content_ids if UUID(cid) in members]
    if not evidence:
        raise SuggestionReviewRefused("None of the cited content still belongs to the Event")
    return event_id, evidence


def _chosen_candidate(suggestion, candidate_id):
    options = (suggestion.suggestion_metadata or {}).get("ambiguous_candidates")
    if suggestion.candidate_id is not None:
        if candidate_id is not None and candidate_id != suggestion.candidate_id:
            raise SuggestionReviewRefused("--candidate differs from the suggested candidate")
        return suggestion.candidate_id
    if candidate_id is None or str(candidate_id) not in (options or []):
        raise SuggestionReviewRefused(
            "Ambiguous suggestion: choose one listed candidate with --candidate"
        )
    return candidate_id


def accept_suggestion(session, suggestion_id, reason, candidate_id=None, canonical_id=None):
    """Apply a reviewed action for one pending suggestion inside the caller's transaction."""

    reason = _reason(reason)
    suggestion = _pending(session, suggestion_id)
    kind = suggestion.suggestion_type
    review = {"suggestion_id": str(suggestion.id), "reason": reason}
    if kind == "new_entity_candidate":
        raise SuggestionReviewRefused(
            "Not in the catalog: AI suggestions never create Subjects or Companies; reject it or "
            "add the entity through the normal ingestion path first"
        )
    if kind == "event_subject_candidate":
        event_id, evidence = _event_members(session, suggestion)
        subject_id = _chosen_candidate(suggestion, candidate_id)
        subject = session.get(Subject, subject_id)
        merged = session.get(SubjectRedirect, subject_id)
        if subject is None or merged is not None or subject.subject_type == SubjectType.EVENT:
            raise SuggestionReviewRefused("The Subject is not an eligible canonical Subject")
        written = 0
        for content_id in evidence:
            written += (
                session.execute(
                    insert(ContentSubjectLink)
                    .values(
                        content_item_id=content_id,
                        subject_id=subject_id,
                        relation_type="mentions",
                        strategy=REVIEWED_STRATEGY,
                        confidence=1.0,
                    )
                    .on_conflict_do_nothing()
                    .returning(ContentSubjectLink.id)
                ).scalar()
                is not None
            )
        session.flush()
        refresh_event_entities(session, [event_id])
        from industry_intelligence.services.search_index import refresh_subject

        refresh_subject(session, subject_id)
        result = {
            "applied": "reviewed_content_subject_links",
            "subject_id": str(subject_id),
            "content_item_ids": [str(cid) for cid in evidence],
            "links_written": written,
        }
    elif kind == "event_company_candidate":
        event_id, evidence = _event_members(session, suggestion)
        company_id = _chosen_candidate(suggestion, candidate_id)
        if session.get(Company, company_id) is None or session.get(CompanyRedirect, company_id):
            raise SuggestionReviewRefused("The Company is not a canonical Company")
        written = (
            session.execute(
                insert(EventCompanyLink)
                .values(
                    event_id=event_id,
                    company_id=company_id,
                    strategy=REVIEWED_STRATEGY,
                    content_count=len(evidence),
                    evidence={"content_item_ids": [str(cid) for cid in evidence], **review},
                )
                .on_conflict_do_nothing()
                .returning(EventCompanyLink.id)
            ).scalar()
            is not None
        )
        written = int(written)
        result = {
            "applied": "reviewed_event_company_link",
            "company_id": str(company_id),
            "links_written": written,
        }
    elif kind == "event_merge_candidate":
        first, second = UUID(suggestion.entity_key), suggestion.candidate_id
        canonical = canonical_id or first
        if canonical not in {first, second}:
            raise SuggestionReviewRefused("--canonical must be one of the two suggested Events")
        duplicate = second if canonical == first else first
        try:
            merged = merge_events(
                session, canonical, duplicate, f"{reason} [AI suggestion {suggestion.id}]"
            )
        except ValueError as exc:
            raise SuggestionReviewRefused(
                f"Merge refused by Event merge safeguards: {exc}"
            ) from exc
        result = {"applied": "reviewed_event_merge", **merged}
    else:  # pragma: no cover - guarded by the table's vocabulary
        raise SuggestionReviewRefused(f"Unknown suggestion type {kind}")
    suggestion.status = "accepted"
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.review_reason = reason
    suggestion.review_result = result
    session.flush()
    return {"suggestion_id": str(suggestion.id), "status": "accepted", **result}


def reject_suggestion(session, suggestion_id, reason):
    reason = _reason(reason)
    suggestion = _pending(session, suggestion_id)
    suggestion.status = "rejected"
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.review_reason = reason
    session.flush()
    return {"suggestion_id": str(suggestion.id), "status": "rejected", "reason": reason}


def reject_artifact(session, artifact_id, reason):
    """Hide a displayable artifact after human review (for example an inaccurate summary)."""

    reason = _reason(reason)
    artifact = session.get(AIArtifact, artifact_id, with_for_update=True)
    if artifact is None:
        raise SuggestionReviewRefused("Artifact not found")
    if artifact.status not in {"valid", "stale"}:
        raise SuggestionReviewRefused(f"Artifact is {artifact.status}")
    artifact.status = "rejected"
    artifact.reviewed_at = datetime.now(UTC)
    artifact.review_note = reason
    session.flush()
    return {"artifact_id": str(artifact.id), "status": "rejected", "reason": reason}
