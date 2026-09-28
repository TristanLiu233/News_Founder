# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` is the maintained source of truth for product semantics, design rules, data sources, phase status, and the completion checklist. It is imported below; keep phase/status updates there rather than duplicating them here.

@AGENTS.md

## Environment and commands

- The Python virtualenv lives at the repository root (`.venv/`); backend commands run from `backend/` (e.g. `../.venv/bin/pytest`).
- `Settings` (`backend/src/industry_intelligence/config.py`) always loads the repository-root `.env` by absolute path, so the CLI, Alembic, and pytest share one `DATABASE_URL` regardless of working directory. `migrations/env.py` overrides the URL in `alembic.ini` with that setting.
- `.env` is gitignored and holds local-only values such as `SOURCE_CONTACT` (the deployment maintainer's own contact, sent only to Wikimedia hosts) and `SEC_CONTACT` (a separate contact, sent only to SEC hosts). `HTTPClient` enforces this per request host. Never print them or copy a real value into tracked files; `.env.example` keeps only placeholders.
- PostgreSQL with pgvector comes from `docker compose up -d` (image `pgvector/pgvector:pg16`).

```bash
# backend/ — API server
../.venv/bin/uvicorn industry_intelligence.main:app --reload

# backend/ — unit tests only (no database needed)
../.venv/bin/pytest -m "not integration"

# backend/ — single file or single test
../.venv/bin/pytest tests/unit/test_search.py
../.venv/bin/pytest tests/unit/test_phase2.py::test_rrf_and_freshness_exact_math

# backend/ — Events from persisted content (incremental; --dry-run rolls back)
../.venv/bin/python -m industry_intelligence.cli event-build --dry-run
../.venv/bin/python -m industry_intelligence.cli event-build

# backend/ — external company rankings (current published versions only)
../.venv/bin/python -m industry_intelligence.cli company-ranking-ingest cmc_tech_market_cap
../.venv/bin/python -m industry_intelligence.cli company-ranking-ingest-all

# frontend/ — dev server (reads NEXT_PUBLIC_API_BASE_URL from frontend/.env.local)
npm run dev
```

Tests marked `integration` (`tests/integration/`) connect to the database from the root `.env` — the same dev database, not a separate test one — and wrap every test in a transaction that is rolled back. They need Docker running and migrations at head. The `settings` fixture in `tests/conftest.py` points at a nonexistent `_test` database and is only for app-factory/API tests that never connect.

## Backend architecture (`backend/src/industry_intelligence/`)

**Source catalogs are explicit dictionaries; there is no plugin discovery.**
- `adapters/source/registry.py` → `SOURCES`: ranking and `market_pulse` lists (`SourceDefinition`, including `list_type`).
- `adapters/source/content_registry.py` → `CONTENT_SOURCES`: chronological `news`/`feed` sources (`ContentSourceDefinition`).
- Adapters are constructed as `Adapter(http: HTTPClient, limit)`, expose `async fetch()`, and keep a `failed_count` of skipped malformed rows. Ranking adapters return `SourceItem` (rank ≥ 1, timezone-aware, one shared `observed_at` per snapshot). Content adapters return `ContentSourceItem`, which has no rank field. Both DTOs live in `adapters/source/base.py`.
- Adding a source means an adapter class, a registry entry, and a fixture-backed test. HTML fixtures live in `tests/fixtures/`; adapter tests inject `httpx.MockTransport` through `HTTPClient(settings, transport=...)`. The Search page builds its source filter from `/api/v1/sources` (`searchable` keys), so no frontend list needs updating.

**Ranking pipeline** (`services/source_ingestion.py`): `ingest()` fetches over HTTP outside any transaction, then `persist()` runs in a single transaction. It takes a per-source `pg_advisory_xact_lock`, short-circuits replays by `(source_key, batch_key)` in `IngestionBatch`, resolves each item to a canonical subject through `services/resolution.resolve_subject`, appends `RankObservation` rows, and finally calls `search_index.refresh_subject` for touched subjects. A non-empty fetch in which every item is invalid raises, so the previous snapshot is preserved.

**Composite ranking** (`services/ranking.py`): `rank_domain()` computes the freshness-weighted RRF from each rank list's current snapshot and stores an immutable `CompositeRankingRun` keyed by input/config hashes; identical inputs return the existing run. `trending()` reads the latest successful run and attaches related-news previews from `services/news.py`.

**Content pipeline** (`services/content_ingestion.py`): `persist_content()` deduplicates by `(source_id, external_id)` or normalized URL, then `link_content_item()` applies `deterministic_matches` (exact canonical name, unique exact alias, case-sensitive uppercase ticker) to create `ContentSubjectLink` rows and refreshes those subjects' search documents. `relink_all_content()` deletes every link and rebuilds them; run it after changing matching rules.

**Search** (`services/search_index.py`, `services/search.py`): `refresh_subject()` rebuilds one subject's `SearchDocument` (aliases plus bounded recent titles; the FTS `tsvector` is a generated column). `search()` gathers lexical candidates, plus pgvector candidates when an `EmbeddingProvider` (`ai/embeddings.py`) is configured, and combines them with `fuse()`. This is a separate RRF context from Trending.

**Transactions:** services take a SQLAlchemy `Session` and do not commit. Callers own the transaction boundary: the CLI uses `session_factory.begin()`, and API routes use `db/session.get_db_session`. `merge_subjects` also relies on the caller's transaction.

**API** (`api/`): thin routers over services. `api/sources.py` serves `/sources`, `/today`, and `/trending`; `api/subjects.py` serves Subject Detail and rank history from `services/subject_detail.py`. `api/company_rankings.py` serves `/company-rankings`, `/company-rankings/{key-or-id}`, its `/history`, and `/companies/{id}/rankings`. `create_app(settings)` in `main.py` does not connect to the database at startup. Ingestion, ranking, relinking, and reindexing are intentionally CLI-only (`cli.py`); there are no refresh endpoints.

**Company rankings (Phase 4)** are a separate pipeline from source `RankList`s. `adapters/company_ranking/registry.py` → `COMPANY_RANKINGS` (`CompanyRankingDefinition`) lists external publisher rankings. Adapters are constructed as `Adapter(http, definition)` and return one `CompanyRankingSnapshot` DTO with entries. `services/company_ranking_ingestion.persist_company_snapshot()` runs in the caller's transaction: it upserts the `Ranking`, replays an existing `snapshot_key`, skips content identical to the neighbouring snapshot, resolves companies through `services/company_resolution.resolve_company`, and writes an immutable `RankingSnapshot` with `RankingEntry` rows. `services/company_rankings.py` computes movement from the preceding snapshot for `api/company_rankings.py`. Wikipedia rankings refuse to fetch unless `SOURCE_CONTACT` is set (Wikimedia User-Agent policy); the contact is passed only to that client (`HTTPClient(settings, contact=...)`). The Wikipedia adapter reads only the current page via `/api/rest_v1/page/html/{title}` (no Action API, no historical revisions). Wikimedia requests currently remain HTTP 403, so these rankings are not live verified.

**Company profiles (Phase 5)** enrich reviewed targets only. `adapters/company_profile/targets.py` → `PROFILE_TARGETS` (reviewed symbol → CIK/LEI/website/CompaniesMarketCap slug/official Chinese-name pages) and `registry.py` → `PROFILE_SOURCES` (`official_website`, `sec_edgar`, `gleif`, `companiesmarketcap`). Adapters are `Adapter(http, definition)` with `async fetch(target)` returning `ExternalCompanyProfile`. `services/company_profile_ingestion.persist_profile_source()` runs in the caller's transaction: it attaches identities conservatively, records names as aliases, upserts one record per (company, source) by content hash (identical content only refreshes `checked_at`), and rebuilds `CompanyProfile` by `FIELD_PRIORITY` with `field_sources`. `services/company_subject_linking.relink_all_companies()` rebuilds deterministic links (exchange code, then unique exact name). `services/company_merge.merge_companies()` is an explicit reviewed merge with redirects. `api/companies.py` serves `/companies` and `/companies/{id}`; `/news?company_id=` reuses the news tables. SEC requires `SEC_CONTACT`.

**Events (Phase 6)** group persisted `ContentItem`s into canonical `Event`s; they are not Subjects and never feed Trending or Search. `services/event_resolution.py` holds the pure, unit-tested matching rules (normalization, identity evidence, conflicts, thresholds, `RESOLVER_VERSION`); `services/event_ingestion.build_events()` (caller's transaction, advisory lock `events`) assigns only content items without an `EventContentLink`, so re-runs are no-ops, and rebuilds derived `EventSubjectLink`/`EventCompanyLink` rows from existing content-subject and company-subject links; `services/event_merge.merge_events()` is the reviewed merge with `EventRedirect`; `services/events.py` is the read model for `api/events.py`. `event-build` is not run automatically after `ingest-content`, and `event-relink` must follow `content-relink` or `company-subject-link`. Changing matching rules does not re-group already processed content (there is no rebuild command). Integration tests that call `build_events` must pass `content_ids=` and use timestamps far from the live corpus, or live content becomes a candidate.

**Placeholders for future phases, not active code paths:** `ai/gateway.py` (`AIGateway` protocol; no phase uses LLMs yet).

## Frontend architecture (`frontend/`)

- All pages are client components: `app/page.tsx` (Home), `app/today`, `app/trending`, `app/search`, `app/rankings`, `app/companies`, `app/company/[id]`, `app/events`, `app/event/[id]`, and `app/subject/[id]`. `components/shared.tsx` also holds `EventList` and `RelatedEvents` (used by Subject and Company Detail). Query state (`?domain=`, search params) is read from `window.location` in effects and written with `history.pushState`, which avoids `useSearchParams` Suspense requirements during `next build`.
- `app/layout.tsx` wraps pages in `LocaleProvider` (`lib/i18n.tsx`; `useLocale()`, locale stored in `localStorage` under `locale`) and the shared `AppHeader`. Reusable UI lives in `components/shared.tsx`.
- UI strings live in `common` (`lib/i18n.tsx`) for shared components and in a per-page dictionary (`copy` or `labels`) keyed by `en` and `zh-CN`. Any new string needs both entries.
- `lib/api.ts` holds hand-written TypeScript types that mirror backend response shapes, plus fetch helpers built on `NEXT_PUBLIC_API_BASE_URL`. Update these types whenever an API response changes.
