# Industry Intelligence & Ranking Platform

The platform collects twelve public rankings plus ten chronological news/feed sources (four Finance, three Computer, three Gaming). It preserves source-native history, resolves ranked entries to auditable canonical subjects, and keeps chronological news separate from market rankings. Phase 4 adds an External Company Ranking Tracker that stores publisher company rankings as immutable snapshots with conservative company identity; Phase 5 adds sourced Company Profiles; Phase 6 groups existing news/feed items into canonical Events with deterministic, auditable rules; Phase 6.1 adds Computer and Gaming news, stricter deterministic content ↔ subject linking, a one-command content pipeline, and bounded Event rebuilds; Phase 7 adds optional, source-grounded AI summaries and reviewable AI suggestions on top of that deterministic layer (AI never decides canonical data). The UI supports English and 简体中文; the selected locale is stored in browser `localStorage`.

## Sources

| Domain | Source | Language | Rank list | Acquisition | Credential | Live verified |
| --- | --- | --- | --- | --- | --- | --- |
| Computer | Hacker News | en | Top Stories | Official Firebase API | No | Yes |
| Computer | GitHub | en | Trending Daily | Public HTML | No | Yes |
| Computer | IT之家 | zh-CN | 日榜 | Public mobile HTML | No | Yes |
| Computer | 虎嗅 | zh-CN | 48小时热文 | Public HTML | No | Yes |
| Finance Market Pulse | CoinGecko | en | Trending Searches | Public API | No | Yes |
| Finance Market Pulse | CoinPaprika | en | Market Cap Ranking | Public API | No | Yes |
| Finance Market Pulse | 东方财富 | zh-CN | A股成交额榜 | Public market-data API | No | Yes |
| Finance Market Pulse | 新浪财经 | zh-CN | A股成交额榜 | Public market-data API | No | Yes |
| Gaming | Steam | en | Most Played | Public HTML | No | Yes |
| Gaming | itch.io | en | Top Sellers | Official RSS | No | Yes |
| Gaming | 游民星空 | zh-CN | 近期热游榜 | Public HTML | No | Yes |
| Gaming | 3DM | zh-CN | 网络游戏排行榜 | Public HTML | No | Yes |

IT之家 daily ranking is read from `https://m.ithome.com/rankm/`; its daily list contains ranked articles and comment counts. 虎嗅's public home page exposes its 48-hour hot-article ordering. 东方财富 and 新浪财经 provide ranked A-share turnover data; turnover remains source-native metadata and is never compared between providers.

The originally requested 36氪 page returned a security-check interstitial, 雪球 explicitly blocked automated access and requested login, and TapTap returned a WAF challenge. 17173 returned HTTP 567 during final live ingestion. The implementation does not bypass those controls. They were replaced with maintainable public rankings in the same domains: 虎嗅, 新浪财经, 游民星空, and 3DM. 东方财富 remained usable through its public market-data endpoint. All substitutions retain explicit rank semantics.

## Architecture

```text
12 External Sources
        ↓
SourceAdapter → SourceItem
        ↓
RankObservation (append-only source snapshots)
        ↓
Canonical Subject
 ├─ SubjectAlias (en / zh-CN, provenance)
 └─ SubjectExternalIdentity (source ID, canonical URL/ID)
        ↓
Weighted freshness-adjusted RRF
        ↓
CompositeRankingRun / CompositeRankingEntry
        ↓
History, trend and source contribution breakdown
        ↓
GET /api/v1/trending
        ↓
English / 简体中文 UI
```

Resolution follows exact external identity, stable canonical ID, canonical URL, exact alias, normalized name, cross-language seed alias, conservative blocked fuzzy matching, then a new subject. Fuzzy candidates are restricted to the same domain and subject type. Name normalization uses Unicode NFKC, whitespace collapse, Latin case folding, and conservative Chinese punctuation normalization; it preserves digits and meaningful characters such as `C++`, `C#`, `.NET`, and `GPT-5`. URL normalization removes fragments and common tracking parameters while retaining functional query parameters.

Cross-language identity does not use online translation, LLMs, embeddings, or AI reranking. Small auditable seed groups include `Black Myth: Wukong ↔ 黑神话：悟空`, `NVIDIA ↔ 英伟达`, and `Microsoft ↔ 微软`. Finance uses `market:ticker` as a strong stock identity and keeps stocks separate from crypto unless a stable identity proves equivalence. The merge service moves observations, aliases, and identities inside the caller's transaction.

## Composite ranking

For canonical subject \(x\), rank list \(s\), list weight \(w_s\), source rank \(r_{x,s}\), and snapshot age \(t_s\):

```math
Score(x)=\sum_s \frac{w_s}{k+r_{x,s}}e^{-\lambda t_s},
\qquad \lambda=\frac{\ln 2}{T_{half}}
```

Defaults are `k = 60`, a 24-hour half-life, and weight `1.0` for every rank list. Configuration is available through `RRF_K`, `FRESHNESS_HALF_LIFE`, and `RANK_LIST_WEIGHTS`. Duplicate appearances of one canonical subject in a rank list contribute only the best rank. Ties use score descending, source count descending, best source rank ascending, then stable subject ID. Language coverage is explanatory and never changes the score. Previous rank and trend also never affect the current score.

Composite Ranking measures cross-platform attention based on rankings already produced by external platforms. It is not a quality score, investment rating, buy recommendation, prediction, or measure of company quality.

## Setup and commands

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
docker compose up -d

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c backend/constraints.txt -e "./backend[dev]"  # exact tested versions

cd backend
alembic upgrade head
python -m industry_intelligence.cli backfill-subjects
python -m industry_intelligence.cli ingest-all
python -m industry_intelligence.cli rank-all
```

The bounded DQ-1 Subject identity repair is dry-run-first. It was applied to the development
database on 2026-09-29; a dry-run now reports no remaining work:

```bash
python -m industry_intelligence.cli subject-identity-repair
python -m industry_intelligence.cli subject-identity-repair --apply  # approval required
```

The DQ-2.1 3DM game-name repair is dry-run-first; `--apply` refuses when any candidate needs review. It
was applied to the development database on 2026-09-29; a dry-run now reports no candidates:

```bash
python -m industry_intelligence.cli gaming-subject-canonical-repair
python -m industry_intelligence.cli gaming-subject-canonical-repair --apply  # approval required
```

The DQ-2 invariant audit is read-only (it never repairs) and exits 1 only on error-level findings:

```bash
python -m industry_intelligence.cli data-quality-audit
python -m industry_intelligence.cli data-quality-audit --json
```

Single-source and single-domain commands include:

```bash
python -m industry_intelligence.cli ingest ithome
python -m industry_intelligence.cli ingest eastmoney_turnover
python -m industry_intelligence.cli ingest gamersky_hot
python -m industry_intelligence.cli rank computer
```

`ingest-all` isolates source failures so one unavailable site does not prevent the other sources from committing. Batch IDs make ingestion retries idempotent. Composite input hashes include the current snapshot IDs and ranking configuration, so identical inputs do not create duplicate runs. Only successful runs are exposed.

## APIs and UI

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process health |
| GET | `/health/db` | PostgreSQL connectivity |
| GET | `/api/v1/sources` | Source status and latest counts |
| GET | `/api/v1/today` | Latest source-native lists |
| GET | `/api/v1/trending` | Latest successful composite rankings |
| GET | `/api/v1/trending?domain=gaming&limit=20` | One domain's composite ranking; optional `limit` (1–100), `total` reports the full size |
| GET | `/api/v1/subjects/{id}` | Canonical subject detail (redirect-aware) |
| GET | `/api/v1/subjects/{id}/history?limit=30` | Composite rank history from successful runs |

Run the backend with `../.venv/bin/uvicorn industry_intelligence.main:app` and the frontend with `npm run dev`. Source brands and raw external content are never translated. Backend machine values remain English (`gaming`, `up`, `zh-CN`).

## Pages

| Route | Purpose | Default size |
| --- | --- | --- |
| `/` | Dashboard composed from existing APIs: Today at a Glance (latest reports per domain), Latest Events (backend recency order), Trending Topics per domain (never a cross-domain ranking), and live External Company Rankings shown as published; no Home score or AI | 3 reports per domain, 5 Events, Top 5 per domain, Top 5 per ranking |
| `/today` | Discover · Today: source-native content, one domain tab at a time. Every domain shows Latest News first (its `news`/`feed` sources from `/api/v1/sources`, read through `/api/v1/news`); Computer and Gaming then show their source rankings, Finance shows Market Pulse in a collapsed secondary section | 5 per source, View More |
| `/trending` | Discover · Trending: cross-source composite attention per domain; rows show the topic, trend, type, and source count; score inputs are in a collapsed per-row Sources list and the RRF explanation in a collapsed Methodology note; Finance is labelled Market Pulse | Top 20, expandable to 50 |
| `/search` | Discover · Search: multilingual topic (Subject) search with a query and domain tabs; type, source (`searchable` keys from `/api/v1/sources`), and language are under More filters; no search-mode control (the backend default is used) | 20 per page |
| `/subject/{id}` | Topic (Subject) Detail: current rank and trend, related Events (hidden when none), rank history, where it appears (source lists), related news collapsed (the same reports are grouped under related Events), aliases, and a collapsed Methodology note (score, observations, link rules) | 20 lists, 10 news per page, 5 related Events |
| `/rankings` | External company rankings per domain: ranking selector (only rankings with a collected snapshot; a direct link still opens a never-collected one), one-line publisher/metric/date/coverage/original-source meta and disclaimer, a collapsed About this ranking note (cadence, acquisition, observed time, source column and description), latest snapshot table with movement, dropped entries, company names linking to Company Detail, on-demand snapshot history (`?domain=&ranking=&snapshot=`) | Top 20, expandable to all stored rows |
| `/companies` | Company directory with company search (names, aliases, legal/localized names, identifiers), domain filter, and profile availability; separate from Subject search | 30 per page, previous/next |
| `/company/{id}` | Company Detail: overview and sourced profile facts, external rankings, related Events, related news, related Topics (each hidden when empty), filed business segments and products, visible profile sources, and a collapsed Data details note (identifiers, field provenance, link rules) | Up to 10 related news, 5 related Events |
| `/events` | Canonical Events grouped from existing news/feed items: domain and event-type filters, latest-report order, report and source counts, Subject/Company links (`?domain=&type=&subject_id=&company_id=&offset=`) | 20 per page, previous/next |
| `/event/{id}` | Event Detail: headline with one meta line (domain, type, first report, report and source counts), labelled AI What Happened and Why It Matters (persisted, validated artifacts with per-point source links; a one-line state when one is unavailable, one combined line when both are), the timeline of reports linking to the original articles, related Topics and Companies (hidden when none), and a collapsed About this event note (time basis, reports by source and language, coverage ≠ importance, grouping, AI model/prompt/time) (redirect-aware) | 10 reports per page, View More |

Header navigation is shared across pages: the brand links Home (`/`), and the primary links are Discover (`/today`; active on `/today`, `/trending`, and `/search`, which share Today | Trending | Search tabs that keep `?domain=`), Events, Rankings, and Companies. There is no `/discover` route. URLs are locale-independent; the locale stays in `localStorage`. Subject names in Trending and Search open the internal Subject Detail page; news titles and source titles open the original external URL in a new tab. Pages only read persisted data: no page load triggers ingestion, ranking, reindexing, embeddings, or LLM calls. The legacy `/?view=trending` and `/?view=today` URLs redirect to `/trending` and `/today`.

Subject Detail resolves merged IDs through `subject_redirects` and returns the canonical subject; the page then replaces the URL with the canonical ID. The current rank comes only from the latest successful composite run for the subject's domain; a subject absent from it is shown as not currently ranked. Source appearances show the latest observation per rank list with appearance count, best rank, first-seen time and whether it is in the current snapshot. Related news reuses `/api/v1/news?subject_id=` and only shows deterministic links; an empty list is a normal state. Display names follow one shared alias rule in Search, Trending and Subject Detail.

## Validation

```bash
cd backend
../.venv/bin/pytest
../.venv/bin/ruff check .
../.venv/bin/ruff format --check .

cd ../frontend
npm run lint
npm run typecheck
npm run build
```

## Phase 2 Closure

Cross-language live overlap: **0 in the current recorded snapshot** in Computer, Finance and Gaming; reliable Chinese/international same-entity overlap was not observed. This is an observation, not an acceptance requirement. No live subjects were artificially merged.

Synthetic deterministic alias resolution is verified separately for Black Myth: Wukong / 黑神话：悟空, NVIDIA / 英伟达, Microsoft / 微软 and Bitcoin / 比特币. It does not prove live bilingual overlap.

Chinese HTML parsers are fixture-backed under `backend/tests/fixtures/`: IT之家, 虎嗅, 游民星空, 3DM and the inactive 17173 parser. These are representative synthetic parser inputs, not evidence of live overlap. Small JSON API samples remain inline.

## Phase 3 — Search

Search retrieves one result per canonical `Subject`. English and Chinese names remain
`SubjectAlias` records attached to that subject; no language-specific subject tables exist.
Historical `RankObservation` rows contribute recent titles, so subjects that have left today's
rankings remain searchable.

```text
Canonical Subject + Aliases + Historical observations
                         ↓
                  Search Document
                         ↓
       Exact / Alias / Prefix / PostgreSQL FTS / pg_trgm
                         +
              Optional pgvector retrieval
                         ↓
                  Search-only RRF
                         ↓
             Canonical Search Results
```

The search projection contains the canonical display name, English and Chinese aliases,
description, and at most eight distinct recent source titles. It is bounded to 12,000
characters by default. `search-reindex` builds it outside user requests; ingestion refreshes
only affected subjects. Subject merges remove the duplicate projection, retain a redirect, and
refresh the canonical projection while preserving historical composite ranking records.

Lexical retrieval uses a generated `tsvector` with PostgreSQL's `simple` configuration, which
avoids English stemming that could damage technical terms such as `C++`, `C#`, `.NET`, and
`GPT-5`. Exact canonical and alias matches rank first, followed by prefix matches, FTS, then a
conservative trigram fallback. `pg_trgm` provides multilingual lexical similarity and typo
tolerance; it is not semantic search.

Semantic retrieval is optional. A small `EmbeddingProvider` protocol and an
OpenAI-compatible HTTP implementation support a configured multilingual model without binding
search logic to one vendor. `SubjectEmbedding` stores only the current vector, model,
dimension, searchable-content hash, and timestamp. Cosine distance is used. Stale hashes and
dimension/model mismatches are excluded. At the current corpus size vectors use an exact scan;
there is no HNSW index.

When both channels are available, search uses weighted reciprocal rank fusion:

```math
SearchRRF(x)=\frac{w_l}{k+r_l(x)}+\frac{w_s}{k+r_s(x)}, \qquad k=60
```

Exact canonical or alias matches are pinned ahead of other fused candidates. Search RRF and
Trending RRF are separate contexts: Trending measures cross-source attention, while Search
measures query relevance. Trending scores never affect search ordering.

Semantic search is optional at runtime. If no embedding endpoint and verified multilingual
model are configured, `hybrid` and `semantic` requests fall back to lexical retrieval and the
response reports `mode_used: "lexical"` and `semantic_search_available: false`. FastAPI startup,
keyword search, and indexing do not require an embedding credential.

Search supports `domain`, `subject_type`, `source`, and indexed-content `language` filters,
plus `lexical`, `semantic`, and `hybrid` modes and `limit`/`offset` pagination. Source filtering
means that the canonical subject has appeared in that source; language filtering describes the
source content language rather than a market identity.

```bash
python -m industry_intelligence.cli search-reindex
python -m industry_intelligence.cli search-reindex --subject-id UUID
python -m industry_intelligence.cli search-embed
```

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/search?q=Bitcoin` | Canonical multilingual search |
| GET | `/api/v1/search?q=剑网3&domain=gaming&language=zh-CN` | Combined filters |
| GET | `/api/v1/search?q=NVIDIA&mode=lexical&limit=20&offset=0` | Explicit mode and pagination |

The bilingual `/search` page provides all filters, loading/error/empty states, an explicit
semantic fallback notice, localized subject names, and URL-backed query state. Source brands
and source-visible titles remain unchanged. Alias examples such as `英伟达 → NVIDIA` and
`Black Myth → 黑神话：悟空` depend on the indexed aliases; deterministic tests verify these
examples without claiming they occur in the current live snapshot.

## Phase 3 closure status

- [x] Semantic schema, pgvector integration, embedding abstraction, and lexical fallback.
- [ ] Live multilingual semantic-provider verification. No provider is configured and the live database has no subject embeddings.
- [x] Deterministic PostgreSQL verification for NVIDIA/英伟达 and Black Myth/黑神话.
- [ ] Live NVIDIA/英伟达 and Black Myth/黑神话 aliases. They were not observed in the current live corpus.

## Phase 3.1 — Finance News and content semantics

```text
Finance
├── News Intelligence
│   ├── CNBC Markets (RSS)
│   ├── Nasdaq Markets (RSS)
│   ├── 东方财富 7×24 (public structured endpoint)
│   └── 新浪财经 7×24 (public structured endpoint)
└── Market Pulse
    ├── CoinGecko Trending Searches
    ├── CoinPaprika Market Cap
    ├── 东方财富 A股成交额榜
    └── 新浪财经 A股成交额榜
```

Reuters was evaluated first, but its maintained newswire RSS delivery requires Reuters credentials; its public investor RSS is company-investor content rather than Markets/Business news. Yahoo Finance's US news RSS was not a stable public acquisition surface during verification. They were explicitly replaced with CNBC Markets and Nasdaq Markets RSS feeds. No login, WAF, CAPTCHA, or private API was bypassed.

`rank_lists.list_type` now distinguishes `ranking` and `market_pulse`. General `ContentItem` records use `news` or `feed`, retain original article URLs and publication times, and deduplicate by `(source_id, external_id)` or normalized URL. `ContentSubjectLink` supports conservative many-to-many deterministic associations. Exact canonical names, unique exact aliases, and explicit uppercase ticker mentions were eligible; substring fuzzy matching is not. (Phase 6.1 replaced these rules with the stricter `content-linker/2`; see below.) A live false-positive ticker association found during validation was rejected and removed. The final live corpus had no reliable links, while deterministic linking tests pass.

Content ingestion refreshes linked subjects' SearchDocument projections so recent news titles contribute search context. Search still returns canonical subjects rather than articles. Finance Trending remains the existing Market Pulse composite ranking and now includes up to three related-news previews when reliable links exist; chronological feed positions never enter RRF.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/news` | Paginated chronological content (`domain`, `source`, `language`, `subject_id`, `limit`, `offset`) |
| GET | `/api/v1/today` | Latest News (five per source) plus separately labelled Market Pulse |
| GET | `/api/v1/trending` | Composite subjects with related-news count and preview |
| GET | `/api/v1/sources` | Ranking/news/feed/market-pulse source semantics |

```bash
python -m industry_intelligence.cli ingest-content cnbc_markets
python -m industry_intelligence.cli ingest-content-all
python -m industry_intelligence.cli content-relink
python -m industry_intelligence.cli search-reindex
```

Live validation on 2026-09-27 persisted 105 content items: CNBC 30, Nasdaq 15, 东方财富 30, and 新浪财经 30. Repeating ingestion persisted zero duplicate CNBC/Nasdaq records. The database contained 340 search documents and 976 historical rank observations. All 100 backend tests passed; Ruff lint/format, frontend ESLint, TypeScript, and the Next.js production build passed.

## Phase 4 — External Company Ranking Tracker

External Rankings are not our opinion. The tracker stores company rankings exactly as external publishers publish them and follows their history. It never computes a platform company score, never combines incompatible metrics (market cap, revenue, total assets), and is separate from Today (source-native lists), Trending (cross-source attention RRF) and Search (query relevance).

```text
External ranking publisher
        ↓
CompanyRankingAdapter → CompanyRankingSnapshot (entries: rank, raw name, raw value, identities)
        ↓
Company resolution (identity → seeded identity pair → unique exact name → new company)
        ↓
Ranking (definition) → RankingSnapshot (immutable, idempotent) → RankingEntry
        ↓
previous rank / rank change / new / dropped, computed from the preceding snapshot
        ↓
GET /api/v1/company-rankings… → /rankings (English / 简体中文)
```

| Domain | Publisher | Ranking | Metric | Acquisition | Stored | Live verified |
| --- | --- | --- | --- | --- | --- | --- |
| Computer | CompaniesMarketCap | Largest tech companies by market cap | Market cap (USD) | Public CSV download (`?download=csv`) | Top 100 of the full list | Yes |
| Finance | CompaniesMarketCap | Largest banks and bank holding companies by market cap | Market cap (USD) | Public CSV download | Top 100 | Yes |
| Finance | Wikipedia | List of largest banks — By total assets | Total assets (US$ bn) | Wikimedia REST API (current page HTML) | Full table (100) | **No** — HTTP 403 (see below) |
| Gaming | CompaniesMarketCap | Largest video game companies by market cap | Market cap (USD) | Public CSV download | Top 100 | Yes |
| Gaming | Wikipedia | List of largest video game companies by revenue | Revenue (US$ bn) | Wikimedia REST API (current page HTML) | Full table (50) | **No** — HTTP 403 (see below) |

companiesmarketcap.com offers each category as a CSV download and its robots.txt does not restrict those paths; the list is updated daily, so the UTC day is the snapshot identity. Wikipedia rankings read only the current page from `/api/rest_v1/page/html/{title}`, the endpoint Wikimedia's Robot policy recommends for page HTML (the Action API is not used). The revision that page reports is the snapshot identity, its modification time is `published_at`, and `source_url` is a human-readable permanent link to that revision. The project never requests historical revisions from Wikimedia: ranking history accumulates only from its own repeated runs (day 1, day 2, day 3, …). The Wikipedia adapters are implemented and fixture-tested but not live verified (see *Wikimedia source contact*). Registered rankings without a snapshot still appear in the Rankings page with an explicit empty state.

### Wikimedia source contact

The Wikipedia ranking adapters use the Wikimedia API. Wikimedia requires
automated clients to provide contact information in the User-Agent.

Configure the repository-root `.env` before running Wikipedia ingestion:

```env
SOURCE_CONTACT=your-email@example.com
```

Replace the placeholder with an email address or other valid contact
information controlled by the deployment owner.
Do not commit personal contact information to the repository.

- Replace `SOURCE_CONTACT` with contact information belonging to the actual deployment maintainer. Do not reuse another developer's private email when deploying your own instance. Every clone or deployment must set its own maintainer contact.
- `.env` is gitignored and must never be committed; `.env.example` only carries the placeholder.
- The contact is sent only on Wikipedia requests, never to the other sources.
- If `SOURCE_CONTACT` is unset, blank, or a documentation placeholder (`example.com`, `.invalid`, `.test`), Wikipedia ingestion stops with a `SourceContactRequired` configuration error before any request. The project never fakes a browser User-Agent or works around Wikimedia's access policy.

Current status: Wikimedia requests remain HTTP 403 from the current application client after SOURCE_CONTACT configuration and policy-compliant endpoint changes. The two Wikipedia rankings are therefore implemented and fixture-tested but NOT live verified. The request was made once per ranking, serially; after the 403 no further attempts were made. The project does not switch HTTP clients, run external command-line fetchers, imitate a browser User-Agent, or otherwise work around Wikimedia's access controls, and OAuth is not integrated. Wikimedia's [Robot policy](https://wikitech.wikimedia.org/wiki/Robot_policy) describes authenticated requests (an OAuth 2.0 access token) and published source CIDRs as stronger forms of client identification; adopting either is a future owner decision.

Candidates rejected during the source audit: Newzoo Top 25 (HTTP 403 Cloudflare challenge), Forbes Global 2000 (only an undocumented internal JSON endpoint, and a cross-industry list), Fortune Global 500 (cross-industry list, terms unclear), Brand Finance Brandirectory (JavaScript-only application), Motley Fool largest companies (cross-industry), and Wikipedia's largest technology companies by revenue (its current table has no explicit rank column).

Company identity is minimal and independent of `Subject`; sourced profile facts live in the Phase 5 profile tables. Resolution uses, in order: an exact external identity (a CompaniesMarketCap symbol or a Wikipedia article title including any section fragment), a reviewed identity-to-identity seed pair (for example `1398.HK` ↔ *Industrial and Commercial Bank of China*), a unique exact normalized name or alias, and otherwise a new company. A name match is rejected when several companies share the name or when the candidate holds a different value in an exclusive scheme (ticker symbol, and since Phase 5 SEC CIK or LEI). Companies merged away by a reviewed merge never match by name. Wikipedia links are not treated as exclusive because editors retarget them. There is no fuzzy matching, so a false negative is preferred to a false merge.

Snapshots are append-only. Re-ingesting the same `snapshot_key` is a replay. A new key whose entries hash to the same content as its neighbour in history is recorded as unchanged instead of duplicated. Movement is `rank_change = previous_rank − current_rank` against the immediately preceding snapshot of the same ranking (ordered by `effective_at`): a company absent before is **New**, one absent now is listed as dropped, and the first snapshot shows no movement.

```bash
python -m industry_intelligence.cli company-ranking-ingest cmc_tech_market_cap
python -m industry_intelligence.cli company-ranking-ingest wikipedia_video_game_revenue
python -m industry_intelligence.cli company-ranking-ingest-all
```

`company-ranking-ingest-all` isolates failures per ranking, reports them, and exits non-zero if any ranking failed. Ingestion always reads the current published version; there is no historical-revision option. Run it periodically (for example daily) to build history.

Offline fixture verification is not production live ingestion. Parser, movement, and history behaviour are covered by fixtures and by PostgreSQL tests; the REST-shaped Wikipedia fixture is derived from the table captured during the source audit and is labelled as such.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/company-rankings?domain=&source=&limit=&offset=` | Ranking definitions with their latest snapshot summary (registered rankings without data report `no_snapshot`) |
| GET | `/api/v1/company-rankings/{key-or-id}?snapshot_id=&limit=&offset=` | One snapshot (latest by default) with entries, previous rank, rank change, movement and dropped entries |
| GET | `/api/v1/company-rankings/{key-or-id}/history?limit=&offset=` | Snapshot timeline, newest first, with the #1 entry |
| GET | `/api/v1/companies/{id}/rankings?limit=` | A company's appearances across external rankings, including dropped points |

Page loads only read PostgreSQL. They never fetch external sites, ingest, or call AI.

## Phase 5 — Company Profiles

Company Intelligence enriches a reviewed set of ranked companies while keeping `Company`
separate from canonical `Subject`. The current target registry contains 15 representative
ranking leaders across Computer, Finance, and Gaming; it is not a bulk profile of all 294
companies. Facts are never generated or machine-translated.

| Source | Stored facts | Live coverage |
| --- | --- | --- |
| Official company website | Website, the homepage's own short description, reviewed localized names | 12 reviewed sites |
| SEC EDGAR | SIC industry, address/incorporation facts, ticker/exchange metadata, current-period XBRL business/segment and product/service member labels | 11 registrants |
| GLEIF | Registered legal/local names, headquarters, jurisdiction | 15 reviewed head-office LEIs |
| CompaniesMarketCap company page | Publisher category tags only | 15 reviewed pages |

Each normalized source record keeps its URL, retrieval/check time, content hash, facts, and
metadata. `CompanyProfile` is a derived field-priority view with `field_sources`, so the UI can
show provenance. Identical content is a replay: it updates `checked_at` without replacing the
original `retrieved_at`. Source failures are isolated. The project does not store page bodies or
CompaniesMarketCap prose descriptions.

SEC's fair-access contact is separate from the Wikimedia contact and is sent only to `sec.gov`
and `data.sec.gov`, including after redirects. Configure it in the gitignored repository-root
`.env`:

```env
SEC_CONTACT=your-email@example.com
```

The deployment owner must replace the placeholder. A real contact must never be committed.
`SOURCE_CONTACT` remains Wikimedia-only, and neither contact is sent to any other host.

The bilingual `/companies` page is a bounded, paginated company directory with company-specific
search and domain filters; `/search` remains canonical Subject search. `/company/[id]` shows
identity, sourced profile facts, external ranking context/history, related news through the
existing content architecture, visible profile provenance, and a canonical Subject link only
when deterministic evidence exists. Company names in `/rankings` link to Company Detail.
Missing profile fields, rankings, news, or Subject links are normal empty states.

```bash
# All reviewed targets and applicable adopted sources
python -m industry_intelligence.cli company-profile-ingest

# Narrow by reviewed ranking symbol and/or source (options may repeat)
python -m industry_intelligence.cli company-profile-ingest --symbol NVDA --source sec_edgar

# Rebuild deterministic Company ↔ Subject links
python -m industry_intelligence.cli company-subject-link

# Explicit reviewed merge; unsafe CIK/LEI or same-snapshot merges are refused
python -m industry_intelligence.cli company-merge CANONICAL_UUID DUPLICATE_UUID --reason "review evidence"
```

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/companies?q=&domain=&ranking=&has_profile=&limit=&offset=` | Bounded company discovery and company search |
| GET | `/api/v1/companies/{id}` | Sourced Company Detail, current ranking context, provenance, and deterministic Subject context |
| GET | `/api/v1/companies/{id}/rankings?limit=` | External ranking appearances and history |
| GET | `/api/v1/news?company_id=&limit=&offset=` | Existing news records related through linked canonical Subjects |

Company identity resolution is exact and auditable: external identity, reviewed identity seed,
unique exact name/alias, then a new company. CompaniesMarketCap symbol, SEC CIK, and LEI are
exclusive schemes. Reviewed merges keep redirects and refuse conflicting legal identities or
same-snapshot collisions. Company↔Subject linking uses exact A-share exchange identity first,
then a unique exact company/stock name or alias. There is no fuzzy, embedding, or LLM matching.

Live validation on 2026-09-28 persisted profiles for all 15 reviewed targets across 53 applicable
source records (12 official website, 11 SEC, 15 GLEIF, 15 CompaniesMarketCap), and a second run
replayed all 53 unchanged. Microsoft and Sony's XBRL labels were live parsed from their
schema-embedded label links. XBRL member tags remain filer-defined and are labelled in the UI as
reported business/segment or product/service members, not asserted to be official divisions;
some filers tag reconciling or non-segment items on these axes (for example an HSBC lending
member and Sony's elimination/"All Other" members), and no filer-specific filter is applied.

## Phase 6 — Event Integration

Events are evidence-backed clusters of existing ContentItems. An Event is one concrete,
time-bounded real-world occurrence ("what happened"); a Subject is who or what it concerns, and a
ContentItem is one report of it. Events are built by deterministic, auditable rules from persisted
news/feed items only; no LLM, embedding, AI summary, score, or prediction is involved, and page
loads never build Events. Events are separate from Today, Trending, Search, Rankings, and
Companies: they never feed Trending or Search, and `source_count` is coverage, not importance.

```bash
# Incremental: assigns only content items that have no Event yet (repeatable, idempotent)
python -m industry_intelligence.cli event-build
# Preview without writing, or bound a run
python -m industry_intelligence.cli event-build --dry-run
python -m industry_intelligence.cli event-build --since 2026-09-27T00:00Z --limit 200
# Rebuild derived Event ↔ Subject/Company links after content-relink or company-subject-link
python -m industry_intelligence.cli event-relink
# Explicit reviewed merge with a redirect; unsafe merges are refused
python -m industry_intelligence.cli event-merge CANONICAL_UUID DUPLICATE_UUID --reason "review evidence"
```

Grouping is conservative: a duplicate Event is preferred to a wrong merge. Inside one domain and a
24-hour publication window, a report joins an Event only with identity evidence plus headline
evidence and no conflict:

- `exact_headline`: identical normalized headline (NFKC, case, punctuation, the leading
  `【headline】` of feed titles, and a short list of feed prefixes such as `市场消息：` removed);
- `attribution_headline`: the same Chinese issuer/speaker prefix before `：` and a similar
  statement (character-bigram/word Dice ≥ 0.6);
- `shared_entity_headline`: a shared Subject or Company from existing deterministic links and a
  similar headline (Dice ≥ 0.7).

Different specific event types, disjoint Subjects or Companies, different issuer prefixes, or
conflicting numbers block a merge; two items from one source merge only on an exact headline;
title similarity or time alone never merges, and a report that matches two Events starts a new
one. Event types (earnings, product launch, acquisition, funding, regulatory, leadership,
partnership, security incident, game release) are assigned only when one keyword family matches;
otherwise `general`. The canonical title is a representative source headline kept verbatim
(boilerplate-free first, then earliest). Event times are content publication times, not verified
occurrence times. Event ↔ Subject links come only from existing content-subject links, and
Event ↔ Company links only through those subjects' company links.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/events?domain=&subject_id=&company_id=&event_type=&from=&to=&limit=&offset=` | Canonical Events, most recently reported first (20 default, 100 max) |
| GET | `/api/v1/events/{id}` | Event Detail with entities, coverage, time basis, and a 5-report preview (redirect-aware) |
| GET | `/api/v1/events/{id}/content?limit=&offset=&order=` | The Event's reports with original URLs (10 default, 50 max) |

Phase 6 live coverage on 2026-09-28 (historical; see Phase 6.1 for the current multi-domain state): the live content corpus was Finance-only (105 items from CNBC,
Nasdaq, 东方财富, 新浪财经), so all 92 live Events are Finance; Computer and Gaming have no content
and therefore no Events. 13 Events have two reports (all 东方财富 + 新浪财经), 79 have one. A
manual audit of 35 Events found no wrong merge. Live content has no deterministic subject links,
so live Events have no Subject or Company relation yet; those paths are covered by PostgreSQL tests
and a disposable preview database, not by live data.

## Phase 6.1 — Multi-domain Content & Event Coverage

Phase 6 had Computer and Gaming Events = 0 because no Computer or Gaming news/feed source existed
(`CONTENT_SOURCES` held only the four Finance sources); the Event resolver was not the cause.
Phase 6.1 adds three Computer and three Gaming publisher RSS feeds, a stricter deterministic
content ↔ subject linker, a one-command content pipeline, and a bounded Event rebuild. The Phase 6
Event resolver and its thresholds are unchanged; nothing uses an LLM, embeddings, or fuzzy matching.

| Domain | Source (key) | Language | Acquisition | Live verified |
| --- | --- | --- | --- | --- |
| Computer | IT之家 (`ithome_news`) | zh-CN | Official RSS | Yes |
| Computer | TechCrunch (`techcrunch`) | en | Official RSS | Yes |
| Computer | Ars Technica Biz & IT (`arstechnica_it`) | en | Official RSS | Yes |
| Gaming | GamesIndustry.biz (`gamesindustry_biz`) | en | Official RSS | Yes |
| Gaming | Gematsu (`gematsu`) | en | Official RSS | Yes |
| Gaming | GameLook 游戏大观 (`gamelook`) | zh-CN | Official RSS (HTTP only) | Yes |

Candidates were checked once each with the application's own HTTP client after reading
robots.txt. Rejected: The Register (robots.txt disallows the feed), 虎嗅 RSS (timeout), 游民星空
RSS (HTTP 403), 3DM and 游戏葡萄 (no public feed), 机核 and 触乐 (mostly podcasts and features), and
Hacker News `newstories` (submissions; HN is already a ranking). Only the headline, original URL,
publication time, source-native categories, and an excerpt of at most 280 characters are stored;
feeds that embed full article bodies (IT之家, GameLook) never have them stored.

Content ↔ Subject linking (`content-linker/2`) links a content item to a Subject of its own domain
only on exact evidence, in this order: `same_article` (the publisher's native article ID equals a
ranked story of the same source, or the article URL equals a Subject's canonical URL — e.g. a
TechCrunch article that is a Hacker News top story), `exchange_code` (`寒武纪(688256)`,
`688256.SH`), `cashtag` (`$TRUMP`), then `exact_name` / `exact_alias`. Names must be unique in the
domain and are matched case-sensitively on word boundaries; single common words ("Stellar",
"Jupiter", "Rust") never match alone; Chinese game titles must appear inside 《》, and any game
title written inside 《》 matches with its exact extent; ranked-story Subjects never match by name;
a name inside a longer matched name does not count. `content-relink` reconciles links (adds new,
removes stale, reports both) and has `--dry-run`. Event ↔ Subject and Event ↔ Company links are
still derived only from these links and existing Company ↔ Subject links.

```bash
# One bounded manual run: ingest every content source (failures isolated per source),
# link the new items, then build Events for all unprocessed content
python -m industry_intelligence.cli content-update-all
python -m industry_intelligence.cli content-update-all --source gematsu --skip-events
# Recompute content ↔ subject links (preview first), then refresh derived Event links
python -m industry_intelligence.cli content-relink --dry-run
python -m industry_intelligence.cli content-relink
python -m industry_intelligence.cli event-relink
# Rebuild the derived Event layer for content published in [from, to)
python -m industry_intelligence.cli event-rebuild --from 2026-09-20T00:00Z --to 2026-09-29T00:00Z --dry-run
```

`event-rebuild` never touches content or content links. Every Event with a report in the window
is rebuilt whole (the output reports the effective content range), other Events keep their IDs,
and rebuilt Events get new IDs. Before deletion, `event_retired_ids` records each old UUID against
its stable representative report plus an ordered report fallback list. Event reads resolve an old
UUID directly through the report's current `EventContentLink` owner, so repeated rebuilds do not
form redirect chains and a split follows the old representative report. Reviewed merge redirects
remain separate. IDs retired before this registry existed cannot be reconstructed reliably. A
window containing a reviewed merge (`EventRedirect`) is refused. Linking
failures never roll back ingested content, and an Event-stage failure never deletes content.

Live state on 2026-09-28: 356 content items (computer 70, finance 206, gaming 80); 335 Events
(computer 70, finance 185 with 21 two-report Events, gaming 80); 6 content ↔ subject links on 5
items and 5 Event ↔ Subject links across all three domains (澜起科技; the Hacker News story of a
TechCrunch article; Warframe, WARDOGS, and 战狗 from GameLook), all manually reviewed with no false
link; 0 Event ↔ Company links, because none of the linked Subjects has a Company link. Computer and
Gaming Events are all single-report: the adopted sources report no common story in the same
language, and cross-language stories are intentionally not merged. A live full-window
`event-rebuild --dry-run` reproduced all 335 clusters.

## Phase 7 — AI Intelligence & Human-in-the-loop Enrichment

AI is an enhancement layer, not the canonical truth layer. Content, Subjects, Companies,
rankings, Events, identities, and links stay deterministic and auditable. AI may summarize,
explain, and suggest; it never merges, links, renames, rewrites identities, changes rankings, or
changes Event membership. A suggestion reaches canonical data only through an explicit reviewed
CLI action that calls an existing service with all of its safeguards.

```text
persisted Event + ContentItems
        ↓  (CLI batch only: ai-event-enrich / ai-entity-suggest / ai-merge-suggest)
AIProvider (configured adapter) → structured JSON
        ↓  schema + evidence validation (deterministic)
ai_artifacts (valid | failed | stale | rejected) · ai_suggestions (pending | accepted | rejected)
        ↓
GET /api/v1/events/{id}/ai  →  Event Detail (labelled "AI-generated", with source links)
```

Page requests never call an AI provider: `/api/v1/events/{id}/ai` reads persisted, validated
artifacts only, and the browser only talks to this project's API.

### Provider configuration

AI is optional. Without configuration every page works and shows "AI insight not available".

```bash
pip install -e "./backend[ai]"   # the Anthropic adapter (official SDK)
# in the gitignored root .env — never commit a real key:
AI_PROVIDER=anthropic
AI_MODEL=<a model your provider account can use>
AI_API_KEY=<your key>
```

`AI_PROVIDER` names an adapter in the explicit `ai/providers` registry; business services only
use the `AIProvider` protocol, so another provider is a new adapter, not a service change. Keys
are hidden from settings output and never stored in artifacts or logs; placeholders count as
unset. The Anthropic adapter passes the key and base URL from project settings explicitly, so
ambient `ANTHROPIC_*` shell variables are never used, and it does not enable server-side model
fallbacks, so the recorded model is the one that served the request. Timeouts, HTTP 429, and 5xx
are retried at most `AI_MAX_RETRIES` times (backoff, `retry-after` honoured, capped at 60 s);
other errors fail immediately. `AI_REQUEST_INTERVAL` spaces requests within a run.

### Artifacts, source grounding, and validation

- The provider sees only the Event's persisted reports under short refs (`C1`, `C2`, …): source,
  language, publication time, title, and an excerpt of at most 280 characters. No browsing, no
  outside knowledge, no other Events.
- Prompts are versioned modules (`ai/prompts/`); a prompt change is a new version, and artifacts
  of an older version are reported `stale` until regenerated.
- **Language policy (AI-LP1):** Event AI is generated only in the Event's primary locale — the one
  locale every report is written in (`event_summary_v4`, `event_why_it_matters_v6`): a Chinese
  Event gets Chinese AI and an English Event English AI, so each Event has at most two tasks. The
  UI locale never selects the AI language, and nothing is translated. Cross-language generation is
  discontinued: mixed-language Events get no AI, and `ai-event-enrich --locale` accepts only an
  Event's primary locale (any other locale is refused before a request or write). The legacy
  cross-language lineage (`event_summary_xlang_v1`/`v2`, `event_why_it_matters_xlang_v1`) and its
  guards stay in the code only for historical artifacts, which are kept but never served.
  Multilingual sources, cross-language aliases and Search, and the bilingual UI are unchanged.
- Output must match a closed JSON schema, and deterministic guards must pass: every point cites
  refs of this Event only; no investment advice, price targets, ratings, or market predictions; a
  single-source Event is never described as multiple reports; every figure appears in the
  evidence, except a scaled figure exactly equal to an evidence figure in the other notation
  (4.2亿元 ↔ 420 million, $11.6 billion ↔ 116亿); the text is in the artifact's locale.
  Legacy cross-language output (no longer generated) was also rejected for a sequence/causal connector ("after which", "as a
  result", 随后, 因此, …) without a signal of the same kind in the reports, and for a Latin ↔
  Chinese name gloss in parentheses unless the reports give both forms. English cross-language
  Summaries (v2) are also rejected for a Latin-script name absent from the reports in an
  attribution, speaker, or institution position, a figure qualifier (up to, about, more than, …)
  the reports do not state for that figure, and Chinese text that is not copied from the reports
  or carries Chinese grammar words. Any failure stores a `failed` artifact (error code only) and
  nothing is displayed.
- A reviewer hides an artifact that fails the manual audit with `ai-artifact-reject` (the API
  never serves a rejected artifact). `GET /api/v1/events/{id}/ai` returns the Event's
  `primary_locale` (null for a mixed-language Event), primary-only `coverage` (`required`,
  `available`, `complete`), and each insight in that locale; the optional `locale` parameter is
  the UI locale, echoed back and never used to pick content. It reports each insight as
  `available`, `not_generated`, `failed`, `rejected` (withdrawn after review), or `stale` (older
  reports or an older prompt version); only `available` carries text. Page requests never call a
  provider. An isolated wording defect may be regenerated once; a
  defect that recurs across Events or prompt versions needs a deterministic guard or stays
  rejected.
- Each artifact records provider, served model, prompt version, locale, the evidence content IDs,
  and an `input_hash` over prompt version, locale, and the exact evidence. A matching valid
  artifact is a replay (no request, no cost). When an Event's reports change, the API reports the
  old artifact as `stale` immediately and the next batch marks it and generates a new version.
- AI Summary: 1–4 factual points in the Event's primary language, English or Simplified Chinese
  (raw titles and names stay in their original language). Why It Matters: 0–3 descriptive
  business/technology/industry/product/regulatory points, or an explicit "not enough context"
  answer — never predictions or advice.

```bash
cd backend
# Preview: candidate Events, cached, stale, and estimated requests — sends nothing, writes nothing
python -m industry_intelligence.cli ai-event-enrich --limit 10 --dry-run
# Bounded run (default 10 Events, max 100; at most 2 requests per Event); repeat --event-id /
# --type to narrow. --locale may only confirm an Event's primary locale.
python -m industry_intelligence.cli ai-event-enrich --event-id <uuid>
# Hide an inaccurate artifact after review
python -m industry_intelligence.cli ai-artifact-reject <artifact-uuid> --reason "evidence says otherwise"
```

### Suggestions and review (Experimental / Future)

Entity suggestions and cross-language Event judgements are implemented and fixture-tested but have
never been run live; they are Experimental / Future and not part of the Phase 7.1 release gate.

- `ai-entity-suggest` asks which entities the reports name. Each name must literally appear in
  the cited reports and is then resolved by deterministic exact lookup (Subject title/alias in the
  Event's domain, Company name/alias, or exact ticker). The model never chooses database IDs.
  Results are Subject or Company candidates (ambiguous ones list their options) or
  `new_entity_candidate` for names not in the catalog, which can only be rejected.
- `ai-merge-suggest` first prefilters cross-language Event pairs deterministically (same domain,
  different report languages, within 72 hours, compatible types, no conflicting entity links, and
  a shared entity link or a shared Latin-script name that few Events mention), then asks the
  provider whether each pair is the same occurrence; only "yes" becomes a pending suggestion.
- Review is CLI-only (there is no write API): `ai-suggestion-list`, `ai-suggestion-accept ID
  --reason … [--candidate ID] [--canonical ID]`, `ai-suggestion-reject ID --reason …`. Accepting a
  Subject candidate writes `reviewed` content ↔ subject links on the cited reports; a Company
  candidate writes a `reviewed` Event ↔ Company link; a merge candidate calls the Phase 6 reviewed
  merge, whose safeguards still refuse unsafe merges. `content-relink` and `event-relink` keep
  reviewed links, and `event-rebuild` refuses windows with reviewed Company links.

### Cost and limitations

Runs are bounded and incremental: test on 10–20 representative Events first, preview with
`--dry-run`, and rerun freely (unchanged evidence is a free replay). AI output can still be
wrong even after validation — the UI labels it "AI-generated", says it may contain errors, and
links every point to its reports, and the full timeline stays on the page. AI output never feeds
Trending, Search, Rankings, company profiles, or scores.

Status on 2026-09-28 (Phase 7 closure): implemented and tested with a fixture provider (no
automated test calls a paid API). Phase 7.1 then configured Anthropic locally and live-verified
and manually audited primary-locale output for 17 Events and a 6-Event bilingual subset; see
`AGENTS.md` (Phase 7.1 checkpoints) for the current state.

## Operations-1 — Daily intelligence update

Status (2026-09-29): **complete; validated with the first full multi-source live update.** After
a verified backup, a bounded `--source gematsu --skip-ai` smoke and then one full
`intelligence-update --skip-ai` run succeeded (exit 0): 25/25 sources, ContentItems 366 → 543,
Events 345 → 520 with every earlier Event unchanged, audit 0 errors / 0 warnings, and no AI
request. A bounded 5-Event AI catch-up (22 requests) then verified the live AI path and
fail-closed serving but rejected every cross-language (Chinese → English) Summary; AI-X1 repaired
that lineage (`event_summary_xlang_v2`), and the same five Summaries then passed review (5
requests). Refreshing the four Summaries made stale by that change (AI-X1.1) served two and
left two unavailable (fail closed). AI-LP1 then made Event AI primary-locale only (cross-language
generation discontinued): a daily AI stage with `--ai-limit N` sends at most 2 × N provider
requests. Bounded daily AI is a separate owner decision; runs keep `--skip-ai` until then.

`intelligence-update` is one explicit CLI process that runs the existing services in dependency
order. It adds no queue, scheduler, or new intelligence logic, and it never runs migrations,
repairs, reviewed merges, `event-rebuild`, company profile ingestion, or embeddings.

```bash
# What a run would do: read-only precheck, sources, pending links/Events, AI plan.
# No network request, no provider call, no database write.
python -m industry_intelligence.cli intelligence-update --plan

# The daily run (text summary; --json for machine output)
python -m industry_intelligence.cli intelligence-update

# Bounded controls
python -m industry_intelligence.cli intelligence-update --skip-ai
python -m industry_intelligence.cli intelligence-update --source gematsu --skip-ai
python -m industry_intelligence.cli intelligence-update --ai-limit 5
# Retry a stopped run: ranking sources already stored under this run ID replay without a request
python -m industry_intelligence.cli intelligence-update --run-id 20260929T000000Z-abcd1234
```

| Stage | Existing service | Transaction | On failure |
| --- | --- | --- | --- |
| `precheck` | DB reachable, Alembic current = head, `data-quality-audit` | read-only | run FAILED, nothing written |
| `rankings` | `ingest` per ranking source (batch key from the run ID) | one per source | source recorded, others continue |
| `composite` | `rank_domain` per domain | one | run FAILED (previous run stays exposed) |
| `company_rankings` | `ingest_company_ranking` per ranking with `daily_update` | one per ranking | ranking recorded, others continue |
| `content` | `ingest_content` per content source (links new items) | one per source | source recorded, others continue |
| `links` | `relink_all_content` + `relink_all_companies` | one | run FAILED; `events` and `ai` skipped |
| `events` | incremental `build_events` + `relink_all_events` | one | run FAILED; `ai` skipped; content kept |
| `ai` (optional) | `enrich_events` for the 10 most recent Events | one per artifact | run DEGRADED; canonical data kept |
| `postcheck` | `data-quality-audit` | read-only | error → run FAILED (never auto-repaired) |

- **Statuses and exit codes:** `SUCCESS` (0); `DEGRADED` (3) when some sources or the AI stage
  failed but every required stage succeeded; `FAILED` (1) when the precheck, postcheck, a
  required stage, or every source of a group failed. Exit code 2 remains argparse's usage error.
- **Idempotency:** content deduplicates by identity/URL, company snapshots by daily key, composite
  runs by input hash, Events are built only for unassigned content, links are reconciled, and
  valid AI artifacts replay. A rerun with the same `--run-id` replays the ranking sources; a new
  run ID records one new, correctly timed ranking snapshot (append-only by design).
- **Event permalinks:** the daily run only creates or joins Events; it never rebuilds, retires,
  or merges, and it fails the `events` stage if a retired ID would appear.
- **AI:** skipped when no provider is configured or with `--skip-ai`; generates only missing,
  stale, or transiently failed (timeout, rate limit, server, authentication) artifacts; a reviewer
  rejection or a validation/refusal/malformed failure is never regenerated unattended (use
  `ai-event-enrich` explicitly). Prompts, validators, and review policy are unchanged.
- **Company rankings:** the two Wikipedia rankings (`daily_update=False`, HTTP 403) are never
  requested unattended; `--source KEY` or `company-ranking-ingest KEY` remain explicit choices.
- Errors are reported as source, exception type, category, and HTTP status only; no messages,
  headers, URLs with parameters, or configuration values are printed.

## Operations-2 — Source health, update lock, and schedule-ready command

Status (2026-09-30): **complete.** `intelligence-update` is now safe to invoke from an external
scheduler: one run at a time (PostgreSQL advisory lock), every run and source attempt recorded,
and a read-only `source-health` report. Operations-2 itself configured no scheduler (Scheduler-1,
below, added one). Migration `20260930_12` adds
two operational tables (`update_runs`, `source_attempts`); no existing table changed.

```bash
# The one command an external scheduler will invoke (AI stays off until the owner decides)
python -m industry_intelligence.cli intelligence-update --skip-ai

# Health of every automatic source, from recorded attempts (read-only, no network request)
python -m industry_intelligence.cli source-health
python -m industry_intelligence.cli source-health --json
```

- **Lock.** A real run takes `pg_try_advisory_lock` (a two-int4 key, which PostgreSQL keeps apart
  from the services' one-bigint transaction locks) on its own unpooled connection before any
  write and holds it for the whole run. If another run holds it, the command exits `4`
  (`ALREADY_RUNNING`) at once with no source request, provider request, or write; it never
  queues. The lock is released explicitly at the end and by PostgreSQL when the process or its
  connection dies, so it cannot stay stuck. `--plan` never takes the lock; it only reports
  whether it is held at that moment.
- **Run history.** Under the lock the run first marks any `update_runs` row still `running` as
  `interrupted` (the held lock proves its process is gone; elapsed time alone never does), then
  records itself and, at the end, its status, exit code, and a stage summary (no logs or
  tracebacks). Runs are not limited per day, and a retry with the same `--run-id` is a new row.
- **Source attempts.** Every attempted source gets one append-only row: outcome `success`,
  `replayed`, `unchanged` (the first three are successful; zero new items is never a failure),
  or `failed` with the failing phase (`transport`, `parse` — e.g. an HTTP 200 whose body cannot
  be read — `persist`, or `other`), category, exception type, HTTP status, a bounded summary
  built from those fields only, and fetched/stored/replayed/invalid counts.
- **Health** is derived on read from the current automatic registry left-joined with attempts:
  `never_run` (no attempt), `failing` (latest attempt failed), `degraded` (latest attempt
  succeeded but rejected malformed rows), `healthy`. Consecutive and recent failures and the
  last error are reported separately, so a recovered source reads `healthy` with its failure
  history still visible.
- **Statuses and exit codes:** `success` 0, `failed` 1 (a fatal orchestration failure), `degraded`
  3 (the orchestration completed but one or more isolated operations failed: some sources, the
  optional AI stage, or recording the run history), `already_running` 4, `--plan` 0 (1 when its
  precheck fails). Exit code 2 remains argparse's usage error.
- **Never automatic:** migrations, repairs (`--apply`), reviewed merges, `event-rebuild`, company
  profile ingestion, embeddings, and cross-language AI.

## Scheduler-1 — Daily update at 10:00 Australia/Sydney

Status (2026-09-30): **enabled and verified** on the development Mac. A macOS launchd agent runs
the one scheduler command daily at 10:00 Australia/Sydney (moved from the initial 08:00 on
2026-09-30). Scheduled AI is off.

- **Mechanism:** a per-user launchd agent (`~/Library/LaunchAgents/local.industry-intelligence.daily-update.plist`,
  generated from `ops/scheduler/local.industry-intelligence.daily-update.plist.template`).
  launchd is the native scheduler on macOS; cron is legacy there, and Docker, GitHub Actions, and
  systemd are not part of this host.
- **Command:** launchd starts `.venv/bin/python ops/scheduler/daily_update.py`, which runs exactly
  `python -m industry_intelligence.cli intelligence-update --skip-ai` from `backend/` with a
  minimal environment (`PATH`, `HOME`, `LANG`, `PYTHONUNBUFFERED`). Configuration comes only from
  the root `.env` through `Settings`; no secret is copied into the plist or the wrapper. The
  Operations-2 lock, precheck, postcheck, `update_runs`, and `source_attempts` stay
  authoritative. The wrapper is Python rather than a shell script because macOS privacy
  protection denies a launchd-started `/bin/sh` access to `~/Documents` (exit 126, "Operation not
  permitted"); the Python app already has that access on this Mac.
- **Time zone:** launchd's `StartCalendarInterval` (`Hour` 10, `Minute` 0) fires in the system
  time zone, which is Australia/Sydney, so the run follows daylight saving (10:00 AEST or AEDT);
  no UTC offset is written anywhere. If the system time zone is changed, the wrapper refuses to
  run (exit 78, `event=refused` in the log) rather than run at another local time. A Mac asleep at
  10:00 runs the job once when it wakes; a Mac that is off, or with the user logged out, skips
  that day.
- **Database:** PostgreSQL comes from Docker (`docker compose up -d`); the container has no
  restart policy and Docker Desktop is not a login item, so the database must be up at 10:00.
  If it is not, the precheck fails and the run exits `1` with nothing written.
- **Logs:** `~/Library/Logs/industry-intelligence/daily-update.log` (stdout and stderr, appended;
  the wrapper adds `event=start` / `event=finish exit=N` lines, which also cover failures before
  a run can be recorded). A run adds a few KB; truncate the file by hand if it grows. Run
  history lives in `update_runs` / `source_attempts`, read through `source-health`.

```bash
# Install or reinstall (absolute paths are filled in from the template)
ROOT="$PWD"; LOG="$HOME/Library/Logs/industry-intelligence"; mkdir -p "$LOG"
sed -e "s|__PROJECT_ROOT__|$ROOT|g" -e "s|__LOG_DIR__|$LOG|g" ops/scheduler/local.industry-intelligence.daily-update.plist.template > ~/Library/LaunchAgents/local.industry-intelligence.daily-update.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.industry-intelligence.daily-update.plist

# Inspect the registered state (calendar descriptor, arguments, runs, last exit code)
launchctl print gui/$(id -u)/local.industry-intelligence.daily-update

# Disable (unload) and enable again
launchctl bootout gui/$(id -u)/local.industry-intelligence.daily-update
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.industry-intelligence.daily-update.plist

# Read-only preflight through the same wrapper (appends --plan; no network, no write)
.venv/bin/python ops/scheduler/daily_update.py --plan

# After a run or an incident
tail -n 60 ~/Library/Logs/industry-intelligence/daily-update.log
cd backend && ../.venv/bin/python -m industry_intelligence.cli source-health
```

Exit codes in the log and in `launchctl print` are the command's: `0` success, `3` degraded
(isolated source or recording failures; check `source-health`), `1` failed (a fatal stage failed;
earlier stages may have committed; investigate before the next run), `4` already running
(another update held the lock; nothing was done), `78` refused by the wrapper (system time zone).
Running the wrapper by hand (without `--plan`) performs a real update.

## Deployment-1 — Production readiness

Status (2026-09-30): audit complete, **nothing deployed**. The development Mac is a valid local
operation (daily launchd update at 10:00 Australia/Sydney), not production. The chosen production
architecture is one Linux VPS with Docker Compose (PostgreSQL + pgvector on a private network,
FastAPI and `next start` behind Caddy with automatic HTTPS on one origin) and systemd timers for
the 10:00 Australia/Sydney `intelligence-update --skip-ai` run and daily backups. Runtime map,
configuration classes, verified production commands, backup/restore policy, readiness matrix,
and the Deployment-2 scope: [docs/deployment.md](docs/deployment.md).

- `ENVIRONMENT=production` hides `/docs`, `/redoc`, and `/openapi.json`.
- `NEXT_PUBLIC_API_BASE_URL` is inlined at build time; a production build needs the public origin.
- Frontend production start: `npm run build && npm start`.
- Backend production start: `uvicorn industry_intelligence.main:app --host 127.0.0.1 --port 8000 --workers 2`
  from `backend/` (no `--reload`).
- Backups: `pg_dump -Fc` (1.4 MB today); restore with `pg_restore` from a file (not a pipe).

## Deployment-2A — Production packaging (templates, nothing deployed)

Status (2026-09-30): production images, compose stack, reverse proxy, systemd timers, backups,
freshness check, failure-notification hook, and restore/cut-over runbooks are in the repository and
verified on an isolated local production-style stack. **No server is provisioned**; the Mac's
launchd agent stays the daily update until the supervised cut-over ([docs/cutover.md](docs/cutover.md)).

| Path | What |
| --- | --- |
| `backend/Dockerfile` | one image for the API (uvicorn, 2 workers, no reload) and every CLI job; non-root |
| `frontend/Dockerfile` | Node 24, `npm ci` → `npm run build` (build arg `NEXT_PUBLIC_API_BASE_URL=https://<domain>`) → `npm start`; non-root |
| `deploy/docker-compose.production.yml` | postgres (pgvector:pg16, private, no published port), backend, frontend, caddy (only 80/443) |
| `deploy/Caddyfile` | `https://{$APP_DOMAIN}`: `/api/*`, `/health`, `/health/db` → backend; rest → frontend |
| `deploy/production.env.example` | placeholders; the real file lives only on the server (`/etc/industry-intelligence/production.env`, root 600) |
| `deploy/bin/ii-compose` | `docker compose` with the production env file and project name |
| `deploy/bin/backup.sh` | `pg_dump -Fc`, 14 daily + 6 monthly, off-host hook |
| `deploy/bin/notify-failure.sh`, `deploy/hooks/*.example` | `OnFailure=` notification through an owner-installed hook |
| `deploy/systemd/` | update 10:00, backup 11:00, freshness 12:30 (`Australia/Sydney`), notify template |

```bash
# On the server (root), from the checkout at /opt/industry-intelligence
deploy/bin/ii-compose build
deploy/bin/ii-compose up -d --wait postgres
deploy/bin/ii-compose run --rm -T backend alembic upgrade head      # explicit; never on start
deploy/bin/ii-compose up -d --wait
deploy/bin/ii-compose run --rm -T backend python -m industry_intelligence.cli intelligence-update --plan --skip-ai

# Read-only freshness check used by the 12:30 timer (also works locally from backend/):
# exit 0 when a successful intelligence-update started within the last 26 hours, else 1
python -m industry_intelligence.cli update-freshness [--max-age-hours 26] [--json]
```

Details: [docs/deployment.md](docs/deployment.md) §8–10, restores: [docs/restore.md](docs/restore.md).
