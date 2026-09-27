# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` is the maintained source of truth for product semantics, design rules, data sources, phase status, and the completion checklist. It is imported below; keep phase/status updates there rather than duplicating them here.

@AGENTS.md

## Environment and commands

- The Python virtualenv lives at the repository root (`.venv/`); backend commands run from `backend/` (e.g. `../.venv/bin/pytest`).
- `Settings` (`backend/src/industry_intelligence/config.py`) always loads the repository-root `.env` by absolute path, so the CLI, Alembic, and pytest share one `DATABASE_URL` regardless of working directory. `migrations/env.py` overrides the URL in `alembic.ini` with that setting.
- PostgreSQL with pgvector comes from `docker compose up -d` (image `pgvector/pgvector:pg16`).

```bash
# backend/ — API server
../.venv/bin/uvicorn industry_intelligence.main:app --reload

# backend/ — unit tests only (no database needed)
../.venv/bin/pytest -m "not integration"

# backend/ — single file or single test
../.venv/bin/pytest tests/unit/test_search.py
../.venv/bin/pytest tests/unit/test_phase2.py::test_rrf_and_freshness_exact_math

# frontend/ — dev server (reads NEXT_PUBLIC_API_BASE_URL from frontend/.env.local)
npm run dev
```

Tests marked `integration` (`tests/integration/`) connect to the database from the root `.env` — the same dev database, not a separate test one — and wrap every test in a transaction that is rolled back. They need Docker running and migrations at head. The `settings` fixture in `tests/conftest.py` points at a nonexistent `_test` database and is only for app-factory/API tests that never connect.

## Backend architecture (`backend/src/industry_intelligence/`)

**Source catalogs are explicit dictionaries; there is no plugin discovery.**
- `adapters/source/registry.py` → `SOURCES`: ranking and `market_pulse` lists (`SourceDefinition`, including `list_type`).
- `adapters/source/content_registry.py` → `CONTENT_SOURCES`: chronological `news`/`feed` sources (`ContentSourceDefinition`).
- Adapters are constructed as `Adapter(http: HTTPClient, limit)`, expose `async fetch()`, and keep a `failed_count` of skipped malformed rows. Ranking adapters return `SourceItem` (rank ≥ 1, timezone-aware, one shared `observed_at` per snapshot). Content adapters return `ContentSourceItem`, which has no rank field. Both DTOs live in `adapters/source/base.py`.
- Adding a source means an adapter class, a registry entry, and a fixture-backed test. HTML fixtures live in `tests/fixtures/`; adapter tests inject `httpx.MockTransport` through `HTTPClient(settings, transport=...)`. The search page's `sourceOptions` list in the frontend is hard-coded and must be updated too.

**Ranking pipeline** (`services/source_ingestion.py`): `ingest()` fetches over HTTP outside any transaction, then `persist()` runs in a single transaction. It takes a per-source `pg_advisory_xact_lock`, short-circuits replays by `(source_key, batch_key)` in `IngestionBatch`, resolves each item to a canonical subject through `services/resolution.resolve_subject`, appends `RankObservation` rows, and finally calls `search_index.refresh_subject` for touched subjects. A non-empty fetch in which every item is invalid raises, so the previous snapshot is preserved.

**Composite ranking** (`services/ranking.py`): `rank_domain()` computes the freshness-weighted RRF from each rank list's current snapshot and stores an immutable `CompositeRankingRun` keyed by input/config hashes; identical inputs return the existing run. `trending()` reads the latest successful run and attaches related-news previews from `services/news.py`.

**Content pipeline** (`services/content_ingestion.py`): `persist_content()` deduplicates by `(source_id, external_id)` or normalized URL, then `link_content_item()` applies `deterministic_matches` (exact canonical name, unique exact alias, case-sensitive uppercase ticker) to create `ContentSubjectLink` rows and refreshes those subjects' search documents. `relink_all_content()` deletes every link and rebuilds them; run it after changing matching rules.

**Search** (`services/search_index.py`, `services/search.py`): `refresh_subject()` rebuilds one subject's `SearchDocument` (aliases plus bounded recent titles; the FTS `tsvector` is a generated column). `search()` gathers lexical candidates, plus pgvector candidates when an `EmbeddingProvider` (`ai/embeddings.py`) is configured, and combines them with `fuse()`. This is a separate RRF context from Trending.

**Transactions:** services take a SQLAlchemy `Session` and do not commit. Callers own the transaction boundary: the CLI uses `session_factory.begin()`, and API routes use `db/session.get_db_session`. `merge_subjects` also relies on the caller's transaction.

**API** (`api/`): thin routers over services. `api/sources.py` serves `/sources`, `/today`, and `/trending`. `create_app(settings)` in `main.py` does not connect to the database at startup. Ingestion, ranking, relinking, and reindexing are intentionally CLI-only (`cli.py`); there are no refresh endpoints.

**Placeholders for future phases, not active code paths:** `ai/gateway.py` (`AIGateway` protocol; Phase 3.1 uses no LLMs), `adapters/ranking/base.py` (`RankingAdapter`), and the `Company`/`RankingSnapshot` models, which are foundation work for Phase 4 company rankings and are separate from source `RankList`s.

## Frontend architecture (`frontend/`)

- Both pages are client components: `app/page.tsx` (Today and Trending, switched by state and `?view=trending`) and `app/search/page.tsx` (URL-backed query state).
- UI strings live in a per-page dictionary (`copy` in `page.tsx`, `labels` in `search/page.tsx`) keyed by `en` and `zh-CN`. Any new string needs both entries. The selected locale is stored in `localStorage` under `locale`.
- `lib/api.ts` holds hand-written TypeScript types that mirror backend response shapes, plus fetch helpers built on `NEXT_PUBLIC_API_BASE_URL`. Update these types whenever an API response changes.
