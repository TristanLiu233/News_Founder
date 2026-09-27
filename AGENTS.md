# Industry Intelligence & Ranking Platform

## 1. Project Mission

- Build a bilingual intelligence product for Computer, Finance, and Gaming.
- Preserve source-native facts and history, resolve them to canonical subjects, and expose auditable retrieval and ranking.
- Treat public-source access, provenance, stable identity, and conservative entity resolution as product requirements.
- Keep large historical datasets in the backend while presenting selective, useful Top-N views in the UI.

## 2. Product Semantics

- `Subject` is the canonical entity and the unit returned by Search and Trending.
- `RankObservation` is an append-only observation from a source that explicitly publishes a ranking.
- Ranking position represents source-native rank and may participate in composite ranking.
- A chronological news/feed position represents recency, not importance.
- Finance News Intelligence and Finance Market Pulse are separate product concepts.
- News Intelligence contains news and chronological feeds.
- Market Pulse contains crypto trends, market-cap lists, active stocks, trading-volume lists, and similar market rankings.
- News/feed items must never be fed into ranking RRF as though their feed order were an importance rank.
- A news item links to its original external article URL.
- A subject is not owned by or directly linked to an arbitrary news provider.
- One content item may relate to multiple canonical subjects.
- Search returns one canonical subject once; related content supplies context rather than becoming a duplicate subject.
- Composite Ranking measures cross-platform attention, not quality, investment merit, or prediction.

## 3. Architecture & Tech Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL.
- PostgreSQL extensions: `pg_trgm` and `vector`/pgvector.
- Frontend: Next.js App Router, React, TypeScript, CSS.
- HTTP acquisition: `httpx` through the shared `HTTPClient`.
- Validation: pytest, Ruff, ESLint, TypeScript, and Next.js production build.
- Source adapters normalize external records at the boundary.
- Ranking ingestion writes append-only source snapshots and ingestion batches.
- Canonical resolution uses identities, aliases, normalization, and conservative matching.
- Search uses a materialized `SearchDocument` projection per canonical subject.
- Lexical retrieval combines exact, alias, prefix, PostgreSQL FTS, and pg_trgm candidates.
- Optional semantic retrieval uses an `EmbeddingProvider` abstraction and pgvector storage.
- Search RRF and Trending RRF are separate scoring contexts.
- Phase 3.1 adds general content ingestion, `ContentItem`, and content-to-subject links.
- Phase 3.2 adds a read-only Subject Detail model (`services/subject_detail.py`) and a shared Next.js layout with routes `/`, `/today`, `/trending`, `/search`, and `/subject/[id]`.

## 4. Permanent Design Rules

- Read this file before changing the repository.
- Inspect current code, migrations, tests, and README before relying on a historical plan.
- Preserve completed functionality and prefer incremental changes over rewrites.
- Use machine values in English: `ranking`, `feed`, `news`, `market_pulse`.
- Translate labels only in the UI; never translate source brands or source-provided titles.
- Preserve original URLs and source timestamps.
- Do not republish full copyrighted or paywalled article bodies.
- Store title, URL, publication time, source, language, legitimate short excerpt, and structured metadata.
- Prefer official API, then RSS, then public structured endpoints, then stable public HTML.
- Do not bypass login, CAPTCHA, WAF, robots restrictions, or access controls.
- Document source substitutions and their failure reasons.
- Do not modify historical Alembic revisions; add a new migration.
- Canonical Subject remains the identity source of truth.
- Entity linking must be deterministic, testable, conservative, and auditable.
- Phase 3.1 does not use LLM extraction, AI classification, summarization, or topic clustering.
- Do not build a full Event Graph before Phase 6.
- Keep existing market observations, snapshots, subjects, and search documents.
- New content ingestion must be idempotent.
- Historical content is retained; UI limits do not imply backend deletion.
- All timestamps are timezone-aware.
- Primary UI page-size rules:
  - Home: Top 5 highlights.
  - Today: about 5 items per source by default.
  - Trending: Top 20 by default.
  - Large datasets: View More, pagination, and filters.
- Subject Detail: current rank, source appearances (about 20 lists), rank history, and 10 related news per page.
- Do not add infinite scroll.
- Canonical Subject Detail (`/subject/{id}`) is the internal destination for Trending and Search subjects; news and source titles link to the original external URL.
- Primary navigation is Home, Today, Trending, Search; Computer, Finance, and Gaming are tabs or filters, never top-level navigation.
- Never create a cross-domain global ranking or compare composite scores across domains.
- Page loads read persisted data only; they never trigger ingestion, ranking, reindexing, embeddings, or LLM calls.
- Frontend source filters use machine source keys from `/api/v1/sources`, never hard-coded display names.
- English and Simplified Chinese UI must remain available.
- Locale persistence must remain functional.
- Source failures must remain isolated during bulk ingestion.

## 5. Current Data Sources

### Computer Rankings

- Hacker News — Top Stories — official Firebase API — English.
- GitHub — Trending Daily — public HTML — English.
- IT之家 — 日榜 — public mobile HTML — Simplified Chinese.
- 虎嗅 — 48小时热文 — public HTML — Simplified Chinese.

### Finance News Intelligence

- CNBC Markets — official public RSS — English — live verified.
- Nasdaq Markets — official public RSS — English — live verified.
- 东方财富 7×24 — public structured endpoint — Simplified Chinese — live verified.
- 新浪财经 7×24 — public structured endpoint — Simplified Chinese — live verified.
- Planned Reuters Markets/Business was replaced because maintained Reuters newswire RSS requires credentials; public investor RSS is not the requested news product.
- Planned Yahoo Finance US news RSS was replaced because no stable public acquisition endpoint was verified.
- Replacements are CNBC Markets and Nasdaq Markets RSS; no access controls were bypassed.

### Finance Market Pulse

- CoinGecko — Trending Searches — public API — English.
- CoinPaprika — Market Cap Ranking — public API — English.
- 东方财富 — A股成交额榜 — public market-data API — Simplified Chinese.
- 新浪财经 — A股成交额榜 — public market-data API — Simplified Chinese.
- These are Market Pulse sources, not Finance News sources.

### Gaming Rankings

- Steam — Most Played — public HTML — English.
- itch.io — Top Sellers — official RSS — English.
- 游民星空 — 近期热游榜 — public HTML — Simplified Chinese.
- 3DM — 网络游戏排行榜 — public HTML — Simplified Chinese.

### Historical Substitutions

- 36氪 returned a security-check interstitial and was replaced by 虎嗅.
- 雪球 required login and explicitly restricted automated access; it was replaced by 新浪财经 market data.
- TapTap returned a WAF challenge and was replaced by 游民星空.
- 17173 returned HTTP 567 during final live ingestion and was replaced by 3DM.

## 6. Core Data Model

- `Source`: external provider, domain, primary language, acquisition type, and base URL.
- `RankList`: a concrete source-published ranking.
- `IngestionBatch`: idempotent snapshot-ingestion record and counts.
- `RankObservation`: append-only subject appearance with rank, title, URL, metric, metadata, and observation time.
- `Subject`: canonical cross-source entity.
- `SubjectAlias`: localized or source-observed names with provenance.
- `SubjectExternalIdentity`: stable source IDs, canonical IDs, tickers, or URLs.
- `CompositeRankingRun`: immutable successful composite calculation keyed by input/config hashes.
- `CompositeRankingEntry`: ranked canonical subject, score, trend, and source breakdown.
- `SearchDocument`: one bounded searchable projection per canonical subject.
- `SubjectEmbedding`: optional current embedding with model, dimension, content hash, and timestamp.
- `ContentItem`: source-neutral news/feed history, stable source identity, URL, publication/observation timestamps, language, excerpt, metadata, and content hash.
- `ContentSubjectLink`: many-to-many deterministic related-news links with strategy and confidence.
- `RankList.list_type`: `ranking` or `market_pulse`; general content uses `news` or `feed`.

## 7. Completed Phases

- Phase 0 — Foundation: completed.
- Phase 1 — Information Retrieval MVP: completed.
- Phase 2 — 12-Source Cross-Platform Intelligence: completed; closure issues resolved.
- Phase 3 — Multilingual Search: implementation completed.
- Phase 3 includes SearchDocument, PostgreSQL FTS, pg_trgm, pgvector schema, embedding abstraction, hybrid search, search RRF, filters, API, and bilingual UI.
- Live semantic-provider verification is not completed because no live multilingual provider is configured.
- NVIDIA/英伟达 and Black Myth/黑神话 are verified by deterministic PostgreSQL tests, not observed in the current live corpus.
- Phase 3.1 — Finance News & Content-Type Refactor: completed.
- Phase 3.2 — Subject Detail, Related News & Page Consolidation: completed.

## 8. Current Phase

- Phase 3.2 — Subject Detail, Related News & Page Consolidation: completed on branch `phase-3.2`, pending user review (not merged, not tagged).
- Split the former combined root page into Home, Today, and Trending routes with shared header navigation and a shared locale provider.
- Added Subject Detail (`/subject/[id]`) backed by `GET /api/v1/subjects/{id}` and `GET /api/v1/subjects/{id}/history`, with related news reused from `GET /api/v1/news?subject_id=`.
- Trending and Search subject names link to Subject Detail.
- The Search source filter is built dynamically from `/api/v1/sources`.
- No data sources, AI features, or schema changes were added.

## 9. Current Implementation Status

- Twelve ranking adapters and append-only snapshot ingestion exist.
- Today, Trending, Sources, and Search APIs exist.
- English/Simplified Chinese UI exists for Home (`/`), Today (`/today`), Trending (`/trending`), Search (`/search`), and Subject Detail (`/subject/[id]`).
- Search indexing refreshes incrementally after ranking ingestion.
- Existing Finance rankings are preserved and explicitly classified as `market_pulse`.
- Migration head is `20260927_06`; it adds content tables and rank-list semantics. Phase 3.2 required no schema migration.
- ContentItem, ContentSubjectLink, News API, Finance News UI, Trending previews, and Search context are implemented.

### Page architecture (Phase 3.2)

- `frontend/app/layout.tsx` wraps every page in `LocaleProvider` (`lib/i18n.tsx`, `localStorage` key `locale`) and `AppHeader` (`components/AppHeader.tsx`). Shared UI helpers live in `components/shared.tsx`; shared strings live in `common` in `lib/i18n.tsx`, and page strings stay in per-page `copy`/`labels` dictionaries.
- `/` Home: `GET /trending?limit=5` (Top 5 per domain, Finance labelled Market Pulse) and `GET /news?domain=finance&limit=5`. No Home API exists. The legacy `/?view=trending` URL redirects to `/trending`.
- `/today`: one domain tab at a time (`?domain=`), 5 items per source with a local View More; Finance shows Latest News first (news View More pages through `/news?source=`), with Market Pulse in a collapsed secondary section.
- `/trending`: domain tabs (`?domain=`), Top 20 via `limit=20`, expandable to 50; trend, rank change, and source count; related-news previews only when the count is non-zero.
- `/search`: Phase 3 behavior preserved; the source filter uses `searchable` source keys from `/api/v1/sources`, and legacy display-name URLs are mapped to keys.
- `/subject/[id]`: loading, error, and not-found states; replaces the URL with the canonical ID after a redirect.

### Subject Detail

- `services/subject_detail.py`: `resolve_canonical` follows `subject_redirects`; `subject_detail` returns identity, `display_names`, ordered aliases, current ranking from `ranking.latest_successful_run`, and up to 20 source appearances (latest observation per rank list, appearance count, best rank, first seen, current-snapshot flag, registry key, and metric label). It also returns the true related-news count. `rank_history` returns successful runs newest first with `limit` and `total`. Composite entries recorded under merged duplicate IDs are included.
- `resolution.display_names` is the single display-name rule used by Search, Trending, and Subject Detail: localized/official aliases, with a non-Chinese canonical name winning in English.
- `/trending` gained backward-compatible `limit`, `total`, and `display_names`; `related_news_count` is now the true linked count rather than the preview length.
- `/sources` gained `searchable`; `merge_subjects` now moves `ContentSubjectLink` rows to the canonical subject.
- Live database verification recorded 105 content items, 0 reliable links, 340 search documents, and 976 observations.
- Live source totals: CNBC 30, Nasdaq 15, 东方财富 30, and 新浪财经 30.
- Repeated international-source ingestion persisted zero duplicates.
- A live ticker false positive was detected, the rule was tightened to case-sensitive uppercase ticker mentions, and all generated links were rebuilt.
- No reliable live related-news match remained; deterministic association tests pass.

## 10. Known Issues / Technical Debt

- Live semantic provider is not configured; subject embeddings are currently absent in the recorded environment.
- Live NVIDIA/英伟达 and Black Myth/黑神话 aliases have not been observed in the current corpus.
- Public upstream sources can change structure or access behavior and require fixture-backed parsers and honest live reporting.
- Full Subject Detail is intentionally deferred.
- True news-topic trending requires event/topic clustering, source counts, article counts, and recency; feed positions are not a substitute.
- Resolved in Phase 3.2: the Search source filter no longer hard-codes display names; it uses `searchable` keys from `/api/v1/sources`.
- Live related-news links remain 0, so Subject Detail and Trending show only the empty related-news state in the live corpus; synthetic links are covered by PostgreSQL tests.
- The live database has two separate `Bitcoin` finance subjects (`42d6184f…`, currently ranked, and `7e1eb498…`, historical CoinPaprika only) and no subject redirects; they were intentionally not merged.
- The frontend redirect replace was not browser-verified against live data because the live database has no redirects; backend redirect resolution is covered by PostgreSQL tests.
- Rank history lists only successful runs that ranked the subject; runs where it was absent are not shown as gaps.
- The frontend has no automated test framework; it is validated by ESLint, TypeScript, the production build, and browser checks.

## 11. Next Planned Work

- Phase 4 — Company Ranking Tracker (next).
- Phase 5 — Company Profiles.
- Phase 6 — Event Integration.
- Phase 7 — AI Intelligence.
- Phase 8 — Product Polish.

### Page Plan

- Implemented in Phase 3.2: Home, Today, Trending, Search, Subject Detail.
- Rankings (Company Rankings) arrives in Phase 4 and Companies (Company Intelligence) in Phase 5; add them to primary navigation only when implemented, with no placeholder pages.
- Long-term primary navigation: Home, Today, Trending, Search, Rankings, Companies.
- Computer, Finance, and Gaming remain tabs or filters rather than unlimited top-level navigation.

## 12. Validation Status

- Phase 3.2: `alembic current` is `20260927_06 (head)`; there is no new migration.
- Phase 3.2 backend: pytest 113 passed (80 unit and 33 PostgreSQL integration); Ruff lint and format checks passed.
- Phase 3.2 frontend: ESLint, TypeScript, and the Next.js production build passed.
- Phase 3.2 live API: `/health`, `/health/db`, `/sources`, `/today`, `/trending` (with `domain`/`limit`), `/search?q=Bitcoin` (with a key source filter), `/news`, `/subjects/{id}`, and `/subjects/{id}/history` returned 200; an unknown subject returned 404 and a malformed ID 422.
- Phase 3.2 browser: English and Simplified Chinese Home, Today, Trending, Search, and Subject Detail; Trending → Subject and Search → Subject clicks; not-found and not-currently-ranked states; the related-news empty state; locale persistence across reload; back/forward on domain tabs; and no horizontal overflow at 375px.
- Phase 3.2 live counts were unchanged by validation: 340 subjects, 976 observations, 9 successful composite runs, 340 search documents, 105 content items, 0 content links, and 0 subject redirects.
- Phase 3.1 migration upgrade/current passed at revision `20260927_06`.
- Backend: 100 tests passed; Ruff lint and format checks passed.
- Frontend: ESLint, TypeScript, and Next.js production build passed.
- Search reindex refreshed 340 canonical subjects.
- Four finance content sources were live verified with 105 persisted items and three sampled records per source.
- Related-news APIs returned successfully; the final live corpus had no reliable link, so no live association is claimed.
- Required backend checks: migration upgrade/current, pytest, Ruff lint, and Ruff format check.
- Required frontend checks: ESLint, TypeScript, and production build.
- Required Phase 3.1 checks: live news ingestion, URL sampling, related-news verification, search reindex, counts, and ranking/search regressions.
- Do not mark live checks complete when only deterministic fixtures or tests passed.

## 13. Important Commands

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
docker compose up -d

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./backend[dev]"

cd backend
alembic upgrade head
alembic current
python -m industry_intelligence.cli backfill-subjects
python -m industry_intelligence.cli ingest-all
python -m industry_intelligence.cli rank-all
python -m industry_intelligence.cli search-reindex
pytest
ruff check .
ruff format --check .

cd ../frontend
npm run lint
npm run typecheck
npm run build
```

- Single-source ingestion: `python -m industry_intelligence.cli ingest SOURCE_KEY`.
- Single content-source ingestion: `python -m industry_intelligence.cli ingest-content SOURCE_KEY`.
- All content sources: `python -m industry_intelligence.cli ingest-content-all`.
- Rebuild deterministic links: `python -m industry_intelligence.cli content-relink`.
- Single-domain ranking: `python -m industry_intelligence.cli rank DOMAIN`.
- Targeted search refresh: `python -m industry_intelligence.cli search-reindex --subject-id UUID`.
- Optional embeddings: `python -m industry_intelligence.cli search-embed`.

## 14. AI Agent Instructions

### Before Changes

1. Read `AGENTS.md` completely.
2. Inspect the repository.
3. Inspect Alembic head and migration history.
4. Inspect relevant tests and fixtures.
5. Do not assume old plans equal current implementation.
6. Preserve completed functionality.
7. Prefer incremental change over rewrite.
8. Check for user-owned or unrelated working-tree changes and preserve them.
9. Verify source access without bypassing restrictions.

### During Changes

1. Keep domain concepts explicit and machine values stable.
2. Add migrations rather than editing applied history.
3. Maintain backward-compatible API behavior unless the active phase explicitly changes it.
4. Add deterministic tests for success, empty, malformed, partial, failure, Unicode, timestamp, identity, and false-positive cases.
5. Keep Search and Trending scoring semantics separate.
6. Keep source brands and source titles untranslated.
7. Do not fabricate live data, aliases, links, counts, or verification.

### At Completion

1. Run validation.
2. Perform required live verification.
3. Update README when public architecture changes.
4. Update `AGENTS.md` with the final repository truth.
5. Record incomplete checks as incomplete.
6. Then produce the final report.

## 15. Last Updated

- 2026-09-27 (Australia/Sydney), after Phase 3.2 implementation and validation.
