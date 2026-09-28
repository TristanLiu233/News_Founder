"""Read model for displayable Event AI insights; persisted data only, never a provider call.

For each insight type the current evidence hash is recomputed from the Event's persisted
content, so staleness never depends on a batch having run:

* ``available``: a ``valid`` artifact built from exactly the current evidence and prompt;
* ``failed``: the latest attempt for the current evidence failed and no valid artifact exists;
* ``stale``: artifacts exist only for older evidence (the Event changed); their text is not
  returned, because it may no longer describe the Event;
* ``not_generated``: nothing was generated (for example no AI provider is configured).

Pending entity or merge suggestions are review material and are never returned here.
"""

from __future__ import annotations

from sqlalchemy import select

from industry_intelligence.ai.prompts.event_insights import INSIGHT_PROMPTS
from industry_intelligence.db.models import AIArtifact
from industry_intelligence.services.ai_enrichment import ENTITY_EVENT
from industry_intelligence.services.ai_evidence import input_hash, load_event_evidence
from industry_intelligence.services.events import resolve_event_redirect


def _iso(value):
    return value.isoformat() if value else None


def _evidence_row(item, position):
    return {
        "content_id": item.content_id,
        "position": position,
        "source": item.source,
        "title": item.title,
        "url": item.url,
        "language": item.language,
        "published_at": _iso(item.published_at),
    }


def _insight(spec, evidence, artifacts, locale):
    digest = input_hash(spec.version, locale, evidence)
    current = [a for a in artifacts if a.input_hash == digest and a.status != "rejected"]
    valid = [a for a in current if a.status == "valid"]
    if valid:
        artifact = max(valid, key=lambda a: a.generated_at)
        items = {item.content_id: (item, index) for index, item in enumerate(evidence.items, 1)}
        output = artifact.output_json or {}
        return {
            "state": "available",
            "artifact": {
                "id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "locale": artifact.locale,
                "provider": artifact.provider,
                "model": artifact.model,
                "prompt_version": artifact.prompt_version,
                "generated_at": _iso(artifact.generated_at),
                "insufficient_evidence": bool(output.get("insufficient_evidence")),
                "points": [
                    {
                        "text": point["text"],
                        "context": point.get("context"),
                        "evidence": [
                            _evidence_row(*items[content_id])
                            for content_id in point.get("evidence", [])
                            if content_id in items
                        ],
                    }
                    for point in output.get("points", [])
                ],
            },
        }
    if current:
        latest = max(current, key=lambda a: a.generated_at)
        return {"state": "failed", "artifact": None, "error_code": latest.error_code}
    if any(a.status in {"valid", "stale"} for a in artifacts):
        return {"state": "stale", "artifact": None}
    return {"state": "not_generated", "artifact": None}


def event_ai(session, event_id, locale="en"):
    """AI insights of one canonical Event in ``locale``; None for an unknown Event."""

    canonical_id = resolve_event_redirect(session, event_id)
    evidence = load_event_evidence(session, canonical_id)
    if evidence is None:
        return None
    artifacts = session.scalars(
        select(AIArtifact).where(
            AIArtifact.entity_type == ENTITY_EVENT,
            AIArtifact.entity_key == str(canonical_id),
            AIArtifact.locale == locale,
            AIArtifact.artifact_type.in_(list(INSIGHT_PROMPTS)),
        )
    ).all()
    by_type = {}
    for artifact in artifacts:
        by_type.setdefault(artifact.artifact_type, []).append(artifact)
    return {
        "event_id": str(canonical_id),
        "requested_id": str(event_id),
        "redirected": canonical_id != event_id,
        "locale": locale,
        "ai_generated": True,
        "content_count": evidence.content_count,
        "source_count": evidence.source_count,
        "single_source": evidence.source_count == 1,
        "insights": {
            artifact_type: _insight(spec, evidence, by_type.get(artifact_type, []), locale)
            for artifact_type, spec in INSIGHT_PROMPTS.items()
        },
    }
