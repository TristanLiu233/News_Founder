"""Bounded, cached, incremental AI enrichment of Events (CLI-only; never on a page request).

For each selected Event, artifact type, and locale the prompt is chosen by the deterministic
generation mode (primary-locale or cross-language lineage) and the evidence hash is computed
from persisted data. A ``valid`` artifact with the same hash, provider, and model is a replay (no
request); older ``valid`` artifacts of the same Event/type/locale with a different hash are marked
``stale``. Otherwise one provider request is made, the output is validated
(``ai_evidence.validate_output``), and exactly one row is written: ``valid`` with the output, or
``failed`` with a short error code (no output kept). Every task commits on its own, so one
failure never rolls back another Event's artifacts.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from uuid import uuid4

from sqlalchemy import select, update

from industry_intelligence.ai.prompts import LANGUAGE_NAMES
from industry_intelligence.ai.prompts.event_insights import INSIGHT_PROMPTS, insight_spec
from industry_intelligence.ai.provider import AIProviderError, generate_with_retry
from industry_intelligence.ai.providers import build_provider
from industry_intelligence.db.models import AIArtifact, Event, EventRedirect
from industry_intelligence.services.ai_evidence import (
    generation_mode,
    input_hash,
    load_event_evidence,
    ref_map,
    render_event,
    render_user_message,
    validate_output,
)
from industry_intelligence.services.events import resolve_event_redirect

ENTITY_EVENT = "event"
LOCALES = ("en", "zh-CN")
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
# Failures that a later run may retry without a reviewer: the provider never produced a checked
# answer (network, rate limit, server, authentication, configuration). A validation failure,
# refusal, or malformed answer was a real (paid) answer and is not retried unattended.
TRANSIENT_ERRORS = frozenset(
    {"timeout", "rate_limited", "server_error", "request_rejected", "not_configured"}
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
    if spec.cross_language:
        payload["output_language"] = LANGUAGE_NAMES.get(locale, "English")
    return payload


def select_events(
    session, event_ids=None, since=None, domain=None, limit=DEFAULT_LIMIT, max_limit=MAX_LIMIT
):
    """Canonical, non-empty Events: the given IDs (redirects followed) or the most recent ones."""

    if event_ids:
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


def _plan(session, events, artifact_types, locales, provider, skip_settled=False):
    tasks, counts = [], Counter()
    for event in events:
        evidence = load_event_evidence(session, event.id)
        if evidence is None or not evidence.items:
            continue
        for artifact_type in artifact_types:
            for locale in locales:
                spec = insight_spec(artifact_type, generation_mode(evidence, locale))
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
                        continue
                if stale:
                    counts["stale"] += 1
                counts["cached" if cached else "would_generate"] += 1
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
    return tasks, counts


def _event_row(event, evidence):
    return {
        "event_id": str(event.id),
        "domain": str(event.domain),
        "canonical_title": event.canonical_title[:120],
        "content_count": evidence.content_count,
        "source_count": evidence.source_count,
        "languages": sorted({item.language for item in evidence.items}),
    }


async def enrich_events(
    settings,
    session_factory,
    *,
    event_ids=None,
    since=None,
    domain=None,
    limit=DEFAULT_LIMIT,
    locales=LOCALES,
    artifact_types=tuple(INSIGHT_PROMPTS),
    dry_run=False,
    provider=None,
    sleep=None,
    skip_settled=False,
):
    """Generate missing Event insight artifacts; ``dry_run`` sends no request and writes nothing.

    ``skip_settled`` (the unattended ``intelligence-update`` stage) leaves a task alone when the
    latest attempt for its current evidence was rejected by a reviewer or failed non-transiently;
    the explicit ``ai-event-enrich`` command keeps retrying them as before.
    """

    if provider is None and not dry_run:
        provider = build_provider(settings)
    with session_factory() as session:
        events = select_events(session, event_ids, since, domain, limit)
        tasks, counts = _plan(session, events, artifact_types, locales, provider, skip_settled)
        event_rows = {}
        for task in tasks:
            event_rows.setdefault(
                str(task["event"].id), _event_row(task["event"], task["evidence"])
            )
    result = {
        "dry_run": dry_run,
        "provider": provider.name if provider else settings.ai_provider,
        "model": provider.model if provider else settings.ai_model,
        "provider_configured": settings.ai_configured,
        "events": list(event_rows.values()),
        "tasks": len(tasks),
        "cached": counts["cached"],
        "stale": counts["stale"],
        "would_generate": counts["would_generate"],
        "estimated_requests": counts["would_generate"],
    }
    if skip_settled:
        result["skipped_rejected"] = counts["skipped_rejected"]
        result["skipped_failed"] = counts["skipped_failed"]
    if dry_run:
        return result
    outcomes, statuses, errors = Counter(), [], Counter()
    requests = 0
    for task in tasks:
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
    return {
        **result,
        "requests": requests,
        "generated": outcomes["valid"],
        "failed": outcomes["failed"],
        "replayed": outcomes["cached"],
        "stale_marked": outcomes["stale_marked"],
        "errors": dict(errors),
        "results": statuses,
    }
