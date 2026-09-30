"""One bounded daily intelligence update (CLI ``intelligence-update``); no daemon, no scheduler.

The run only orchestrates existing services, in dependency order, each with its own
transaction boundary, so a failure never rolls back work an earlier stage committed:

1. ``precheck``: database reachable, Alembic current = head, ``data-quality-audit`` without an
   error (read-only). A failure stops the run before any write.
2. ``rankings``: each ranking source (``source_ingestion.ingest``), fetched outside a transaction
   and persisted in its own; batch keys derive from the run ID, so a rerun with ``--run-id``
   replays sources already stored without a request. Search documents of touched Subjects are
   refreshed inside each source's transaction (existing behaviour).
3. ``composite``: ``rank_domain`` per domain (immutable run keyed by its inputs; a replay when
   no ranking batch changed).
4. ``company_rankings``: each ranking with ``daily_update`` (``ingest_company_ranking``; daily
   snapshot keys replay).
5. ``content``: each content source (``ingest_content``: dedup by identity/URL, new items linked
   in a SAVEPOINT).
6. ``links``: ``relink_all_content`` (keeps ``reviewed`` links) and ``relink_all_companies`` in
   one transaction, so new Subjects and Companies reach earlier content.
7. ``events``: incremental ``build_events`` and ``relink_all_events`` in one transaction. Events
   are only created or joined: nothing is rebuilt, retired, or merged, so every Event permalink
   keeps resolving (``event-rebuild`` stays manual).
8. ``ai``: optional and bounded (Product-1): ``--ai-limit`` Events (default 12, hard cap 15,
   validated in code) chosen deterministically by ``ai_enrichment.select_bounded_events``
   (current Trending Events, then the most recent ones, round-robin across domains). The plan is
   computed first; only when a task needs generating is the provider preflighted (zero-generation
   Models API check) before any Messages request. ``enrich_events`` then generates only missing,
   stale, or transiently failed artifacts (valid artifacts replay, reviewer rejections and
   non-transient failures are left for an operator) and stops at the first refused credential.
   Primary locale only (AI-LP1): at most two tasks and so two provider requests per Event.
9. ``postcheck``: ``data-quality-audit`` again (read-only).

Source failures are isolated per source. A failed ``links`` stage skips ``events`` and ``ai``;
a failed ``events`` stage skips ``ai``. Status: ``failed`` when a precheck, postcheck, or
required stage failed, or every attempted source of a group failed; ``degraded`` when the
orchestration completed but one or more isolated operations failed (some sources, the optional
AI stage, or recording the run history); otherwise ``success``. Repairs, merges, migrations,
rebuilds, and profile ingestion are never part of this run.

Operations-2: ``execute_update`` (the CLI's real run) first takes the global update lock
(``update_operations.UpdateLock``); when another run holds it the result is ``already_running``
with no request and no write. Under the lock it records the run in ``update_runs`` and every
source attempt in ``source_attempts`` (the facts behind ``source-health``). ``plan_update``
never takes the lock and only reports whether it is held right now.
"""

from __future__ import annotations

import inspect
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text

from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES, NEWS_RANKINGS
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.db.models import (
    ContentItem,
    Event,
    EventContentLink,
    EventRedirect,
    EventRetiredId,
    RankObservation,
    Subject,
)
from industry_intelligence.db.models.ai import AIArtifact
from industry_intelligence.db.models.search import SubjectRedirect
from industry_intelligence.domain.enums import Domain
from industry_intelligence.services.ingestion_phase import failure_phase

logger = logging.getLogger(__name__)

SUCCESS, DEGRADED, FAILED, SKIPPED = "success", "degraded", "failed", "skipped"
ALREADY_RUNNING = "already_running"
# 2 is argparse's usage error; 4 means another run holds the lock and nothing was done.
EXIT_CODES = {SUCCESS: 0, FAILED: 1, DEGRADED: 3, ALREADY_RUNNING: 4}
# Product-1: the bounded AI stage's Event cap (see ``ai_enrichment.validate_max_events``).
DEFAULT_AI_LIMIT = 12
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


@dataclass
class StageResult:
    name: str
    status: str = SUCCESS
    counts: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    reason: str | None = None
    duration_ms: int = 0
    # Per-item plan rows (the AI stage's selected Events); output only, never recorded.
    details: list = field(default_factory=list)


@dataclass
class UpdateResult:
    run_id: str
    started_at: str
    finished_at: str | None = None
    status: str = SUCCESS
    stages: list = field(default_factory=list)
    deltas: dict = field(default_factory=dict)
    recording: dict | None = None

    @property
    def exit_code(self):
        return EXIT_CODES[self.status]

    def stage(self, name):
        return next((stage for stage in self.stages if stage.name == name), None)

    def to_dict(self):
        return asdict(self)


def _check_ai_limit(ai_limit):
    """Refuse an out-of-range AI Event cap before any lock, request, or write."""

    from industry_intelligence.services.ai_enrichment import validate_max_events

    return validate_max_events(ai_limit)


def new_run_id(now=None):
    return f"{(now or datetime.now(UTC)).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def ranking_batch_key(run_id):
    return f"update:{run_id}"[:100]


def error_detail(key, exc):
    """A safe, categorised error: exception type, category, failing phase, and HTTP status only
    (no message, headers, or URL parameters)."""

    detail = {"source": key, "error": type(exc).__name__, "phase": failure_phase(exc)}
    if isinstance(exc, httpx.HTTPStatusError):
        detail["category"] = "http_status"
        detail["status_code"] = exc.response.status_code
    elif isinstance(exc, httpx.TimeoutException):
        detail["category"] = "timeout"
    elif isinstance(exc, httpx.TransportError | ConnectionError | OSError):
        detail["category"] = "network"
    elif isinstance(exc, ValueError):
        detail["category"] = "invalid_response"
    else:
        detail["category"] = "error"
    return detail


def select_sources(keys=None):
    """(ranking, company ranking, content) keys of the run; ``keys`` narrows it explicitly.

    The content group holds the chronological feeds (``CONTENT_SOURCES``) and the publishers'
    ranked article lists (``NEWS_RANKINGS``)."""

    if keys:
        wanted = set(keys)
        return (
            [key for key in SOURCES if key in wanted],
            [key for key in COMPANY_RANKINGS if key in wanted],
            [key for key in (*CONTENT_SOURCES, *NEWS_RANKINGS) if key in wanted],
        )
    return (
        list(SOURCES),
        [key for key, definition in COMPANY_RANKINGS.items() if definition.daily_update],
        # Chronological feeds, then publishers' ranked article lists (both produce content).
        [*CONTENT_SOURCES, *NEWS_RANKINGS],
    )


# --- read-only checks ------------------------------------------------------------------------


def migration_state(session):
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    heads = set(ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads())
    current = set(MigrationContext.configure(session.connection()).get_current_heads())
    return sorted(current), sorted(heads)


def run_audit(session_factory):
    from industry_intelligence.services.data_quality_audit import audit_data_quality

    session = session_factory()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = audit_data_quality(session)
    finally:
        session.rollback()
        session.close()
    return report["summary"]["result"]


def check_state(session_factory, settings, check=migration_state, audit=run_audit, name="precheck"):
    stage = StageResult(name)
    try:
        session = session_factory()
        try:
            session.execute(text("SET TRANSACTION READ ONLY"))
            session.execute(text("SELECT 1"))
            stage.counts["database"] = "reachable"
            if check is not None:
                current, heads = check(session)
                stage.counts["alembic_current"] = current
                stage.counts["alembic_heads"] = heads
                if not heads or current != heads:
                    stage.status, stage.reason = FAILED, "alembic_not_at_head"
        finally:
            session.rollback()
            session.close()
    except Exception as exc:
        stage.status, stage.reason = FAILED, "database_unavailable"
        stage.errors.append({"error": type(exc).__name__})
        return stage
    stage.counts["ai_provider_configured"] = settings.ai_configured
    if stage.status == FAILED:
        return stage
    try:
        audit_result = audit(session_factory)
    except Exception as exc:
        stage.status, stage.reason = FAILED, "audit_failed"
        stage.errors.append({"error": type(exc).__name__})
        return stage
    stage.counts["audit_errors"] = audit_result["errors"]
    stage.counts["audit_warnings"] = audit_result["warnings"]
    if audit_result["errors"]:
        stage.status, stage.reason = FAILED, "data_quality_errors"
    return stage


def table_counts(session_factory):
    """Row counts behind the run's delta report (read-only)."""

    active = Subject.id.not_in(select(SubjectRedirect.duplicate_id))
    queries = {
        "subjects": select(func.count()).select_from(Subject).where(active),
        "rank_observations": select(func.count()).select_from(RankObservation),
        "content_items": select(func.count()).select_from(ContentItem),
        "events": select(func.count()).select_from(Event),
        "event_retired_ids": select(func.count()).select_from(EventRetiredId),
        "event_redirects": select(func.count()).select_from(EventRedirect),
        "ai_artifacts": select(func.count()).select_from(AIArtifact),
    }
    with session_factory() as session:
        return {name: session.scalar(query) for name, query in queries.items()}


# --- source stages ---------------------------------------------------------------------------


def _source_status(stage):
    failed = [row for row in stage.sources if "error" in row]
    stage.counts["attempted"] = len(stage.sources)
    stage.counts["succeeded"] = len(stage.sources) - len(failed)
    stage.counts["failed"] = len(failed)
    if not stage.sources:
        stage.status, stage.reason = SKIPPED, "no_sources_selected"
    elif len(failed) == len(stage.sources):
        stage.status, stage.reason = FAILED, "all_sources_failed"
    elif failed:
        stage.status = DEGRADED


async def _each_source(stage, keys, ingest_one, keep, record=None, kind=None):
    """Ingest each source in turn; ``record`` (under the update lock) stores every attempt."""

    for key in keys:
        started_at = datetime.now(UTC)
        try:
            result = await ingest_one(key)
        except Exception as exc:
            detail = error_detail(key, exc)
            if record is not None:
                record(kind, key, started_at, datetime.now(UTC), detail=detail)
            logger.warning(
                "stage=%s source=%s event=source_failed error=%s category=%s",
                stage.name,
                key,
                detail["error"],
                detail["category"],
            )
            stage.sources.append(detail)
            continue
        if record is not None:
            record(kind, key, started_at, datetime.now(UTC), result=result)
        stage.sources.append({"source": key, **{name: result.get(name) for name in keep}})
    _source_status(stage)


async def ranking_stage(settings, session_factory, keys, run_id, record=None):
    from industry_intelligence.services.source_ingestion import ingest

    stage = StageResult("rankings")
    batch_key = ranking_batch_key(run_id)
    await _each_source(
        stage,
        keys,
        lambda key: ingest(key, settings, session_factory, batch_key),
        ("fetched_count", "persisted_count", "skipped_count", "failed_count", "replayed"),
        record,
        "ranking",
    )
    ok = [row for row in stage.sources if "error" not in row]
    stage.counts["observations_persisted"] = sum(row["persisted_count"] or 0 for row in ok)
    stage.counts["replayed_sources"] = sum(1 for row in ok if row.get("replayed"))
    return stage


async def company_ranking_stage(settings, session_factory, keys, record=None):
    from industry_intelligence.services.company_ranking_ingestion import ingest_company_ranking

    stage = StageResult("company_rankings")
    await _each_source(
        stage,
        keys,
        lambda key: ingest_company_ranking(key, settings, session_factory),
        ("snapshot_key", "status", "persisted_count", "replayed", "unchanged"),
        record,
        "company_ranking",
    )
    return stage


async def content_stage(settings, session_factory, keys, record=None, run_id=None):
    from industry_intelligence.services.content_ingestion import ingest_content
    from industry_intelligence.services.news_ranking_ingestion import ingest_news_ranking

    stage = StageResult("content")
    # A ranked list stores one snapshot per run: a rerun with the same run ID replays it.
    batch_key = ranking_batch_key(run_id) if run_id else None

    def ingest_one(key):
        if key in NEWS_RANKINGS:
            return ingest_news_ranking(key, settings, session_factory, batch_key)
        return ingest_content(key, settings, session_factory)

    await _each_source(
        stage,
        keys,
        ingest_one,
        (
            "domain",
            "fetched_count",
            "persisted_count",
            "replayed_count",
            "invalid_count",
            "failed_count",
            "link_count",
            "link_error",
        ),
        record,
        "content",
    )
    ok = [row for row in stage.sources if "error" not in row]
    for name in ("persisted_count", "replayed_count", "invalid_count", "link_count"):
        stage.counts[name] = sum(row[name] or 0 for row in ok)
    link_errors = [row["source"] for row in ok if row.get("link_error")]
    if link_errors:
        # Content is kept; ``links`` recomputes every link below.
        stage.counts["link_errors"] = len(link_errors)
        if stage.status == SUCCESS:
            stage.status = DEGRADED
    return stage


# --- deterministic stages --------------------------------------------------------------------


def _in_transaction(stage, session_factory, work):
    """Run ``work(session)`` in one transaction; a failure rolls back only this stage."""

    try:
        with session_factory.begin() as session:
            stage.counts.update(work(session))
    except Exception as exc:
        stage.status, stage.reason = FAILED, "stage_error"
        stage.errors.append({"error": type(exc).__name__})
        logger.error("stage=%s event=stage_failed error=%s", stage.name, type(exc).__name__)
    return stage


def composite_stage(settings, session_factory, domains=tuple(Domain)):
    from industry_intelligence.services.ranking import rank_domain

    def work(session):
        created = [rank_domain(session, domain, settings)[1] for domain in domains]
        return {"runs_created": sum(created), "runs_replayed": len(created) - sum(created)}

    return _in_transaction(StageResult("composite"), session_factory, work)


def links_stage(session_factory):
    from industry_intelligence.db.models import CompanySubjectLink
    from industry_intelligence.services.company_subject_linking import relink_all_companies
    from industry_intelligence.services.content_ingestion import relink_all_content

    def pairs(session):
        return set(
            session.execute(
                select(
                    CompanySubjectLink.company_id,
                    CompanySubjectLink.subject_id,
                    CompanySubjectLink.strategy,
                )
            ).all()
        )

    def work(session):
        content = relink_all_content(session)
        before = pairs(session)
        companies = relink_all_companies(session)
        after = pairs(session)
        return {
            "content_links": content["links"],
            "content_links_added": content["added"],
            "content_links_removed": content["removed"],
            "content_links_strategy_changed": content["strategy_changed"],
            "content_links_reviewed_kept": content["reviewed_kept"],
            "company_subject_links": companies["links"],
            "company_subject_links_added": len(after - before),
            "company_subject_links_removed": len(before - after),
        }

    return _in_transaction(StageResult("links"), session_factory, work)


def events_stage(session_factory):
    from industry_intelligence.services.event_ingestion import build_events, relink_all_events

    def count(session, model):
        return session.scalar(select(func.count()).select_from(model))

    def work(session):
        retired, redirects = count(session, EventRetiredId), count(session, EventRedirect)
        built = build_events(session)
        relinked = relink_all_events(session)
        unassigned = session.scalar(
            select(func.count())
            .select_from(ContentItem)
            .outerjoin(EventContentLink, EventContentLink.content_item_id == ContentItem.id)
            .where(EventContentLink.id.is_(None))
        )
        if unassigned or count(session, EventRetiredId) != retired:
            # Incremental building assigns every item and never retires an Event.
            raise RuntimeError("event invariant violated")
        return {
            "processed": built["processed"],
            "created_events": built["created_events"],
            "joined_existing": built["joined_existing"],
            "touched_events": built["touched_events"],
            "event_subject_links": relinked["subject_links"],
            "event_company_links": relinked["company_links"],
            "retired_ids_registered": count(session, EventRetiredId) - retired,
            "redirects_created": count(session, EventRedirect) - redirects,
        }

    return _in_transaction(StageResult("events"), session_factory, work)


async def ai_stage(settings, session_factory, limit, provider=None, dry_run=False):
    from industry_intelligence.ai.provider import AIProviderError, preflight
    from industry_intelligence.ai.providers import build_provider
    from industry_intelligence.services.ai_enrichment import (
        SELECTION_RULE,
        enrich_events,
        select_bounded_events,
        validate_max_events,
    )

    stage = StageResult("ai")
    limit = validate_max_events(limit)
    if provider is None and not settings.ai_configured:
        stage.status, stage.reason = SKIPPED, "provider_not_configured"
        return stage
    try:
        with session_factory() as session:
            event_ids = select_bounded_events(session, limit)
        stage.counts = {"max_events": limit, "selection": SELECTION_RULE}
        if not event_ids:
            stage.status, stage.reason = SKIPPED, "no_eligible_events"
            return stage
        if not dry_run and provider is None:
            provider = build_provider(settings)  # builds a client; sends nothing
        options = {"event_ids": event_ids, "provider": provider, "skip_settled": True}
        planned = await enrich_events(settings, session_factory, dry_run=True, **options)
        result = planned
        if not dry_run:
            if planned["would_generate"]:
                await preflight(provider)
            result = await enrich_events(settings, session_factory, **options)
    except AIProviderError as exc:
        stage.status, stage.reason = FAILED, "provider_preflight_failed"
        stage.counts.update(requests=0)
        stage.errors.append({"error": exc.code})
        logger.warning("stage=ai event=preflight_failed error=%s", exc.code)
        return stage
    except Exception as exc:
        stage.status, stage.reason = FAILED, "provider_error"
        stage.errors.append({"error": type(exc).__name__})
        logger.warning("stage=ai event=stage_failed error=%s", type(exc).__name__)
        return stage
    keys = (
        "tasks",
        "cached",
        "stale",
        "would_generate",
        "skipped_rejected",
        "skipped_failed",
        "skipped_no_primary_locale",
    )
    domains = Counter(row["domain"] for row in planned["events"])
    stage.counts.update(
        selected_events=len(event_ids),
        selected_by_domain=", ".join(f"{d} {domains[d]}" for d in sorted(domains)),
        **{key: result[key] for key in keys},
    )
    stage.details = planned["events"]
    if not dry_run:
        errors = result["errors"]
        stage.counts.update(
            requests=result["requests"],
            generated=result["generated"],
            failed=result["failed"],
            validation_failed=errors.get("validation_failed", 0),
            stale_marked=result["stale_marked"],
            not_attempted=result["not_attempted"],
        )
        stage.errors = [{"error": code, "count": n} for code, n in errors.items()]
        if result["aborted"]:
            stage.status, stage.reason = FAILED, result["aborted"]
        elif result["failed"]:
            stage.status, stage.reason = DEGRADED, "generation_failures"
    return stage


# --- orchestration ---------------------------------------------------------------------------

# A failed (or itself blocked) dependency skips the stage; the failure already fails the run.
DEPENDS_ON = {"events": ("links",), "ai": ("events",)}


def _blocked_by(result, name):
    for dependency in DEPENDS_ON.get(name, ()):
        stage = result.stage(dependency)
        if stage.status == FAILED or (stage.reason or "").startswith("blocked_by_"):
            return dependency
    return None


def overall_status(stages):
    """``failed`` if any stage but the optional AI stage failed; AI failures and partial source
    failures only degrade the run; skipped stages (options, no provider) change nothing."""

    status = SUCCESS
    for stage in stages:
        if stage.status == FAILED:
            if stage.name == "ai":
                status = DEGRADED if status == SUCCESS else status
            else:
                return FAILED
        elif stage.status == DEGRADED and status == SUCCESS:
            status = DEGRADED
    return status


def _finish(stage, started):
    stage.duration_ms = round((perf_counter() - started) * 1000)
    logger.info(
        "stage=%s event=stage_completed status=%s duration_ms=%s",
        stage.name,
        stage.status,
        stage.duration_ms,
    )
    return stage


async def run_update(
    settings,
    session_factory,
    *,
    run_id=None,
    sources=None,
    skip_ai=False,
    ai_limit=DEFAULT_AI_LIMIT,
    provider=None,
    check=migration_state,
    audit=run_audit,
    recorder=None,
    started_at=None,
):
    """Run every stage once; returns an ``UpdateResult`` (never raises for a stage failure).

    This is the lock-free core; ``execute_update`` wraps it with the global lock and the run
    history (``recorder``)."""

    _check_ai_limit(ai_limit)
    run_id = run_id or new_run_id()
    started_at = started_at or datetime.now(UTC)
    result = UpdateResult(run_id=run_id, started_at=started_at.isoformat())
    record = recorder.attempt if recorder is not None else None
    rank_keys, company_keys, content_keys = select_sources(sources)
    started = perf_counter()
    precheck = _finish(check_state(session_factory, settings, check, audit), started)
    result.stages.append(precheck)
    if precheck.status == FAILED:
        result.status = FAILED
        result.finished_at = datetime.now(UTC).isoformat()
        return result
    before = table_counts(session_factory)

    async def ai():
        if skip_ai:
            return StageResult("ai", status=SKIPPED, reason="skip_ai")
        return await ai_stage(settings, session_factory, ai_limit, provider)

    db = session_factory
    steps = (
        ("rankings", lambda: ranking_stage(settings, db, rank_keys, run_id, record=record)),
        ("composite", lambda: composite_stage(settings, db)),
        (
            "company_rankings",
            lambda: company_ranking_stage(settings, db, company_keys, record=record),
        ),
        (
            "content",
            lambda: content_stage(settings, db, content_keys, record=record, run_id=run_id),
        ),
        ("links", lambda: links_stage(db)),
        ("events", lambda: events_stage(db)),
        ("ai", ai),
        ("postcheck", lambda: check_state(db, settings, None, audit, "postcheck")),
    )
    for name, step in steps:
        blocked = _blocked_by(result, name)
        if blocked:
            result.stages.append(StageResult(name, status=SKIPPED, reason=f"blocked_by_{blocked}"))
            continue
        started = perf_counter()
        stage = step()
        if inspect.isawaitable(stage):
            stage = await stage
        result.stages.append(_finish(stage, started))
    result.deltas = {
        name: count - before[name] for name, count in table_counts(session_factory).items()
    }
    result.status = overall_status(result.stages)
    result.finished_at = datetime.now(UTC).isoformat()
    logger.info("run=%s event=update_completed status=%s", run_id, result.status)
    return result


def _stage_summary(result):
    """What ``update_runs.stage_summary`` keeps: statuses, reasons, durations, failed sources,
    and deltas (never logs, messages, or tracebacks)."""

    return {
        "stages": [
            {
                "name": stage.name,
                "status": stage.status,
                "reason": stage.reason,
                "duration_ms": stage.duration_ms,
                "failed_sources": [row["source"] for row in stage.sources if "error" in row],
            }
            for stage in result.stages
        ],
        "deltas": result.deltas,
    }


async def execute_update(
    settings,
    session_factory,
    *,
    run_id=None,
    sources=None,
    skip_ai=False,
    ai_limit=DEFAULT_AI_LIMIT,
    provider=None,
    check=migration_state,
    audit=run_audit,
    lock=None,
):
    """The real, lock-protected run behind ``intelligence-update``.

    1. ``pg_try_advisory_lock`` on a dedicated connection; held elsewhere → ``already_running``
       (exit 4) with no source request, provider request, or write.
    2. Under the lock: mark abandoned ``running`` rows ``interrupted``, record this run.
    3. ``run_update`` (every stage, each source attempt recorded).
    4. Record the final status; release the lock (``finally``; PostgreSQL also releases it if
       the process dies)."""

    from industry_intelligence.services.update_operations import RunRecorder, UpdateLock

    _check_ai_limit(ai_limit)
    run_id = run_id or new_run_id()
    started_at = datetime.now(UTC)
    lock = lock or UpdateLock(settings.database_url)
    try:
        acquired = lock.acquire()
    except Exception as exc:
        lock.release()
        stage = StageResult("lock", status=FAILED, reason="database_unavailable")
        stage.errors.append({"error": type(exc).__name__})
        return UpdateResult(
            run_id=run_id,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            status=FAILED,
            stages=[stage],
        )
    if not acquired:
        lock.release()  # nothing held; only disposes the dedicated engine
        logger.warning("run=%s event=update_refused status=%s", run_id, ALREADY_RUNNING)
        return UpdateResult(
            run_id=run_id,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            status=ALREADY_RUNNING,
            stages=[StageResult("lock", status=SKIPPED, reason=ALREADY_RUNNING)],
        )
    try:
        recorder = RunRecorder(session_factory)
        recorder.open(
            run_id,
            started_at,
            {
                "sources": sorted(sources) if sources else None,
                "skip_ai": skip_ai,
                "ai_limit": ai_limit,
            },
        )
        try:
            result = await run_update(
                settings,
                session_factory,
                run_id=run_id,
                sources=sources,
                skip_ai=skip_ai,
                ai_limit=ai_limit,
                provider=provider,
                check=check,
                audit=audit,
                recorder=recorder,
                started_at=started_at,
            )
        except Exception as exc:
            logger.error("run=%s event=update_crashed error=%s", run_id, type(exc).__name__)
            stage = StageResult("orchestration", status=FAILED, reason="unexpected_error")
            stage.errors.append({"error": type(exc).__name__})
            result = UpdateResult(
                run_id=run_id,
                started_at=started_at.isoformat(),
                finished_at=datetime.now(UTC).isoformat(),
                status=FAILED,
                stages=[stage],
            )
        if recorder.failed and result.status == SUCCESS:
            result.status = DEGRADED
        recorder.close(result.status, result.exit_code, _stage_summary(result))
        if recorder.failed and result.status == SUCCESS:
            result.status = DEGRADED
        result.recording = recorder.summary()
        return result
    finally:
        lock.release()


async def plan_update(
    settings,
    session_factory,
    *,
    sources=None,
    skip_ai=False,
    ai_limit=DEFAULT_AI_LIMIT,
    check=migration_state,
    audit=run_audit,
):
    """What a run would do, from persisted data only: no network request, provider call, or
    database write (read-only transactions; the AI part is ``enrich_events(dry_run=True)``)."""

    from industry_intelligence.services.content_ingestion import relink_all_content
    from industry_intelligence.services.update_operations import lock_state

    _check_ai_limit(ai_limit)
    ranking_keys, company_keys, content_keys = select_sources(sources)
    precheck = check_state(session_factory, settings, check, audit)
    plan = {
        "status": "plan",
        "precheck": asdict(precheck),
        "stages": [
            {"stage": "rankings", "sources": ranking_keys},
            {"stage": "composite", "domains": [str(domain) for domain in Domain]},
            {"stage": "company_rankings", "sources": company_keys},
            {"stage": "content", "sources": content_keys},
            {"stage": "links"},
            {"stage": "events"},
            {"stage": "ai"},
            {"stage": "postcheck"},
        ],
        "manual_only_company_rankings": [
            key for key, definition in COMPANY_RANKINGS.items() if not definition.daily_update
        ],
    }
    if precheck.status == FAILED:
        return plan
    session = session_factory()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        plan["lock"] = {
            "state": lock_state(session),
            "note": "informational only; a real run takes the lock itself and refuses to start "
            "while another run holds it",
        }
        relink = relink_all_content(session, dry_run=True)
        plan["stages"][4]["content_link_preview"] = {
            key: relink[key] for key in ("links", "added", "removed", "strategy_changed")
        }
        plan["stages"][5]["content_items_without_event"] = session.scalar(
            select(func.count())
            .select_from(ContentItem)
            .outerjoin(EventContentLink, EventContentLink.content_item_id == ContentItem.id)
            .where(EventContentLink.id.is_(None))
        )
    finally:
        session.rollback()
        session.close()
    if skip_ai:
        plan["stages"][6].update(status=SKIPPED, reason="skip_ai")
    else:
        ai = await ai_stage(settings, session_factory, ai_limit, dry_run=True)
        status = "planned" if ai.status == SUCCESS else ai.status
        plan["stages"][6].update(status=status, reason=ai.reason, **ai.counts)
        plan["stages"][6]["selected_events"] = ai.details
    return plan


# --- output ----------------------------------------------------------------------------------


def _counts(counts):
    return ", ".join(f"{key} {value}" for key, value in counts.items())


def render_text(result):
    if result.status == ALREADY_RUNNING:
        return (
            f"Intelligence Update {result.run_id}\n\n"
            "Another intelligence-update holds the update lock; nothing was requested or "
            "written.\n\nOverall: ALREADY_RUNNING"
        )
    lines = [f"Intelligence Update {result.run_id}", ""]
    for stage in result.stages:
        head = f"{stage.name}: {stage.status.upper()}"
        if stage.reason:
            head += f" ({stage.reason})"
        lines.append(f"{head} [{stage.duration_ms} ms]")
        if stage.counts:
            lines.append(f"  {_counts(stage.counts)}")
        for row in stage.details:
            lines.append(
                f"  event {row['event_id']} {row['domain']} sources {row['source_count']} "
                f"locale {row['primary_locale']}: {_counts(row['tasks']) or 'no tasks'}"
            )
        for row in stage.sources:
            if "error" in row:
                status = f" {row['status_code']}" if "status_code" in row else ""
                lines.append(
                    f"  FAILED {row['source']}: {row['category']}{status} ({row['error']})"
                )
        for error in stage.errors:
            lines.append(f"  error: {_counts(error)}")
    if result.deltas:
        lines += ["", f"Database changes: {_counts(result.deltas)}"]
    if result.recording is not None:
        recording = result.recording
        line = (
            f"Run history: run recorded {'yes' if recording['run_recorded'] else 'NO'}, "
            f"attempts recorded {recording['attempts_recorded']}"
        )
        if recording["attempts_unrecorded"]:
            line += f", attempts NOT recorded {recording['attempts_unrecorded']}"
        if recording["interrupted_runs_marked"]:
            line += f", marked interrupted: {', '.join(recording['interrupted_runs_marked'])}"
        lines += ["", line]
    lines += ["", f"Overall: {result.status.upper()}"]
    return "\n".join(lines)
