# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` is the maintained source of truth for product semantics, design rules, data sources, phase status, and the completion checklist. It is imported below; keep phase/status updates there rather than duplicating them here.

@AGENTS.md

## Environment and commands

- The Python virtualenv lives at the repository root (`.venv/`); backend commands run from `backend/` (e.g. `../.venv/bin/pytest`).
- `Settings` (`backend/src/industry_intelligence/config.py`) always loads the repository-root `.env` by absolute path, so the CLI, Alembic, and pytest share one `DATABASE_URL` regardless of working directory. `migrations/env.py` overrides the URL in `alembic.ini` with that setting.
- `.env` is gitignored and holds local-only values such as `AI_API_KEY` (Phase 7), `SOURCE_CONTACT` (the deployment maintainer's own contact, sent only to Wikimedia hosts) and `SEC_CONTACT` (a separate contact, sent only to SEC hosts). `HTTPClient` enforces this per request host. Never print them or copy a real value into tracked files; `.env.example` keeps only placeholders.
- Local runtime (Local-1): PostgreSQL 16.15 runs natively as the launchd agent `local.industry-intelligence.postgresql` on `127.0.0.1:5433` (binaries in the conda env `ii-pg16`, `/opt/anaconda3/envs/ii-pg16/bin`; pgvector 0.8.6 built from source into it; data in `~/Library/Application Support/industry-intelligence/postgresql16`; superuser = the macOS user via peer auth on the `/tmp` socket, app role `industry`). Daily local operation does not need Docker Desktop. `docker compose up -d` (`docker-compose.yml`, `pgvector/pgvector:pg16` on 127.0.0.1:5432) is only an optional alternative and the rollback path; Docker Compose stays the production mechanism (Deployment-2A). Install, backup, restore, rollback: README "Local runtime"; `ops/postgres/` holds the service template and cluster settings.
- `backend/constraints.txt` pins the exact tested dependency set: `pip install -c backend/constraints.txt -e "./backend[dev]"`. The backend must stay an editable install/checkout: `Settings` and the update precheck locate `.env` and `backend/alembic.ini` relative to the source tree.
- Production readiness (Deployment-1; nothing deployed): `docs/deployment.md` holds the runtime map, env-var classes, verified production commands, backup/restore policy, the chosen architecture (single VPS, Compose, Caddy, systemd timers), and the Deployment-2 scope. `ENVIRONMENT=production` hides `/docs`, `/redoc`, `/openapi.json`.
- Production packaging (Deployment-2A; nothing provisioned): `backend/Dockerfile` (one image for the API and every CLI job; editable install at `/app/backend`, so the source-relative `.env`/`alembic.ini` lookups still hold), `frontend/Dockerfile` (build arg `NEXT_PUBLIC_API_BASE_URL=https://<APP_DOMAIN>`), `deploy/docker-compose.production.yml` (only Caddy publishes ports; postgres on an internal network), `deploy/Caddyfile`, `deploy/production.env.example`, `deploy/bin/{ii-compose,backup.sh,notify-failure.sh}`, `deploy/hooks/*.example`, `deploy/systemd/` (update 10:00, backup 11:00, freshness 12:30 Australia/Sydney). Always run the stack through `deploy/bin/ii-compose` (env file `/etc/industry-intelligence/production.env`, overridable with `II_ENV_FILE`/`II_PROJECT` for an isolated local stack). Migrations are explicit (`ii-compose run --rm -T backend alembic upgrade head`), never on start. Runbooks: `docs/cutover.md` (Deployment-2B), `docs/restore.md`. `tests/unit/test_deploy_templates.py` and `test_backup_script.py` pin the templates and backup behaviour.

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

# backend/ — the daily run (Operations-1/2): update lock → precheck → rankings → composite →
# company rankings → content → links → events → AI (optional) → postcheck; --plan is read-only
# (no network, no writes, never takes the lock). The scheduler-ready command is --skip-ai.
../.venv/bin/python -m industry_intelligence.cli intelligence-update --plan
../.venv/bin/python -m industry_intelligence.cli intelligence-update --skip-ai
# exit 0 success, 3 degraded, 1 failed, 4 already running; tests writing every stage (and the
# migration round trip) need a *_test DATABASE_URL:
DATABASE_URL=postgresql+psycopg://…/industry_intelligence_test ../.venv/bin/pytest tests/integration/test_intelligence_update_postgres.py tests/integration/test_operations_postgres.py

# backend/ — source health from recorded attempts (read-only, no network request)
../.venv/bin/python -m industry_intelligence.cli source-health --json

# backend/ — freshness (read-only): exit 0 if a successful intelligence-update started in the last
# 26 h, else 1 (only status success counts; the production 12:30 timer runs it)
../.venv/bin/python -m industry_intelligence.cli update-freshness

# repository root — the daily launchd agent (Scheduler-1): 10:00 Australia/Sydney, runs exactly
# `intelligence-update --skip-ai` through ops/scheduler/daily_update.py; log in
# ~/Library/Logs/industry-intelligence/daily-update.log. Inspect / disable / enable:
launchctl print gui/$(id -u)/local.industry-intelligence.daily-update
launchctl bootout gui/$(id -u)/local.industry-intelligence.daily-update
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.industry-intelligence.daily-update.plist
# read-only preflight through the same wrapper (running it without --plan is a real update)
.venv/bin/python ops/scheduler/daily_update.py --plan

# backend/ — one bounded content run: ingest all content sources → link new items → event-build
../.venv/bin/python -m industry_intelligence.cli content-update-all
# backend/ — recompute content ↔ subject links (preview first), then derived Event links
../.venv/bin/python -m industry_intelligence.cli content-relink --dry-run
../.venv/bin/python -m industry_intelligence.cli event-relink
# backend/ — bounded rebuild of derived Events for content published in [from, to)
../.venv/bin/python -m industry_intelligence.cli event-rebuild --from 2026-09-20T00:00Z --to 2026-09-29T00:00Z --dry-run

# backend/ — 3DM game-name repair (DQ-2.1): dry-run by default; --apply only with owner approval
../.venv/bin/python -m industry_intelligence.cli gaming-subject-canonical-repair

# backend/ — read-only data-quality invariant audit (exit 1 only on error-level findings)
../.venv/bin/python -m industry_intelligence.cli data-quality-audit --json

# backend/ — external company rankings (current published versions only)
../.venv/bin/python -m industry_intelligence.cli company-ranking-ingest cmc_tech_market_cap
../.venv/bin/python -m industry_intelligence.cli company-ranking-ingest-all

# backend/ — Phase 7 AI (CLI only; bounded, cached; --dry-run sends no provider request)
../.venv/bin/python -m industry_intelligence.cli ai-event-enrich --limit 10 --dry-run
../.venv/bin/python -m industry_intelligence.cli ai-entity-suggest --event-id UUID
../.venv/bin/python -m industry_intelligence.cli ai-merge-suggest --dry-run
../.venv/bin/python -m industry_intelligence.cli ai-suggestion-list
../.venv/bin/python -m industry_intelligence.cli ai-suggestion-accept UUID --reason "evidence"

# frontend/ — dev server (reads NEXT_PUBLIC_API_BASE_URL from frontend/.env.local)
npm run dev
# frontend/ — production build and server (NEXT_PUBLIC_API_BASE_URL is inlined at build time)
npm run build && npm start
```

Tests marked `integration` (`tests/integration/`) connect to the database from the root `.env` — the same dev database, not a separate test one — and wrap every test in a transaction that is rolled back. They need the local PostgreSQL service running and migrations at head. The `settings` fixture in `tests/conftest.py` points at a nonexistent `_test` database and is only for app-factory/API tests that never connect.

## Backend architecture (`backend/src/industry_intelligence/`)

**Source catalogs are explicit dictionaries; there is no plugin discovery.**
- `adapters/source/registry.py` → `SOURCES`: ranking and `market_pulse` lists (`SourceDefinition`, including `list_type`).
- `adapters/source/content_registry.py` → `CONTENT_SOURCES`: chronological `news`/`feed` sources (`ContentSourceDefinition`).
- Adapters are constructed as `Adapter(http: HTTPClient, limit)`, expose `async fetch()`, and keep a `failed_count` of skipped malformed rows. Ranking adapters return `SourceItem` (rank ≥ 1, timezone-aware, one shared `observed_at` per snapshot). Content adapters return `ContentSourceItem`, which has no rank field. Both DTOs live in `adapters/source/base.py`.
- Adding a source means an adapter class, a registry entry, and a fixture-backed test. HTML fixtures live in `tests/fixtures/`; adapter tests inject `httpx.MockTransport` through `HTTPClient(settings, transport=...)`. The Search page builds its source filter from `/api/v1/sources` (`searchable` keys), so no frontend list needs updating.

**Ranking pipeline** (`services/source_ingestion.py`): `ingest()` fetches over HTTP outside any transaction, then `persist()` runs in a single transaction. It takes a per-source `pg_advisory_xact_lock`, short-circuits replays by `(source_key, batch_key)` in `IngestionBatch`, resolves each item to a canonical subject through `services/resolution.resolve_subject`, appends `RankObservation` rows, and finally calls `search_index.refresh_subject` for touched subjects. A non-empty fetch in which every item is invalid raises, so the previous snapshot is preserved.

**Composite ranking** (`services/ranking.py`): `rank_domain()` computes the freshness-weighted RRF from each rank list's current snapshot and stores an immutable `CompositeRankingRun` keyed by input/config hashes; identical inputs return the existing run. `trending()` reads the latest successful run and attaches related-news previews from `services/news.py`.

**Content pipeline** (`services/content_ingestion.py`): `persist_content()` deduplicates by `(source_id, identity_key)` (external ID, else URL) and treats an already stored normalized URL of the same source as a replay, then links the new items inside a SAVEPOINT (a linking failure is reported as `link_error` and never rolls back content). Linking rules are pure functions in `services/content_linking.py` (`content-linker/2`: `same_article` → `exchange_code` → `cashtag` → unique `exact_name`/`exact_alias` with eligibility rules; see its docstring). `relink_all_content()` reconciles stored links with recomputed ones (adds, removes stale, reports counts; `dry_run` lists them) and refreshes affected search documents; run it after changing matching rules or after ranking ingestion adds Subjects, then run `event-relink`. `services/content_pipeline.update_all_content()` (CLI `content-update-all`) chains per-source ingestion and one `build_events` run with per-stage failure isolation. Computer/Gaming news adapters live in `adapters/source/news_feeds.py` (publisher RSS, excerpts ≤ 280 characters; full bodies are never stored).

**Daily update (Operations-1)** (`services/intelligence_update.py`, CLI `intelligence-update`) only orchestrates existing services, each stage in its own transaction (sources one transaction each): read-only precheck (DB, Alembic head, `data-quality-audit` errors block) → ranking `ingest` (batch key from the run ID, so `--run-id` retries replay) → `rank_domain` → `ingest_company_ranking` for rankings with `daily_update` (Wikipedia is manual-only) → `ingest_content` → `relink_all_content` + `relink_all_companies` → incremental `build_events` + `relink_all_events` (never rebuilds or retires Events) → `enrich_events(skip_settled=True)` (skips reviewer rejections and non-transient failures) → read-only postcheck. Source failures are isolated per source; a failed `links`/`events` stage skips its dependants. `StageResult`/`UpdateResult` carry counts and safe error categories; `render_text` prints the operator summary. Its PostgreSQL tests skip unless the database name ends in `_test`.

**Operations-2** (`services/update_operations.py`, `services/source_health.py`, `services/ingestion_phase.py`, `db/models/operations.py`, migration `20260930_12`): the CLI's real run is `execute_update`, which wraps the lock-free `run_update`. It takes `UpdateLock` (`pg_try_advisory_lock` on the two-int4 `UPDATE_LOCK_KEY`, on a dedicated `NullPool` autocommit connection held for the whole run; never an ordinary stage session) and returns `already_running` (exit 4) with no request or write when it is held. Under the lock `RunRecorder` marks stale `running` `update_runs` rows `interrupted`, records the run, one append-only `source_attempts` row per attempted source (via the `record` callback of `_each_source`), and the final status; recording failures only degrade the run. The three ingestion services wrap fetch and persist in `ingestion_phase(...)`, which only tags the re-raised exception, so `error_detail` reports `transport`/`parse`/`persist`/`other`. `source-health` derives health (`never_run`, `failing`, `degraded` = latest attempt rejected rows, `healthy`) on read from the current automatic registry (`select_sources()`) left-joined with attempts; there is no current-health table. `plan_update` never takes the lock and only reports `lock_state`.

**Deployment-2A** (`services/update_freshness.py`, CLI `update-freshness`): SELECT-only over `update_runs` in a read-only transaction; fresh = a `success` run started within `--max-age-hours` (default 26, inclusive; runs after `now` ignored); `degraded`/`failed`/`running`/`interrupted` never count. Production scheduling (systemd units in `deploy/systemd/`) invokes `ii-compose run --rm -T backend python -m industry_intelligence.cli …` and keeps the real exit code (no `SuccessExitStatus`), with `OnFailure=industry-intelligence-notify@%n.service`.

**Scheduler-1** (`ops/scheduler/`): a per-user launchd agent (`local.industry-intelligence.daily-update`, installed from `local.industry-intelligence.daily-update.plist.template` into `~/Library/LaunchAgents/` with absolute paths filled in) fires at `StartCalendarInterval` 10:00 in the system time zone (Australia/Sydney, so DST is followed; no UTC offset anywhere). launchd starts `.venv/bin/python ops/scheduler/daily_update.py` directly — a launchd-started `/bin/sh` is denied `~/Documents` by macOS privacy protection (exit 126) — and the wrapper runs `python -m industry_intelligence.cli intelligence-update --skip-ai` from `backend/` with a minimal environment (configuration only from the root `.env`), logs `event=start`/`event=finish exit=N`, and refuses with exit 78 when the system zone is not Australia/Sydney. There is no scheduler-level lock or once-per-day rule: the Operations-2 advisory lock is the only concurrency control. `tests/unit/test_scheduler_wrapper.py` covers the command, the refusal, and the template.

**Search** (`services/search_index.py`, `services/search.py`): `refresh_subject()` rebuilds one subject's `SearchDocument` (aliases plus bounded recent titles; the FTS `tsvector` is a generated column). `search()` gathers lexical candidates, plus pgvector candidates when an `EmbeddingProvider` (`ai/embeddings.py`) is configured, and combines them with `fuse()`. This is a separate RRF context from Trending.

**Transactions:** services take a SQLAlchemy `Session` and do not commit. Callers own the transaction boundary: the CLI uses `session_factory.begin()`, and API routes use `db/session.get_db_session`. `merge_subjects` also relies on the caller's transaction.

**API** (`api/`): thin routers over services. `api/sources.py` serves `/sources`, `/today`, and `/trending`; `api/subjects.py` serves Subject Detail and rank history from `services/subject_detail.py`. `api/company_rankings.py` serves `/company-rankings`, `/company-rankings/{key-or-id}`, its `/history`, and `/companies/{id}/rankings`. `create_app(settings)` in `main.py` does not connect to the database at startup. Ingestion, ranking, relinking, and reindexing are intentionally CLI-only (`cli.py`); there are no refresh endpoints.

**Company rankings (Phase 4)** are a separate pipeline from source `RankList`s. `adapters/company_ranking/registry.py` → `COMPANY_RANKINGS` (`CompanyRankingDefinition`) lists external publisher rankings. Adapters are constructed as `Adapter(http, definition)` and return one `CompanyRankingSnapshot` DTO with entries. `services/company_ranking_ingestion.persist_company_snapshot()` runs in the caller's transaction: it upserts the `Ranking`, replays an existing `snapshot_key`, skips content identical to the neighbouring snapshot, resolves companies through `services/company_resolution.resolve_company`, and writes an immutable `RankingSnapshot` with `RankingEntry` rows. `services/company_rankings.py` computes movement from the preceding snapshot for `api/company_rankings.py`. Wikipedia rankings refuse to fetch unless `SOURCE_CONTACT` is set (Wikimedia User-Agent policy); the contact is passed only to that client (`HTTPClient(settings, contact=...)`). The Wikipedia adapter reads only the current page via `/api/rest_v1/page/html/{title}` (no Action API, no historical revisions). Wikimedia requests currently remain HTTP 403, so these rankings are not live verified.

**Company profiles (Phase 5)** enrich reviewed targets only. `adapters/company_profile/targets.py` → `PROFILE_TARGETS` (reviewed symbol → CIK/LEI/website/CompaniesMarketCap slug/official Chinese-name pages) and `registry.py` → `PROFILE_SOURCES` (`official_website`, `sec_edgar`, `gleif`, `companiesmarketcap`). Adapters are `Adapter(http, definition)` with `async fetch(target)` returning `ExternalCompanyProfile`. `services/company_profile_ingestion.persist_profile_source()` runs in the caller's transaction: it attaches identities conservatively, records names as aliases, upserts one record per (company, source) by content hash (identical content only refreshes `checked_at`), and rebuilds `CompanyProfile` by `FIELD_PRIORITY` with `field_sources`. `services/company_subject_linking.relink_all_companies()` rebuilds deterministic links (exchange code, then unique exact name). `services/company_merge.merge_companies()` is an explicit reviewed merge with redirects. `api/companies.py` serves `/companies` and `/companies/{id}`; `/news?company_id=` reuses the news tables. SEC requires `SEC_CONTACT`.

**Events (Phase 6)** group persisted `ContentItem`s into canonical `Event`s; they are not Subjects and never feed Trending or Search. `services/event_resolution.py` holds the pure, unit-tested matching rules (normalization, identity evidence, conflicts, thresholds, `RESOLVER_VERSION`); `services/event_ingestion.build_events()` (caller's transaction, advisory lock `events`) assigns only content items without an `EventContentLink`, so re-runs are no-ops, and rebuilds derived `EventSubjectLink`/`EventCompanyLink` rows from existing content-subject and company-subject links; `services/event_merge.merge_events()` is the reviewed merge with `EventRedirect`; `services/events.py` is the read model for `api/events.py`. `event-build` is not run automatically after `ingest-content` (use `content-update-all` for the chained run), and `event-relink` must follow `content-relink` or `company-subject-link`. `services/event_rebuild.rebuild_events(start, end)` (CLI `event-rebuild`) records each deleted UUID in `event_retired_ids` within the rebuild transaction, then rebuilds Events without touching content. Old IDs resolve through the stored representative ContentItem's current owner (ordered member IDs are deterministic fallbacks), not through rebuild redirect chains; reviewed `EventRedirect` merge semantics remain separate. The command refuses windows containing reviewed merges. Changing matching rules does not re-group already processed content until `event-rebuild` is run for a window. Integration tests that call `build_events` must pass `content_ids=` and use timestamps far from the live corpus, or live content becomes a candidate. Tests that exercise a session factory (orchestration) bind a `sessionmaker(bind=connection, join_transaction_mode="create_savepoint")` to the rolled-back test connection (see `tests/integration/test_content_pipeline_postgres.py`).

**AI enrichment (Phase 7)** is an enhancement layer over the deterministic data, never canonical truth. Services use only the `ai/provider.py` `AIProvider` protocol (`generate_structured`) and `generate_with_retry` (bounded; transient errors only); adapters live in the explicit `ai/providers` registry (`anthropic` via the official SDK, optional extra `backend[ai]`; `fixture` for tests). Settings `AI_PROVIDER`/`AI_MODEL`/`AI_API_KEY` come only from the root `.env` (placeholders count as unset; never print or commit a key); the Anthropic adapter passes key and base URL explicitly so the shell's own `ANTHROPIC_*` variables are ignored. Prompts are versioned `PromptSpec`s in `ai/prompts/` (change = new version). Language policy (AI-LP1): Summary / Why It Matters are generated and served only in the Event's primary locale (`ai_evidence.primary_locale`: the one locale every report is in; mixed-language Events get no AI) with `INSIGHT_PROMPTS` (`event_summary_v4` / `event_why_it_matters_v6`, audited in Phase 7.1 Checkpoint D), so at most two tasks per Event; `enrich_events(locales=None)` is the policy, and an explicit non-primary locale raises `CrossLanguageGenerationDisabled` before any request or write (CLI exit 1, `cross_language_generation_disabled`). The UI locale never selects AI content: `GET /events/{id}/ai` returns `primary_locale`, primary-only `coverage`, and echoes the optional `locale`. The cross-language lineage (`CROSS_LANGUAGE_PROMPTS`, `insight_spec`, `generation_mode`, the xlang and English-output guards) is legacy code kept for historical artifacts, which stay in `ai_artifacts` but are never selected, served, or counted. `services/ai_evidence.py` renders an Event's persisted reports under refs and validates output deterministically (schema, own-Event evidence refs, no advice/prediction, no multi-source claims for single-source Events, figures present in evidence, locale). `services/ai_enrichment.py` generates `ai_artifacts` (cache by `input_hash`, stale marking, one commit per task); `services/ai_insights.py` is the read model for `GET /api/v1/events/{id}/ai`, which never calls a provider; `services/ai_suggestions.py` creates `ai_suggestions` (entity candidates resolved by exact catalog lookup; cross-language merge candidates after a deterministic prefilter) and applies reviewed CLI actions through existing services (`reviewed` ContentSubjectLink / EventCompanyLink, `merge_events`). `content-relink`, `event-relink`, and `merge_events` preserve `reviewed` links; `event-rebuild` refuses windows with reviewed Company links. Page loads never trigger AI. Every real run costs money: preview with `--dry-run` and keep batches small (10–20 Events). Automated tests use `FixtureProvider` or an `httpx2.MockTransport` and never call a real provider.

## Frontend architecture (`frontend/`)

- All pages are client components: `app/page.tsx` (Home), `app/today`, `app/trending`, `app/search`, `app/rankings`, `app/companies`, `app/company/[id]`, `app/events`, `app/event/[id]`, and `app/subject/[id]`. `components/shared.tsx` also holds `EventList` and `RelatedEvents` (used by Subject and Company Detail). Query state (`?domain=`, search params) is read from `window.location` in effects and written with `history.pushState`, which avoids `useSearchParams` Suspense requirements during `next build`.
- `app/layout.tsx` wraps pages in `LocaleProvider` (`lib/i18n.tsx`; `useLocale()`, locale stored in `localStorage` under `locale`) and the shared `AppHeader`. Reusable UI lives in `components/shared.tsx`.
- UI strings live in `common` (`lib/i18n.tsx`) for shared components and in a per-page dictionary (`copy` or `labels`) keyed by `en` and `zh-CN`. Any new string needs both entries.
- `app/event/[id]` shows labelled AI Summary / Why It Matters cards from `GET /events/{id}/ai` (states available, not generated, failed, rejected, stale; only available shows text); an AI request failure never breaks the rest of the page. AI text is in the Event's primary language whatever the UI locale is (marked with `lang`, plus a one-line note when it differs from the UI); a locale switch does not refetch it.
- `lib/api.ts` holds hand-written TypeScript types that mirror backend response shapes, plus fetch helpers built on `NEXT_PUBLIC_API_BASE_URL`. Update these types whenever an API response changes.
