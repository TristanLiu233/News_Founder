"""Run manual ingestion without exposing unauthenticated refresh endpoints."""

import argparse
import asyncio
import json
from datetime import datetime
from uuid import UUID

from industry_intelligence.adapters.company_profile.registry import PROFILE_SOURCES
from industry_intelligence.adapters.company_profile.targets import TARGETS_BY_SYMBOL
from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.config import get_settings
from industry_intelligence.db.session import create_database_engine, create_session_factory
from industry_intelligence.domain.enums import Domain
from industry_intelligence.logging import configure_logging
from industry_intelligence.services.ranking import rank_domain
from industry_intelligence.services.resolution import backfill_subject_identities
from industry_intelligence.services.source_ingestion import ingest, ingest_all_enabled_sources


def _aware_datetime(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 datetime: {value}") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("needs a timezone offset, e.g. 2026-09-27T00:00Z")
    return parsed


def _without_ids(result):
    for item in result if isinstance(result, list) else [result]:
        if isinstance(item, dict):
            item.pop("content_ids", None)
    return result


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    single = sub.add_parser("ingest")
    single.add_argument("source", choices=SOURCES)
    single.add_argument("--batch-id")
    sub.add_parser("ingest-all")
    content = sub.add_parser("ingest-content")
    content.add_argument("source", choices=CONTENT_SOURCES)
    sub.add_parser("ingest-content-all")
    relink = sub.add_parser("content-relink")
    # Recompute deterministic content ↔ subject links; --dry-run lists them and writes nothing.
    relink.add_argument("--dry-run", action="store_true")
    update = sub.add_parser("content-update-all")
    # ingest (per source) → link new items → event-build; repeat --source to narrow the run.
    update.add_argument("--source", action="append", choices=CONTENT_SOURCES)
    update.add_argument("--skip-events", action="store_true")
    intelligence = sub.add_parser("intelligence-update")
    # The daily run, under the global update lock: precheck → rankings → composite → company
    # rankings → content → links → events → AI (optional) → postcheck. Exit 0 success,
    # 3 degraded, 1 failed, 4 already running (another run holds the lock; nothing was done).
    intelligence.add_argument("--plan", action="store_true")
    intelligence.add_argument(
        "--source", action="append", choices=[*SOURCES, *COMPANY_RANKINGS, *CONTENT_SOURCES]
    )
    intelligence.add_argument("--skip-ai", action="store_true")
    intelligence.add_argument("--ai-limit", type=int, default=10)
    intelligence.add_argument("--run-id")
    intelligence.add_argument("--json", action="store_true")
    rank = sub.add_parser("rank")
    rank.add_argument("domain", choices=list(Domain))
    sub.add_parser("rank-all")
    sub.add_parser("backfill-subjects")
    identity_repair = sub.add_parser("subject-identity-repair")
    # Safe by default: writes require the explicit --apply acknowledgement.
    identity_repair.add_argument("--apply", action="store_true")
    gaming_repair = sub.add_parser("gaming-subject-canonical-repair")
    # 3DM game names carrying ranking-row text; dry-run unless --apply is given.
    gaming_repair.add_argument("--apply", action="store_true")
    health = sub.add_parser("source-health")
    # Read-only: health of every automatic source from recorded attempts; no network request.
    health.add_argument("--json", action="store_true")
    freshness = sub.add_parser("update-freshness")
    # Read-only: exit 0 when a successful intelligence-update started within the window, else 1.
    freshness.add_argument("--max-age-hours", type=float, default=26)
    freshness.add_argument("--json", action="store_true")
    audit = sub.add_parser("data-quality-audit")
    # Read-only invariant audit; exits 1 only when an error-level check fails.
    audit.add_argument("--json", action="store_true")
    reindex_parser = sub.add_parser("search-reindex")
    reindex_parser.add_argument("--subject-id", type=UUID)
    sub.add_parser("search-embed")
    company = sub.add_parser("company-ranking-ingest")
    # Current published versions only: no option requests historical source revisions.
    company.add_argument("ranking", choices=COMPANY_RANKINGS)
    sub.add_parser("company-ranking-ingest-all")
    profile = sub.add_parser("company-profile-ingest")
    # Reviewed targets only; repeat --symbol/--source to narrow the run.
    profile.add_argument("--symbol", action="append", choices=TARGETS_BY_SYMBOL)
    profile.add_argument("--source", action="append", choices=PROFILE_SOURCES)
    sub.add_parser("company-subject-link")
    merge = sub.add_parser("company-merge")
    # A reviewed, explicit merge of one duplicate company into its canonical company.
    merge.add_argument("canonical", type=UUID)
    merge.add_argument("duplicate", type=UUID)
    merge.add_argument("--reason", required=True)
    events = sub.add_parser("event-build")
    # Incremental: only content items without an Event link are processed.
    events.add_argument("--since", type=_aware_datetime)
    events.add_argument("--limit", type=int)
    events.add_argument("--dry-run", action="store_true")
    sub.add_parser("event-relink")
    rebuild = sub.add_parser("event-rebuild")
    # Bounded rebuild of derived Events for content published in [--from, --to).
    rebuild.add_argument("--from", dest="start", type=_aware_datetime, required=True)
    rebuild.add_argument("--to", dest="end", type=_aware_datetime, required=True)
    rebuild.add_argument("--dry-run", action="store_true")
    event_merge = sub.add_parser("event-merge")
    # A reviewed, explicit merge of one duplicate Event into its canonical Event.
    event_merge.add_argument("canonical", type=UUID)
    event_merge.add_argument("duplicate", type=UUID)
    event_merge.add_argument("--reason", required=True)
    # Phase 7 AI enrichment: bounded, cached, CLI-only; --dry-run sends no provider request.
    ai_enrich = sub.add_parser("ai-event-enrich")
    ai_suggest = sub.add_parser("ai-entity-suggest")
    ai_merge = sub.add_parser("ai-merge-suggest")
    for ai_parser in (ai_enrich, ai_suggest, ai_merge):
        ai_parser.add_argument("--since", type=_aware_datetime)
        ai_parser.add_argument("--domain", choices=list(Domain))
        ai_parser.add_argument("--limit", type=int, default=10)
        ai_parser.add_argument("--dry-run", action="store_true")
    for ai_parser in (ai_enrich, ai_suggest):
        ai_parser.add_argument("--event-id", action="append", type=UUID)
    ai_enrich.add_argument("--locale", action="append", choices=["en", "zh-CN"])
    ai_enrich.add_argument(
        "--type", action="append", choices=["event_summary", "event_why_it_matters"]
    )
    ai_list = sub.add_parser("ai-suggestion-list")
    ai_list.add_argument(
        "--status", default="pending", choices=["pending", "accepted", "rejected", "all"]
    )
    ai_list.add_argument(
        "--type",
        choices=[
            "event_subject_candidate",
            "event_company_candidate",
            "new_entity_candidate",
            "event_merge_candidate",
        ],
    )
    ai_list.add_argument("--limit", type=int, default=50)
    ai_accept = sub.add_parser("ai-suggestion-accept")
    # A reviewed action through existing services; merges keep every Event merge safeguard.
    ai_accept.add_argument("suggestion", type=UUID)
    ai_accept.add_argument("--reason", required=True)
    ai_accept.add_argument("--candidate", type=UUID)
    ai_accept.add_argument("--canonical", type=UUID)
    ai_reject = sub.add_parser("ai-suggestion-reject")
    ai_reject.add_argument("suggestion", type=UUID)
    ai_reject.add_argument("--reason", required=True)
    ai_hide = sub.add_parser("ai-artifact-reject")
    ai_hide.add_argument("artifact", type=UUID)
    ai_hide.add_argument("--reason", required=True)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        if args.command in {"search-reindex", "search-embed"}:
            from industry_intelligence.services.search_index import embed_documents, reindex

            with factory.begin() as session:
                result = (
                    reindex(session, settings, args.subject_id)
                    if args.command == "search-reindex"
                    else embed_documents(session, settings)
                )
        elif args.command == "backfill-subjects":
            with factory.begin() as session:
                result = backfill_subject_identities(session)
        elif args.command == "subject-identity-repair":
            from sqlalchemy import text

            from industry_intelligence.services.subject_identity_repair import (
                repair_subject_identities,
            )

            session = factory()
            try:
                if not args.apply:
                    session.execute(text("SET TRANSACTION READ ONLY"))
                result = repair_subject_identities(session, dry_run=not args.apply)
                session.commit() if args.apply else session.rollback()
            finally:
                session.close()
        elif args.command == "gaming-subject-canonical-repair":
            from sqlalchemy import text

            from industry_intelligence.services.gaming_subject_repair import (
                repair_gaming_subjects,
            )

            session = factory()
            try:
                if not args.apply:
                    session.execute(text("SET TRANSACTION READ ONLY"))
                result = repair_gaming_subjects(session, dry_run=not args.apply)
                session.commit() if args.apply else session.rollback()
            finally:
                session.close()
        elif args.command == "data-quality-audit":
            from sqlalchemy import text

            from industry_intelligence.services.data_quality_audit import (
                audit_data_quality,
                render_text,
            )

            session = factory()
            try:
                session.execute(text("SET TRANSACTION READ ONLY"))
                report = audit_data_quality(session)
            finally:
                session.rollback()
                session.close()
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            else:
                print(render_text(report))
            raise SystemExit(0 if report["summary"]["result"]["passed"] else 1)
        elif args.command == "source-health":
            from sqlalchemy import text

            from industry_intelligence.services.source_health import render_text, source_health

            session = factory()
            try:
                session.execute(text("SET TRANSACTION READ ONLY"))
                report = source_health(session)
            finally:
                session.rollback()
                session.close()
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                print(render_text(report))
            raise SystemExit(0)
        elif args.command == "update-freshness":
            from datetime import timedelta

            from sqlalchemy import text

            from industry_intelligence.services.update_freshness import (
                render_text,
                update_freshness,
            )

            if not 0 < args.max_age_hours <= 24 * 14:
                parser.error("--max-age-hours must be in (0, 336]")
            session = factory()
            try:
                session.execute(text("SET TRANSACTION READ ONLY"))
                report = update_freshness(session, max_age=timedelta(hours=args.max_age_hours))
            finally:
                session.rollback()
                session.close()
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                print(render_text(report))
            raise SystemExit(0 if report["status"] == "fresh" else 1)
        elif args.command == "intelligence-update":
            from industry_intelligence.services.intelligence_update import (
                execute_update,
                plan_update,
                render_text,
            )

            options = {"sources": args.source, "skip_ai": args.skip_ai, "ai_limit": args.ai_limit}
            if args.plan:
                plan = asyncio.run(plan_update(settings, factory, **options))
                print(json.dumps(plan, indent=2, ensure_ascii=False, default=str))
                raise SystemExit(1 if plan["precheck"]["status"] == "failed" else 0)
            update = asyncio.run(execute_update(settings, factory, run_id=args.run_id, **options))
            if args.json:
                print(json.dumps(update.to_dict(), indent=2, ensure_ascii=False, default=str))
            else:
                print(render_text(update))
            raise SystemExit(update.exit_code)
        elif args.command in {"rank", "rank-all"}:
            domains = [Domain(args.domain)] if args.command == "rank" else list(Domain)
            result = []
            with factory.begin() as session:
                for domain in domains:
                    run, created = rank_domain(session, domain, settings)
                    result.append(
                        {
                            "domain": domain,
                            "run_id": str(run.id),
                            "created": created,
                            "diagnostics": run.diagnostics,
                        }
                    )
        elif args.command == "content-relink":
            from industry_intelligence.services.content_ingestion import relink_all_content

            session = factory()
            try:
                result = relink_all_content(session, dry_run=args.dry_run)
                session.rollback() if args.dry_run else session.commit()
            finally:
                session.close()
        elif args.command == "content-update-all":
            from industry_intelligence.services.content_pipeline import update_all_content

            result = asyncio.run(
                update_all_content(settings, factory, args.source, events=not args.skip_events)
            )
            if result["totals"].get("failed_sources") or "error" in result.get("events", {}):
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
                raise SystemExit(1)
        elif args.command == "event-rebuild":
            from industry_intelligence.services.event_rebuild import rebuild_events

            session = factory()
            try:
                result = rebuild_events(session, args.start, args.end)
                result.pop("partition", None)
                result = {"dry_run": args.dry_run, **result}
                session.rollback() if args.dry_run else session.commit()
            finally:
                session.close()
        elif args.command in {"company-ranking-ingest", "company-ranking-ingest-all"}:
            from industry_intelligence.services.company_ranking_ingestion import (
                ingest_all_company_rankings,
                ingest_company_ranking,
            )

            result = asyncio.run(
                ingest_company_ranking(args.ranking, settings, factory)
                if args.command == "company-ranking-ingest"
                else ingest_all_company_rankings(settings, factory)
            )
        elif args.command == "company-profile-ingest":
            from industry_intelligence.services.company_profile_ingestion import (
                ingest_company_profiles,
            )

            result = asyncio.run(
                ingest_company_profiles(settings, factory, args.symbol, args.source)
            )
        elif args.command == "company-subject-link":
            from industry_intelligence.services.company_subject_linking import relink_all_companies

            with factory.begin() as session:
                result = relink_all_companies(session)
        elif args.command == "company-merge":
            from industry_intelligence.services.company_merge import merge_companies

            with factory.begin() as session:
                result = merge_companies(session, args.canonical, args.duplicate, args.reason)
        elif args.command == "event-build":
            from industry_intelligence.services.event_ingestion import (
                build_events,
                preview_clusters,
            )

            session = factory()
            try:
                result = build_events(session, args.since, args.limit)
                if args.dry_run:
                    result = {"dry_run": True, **result, "clusters": preview_clusters(session)}
                    session.rollback()
                else:
                    session.commit()
            finally:
                session.close()
        elif args.command == "event-relink":
            from industry_intelligence.services.event_ingestion import relink_all_events

            with factory.begin() as session:
                result = relink_all_events(session)
        elif args.command == "event-merge":
            from industry_intelligence.services.event_merge import merge_events

            with factory.begin() as session:
                result = merge_events(session, args.canonical, args.duplicate, args.reason)
        elif args.command in {"ai-event-enrich", "ai-entity-suggest", "ai-merge-suggest"}:
            from industry_intelligence.ai.provider import AIProviderNotConfigured
            from industry_intelligence.services.ai_enrichment import (
                CrossLanguageGenerationDisabled,
                enrich_events,
            )
            from industry_intelligence.services.ai_suggestions import (
                suggest_entities,
                suggest_event_merges,
            )

            common = {
                "since": args.since,
                "domain": args.domain,
                "limit": args.limit,
                "dry_run": args.dry_run,
            }
            try:
                if args.command == "ai-event-enrich":
                    run = enrich_events(
                        settings,
                        factory,
                        event_ids=args.event_id,
                        locales=tuple(args.locale) if args.locale else None,
                        artifact_types=tuple(
                            args.type or ("event_summary", "event_why_it_matters")
                        ),
                        **common,
                    )
                elif args.command == "ai-entity-suggest":
                    run = suggest_entities(settings, factory, event_ids=args.event_id, **common)
                else:
                    run = suggest_event_merges(settings, factory, **common)
                result = asyncio.run(run)
            except AIProviderNotConfigured as exc:
                print(json.dumps({"error": "ai_provider_not_configured", "detail": str(exc)}))
                raise SystemExit(1) from exc
            except CrossLanguageGenerationDisabled as exc:
                print(
                    json.dumps(
                        {
                            "error": "cross_language_generation_disabled",
                            "detail": str(exc),
                            "refused": exc.refused,
                        },
                        ensure_ascii=False,
                    )
                )
                raise SystemExit(1) from exc
        elif args.command in {
            "ai-suggestion-list",
            "ai-suggestion-accept",
            "ai-suggestion-reject",
            "ai-artifact-reject",
        }:
            from industry_intelligence.services.ai_suggestions import (
                SuggestionReviewRefused,
                accept_suggestion,
                list_suggestions,
                reject_artifact,
                reject_suggestion,
            )

            try:
                with factory.begin() as session:
                    if args.command == "ai-suggestion-list":
                        status = None if args.status == "all" else args.status
                        result = list_suggestions(session, status, args.type, args.limit)
                    elif args.command == "ai-suggestion-accept":
                        result = accept_suggestion(
                            session, args.suggestion, args.reason, args.candidate, args.canonical
                        )
                    elif args.command == "ai-suggestion-reject":
                        result = reject_suggestion(session, args.suggestion, args.reason)
                    else:
                        result = reject_artifact(session, args.artifact, args.reason)
            except SuggestionReviewRefused as exc:
                print(json.dumps({"error": "review_refused", "detail": str(exc)}))
                raise SystemExit(1) from exc
        elif args.command in {"ingest-content", "ingest-content-all"}:
            from industry_intelligence.services.content_ingestion import (
                ingest_all_content,
                ingest_content,
            )

            result = _without_ids(
                asyncio.run(
                    ingest_content(args.source, settings, factory)
                    if args.command == "ingest-content"
                    else ingest_all_content(settings, factory)
                )
            )
        else:
            result = asyncio.run(
                ingest(args.source, settings, factory, args.batch_id)
                if args.command == "ingest"
                else ingest_all_enabled_sources(settings, factory)
            )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        if isinstance(result, list) and any("error" in item for item in result):
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
