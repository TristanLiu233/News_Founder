"""Run manual ingestion without exposing unauthenticated refresh endpoints."""

import argparse
import asyncio
import json
from uuid import UUID

from industry_intelligence.adapters.source.content_registry import CONTENT_SOURCES
from industry_intelligence.adapters.source.registry import SOURCES
from industry_intelligence.config import get_settings
from industry_intelligence.db.session import create_database_engine, create_session_factory
from industry_intelligence.domain.enums import Domain
from industry_intelligence.logging import configure_logging
from industry_intelligence.services.ranking import rank_domain
from industry_intelligence.services.resolution import backfill_subject_identities
from industry_intelligence.services.source_ingestion import ingest, ingest_all_enabled_sources


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
    sub.add_parser("content-relink")
    rank = sub.add_parser("rank")
    rank.add_argument("domain", choices=list(Domain))
    sub.add_parser("rank-all")
    sub.add_parser("backfill-subjects")
    reindex_parser = sub.add_parser("search-reindex")
    reindex_parser.add_argument("--subject-id", type=UUID)
    sub.add_parser("search-embed")
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

            with factory.begin() as session:
                result = relink_all_content(session)
        elif args.command in {"ingest-content", "ingest-content-all"}:
            from industry_intelligence.services.content_ingestion import (
                ingest_all_content,
                ingest_content,
            )

            result = asyncio.run(
                ingest_content(args.source, settings, factory)
                if args.command == "ingest-content"
                else ingest_all_content(settings, factory)
            )
        else:
            result = asyncio.run(
                ingest(args.source, settings, factory, args.batch_id)
                if args.command == "ingest"
                else ingest_all_enabled_sources(settings, factory)
            )
        print(json.dumps(result, indent=2))
        if isinstance(result, list) and any("error" in item for item in result):
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
