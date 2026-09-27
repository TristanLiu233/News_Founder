# Industry Intelligence & Ranking Platform

The platform collects twelve public rankings plus four finance news/feed sources. It preserves source-native history, resolves ranked entries to auditable canonical subjects, and keeps chronological news separate from market rankings. The UI supports English and 简体中文; the selected locale is stored in browser `localStorage`.

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
python -m pip install -e "./backend[dev]"

cd backend
alembic upgrade head
python -m industry_intelligence.cli backfill-subjects
python -m industry_intelligence.cli ingest-all
python -m industry_intelligence.cli rank-all
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
| GET | `/api/v1/trending?domain=gaming` | One domain's composite ranking |

Run the backend with `../.venv/bin/uvicorn industry_intelligence.main:app` and the frontend with `npm run dev`. Today keeps original titles and metrics. Trending displays canonical/localized names, score, source count, language coverage, trend, and an expandable contribution breakdown. Source brands and raw external content are never translated. Backend machine values remain English (`gaming`, `up`, `zh-CN`).

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

`rank_lists.list_type` now distinguishes `ranking` and `market_pulse`. General `ContentItem` records use `news` or `feed`, retain original article URLs and publication times, and deduplicate by `(source_id, external_id)` or normalized URL. `ContentSubjectLink` supports conservative many-to-many deterministic associations. Exact canonical names, unique exact aliases, and explicit uppercase ticker mentions are eligible; substring fuzzy matching is not. A live false-positive ticker association found during validation was rejected and removed. The final live corpus had no reliable links, while deterministic linking tests pass.

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
