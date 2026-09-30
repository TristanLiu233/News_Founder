"""Bounded, cached, incremental AI enrichment of Events (CLI-only; never on a page request).

AI-LP1 language policy: Event AI is generated only in the Event's primary locale
(``ai_evidence.primary_locale``: the one locale every report is written in) with the primary
prompts, so each Event has at most two tasks (Summary and Why It Matters). Cross-language
generation is discontinued: an explicit request for another locale is refused before any
request or write, and mixed-language Events are skipped. The legacy cross-language prompts stay
in ``event_insights`` only for their historical artifacts.

For each task the evidence hash is computed from persisted data. A ``valid`` artifact with the
same hash, provider, and model is a replay (no request); older ``valid`` artifacts of the same
Event/type/locale with a different hash are marked ``stale``. Otherwise one provider request is
made, the output is validated (``ai_evidence.validate_output``), and exactly one row is written:
``valid`` with the output, or ``failed`` with a short error code (no output kept). Every task
commits on its own, so one failure never rolls back another Event's artifacts.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from uuid import UUID, uuid4

from sqlalchemy import select, update

from industry_intelligence.ai.prompts import LANGUAGE_NAMES
from industry_intelligence.ai.prompts.event_insights import INSIGHT_PROMPTS
from industry_intelligence.ai.provider import AIProviderError, generate_with_retry
from industry_intelligence.ai.providers import build_provider
from industry_intelligence.db.models import AIArtifact, Event, EventRedirect
from industry_intelligence.domain.enums import Domain
from industry_intelligence.services.ai_evidence import (
    evidence_quality,
    excerpt_state,
    input_hash,
    load_event_evidence,
    primary_locale,
    ref_map,
    render_event,
    render_user_message,
    task_eligibility,
    validate_output,
)
from industry_intelligence.services.events import resolve_event_redirect

ENTITY_EVENT = "event"
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
# Product-1: the unattended ``intelligence-update`` AI stage is capped per run (never "all").
DEFAULT_MAX_EVENTS = 12
AI_MAX_EVENTS = 15
SELECTION_RULE = "trending_then_recent_round_robin"
# Failures that a later run may retry without a reviewer: the provider never produced a checked
# answer (network, rate limit, server, authentication, configuration). A validation failure,
# refusal, or malformed answer was a real (paid) answer and is not retried unattended.
TRANSIENT_ERRORS = frozenset(
    {"timeout", "rate_limited", "server_error", "request_rejected", "not_configured"}
)


class CrossLanguageGenerationDisabled(ValueError):
    """An explicit locale that is not an Event's primary locale (AI-LP1 product policy)."""

    def __init__(self, refused):
        self.refused = refused
        super().__init__(
            "Cross-language AI generation is disabled by product policy: Event AI is generated "
            "only in each Event's primary locale (omit --locale). Refused: "
            + ", ".join(f"{row['event_id']} {row['locale']}" for row in refused)
        )


def system_prompt(spec, locale):
    language = LANGUAGE_NAMES.get(locale, "English") if spec.localized else "English"
    return spec.system.replace("{language}", language)


def insight_payload(spec, evidence, locale):
    """The user message payload of one insight task: the Event, plus the output language for
    cross-language prompts.

    Phase 7.1 Checkpoint E: with Chinese reports, English output was written in Chinese when
    the language was named only in the system prompt, so cross-language requests repeat it next
    to the reports. Primary-locale requests stay exactly as audited in Checkpoint D.
    """

    payload = render_event(evidence)
    if spec.grounding_guards:
        # Product-1.1: the deterministic evidence quality and which excerpts were cut.
        payload["evidence_quality"] = evidence_quality(evidence)["class"]
        for report, item in zip(payload["reports"], evidence.items, strict=True):
            report["excerpt_cut"] = excerpt_state(item.excerpt, item.title)[0] == "truncated"
    if spec.cross_language:
        payload["output_language"] = LANGUAGE_NAMES.get(locale, "English")
    return payload


def select_events(
    session, event_ids=None, since=None, domain=None, limit=DEFAULT_LIMIT, max_limit=MAX_LIMIT
):
    """Canonical, non-empty Events: the given IDs (redirects followed) or the most recent ones."""

    if event_ids is not None:
        ids = []
        for event_id in event_ids:
            canonical = resolve_event_redirect(session, event_id)
            if canonical not in ids and session.get(Event, canonical) is not None:
                ids.append(canonical)
        return [session.get(Event, event_id) for event_id in ids]
    query = select(Event).where(
        Event.content_count > 0, Event.id.not_in(select(EventRedirect.duplicate_id))
    )
    if since is not None:
        query = query.where(Event.last_published_at >= since)
    if domain:
        query = query.where(Event.domain == domain)
    limit = max(1, min(limit or DEFAULT_LIMIT, max_limit))
    return session.scalars(
        query.order_by(Event.last_published_at.desc(), Event.id).limit(limit)
    ).all()


def validate_max_events(value):
    """The bounded AI stage's Event cap: an integer in 1..``AI_MAX_EVENTS``, else ValueError."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("the AI Event cap must be an integer")
    if not 1 <= value <= AI_MAX_EVENTS:
        raise ValueError(f"the AI Event cap must be between 1 and {AI_MAX_EVENTS}")
    return value


def _eligible(session, event_id):
    """Existing Phase 7 eligibility: a canonical, non-empty Event with a primary locale."""

    if resolve_event_redirect(session, event_id) != event_id:
        return False
    event = session.get(Event, event_id)
    if event is None or event.content_count <= 0:
        return False
    evidence = load_event_evidence(session, event_id)
    if not (evidence and evidence.items) or primary_locale(evidence) is None:
        return False
    # Product-1.1 evidence-quality gate: at least one task the evidence can support.
    return any(eligible for eligible, _ in task_eligibility(evidence_quality(evidence)).values())


def select_bounded_events(session, max_events=DEFAULT_MAX_EVENTS):
    """Deterministic, capped Event sample for the unattended AI stage (Product-1).

    Per domain (``Domain`` order: computer, finance, gaming), candidates are the Events currently
    on that domain's Trending (the Trending read model's own order: list catalog order, then each
    publisher's native rank), then the domain's most recently reported Events
    (``last_published_at`` desc, ID). Only eligible Events count (``_eligible``: canonical,
    primary locale, and — Product-1.1 — at least one task its evidence quality supports), so the
    cap means "up to" that many Events. The domains are
    then taken round-robin, one Event each in turn, until ``max_events``; a domain that runs out
    is skipped. No score is computed and no AI is used to choose; an Event already enriched is
    still selected (its cached artifacts cost no request).
    """

    from industry_intelligence.services.trending import domain_trending

    max_events = validate_max_events(max_events)
    queues = {}
    for domain in Domain:
        # The native view keeps the Product-1 selection unchanged (catalog order, no age filter).
        trending = domain_trending(session, domain, limit=None, summaries=False, view="native")
        candidates = [UUID(item["event"]["id"]) for item in trending["items"]]
        candidates += session.scalars(
            select(Event.id)
            .where(
                Event.domain == domain,
                Event.content_count > 0,
                Event.id.not_in(select(EventRedirect.duplicate_id)),
            )
            .order_by(Event.last_published_at.desc(), Event.id)
            .limit(max_events * 4)
        ).all()
        queue, seen = [], set()
        for event_id in candidates:
            if event_id in seen:
                continue
            seen.add(event_id)
            if _eligible(session, event_id):
                queue.append(event_id)
            if len(queue) == max_events:
                break
        queues[str(domain)] = queue
    return round_robin([queues[str(domain)] for domain in Domain], max_events)


def round_robin(queues, limit):
    """One item from each queue in turn (queue order kept), skipping empty queues, up to
    ``limit`` items. Pure; the queues are not modified."""

    queues = [list(queue) for queue in queues]
    selected = []
    while len(selected) < limit and any(queues):
        for queue in queues:
            if queue and len(selected) < limit:
                selected.append(queue.pop(0))
    return selected


def find_cached(session, entity_type, entity_key, spec, locale, digest, provider=None, model=None):
    query = select(AIArtifact).where(
        AIArtifact.entity_type == entity_type,
        AIArtifact.entity_key == entity_key,
        AIArtifact.artifact_type == spec.artifact_type,
        AIArtifact.locale == locale,
        AIArtifact.input_hash == digest,
        AIArtifact.status == "valid",
    )
    if provider is not None:
        query = query.where(AIArtifact.provider == provider, AIArtifact.model == model)
    return session.scalars(query.order_by(AIArtifact.generated_at.desc())).first()


def mark_stale(session, entity_type, entity_key, artifact_type, locale, digest):
    """Mark valid artifacts built from other evidence (or another prompt version) as stale."""

    return session.execute(
        update(AIArtifact)
        .where(
            AIArtifact.entity_type == entity_type,
            AIArtifact.entity_key == entity_key,
            AIArtifact.artifact_type == artifact_type,
            AIArtifact.locale == locale,
            AIArtifact.status == "valid",
            AIArtifact.input_hash != digest,
        )
        .values(status="stale")
    ).rowcount


async def generate_artifact(
    provider,
    settings,
    spec,
    *,
    entity_type,
    entity_key,
    locale,
    digest,
    payload,
    mapping,
    items,
    source_count=None,
    sleep=None,
):
    """One provider request plus validation; returns an unsaved ``AIArtifact`` (valid/failed)."""

    artifact = AIArtifact(
        id=uuid4(),
        entity_type=entity_type,
        entity_key=entity_key,
        artifact_type=spec.artifact_type,
        locale=locale,
        provider=provider.name,
        model=provider.model,
        prompt_version=spec.version,
        input_hash=digest,
        source_content_ids=sorted({item.content_id for item in items}),
        artifact_metadata={},
    )
    try:
        response, attempts = await generate_with_retry(
            provider,
            max_retries=settings.ai_max_retries,
            sleep=sleep,
            system=system_prompt(spec, locale),
            user=render_user_message(payload),
            schema=spec.schema,
            schema_name=spec.artifact_type,
        )
    except AIProviderError as exc:
        artifact.status = "failed"
        artifact.error_code = exc.code
        artifact.artifact_metadata = {"attempts": getattr(exc, "attempts", 1)}
        if getattr(exc, "authentication", False):
            # The credential was refused: the batch stops here (see ``enrich_events``).
            artifact.artifact_metadata["authentication_failed"] = True
        return artifact
    artifact.model = response.model or provider.model
    outcome = validate_output(spec, response.data, mapping, items, locale, source_count)
    artifact.artifact_metadata = {"attempts": attempts, "usage": response.usage}
    if outcome.valid:
        artifact.status = "valid"
        artifact.output_json = outcome.output
    else:
        artifact.status = "failed"
        artifact.error_code = "validation_failed"
        artifact.artifact_metadata["validation_errors"] = outcome.errors[:10]
    return artifact


def _settled_attempt(session, event_key, spec, locale, digest):
    """``rejected``/``failed`` when the latest attempt for exactly this evidence and prompt is a
    reviewer rejection or a non-transient failure; such tasks wait for an explicit operator run."""

    latest = session.scalars(
        select(AIArtifact)
        .where(
            AIArtifact.entity_type == ENTITY_EVENT,
            AIArtifact.entity_key == event_key,
            AIArtifact.artifact_type == spec.artifact_type,
            AIArtifact.locale == locale,
            AIArtifact.input_hash == digest,
        )
        .order_by(AIArtifact.generated_at.desc())
        .limit(1)
    ).first()
    if latest is None:
        return None
    if latest.status == "rejected":
        return "rejected"
    if latest.status == "failed" and latest.error_code not in TRANSIENT_ERRORS:
        return "failed"
    return None


def _task_locale(event, evidence, locales):
    """The one locale an Event's AI is generated in (its primary locale), or None to skip it.

    ``locales`` None is the normal policy. An explicit ``locales`` may only name the Event's
    primary locale; anything else is a cross-language request and is returned as refused.
    """

    primary = primary_locale(evidence)
    if locales is None:
        return primary, []
    refused = [
        {"event_id": str(event.id), "locale": locale, "primary_locale": primary}
        for locale in locales
        if locale != primary
    ]
    return (primary if primary in locales else None), refused


def _plan(session, events, artifact_types, locales, provider, skip_settled=False):
    """Plan the primary-locale tasks; raises ``CrossLanguageGenerationDisabled`` (before any
    request or write) when ``locales`` names a non-primary locale of a selected Event."""

    tasks, counts, planned, refused = [], Counter(), [], []
    # Per selected Event: what its tasks would do (the plan printed before a bounded run).
    rows = {}
    for event in events:
        evidence = load_event_evidence(session, event.id)
        if evidence is None or not evidence.items:
            continue
        locale, rejected = _task_locale(event, evidence, locales)
        refused.extend(rejected)
        if locale is None:
            if primary_locale(evidence) is None:
                counts["skipped_no_primary_locale"] += 1
            continue
        quality = evidence_quality(evidence)
        eligibility = task_eligibility(quality)
        planned.append((event, evidence, locale, eligibility))
        rows[str(event.id)] = {
            **_event_row(event, evidence),
            "evidence_quality": quality["class"],
            "eligible": {kind: eligibility[kind][0] for kind in artifact_types},
            "task_states": {},
            "tasks": Counter(),
        }
    if refused:
        raise CrossLanguageGenerationDisabled(refused)
    for event, evidence, locale, eligibility in planned:
        for artifact_type in artifact_types:
            spec = INSIGHT_PROMPTS[artifact_type]
            if not eligibility[artifact_type][0]:
                # Product-1.1: the evidence cannot ground this task; no request is made (and it
                # is neither cached nor failed).
                counts["skipped_insufficient_evidence"] += 1
                rows[str(event.id)]["tasks"]["skipped_insufficient_evidence"] += 1
                rows[str(event.id)]["task_states"][artifact_type] = "skipped_insufficient_evidence"
                continue
            counts[f"eligible_{artifact_type}"] += 1
            digest = input_hash(spec.version, locale, evidence)
            cached = find_cached(
                session,
                ENTITY_EVENT,
                str(event.id),
                spec,
                locale,
                digest,
                provider.name if provider else None,
                provider.model if provider else None,
            )
            stale = session.scalar(
                select(AIArtifact.id)
                .where(
                    AIArtifact.entity_type == ENTITY_EVENT,
                    AIArtifact.entity_key == str(event.id),
                    AIArtifact.artifact_type == artifact_type,
                    AIArtifact.locale == locale,
                    AIArtifact.status == "valid",
                    AIArtifact.input_hash != digest,
                )
                .limit(1)
            )
            if not cached and skip_settled:
                settled = _settled_attempt(session, str(event.id), spec, locale, digest)
                if settled:
                    counts[f"skipped_{settled}"] += 1
                    rows[str(event.id)]["tasks"][f"skipped_{settled}"] += 1
                    rows[str(event.id)]["task_states"][artifact_type] = f"skipped_{settled}"
                    continue
            if stale:
                counts["stale"] += 1
            counts["cached" if cached else "would_generate"] += 1
            state = "cached" if cached else "would_generate"
            rows[str(event.id)]["tasks"][state] += 1
            rows[str(event.id)]["task_states"][artifact_type] = state
            tasks.append(
                {
                    "event": event,
                    "evidence": evidence,
                    "spec": spec,
                    "locale": locale,
                    "digest": digest,
                    "cached": cached is not None,
                    "stale": stale is not None,
                }
            )
    for row in rows.values():
        row["tasks"] = dict(row["tasks"])
    return tasks, counts, list(rows.values())


def _event_row(event, evidence):
    return {
        "event_id": str(event.id),
        "domain": str(event.domain),
        "canonical_title": event.canonical_title[:120],
        "content_count": evidence.content_count,
        "source_count": evidence.source_count,
        "languages": sorted({item.language for item in evidence.items}),
        "primary_locale": primary_locale(evidence),
    }


async def enrich_events(
    settings,
    session_factory,
    *,
    event_ids=None,
    since=None,
    domain=None,
    limit=DEFAULT_LIMIT,
    locales=None,
    artifact_types=tuple(INSIGHT_PROMPTS),
    dry_run=False,
    provider=None,
    sleep=None,
    skip_settled=False,
):
    """Generate missing Event insight artifacts; ``dry_run`` sends no request and writes nothing.

    Every task is in the Event's primary locale (at most two tasks per Event). ``locales`` is
    only an explicit confirmation of that locale: naming any other locale for a selected Event
    raises ``CrossLanguageGenerationDisabled`` before any request or write.

    ``skip_settled`` (the unattended ``intelligence-update`` stage) leaves a task alone when the
    latest attempt for its current evidence was rejected by a reviewer or failed non-transiently;
    the explicit ``ai-event-enrich`` command keeps retrying them as before.
    """

    if provider is None and not dry_run:
        provider = build_provider(settings)
    with session_factory() as session:
        events = select_events(session, event_ids, since, domain, limit)
        tasks, counts, event_rows = _plan(
            session, events, artifact_types, locales, provider, skip_settled
        )
    result = {
        "dry_run": dry_run,
        "provider": provider.name if provider else settings.ai_provider,
        "model": provider.model if provider else settings.ai_model,
        "provider_configured": settings.ai_configured,
        "events": event_rows,
        "tasks": len(tasks),
        "cached": counts["cached"],
        "stale": counts["stale"],
        "would_generate": counts["would_generate"],
        "estimated_requests": counts["would_generate"],
        "skipped_no_primary_locale": counts["skipped_no_primary_locale"],
        "skipped_insufficient_evidence": counts["skipped_insufficient_evidence"],
        "summary_tasks_eligible": counts["eligible_event_summary"],
        "why_tasks_eligible": counts["eligible_event_why_it_matters"],
    }
    if skip_settled:
        result["skipped_rejected"] = counts["skipped_rejected"]
        result["skipped_failed"] = counts["skipped_failed"]
    if dry_run:
        return result
    outcomes, statuses, errors = Counter(), [], Counter()
    requests, aborted, not_attempted = 0, None, 0
    for index, task in enumerate(tasks):
        event_key = str(task["event"].id)
        if task["cached"]:
            outcomes["cached"] += 1
            statuses.append(
                {
                    "event_id": event_key,
                    "type": task["spec"].artifact_type,
                    "locale": task["locale"],
                    "status": "cached",
                }
            )
            continue
        if requests and settings.ai_request_interval:
            await (sleep or asyncio.sleep)(settings.ai_request_interval)
        requests += 1
        evidence = task["evidence"]
        artifact = await generate_artifact(
            provider,
            settings,
            task["spec"],
            entity_type=ENTITY_EVENT,
            entity_key=event_key,
            locale=task["locale"],
            digest=task["digest"],
            payload=insight_payload(task["spec"], evidence, task["locale"]),
            mapping=ref_map(evidence),
            items=evidence.items,
            source_count=evidence.source_count,
            sleep=sleep,
        )
        status, error_code, artifact_id = artifact.status, artifact.error_code, str(artifact.id)
        refused = bool(artifact.artifact_metadata.get("authentication_failed"))
        with session_factory.begin() as session:
            if status == "valid":
                outcomes["stale_marked"] += mark_stale(
                    session,
                    ENTITY_EVENT,
                    event_key,
                    task["spec"].artifact_type,
                    task["locale"],
                    task["digest"],
                )
                if find_cached(
                    session,
                    ENTITY_EVENT,
                    event_key,
                    task["spec"],
                    task["locale"],
                    task["digest"],
                    artifact.provider,
                    artifact.model,
                ):
                    # A concurrent run stored the same artifact; keep the first (replay).
                    outcomes["cached"] += 1
                    continue
            session.add(artifact)
        outcomes[status] += 1
        if error_code:
            errors[error_code] += 1
        statuses.append(
            {
                "event_id": event_key,
                "type": task["spec"].artifact_type,
                "locale": task["locale"],
                "status": status,
                "error_code": error_code,
                "artifact_id": artifact_id,
            }
        )
        if refused:
            # Never repeat a refused credential: the remaining tasks are not attempted.
            aborted = "authentication_failed"
            not_attempted = sum(1 for rest in tasks[index + 1 :] if not rest["cached"])
            break
    return {
        **result,
        "requests": requests,
        "generated": outcomes["valid"],
        "failed": outcomes["failed"],
        "replayed": outcomes["cached"],
        "stale_marked": outcomes["stale_marked"],
        "errors": dict(errors),
        "aborted": aborted,
        "not_attempted": not_attempted,
        "results": statuses,
    }
