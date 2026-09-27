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
- A `Company` (Phase 4) is a minimal cross-domain identity for external company rankings; it is separate from `Subject`. Phase 5 attaches sourced profile facts through `CompanyProfile`/`CompanyProfileSource`; a Company may be deterministically linked to a Subject, but the two are never the same record.
- Company Rankings != Company Profiles; Company Profile != AI-generated analysis. A profile shows source-published facts with provenance; it never asserts competitive advantages, scores, or investment views.
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
- Phase 5 adds Company Profiles: `adapters/company_profile/` (DTO/protocol, official website, SEC EDGAR, GLEIF, and CompaniesMarketCap profile adapters, reviewed `PROFILE_TARGETS`, explicit `PROFILE_SOURCES`), `services/company_profile_ingestion.py`, `services/company_profiles.py`, `services/company_subject_linking.py`, `services/company_merge.py`, `api/companies.py`, and the `/companies` and `/company/[id]` pages. No LLM is used.

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
- Primary navigation is Home, Today, Trending, Search, Rankings, Companies; Computer, Finance, and Gaming are tabs or filters, never top-level navigation.
- Never create a cross-domain global ranking or compare composite scores across domains.
- Page loads read persisted data only; they never trigger ingestion, ranking, reindexing, embeddings, or LLM calls.
- Frontend source filters use machine source keys from `/api/v1/sources`, never hard-coded display names.
- English and Simplified Chinese UI must remain available.
- Locale persistence must remain functional.
- Source failures must remain isolated during bulk ingestion.
- Company ranking snapshots are immutable and idempotent: a repeated `snapshot_key` is a replay and identical neighbouring content is not duplicated; history is never rewritten to simulate a latest list.
- Store the publisher's real Top N (or a documented tracked prefix); never pad or fabricate positions, metrics, or identity matches.
- Company resolution is conservative: exact identity, reviewed identity-to-identity seed pairs, unique exact name; no fuzzy matching. Exclusive identity schemes are the CompaniesMarketCap ticker symbol, SEC CIK, and LEI; Wikipedia article titles are not exclusive. Companies merged away by a reviewed merge never match by name.
- Company profile facts, Chinese names, Company ↔ Subject links, and related news are never manufactured: Chinese names come only from an official page that literally contains them, a registered legal name, or a deterministically linked Subject title; nothing is machine-translated. Company merges are explicit and reviewed (`company-merge` with a reason), never automatic.
- Honour publisher API policies (for example Wikimedia's User-Agent contact requirement) through configuration; never fake a browser User-Agent, switch or imitate HTTP clients to pass bot detection, or bypass a policy. Personal contact details stay in the local `.env` and are never committed; each deployment uses its own maintainer contact.
- Maintainer contacts are scoped per publisher, both by client opt-in and by request host (re-checked on every request, including redirects): `SOURCE_CONTACT` (`HTTPClient(..., contact=...)`, rankings with `requires_contact`) reaches only Wikimedia hosts (`wikipedia.org`, `wikimedia.org`, `wikidata.org`); `SEC_CONTACT` (`HTTPClient(..., sec_contact=...)`, profile sources with `requires_sec_contact`) reaches only SEC hosts (`sec.gov`, `data.sec.gov`) in SEC's declared format `IndustryIntelligenceRankingPlatform/0.1 <contact>`. The two are never reused for each other or sent to any other host. Blank and `example.com`/`.invalid`/`.test` placeholders count as unset. Real values exist only in the gitignored `.env`; `.env.example` keeps `your-email@example.com`.
- SEC EDGAR access stays low-frequency and bounded: at most about 2 requests/second within one adapter (0.5 s spacing), 1 s between companies, far below SEC's 10 requests/second ceiling.
- Company profile facts come only from adopted sources through reviewed targets; a fetch failure, robots disallow, or HTTP 403 is recorded and never retried with another client or worked around.

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

### Company Profile Sources (Phase 5)

Status for each adopted source: adapter, persistence, provenance, replay handling, and read API are implemented. All 15 reviewed targets were live fetched, parsed, persisted, and replay-verified on 2026-09-28 using only their reviewed source mappings.

- Official company website — `official_website` — official company source — public HTML, robots.txt honoured (RFC 9309) — website URL, the homepage's own self-description (`<meta name="description">`, Open Graph, or schema.org JSON-LD; stored as a short attributed excerpt in its own language, never rewritten), and reviewed Chinese names accepted only if they literally appear on the cited official page. Audit: 12/12 configured sites fetched; 9 descriptions (Tencent, NetEase, and JPMorgan homepages publish none); 9 Chinese names verified (英伟达, 微软, 摩根大通, 汇丰, 中国工商银行, 腾讯, 索尼, 网易, 任天堂).
- SEC EDGAR — `sec_edgar` — official regulatory filings — `data.sec.gov/submissions` JSON plus the latest 10-K/20-F XBRL instance and label linkbase in the EDGAR archive, with the declared `SEC_CONTACT` User-Agent — SIC industry, business address, US incorporation state, tickers/exchanges, reported business/segment members (`business_areas`) and product/service members (`key_products`) as the filer's own current-period XBRL member labels. Live persistence: 11/11 reviewed registrants, all replayed unchanged. Microsoft and Sony were live verified through the schema-embedded `.xsd` label-link fallback: Microsoft persisted 3 business/segment labels and 12 product/service labels; Sony persisted 10 and 14 respectively. XBRL member tagging is filer-defined and can include dimensions that are not strict business divisions (for example a lending-related HSBC member); provenance is retained and no filer-specific correction is applied.
- GLEIF — `gleif` — Global LEI System registry — documented public API `api.gleif.org/api/v1/lei-records/{LEI}` (no key, CC0) — registered legal name in its own language (e.g. 騰訊控股有限公司, 中国建设银行股份有限公司, 任天堂株式会社), alternative-language legal names, headquarters address, legal jurisdiction. Only reviewed head-office LEIs are read (category GENERAL, ACTIVE); a branch or inactive record is rejected. Audit: 15/15 fetched and valid.
- CompaniesMarketCap company page — `companiesmarketcap` — secondary publisher — public HTML (robots.txt allows company pages) — the publisher's category tags only, kept verbatim without emoji; the page must show the target's ticker in its title. The publisher's prose description is not stored. Audit: 15/15 fetched.
- Rejected for company profiles: Wikidata (HTTP 403 on one request from the application client with the configured Wikimedia contact, 2026-09-27; not retried); GLEIF full-text search as identity evidence (returns ETFs, branches, subsidiaries — used only during review, never in ingestion); official sites of Alphabet (`abc.xyz`, HTTP 403), TSMC (`tsmc.com`, HTTP 403 including robots.txt) and China Construction Bank (`ccb.com`, connection failure) — left out of targets, not retried; CompaniesMarketCap prose descriptions (secondary, unattributed prose); Yahoo Finance / Nasdaq profile endpoints (undocumented, cookie/crumb protected); Alpha Vantage / Financial Modeling Prep / OpenCorporates (API keys, not configured); exchange profile JSON of SSE/SZSE/HKEX (undocumented internal endpoints).
- Coverage strategy: 15 reviewed targets, the Phase 4 ranking leaders across domains (Computer: NVIDIA, Apple, Alphabet, Microsoft, TSMC; Finance: JPMorgan Chase, China Construction Bank, Bank of America, HSBC, ICBC; Gaming: Tencent, Sony, NetEase, Nintendo, Take-Two, plus Microsoft). The other 279 companies keep only their Phase 4 identity and rankings.

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
- `CompanyAlias`: published names with provenance: `alias_source` is the ranking key or profile source key; `alias_type` is `source_name`, `legal_name`, `transliterated_legal_name`, or `localized_name`; `locale` as published.
- `CompanyIdentity`: unique `(scheme, value)` such as `companiesmarketcap:symbol`, `wikipedia:en` (article title, section fragment kept), `sec:cik`, or `gleif:lei`.
- `Ranking`: one external ranking definition with a stable `key`, `source_key`, domain, publisher, source-provided name, metric type/label/unit, frequency, acquisition, language, and source URL.
- `RankingSnapshot`: one immutable observation, unique `(ranking_id, snapshot_key)`, with `observed_at`, optional `published_at`, `effective_at` (history order), source URL, content hash, status, stored and source entry counts, and metadata. The Phase 0 per-company placeholder table was rebuilt.
- `RankingEntry`: company position in a snapshot with rank, raw name, raw value, optional parsed metric value, resolution strategy, and metadata; unique `(snapshot_id, company_id)`.
- Phase 5 (migration `20260927_08`) removed the unused, all-NULL Phase 0 `companies.website` and `companies.description` columns (the upgrade refuses if any value exists) and added:
- `CompanyProfile` (`company_profiles`, PK/FK `company_id` → `companies` ON DELETE CASCADE): the current field-level merge of a company's source records — `legal_name`, `legal_name_locale`, `website`, `country` (ISO alpha-2, indexed), `headquarters`, `jurisdiction`, `industry`, `categories` (JSON list), `description`, `description_language`, `business_areas` (JSON list), `key_products` (JSON list), `field_sources` (JSON field → source key), `profile_updated_at`. Derived only; rebuilt from `company_profile_sources`.
- `CompanyProfileSource` (`company_profile_sources`): one current normalized record per `(company_id, source_key)` (unique) with `source_type`, `source_name`, `source_url`, `retrieved_at` (first retrieval of the current content), `checked_at` (last confirmation), optional `published_at` (filing date / registry update), `content_hash`, `facts` (JSON), `source_metadata` (JSON). No page bodies are stored.
- `CompanySubjectLink` (`company_subject_links`): deterministic company ↔ canonical subject association, unique `(company_id, subject_id)`, both FKs ON DELETE CASCADE and indexed, with `strategy` and JSON `evidence`.
- `CompanyRedirect` (`company_redirects`): reviewed merge record, PK `duplicate_id` → `canonical_id` (both FK `companies`, `canonical_id` indexed, CHECK distinct), `reason`, `merged_at`. Duplicate company rows are kept; the downgrade refuses to discard existing redirects.

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
- Phase 4 — Company Ranking Tracker: completed and committed on branch `phase-4` (`02cc0d7`); the two Wikipedia rankings are implemented and fixture-tested but NOT live verified (HTTP 403).
- Phase 5 — Company Profiles: completed on branch `phase-5` (uncommitted, awaiting owner review) with representative live coverage of 15 reviewed targets; see Current Phase.

## 8. Current Phase

- **Phase 5 — Company Profiles: Completed** (2026-09-28). **Next Phase: Phase 6 — Event Integration** (not started; start only when the owner asks).
- Git: branch `phase-5`, created from `phase-4` at `02cc0d7`. All Phase 5 work is uncommitted in the working tree, awaiting the owner's review; nothing was committed, merged, pushed, or tagged. Other branches: `main` (`ca54228`), `phase-3.2` (`840244e`), `phase-4` (`02cc0d7`); only tag `phase-3.1-baseline`.
- Completed does not mean every company is profiled, every field is populated, every company is linked to a Subject, or every company has related news; those are coverage states, not architecture gaps. Completion rests on: complete profile architecture with provenance, all 15 reviewed targets processed live across three domains, working APIs and pages, deterministic linking, normal incomplete-profile states, passing tests, browser verification, and current documentation.

### Phase 5 checkpoints (all synchronized)

- A — Repository audit (2026-09-27): baseline before any Phase 5 change was `20260927_07`, pytest 167 (119 unit, 48 integration), Ruff/ESLint/TypeScript/build passing; baseline live counts 294 companies, 294 aliases, 294 identities (all `companiesmarketcap:symbol`), 3 rankings, 3 snapshots, 300 entries, 340 subjects, 976 observations, 105 content items. These are historical baseline numbers.
- B — Source audit: see Company Profile Sources (Phase 5). The owner chose a separate `SEC_CONTACT` (local `.env` only) scoped to SEC hosts; `HTTPClient` scopes both contacts per request host.
- C — Schema: migration `20260927_08`; upgrade → downgrade → upgrade on the dev database; a disposable empty database migrated through all eight revisions, round-tripped `08`, and was dropped. `alembic check` shows no difference for the new company tables (older pre-existing drift in content, rank-list, search, and subject tables is unchanged by Phase 5).
- D — Profile ingestion: all 15 reviewed targets live attempted and persisted as 53 source records (official website 12/12 configured, SEC EDGAR 11/11, GLEIF 15/15, CompaniesMarketCap 15/15) with no failed attempt; a full second run replayed all 53 unchanged with no identity conflicts. Microsoft's and Sony's schema-embedded XBRL labels were live parsed.
- E — Identity and reviewed merge: funnel and safeguards as in Company Profiles (Phase 5) below; merge/redirect behavior is verified by synthetic PostgreSQL tests; the live database has 0 company redirects (no live merge was needed).
- F — Company ↔ Subject: live relink over all 294 companies produced 2 `exchange_code` links (Cambricon Technologies `688256.SS` ↔ 寒武纪 `SH:688256`; CXMT `688825.SS` ↔ 长鑫科技 `SH:688825`); no exact-name live link; ambiguity and no-match cases are synthetic-test verified.
- G — Backend API: `GET /api/v1/companies`, `GET /api/v1/companies/{id}`, `/news?company_id=`, redirect-aware `/companies/{id}/rankings`.
- H — Frontend and browser verification (2026-09-28): see Validation Status.
- I — Final validation (2026-09-28): see Validation Status; one real defect found by the live audit (IFRS labels ending in lowercase " [member]" were not stripped) was fixed in code with a unit test and the full backend suite re-run; the live Sony record was intentionally not re-ingested during the read-only audit (see Known Issues).
- J — Closure: this file was re-read top to bottom and reconciled with the repository, migration, tests, live database, routes, browser verification, and README before marking Phase 5 Completed.

### Phase 4 summary (completed)

- Phase 4 — Company Ranking Tracker: completed and committed on branch `phase-4` as `02cc0d7` ("feat: add External Company Ranking Tracker (Phase 4)"), branched from `phase-3.2` (`840244e`). Not merged into `main`, not pushed, not tagged; awaiting the owner's merge decision.
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
- Migration head is `20260927_08` (Phase 5 company profiles); `20260927_07` was the Phase 4 head.
- English/Simplified Chinese UI also exists for Rankings (`/rankings`), Companies (`/companies`), and Company Detail (`/company/[id]`).

### Company Profiles (Phase 5)

- Reviewed targets: `adapters/company_profile/targets.py` `PROFILE_TARGETS` (15, reviewed 2026-09-27), each located by its `companiesmarketcap:symbol` and carrying only reviewed `cik`, `lei`, `website`, `cmc_slug`, and `localized_names` (locale, name, official page). Adding a target is a reviewed change.
- Adapters: `Adapter(http, definition)` with `async fetch(target) -> ExternalCompanyProfile` (facts limited to `PROFILE_FIELDS`, identities, `LocalizedName`s, source URL, retrieval/publication time, metadata) and `failed_count`. `ProfileRejected` marks an identity/validity failure (GLEIF branch/inactive/other LEI, CompaniesMarketCap page without the target ticker, SEC CIK mismatch). The official-website adapter honours robots.txt (RFC 9309) and raises `RobotsDisallowed`.
- Ingestion (`company-profile-ingest [--symbol ...] [--source ...]`): per source, one HTTP client (SEC client with `sec_contact` only); an unobserved target symbol is skipped without a request; SEC without `SEC_CONTACT` is skipped without a request (`SecContactRequired`); each source/target failure is isolated. `persist_profile_source` (caller's transaction, shared `company_rankings` advisory lock) attaches identities (refusing one held by another company or a second value in an exclusive scheme, recorded as `identity_conflicts`), records names as aliases, upserts the (company, source) record by content hash (facts, names, identities, `published_at`): identical content only refreshes `checked_at`; changed content replaces facts and `retrieved_at`. Websites must be absolute http(s) URLs; list fields are de-duplicated.
- Field priority (`FIELD_PRIORITY`): legal name GLEIF > SEC; website official site > SEC; description official site only; headquarters/country/jurisdiction GLEIF > SEC; industry SEC (SIC); categories CompaniesMarketCap; business areas and key products SEC XBRL. `legal_name_locale` and `description_language` follow their primary field. `field_sources` records the winning source per field.
- SEC XBRL rule: members on the segment axes (`StatementBusinessSegmentsAxis`, `SegmentsAxis`) and product axes (`ProductOrServiceAxis`, `ProductsAndServicesAxis`) used in contexts ending at `DocumentPeriodEndDate`, excluding a documented list of standard structural members; labels are the filer's terse (else standard) English labels with the member suffix stripped, from `_lab.xml` or, when absent, the schema-embedded label link; at most 24 per list. They are reported segment/product members, not guaranteed formal business divisions.
- Company ↔ Subject (`company-subject-link`, delete-and-rebuild): exact A-share exchange code (`NNNNNN.SS/.SZ` ↔ Subject `SH:/SZ:NNNNNN`) first, then a unique exact normalized company name/alias ↔ alias of exactly one canonical `company`/`stock` Subject; subjects resolve through `subject_redirects`; one subject links to at most one company; no fuzzy, partial, or cross-type matching. Run it after ranking or profile ingestion; it is not automatic.
- Reviewed merge (`company-merge CANONICAL DUPLICATE --reason`): follows redirects; moves ranking entries, identities, non-duplicate aliases, profile records (the canonical's record wins per source), and subject links; keeps the duplicate row with a `company_redirects` row; refuses different CIK/LEI values, a snapshot listing both companies, cycles, repeat merges, and a missing reason. Different tickers alone do not block it.
- Read model: `list_companies` (non-redirected companies; `q` matches normalized names/aliases by substring and identifiers exactly; `domain`/`ranking` by ranking membership; `has_profile`; with `ranking` ordered by that ranking's latest rank, otherwise profiled first then name; never compares ranks across rankings) and `company_detail` (redirect-resolved identity, aliases, identities, profile, sources with `used_fields`, current rankings from Phase 4 `company_rankings`, linked subjects, related-news count). Chinese display name preference: verified localized name > linked Subject Chinese title > Chinese legal name > English canonical name.
- Pages: `/companies` (30 per page, previous/next, URL-backed search and domain filter, profile cue, current ranking badges); `/company/[id]` (identity, profile facts, overview with source/language, reported segment/product members, external rankings and history, up to 10 related news via `/news?company_id=`, Subject links, source provenance, normal empty states); `/rankings` company names link to `/company/[id]`. Page loads read persisted data only.

### Company Ranking Tracker (Phase 4)

- Adapters are constructed as `Adapter(http, definition)`, return one `CompanyRankingSnapshot` of the current published version, and count malformed rows in `failed_count`. There is no historical-revision option in adapters, services, or the CLI; history comes only from this project's periodic snapshots.
- `persist_company_snapshot` (caller's transaction) takes a global `company_rankings` advisory lock, upserts the ranking definition by key, replays an existing `snapshot_key`, skips content identical to its neighbour, resolves companies, and writes the snapshot once with final counts (never updated). A snapshot with no valid entries raises and preserves history.
- Snapshot keys: CompaniesMarketCap `daily:YYYY-MM-DD` (UTC); Wikipedia `revision:<revid>` read from the current REST page (`<html about=…/Special:Redirect/revision/N>`, ETag fallback), with `dc:modified` as `published_at`. Re-reading an unchanged current page is a replay.
- Resolution strategies recorded per entry: `external_identity`, `seed_identity`, `exact_name`, `new_company`.
- Seed identity pairs (reviewed 2026-09-27): 1398.HK/ICBC, SAN/Banco Santander, TD/Toronto-Dominion Bank, MUFG/MUFG, 3968.HK/China Merchants Bank, ING/ING Group, SMFG/SMBC Group, RBLX/Roblox Corporation, 036570.KS/NCSoft.
- `/company-rankings` merges persisted rankings with registered-but-unobserved ones (`id: null`, `status: no_snapshot`). Rankings are addressed by key or UUID.
- `/rankings` URL state: `?domain=&ranking=&snapshot=`; a ranking from another domain corrects the domain with `replaceState`. Since Phase 5, company names link to `/company/[id]`.
- Historical Phase 4 live data (2026-09-27): 294 companies, 294 aliases, 294 identities, 3 rankings with snapshots (5 registered), 3 snapshots, 300 entries; 6 companies (Microsoft, Tencent, Sony, NetEase, Nintendo, Sea Limited) appear in two rankings via their symbol.

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
- Company English display names are the ranking publishers' names. Chinese names exist only where sourced: 11 companies have a Chinese alias (9 verified official localized names, plus Chinese legal names for TSMC and China Construction Bank), and CXMT and Cambricon get Chinese display names from their linked A-share Subjects. All other companies show the English name in the Chinese UI.
- Only the 15 reviewed targets have profiles; the other 279 companies have Phase 4 identity and rankings only. Profiles refresh only when `company-profile-ingest` is run.
- Official websites of Alphabet (HTTP 403), TSMC (HTTP 403), and China Construction Bank (connection failure) are not configured, so those profiles have no website or description. Tencent, NetEase, and JPMorgan homepages publish no description. Descriptions are the companies' own homepage metadata (marketing wording, sometimes Japanese or Chinese) and are shown as attributed excerpts.
- SEC XBRL segment/product members are filer-tagged: some are reconciling or non-segment items (an HSBC lending member; Sony's elimination and "All Other" members; generic product members such as Microsoft's "Product" and "Other"). No filer-specific filter is applied. Non-SEC registrants (China Construction Bank, ICBC, Tencent, Nintendo) have no business areas, products, or SIC industry.
- The live Sony SEC record (ingested 2026-09-27/28) still stores labels ending in " [member]" (lowercase, IFRS style). The parser was fixed on 2026-09-28 to strip that suffix; the stored record will be updated (reported as `updated`) on the next profile ingestion run. It was not re-ingested during the read-only final audit.
- Live Company ↔ Subject links are 2 (exchange code); live company-related news is 0 because live content-subject links are 0.
- `company-subject-link` is a manual rebuild; companies created by later ranking ingestion are unlinked until it runs.
- GLEIF headquarters text is as registered (uppercase or local script, for example "SANTA CLARA, US-CA", "東京都 港区").
- Only one CompaniesMarketCap snapshot per UTC day is kept; intra-day re-runs are replays.

## 11. Next Planned Work

- Phase 4 — Company Ranking Tracker (completed; committed on `phase-4`, not merged).
- Phase 5 — Company Profiles (completed on `phase-5`, uncommitted, awaiting owner review).
- Phase 6 — Event Integration (next; start only when the owner asks).
- Phase 7 — AI Intelligence.
- Phase 8 — Product Polish.

### Page Plan

- Implemented in Phase 3.2: Home, Today, Trending, Search, Subject Detail.
- Implemented in Phase 4: Rankings (`/rankings`, in primary navigation).
- Implemented in Phase 5: Companies (`/companies`) and Company Detail (`/company/[id]`); primary navigation is Home, Today, Trending, Search, Rankings, Companies.
- Computer, Finance, and Gaming remain tabs or filters rather than unlimited top-level navigation.

## 12. Validation Status

- Phase 5 final (2026-09-28, last actual runs): `alembic current` = `20260927_08 (head)`; pytest 234 passed (163 unit, 71 PostgreSQL integration; the only warning is the pre-existing Starlette TestClient/httpx deprecation); Ruff check passed; Ruff format check passed (111 files); frontend ESLint passed, TypeScript passed, Next.js production build passed with 9 routes (`/`, `/_not-found`, `/companies`, `/company/[id]`, `/rankings`, `/search`, `/subject/[id]`, `/today`, `/trending`). An earlier Phase 5 run of 233 (162 unit) preceded the member-suffix fix and its test.
- Phase 5 final live counts (read-only audit, 2026-09-28; identical before and after the test suite): 294 companies, 324 company aliases (294 source_name, 19 legal_name, 2 transliterated_legal_name, 9 localized_name), 320 company identities (294 symbol, 11 `sec:cik`, 15 `gleif:lei`); 15 profiles; 15 companies with ≥1 source record; 53 profile source records (official website 12, SEC EDGAR 11, GLEIF 15, CompaniesMarketCap 15 companies); profiles with website 12, description 9, industry 11, categories 15, business areas 9, key products 10; 11 companies with a Chinese alias (9 localized, 4 with a Chinese legal name, overlapping); 2 company-subject links (both `exchange_code`: Cambricon ↔ 寒武纪, CXMT ↔ 长鑫科技); 0 companies with related news; 0 company redirects; 3 rankings, 3 snapshots, 300 ranking entries; 340 subjects, 976 observations, 9 composite runs, 340 search documents, 105 content items, 0 content links, 0 subject redirects.
- Phase 5 live spot check (read-only): NVIDIA four-source profile, three aliases with provenance, rank 1 in tech; Microsoft and Sony SEC records parsed via schema-embedded labels (Microsoft 3 segment / 12 product members; Sony 10 / 14); JPMorgan GLEIF + SEC 10-K records; CXMT and Cambricon links by exchange code; China Construction Bank partial profile with website, description, industry, and lists null/empty and `field_sources` naming only GLEIF and CompaniesMarketCap; Amazon without a profile.
- Phase 5 browser (2026-09-28, production build against the dev database): `/companies`, `/company/[id]`, `/rankings`, `/`, `/today`, `/trending`, `/search`, `/subject/[id]`; NVIDIA (full profile, Chinese alias), China Construction Bank (partial), Sony (SEC, local name), Amazon and CXMT (no profile), missing UUID 404, empty news and missing-Subject states, the live CXMT → 长鑫科技 Subject link; Companies → Company, Rankings → Company, Company → Ranking, Company → external source, Company → Subject; English/Simplified Chinese with persistence across reload; back/forward/reload; 375 px, 768 px, 1280 px without horizontal overflow; empty console; backend logs showed only persisted read APIs during page loads. No frontend code changed after this verification.
- Phase 5 contact hygiene (2026-09-28): real `SOURCE_CONTACT` and `SEC_CONTACT` occurrences in tracked or unignored files and in `git diff` = 0; `.env` is ignored and untracked; `.env.example` and README contain only `your-email@example.com`.
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
- Company profiles (reviewed targets): `python -m industry_intelligence.cli company-profile-ingest [--symbol SYMBOL] [--source SOURCE_KEY]` (options repeat; SEC requires `SEC_CONTACT`).
- Rebuild Company ↔ Subject links: `python -m industry_intelligence.cli company-subject-link`.
- Reviewed company merge: `python -m industry_intelligence.cli company-merge CANONICAL_UUID DUPLICATE_UUID --reason "evidence"`.
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

- 2026-09-28 (Australia/Sydney), after Phase 5 final validation, live audit, contact check, and closure (Checkpoints A–J) on the uncommitted `phase-5` working tree.
