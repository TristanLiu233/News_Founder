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
- External Company Rankings are publisher rankings shown as published: External Rankings != Our Opinion.
- Today != Trending != Search != Company Rankings. Trending RRF and Search relevance are never used as company rankings, and company rankings never feed Trending or Search.
- A `Company` (Phase 4) is a minimal cross-domain identity for external company rankings; it is separate from `Subject` and is not a profile.
- Different company rankings (market cap, revenue, total assets) are never combined into a platform company score.

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
- Phase 4 adds the External Company Ranking Tracker: `adapters/company_ranking/` (protocol, CompaniesMarketCap CSV and Wikipedia REST adapters, explicit `COMPANY_RANKINGS` registry), `services/company_resolution.py`, `services/company_ranking_ingestion.py`, `services/company_rankings.py`, `api/company_rankings.py`, and the `/rankings` page.

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
- Company ranking snapshots are immutable and idempotent: a repeated `snapshot_key` is a replay and identical neighbouring content is not duplicated; history is never rewritten to simulate a latest list.
- Store the publisher's real Top N (or a documented tracked prefix); never pad or fabricate positions, metrics, or identity matches.
- Company resolution is conservative: exact identity, reviewed identity-to-identity seed pairs, unique exact name; no fuzzy matching. Only ticker symbols are exclusive identities.
- Honour publisher API policies (for example Wikimedia's User-Agent contact requirement) through configuration; never fake a browser User-Agent, switch or imitate HTTP clients to pass bot detection, or bypass a policy. Personal contact details stay in the local `.env` and are never committed; each deployment uses its own maintainer contact.
- `SOURCE_CONTACT` is sent only by HTTP clients that opt in (`HTTPClient(..., contact=...)`, used for rankings with `requires_contact`); blank and `example.com`/`.invalid`/`.test` placeholders count as unset.

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

### External Company Rankings (Phase 4)

- CompaniesMarketCap — Largest tech companies by market cap — computer — public CSV download — English — live verified (Top 100 of 1,129).
- CompaniesMarketCap — Largest banks and bank holding companies by market cap — finance — public CSV download — English — live verified (Top 100 of 719).
- CompaniesMarketCap — Largest video game companies by market cap — gaming — public CSV download — English — live verified (Top 100 of 231).
- Wikipedia — List of largest banks (by total assets) — finance — Wikimedia REST API current page HTML — English — implemented and fixture-tested, NOT live verified (HTTP 403).
- Wikipedia — List of largest video game companies by revenue — gaming — Wikimedia REST API current page HTML — English — implemented and fixture-tested, NOT live verified (HTTP 403).
- Rejected: Newzoo (403 Cloudflare challenge), Forbes Global 2000 (undocumented internal JSON; cross-industry), Fortune Global 500 (cross-industry; terms unclear), Brand Finance (JavaScript-only app), Motley Fool (cross-industry), Wikipedia largest technology companies by revenue (no explicit rank column).
- Wikipedia first answered 403 without a User-Agent contact. The owner then configured `SOURCE_CONTACT` locally (value only in the gitignored `.env`), and the adapter was refactored to Wikimedia's policy-recommended current-page REST endpoint. Wikimedia requests remain HTTP 403 from the current application client after SOURCE_CONTACT configuration and policy-compliant endpoint changes. One request per ranking was made, serially; no further attempts, alternative clients, command-line fetchers, browser User-Agents, or OAuth were used. The cause of the 403 is not established by the project's evidence and must not be documented as a specific blocking mechanism.
- Wikimedia's Robot policy (https://wikitech.wikimedia.org/wiki/Robot_policy) prefers `/api/rest_v1/page/html/{title}` or `/wiki/` pages over the Action API for page HTML, discourages requests for non-current revisions, and describes authentication (OAuth 2.0) or published source CIDRs as stronger client identification. The adapter now follows the first two; authentication is a future owner decision.

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
- `Company`: minimal cross-domain identity (`canonical_name`, `normalized_name`; `canonical_name` is not unique). The Phase 0 `domain` column was removed because a company can appear in several domains.
- `CompanyAlias`: published names with provenance (`alias_source` = ranking key).
- `CompanyIdentity`: unique `(scheme, value)` such as `companiesmarketcap:symbol` or `wikipedia:en` (article title, section fragment kept).
- `Ranking`: one external ranking definition with a stable `key`, `source_key`, domain, publisher, source-provided name, metric type/label/unit, frequency, acquisition, language, and source URL.
- `RankingSnapshot`: one immutable observation, unique `(ranking_id, snapshot_key)`, with `observed_at`, optional `published_at`, `effective_at` (history order), source URL, content hash, status, stored and source entry counts, and metadata. The Phase 0 per-company placeholder table was rebuilt.
- `RankingEntry`: company position in a snapshot with rank, raw name, raw value, optional parsed metric value, resolution strategy, and metadata; unique `(snapshot_id, company_id)`.

## 7. Completed Phases

- Phase 0 — Foundation: completed.
- Phase 1 — Information Retrieval MVP: completed.
- Phase 2 — 12-Source Cross-Platform Intelligence: completed; closure issues resolved.
- Phase 3 — Multilingual Search: implementation completed.
- Phase 3 includes SearchDocument, PostgreSQL FTS, pg_trgm, pgvector schema, embedding abstraction, hybrid search, search RRF, filters, API, and bilingual UI.
- Live semantic-provider verification is not completed because no live multilingual provider is configured.
- NVIDIA/英伟达 and Black Myth/黑神话 are verified by deterministic PostgreSQL tests, not observed in the current live corpus.
- Phase 3.1 — Finance News & Content-Type Refactor: completed.
- Phase 3.2 — Subject Detail, Related News & Page Consolidation: completed (committed on branch `phase-3.2`).

## 8. Current Phase

- Phase 4 — Company Ranking Tracker: implemented on branch `phase-4` (branched from committed `phase-3.2`), pending user review; not committed, not merged, not tagged.
- Replaced the unused Phase 0 `Company`/`Ranking`/`RankingSnapshot`/`RankingAdapter` placeholders through migration `20260927_07` (the upgrade refuses to run if placeholder rows exist).
- Added three live CompaniesMarketCap rankings and two Wikipedia rankings that are implemented and fixture-tested but NOT live verified (HTTP 403 after `SOURCE_CONTACT` configuration and the REST endpoint change).
- Added `/rankings` to primary navigation (Home, Today, Trending, Search, Rankings) with domain tabs, ranking selector, snapshot table, movement, dropped entries, on-demand history, and per-company "other rankings".
- No company profiles, AI features, scores, or cross-ranking aggregation were added.

## 9. Current Implementation Status

- Twelve ranking adapters and append-only snapshot ingestion exist.
- Today, Trending, Sources, and Search APIs exist.
- English/Simplified Chinese UI exists for Home (`/`), Today (`/today`), Trending (`/trending`), Search (`/search`), and Subject Detail (`/subject/[id]`).
- Search indexing refreshes incrementally after ranking ingestion.
- Existing Finance rankings are preserved and explicitly classified as `market_pulse`.
- Migration `20260927_06` added content tables and rank-list semantics; Phase 3.2 required no schema migration.
- ContentItem, ContentSubjectLink, News API, Finance News UI, Trending previews, and Search context are implemented.
- Migration head is now `20260927_07` (Phase 4 company rankings).

### Company Ranking Tracker (Phase 4)

- Adapters are constructed as `Adapter(http, definition)`, return one `CompanyRankingSnapshot` of the current published version, and count malformed rows in `failed_count`. There is no historical-revision option in adapters, services, or the CLI; history comes only from this project's periodic snapshots.
- `persist_company_snapshot` (caller's transaction) takes a global `company_rankings` advisory lock, upserts the ranking definition by key, replays an existing `snapshot_key`, skips content identical to its neighbour, resolves companies, and writes the snapshot once with final counts (never updated). A snapshot with no valid entries raises and preserves history.
- Snapshot keys: CompaniesMarketCap `daily:YYYY-MM-DD` (UTC); Wikipedia `revision:<revid>` read from the current REST page (`<html about=…/Special:Redirect/revision/N>`, ETag fallback), with `dc:modified` as `published_at`. Re-reading an unchanged current page is a replay.
- Resolution strategies recorded per entry: `external_identity`, `seed_identity`, `exact_name`, `new_company`.
- Seed identity pairs (reviewed 2026-09-27): 1398.HK/ICBC, SAN/Banco Santander, TD/Toronto-Dominion Bank, MUFG/MUFG, 3968.HK/China Merchants Bank, ING/ING Group, SMFG/SMBC Group, RBLX/Roblox Corporation, 036570.KS/NCSoft.
- `/company-rankings` merges persisted rankings with registered-but-unobserved ones (`id: null`, `status: no_snapshot`). Rankings are addressed by key or UUID.
- `/rankings` URL state: `?domain=&ranking=&snapshot=`; a ranking from another domain corrects the domain with `replaceState`. Company names are plain text (no company page before Phase 5).
- Live data (2026-09-27): 294 companies, 294 aliases, 294 identities, 3 rankings with snapshots (5 registered), 3 snapshots, 300 entries; 6 companies (Microsoft, Tencent, Sony, NetEase, Nintendo, Sea Limited) appear in two rankings via their symbol.

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
- Wikipedia company rankings are not live verified. Wikimedia requests remain HTTP 403 from the current application client after SOURCE_CONTACT configuration and policy-compliant endpoint changes. `SOURCE_CONTACT` stays configured locally and is sent only on Wikimedia requests. Enabling them later is an owner decision (for example Wikimedia OAuth 2.0 authentication).
- Each live company ranking has one snapshot (2026-09-27), so live movement is not yet observable; movement, New, dropped, and history switching were verified by PostgreSQL tests and in a disposable preview database loaded offline with two Wikipedia revisions captured during the source audit (deleted afterwards). That was offline fixture verification, not production live ingestion.
- CompaniesMarketCap snapshots store a Top 100 prefix of the full category list, so New/dropped at the boundary means entering or leaving the tracked Top 100.
- A Wikipedia row that links to a parent company's article gives that parent's article as the identity (for example "NetEase Games" once linked to "NetEase"); section fragments are kept, but such rows can share an article identity with a later parent row.
- Company display names are the publishers' English names; no localized company aliases exist yet.
- Only one CompaniesMarketCap snapshot per UTC day is kept; intra-day re-runs are replays.

## 11. Next Planned Work

- Phase 4 — Company Ranking Tracker (implemented, pending review).
- Phase 5 — Company Profiles (next; do not start before Phase 4 review).
- Phase 6 — Event Integration.
- Phase 7 — AI Intelligence.
- Phase 8 — Product Polish.

### Page Plan

- Implemented in Phase 3.2: Home, Today, Trending, Search, Subject Detail.
- Implemented in Phase 4: Rankings (`/rankings`, in primary navigation).
- Companies (Company Intelligence) arrives in Phase 5; add it to primary navigation only when implemented, with no placeholder pages.
- Long-term primary navigation: Home, Today, Trending, Search, Rankings, Companies.
- Computer, Finance, and Gaming remain tabs or filters rather than unlimited top-level navigation.

## 12. Validation Status

- Phase 4: `alembic current` is `20260927_07 (head)`; upgrade → downgrade → upgrade round trip passed on the dev database, and a fresh empty database migrated through all seven revisions.
- Phase 4 baseline before changes: pytest 113 passed (80 unit, 33 integration); Ruff, ESLint, TypeScript, and build passed.
- Phase 4 backend (after the Wikimedia compliance refactor): pytest 167 passed (119 unit, 48 PostgreSQL integration); Ruff lint and format checks passed. Integration tests left live counts unchanged. Tests cover: no Action API HTML ingestion, no historical-revision option, `SOURCE_CONTACT` sent only to Wikimedia, placeholder contacts treated as unset, REST fixture parsing, and current-page snapshot idempotency.
- Phase 4 frontend: ESLint, TypeScript, and the Next.js production build (including `/rankings`) passed.
- Phase 4 live: three CompaniesMarketCap rankings fetched, parsed, persisted, and read through the API; same-day re-ingestion replayed with zero new rows; `company-ranking-ingest-all` isolated the two Wikipedia failures and exited 1 (first `SourceContactRequired`; after configuring `SOURCE_CONTACT` locally, HTTP 403). After the REST current-page refactor, one serial request per Wikipedia ranking still returned HTTP 403; no further attempts were made.
- Phase 4 live API: `/company-rankings` (with domain, limit/offset), `/company-rankings/{key}`, `/history`, and `/companies/{id}/rankings` returned 200; unknown ranking or snapshot 404; malformed reference 422; existing Phase 3.2 endpoints still 200.
- Phase 4 browser: English and Simplified Chinese Rankings; Computer, Finance, and Gaming tabs; ranking switching including the no-snapshot state; Show all; history; "other rankings"; external source links; unknown ranking and snapshot not-found; cross-domain link correction; refresh and back/forward; 375px, 768px, and desktop without horizontal overflow; Home, Today, Trending → Subject, and Search → Subject regression; no console errors in a fresh tab. Movement and historical snapshot rendering were checked against the disposable preview database, not live data.
- Phase 4 live counts for earlier phases were unchanged: 340 subjects, 976 observations, 9 composite runs, 340 search documents, 105 content items, 0 content links, 0 redirects.
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
- Single company ranking: `python -m industry_intelligence.cli company-ranking-ingest RANKING_KEY` (current published version only).
- All company rankings: `python -m industry_intelligence.cli company-ranking-ingest-all`.
- Wikipedia rankings require `SOURCE_CONTACT` (the deployment maintainer's own URL or email) in the local `.env`; they are currently still blocked by Wikimedia (see Known Issues).

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

- 2026-09-27 (Australia/Sydney), after Phase 4 implementation and validation.
