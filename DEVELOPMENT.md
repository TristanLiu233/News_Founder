# DEVELOPMENT.md

Permanent, chronological development log: what each phase set out to do, what it actually
changed, and the engineering decisions behind it. It complements, and never replaces:

- `README.md` — product and project overview, setup, and commands;
- `AGENTS.md` / `CLAUDE.md` — agent operating instructions, product rules, and current status.

Append one dated entry per meaningful phase (newest last). Record actual results only: exact
files, migration revision, test counts, check results, and live verification. Never leave
planned or speculative results in a finished entry.

## Permanent Development Rules

- External Rankings != Our Opinion: a publisher's native rank is stored and shown exactly as
  published; the platform never creates popularity, trend, hotness, weighted, combined, or AI
  scores, and never compares ranks from different publishers.
- AI != Canonical Truth. Evidence / Source First. Precision > Coverage.
- Domains are Computer, Finance, and Gaming. Finance is industry and market news and events,
  never stock recommendations, stock-ranking products, or investment advice.
- Canonical identity (Subject, Company, Event) is never relaxed for coverage; no invented
  cross-language overlap, aliases, company relationships, or popularity signals.
- A chronological feed position is recency, never a rank.
- Source access honours robots.txt and publisher policy with the application's honest
  `HTTPClient`; blocked sources are recorded, never worked around.
- Scheduled AI stays off (`intelligence-update --skip-ai`); page requests never call AI.
- Read `AGENTS.md` and the current code, migrations, and tests before relying on any plan.

## Current Baseline

- Branch `phase-7`; Alembic head `20260930_12` (unchanged by Trending-1).
- Backend: pytest 776 passed, 1 skipped (pre-existing Hacker News skip) on the isolated
  `industry_intelligence_test`; Ruff check and format check clean (after Trending-1).
- Frontend: ESLint, TypeScript, and the Next.js production build (11 routes) pass.
- Live dev database: `data-quality-audit` 0 errors / 0 warnings / 6 info; `source-health`
  29/29 healthy (25 earlier sources + 4 publisher ranked lists).
- Local runtime: native PostgreSQL 16 (Local-1); daily launchd `intelligence-update --skip-ai`
  at 10:00 Australia/Sydney.

## Development Entry Template

```
## YYYY-MM-DD — <Phase name>

Status: <in progress | complete | stopped (reason)>

### Problem
### Scope / Exclusions
### Audit findings
### Design decisions
### Data-model decision
### Adapter changes
### Backend changes
### Frontend changes
### Migrations
### Tests
### Real-data verification
### Known limitations
### Next step
```

## 2026-09-30 — Trending Native-Rank / Event-Centric Redesign

Status: complete (2026-09-30). Not committed (owner instruction: no commit, no push).

### Problem

1. `/trending` shows canonical Subjects (tickers, coins, games, repositories, ranked stories),
   not popular news or Events.
2. Chronological feeds cannot represent platform popularity; only a publisher's own ranked list
   of articles can.

### Scope / Exclusions

- In scope: native ranked article lists from existing publishers (新浪财经, Gematsu, 3DM,
  Hacker News) → ContentItem → existing Event pipeline → Event-centric `/trending`.
- Excluded: any platform score (trend/hot/weighted/combined/AI), cross-source rank
  comparison, Event/Subject canonical redesign, EventCompanyLink expansion, new providers,
  cross-language AI, AI on GET requests, unrelated refactors.

### Audit findings (Checkpoint A, 2026-09-30)

Start gate: `phase-7` at `28d08cb`, clean tree, Alembic head `20260930_12`, baseline pytest
748 passed / 1 skipped, Ruff clean.

Current Trending, traced end to end (all three domains use the same pipeline):

- Ingestion: `services/source_ingestion.py` stores every `SOURCES` item (rankings and Market
  Pulse) as a `RankObservation` of a canonical `Subject`; `IngestionBatch` records each
  snapshot.
- Stored trend data: `services/ranking.rank_domain()` fuses the current snapshot of every
  source of a domain into a freshness-weighted RRF score per Subject (`CompositeRankingRun` /
  `CompositeRankingEntry`).
- Service/API: `services/ranking.trending()` reads the latest composite run and returns
  Subject rows (`subject_id`, `final_score`, per-source weight/freshness/contribution) through
  `GET /api/v1/trending`.
- Frontend: `app/trending/page.tsx` renders `TrendingCard` → `TrendingTopicSummary`
  (`components/shared.tsx`), linking `/subject/[id]`; Home reuses it for Trending Topics.
- Root cause classification: **A** (the backend service ranks and returns Subjects) and **E**
  (native rankings exist only at entity level: Finance = CoinGecko/CoinPaprika/A-share turnover
  Market Pulse; Gaming = Steam/itch.io/游民星空/3DM game charts; Computer = HN/IT之家/虎嗅 ranked
  stories stored as `event`-type Subjects plus GitHub repositories). Not B/C/D/F: no Event
  data is converted or bypassed, and chronological feeds never enter the composite. Events
  (Phase 6) are built only from chronological `ContentItem`s and never reach Trending.

Native ranked article lists found (application `HTTPClient`, robots.txt read first, one
request each):

- 新浪财经 `热门新闻排行` (`finance.sina.com.cn/topnews/`): 新闻榜 · 点击量排行 rendered from
  `top.finance.sina.com.cn/ws/GetTopDataList.php` (`top_type=day`, `top_time=YYYYMMDD`,
  `top_order=DESC` by `top_num` clicks; the page numbers rows 序号 1–20). robots.txt of
  `finance.sina.com.cn` is empty; `top.finance.sina.com.cn/robots.txt` answers an HTML error
  page (no rules). Rows carry title, URL, outlet (`media`), creation date/time, and click count.
- Gematsu `Popular Posts` (homepage `section.gematsu-popular`, 4 posts in publisher order, no
  dates; robots.txt allows `/`, Crawl-delay 10). All four were already stored from the RSS (one
  under an earlier URL slug). Article pages carry `article:published_time`.
- 3DM `近期热门` (`www.3dmgame.com/news/`, `div.net` after the 近期热门 title, 8 links in
  publisher order, no dates; robots.txt allows `/news/`). Article pages carry a JSON-LD
  `pubDate` (Beijing time). The list mixes hardware/tech items with games (published as is).
- Hacker News: the official Firebase `topstories` is already fetched as a Subject ranking
  (`hacker_news`); item JSON carries the submission `time`.

Storage audit: `ContentItem` rows are write-once (replays never update them), so per-item JSON
cannot hold repeated rank observations without mutating content and losing snapshot identity.
The existing snapshot records can: `IngestionBatch` (one row per observed list snapshot,
idempotent `(source_key, batch_key)`, unique `(rank_list_id, observed_at)`, JSON `result`)
anchored to a `RankList` (a concrete list published by a source). `RankList`/`IngestionBatch`
readers (`today`, `rank_domain`, `subject_detail`, `data_quality_audit`) select by `SOURCES`
keys or through `RankObservation`s only, so rows for a new list kind do not leak into them.

### Design decisions

- `/trending` returns Events. The platform never scores: each card carries every publisher's
  own rank for its reports (`external_rankings`), exactly as published; ranks from different
  publishers are never compared or combined; there is no platform position number in the UI.
- Display order (`ordering: list_catalog_order_then_native_rank`): the domain's ranked lists in
  fixed catalog order, each in its publisher's own order; an Event listed more than once appears
  once, at its first listing, with all its ranks. The "most recently observed evidence" rule was
  rejected because every list is fetched in the same run seconds apart, so it would order
  publishers by fetch order; the catalog rule states that honestly. The UI Methodology note says
  ranks are source-specific and the order is not a popularity judgement.
- Entity trend != news trend: Steam/itch.io/游民星空/3DM game charts, CoinGecko/CoinPaprika,
  A-share turnover, GitHub, and the ranked-story Subject lists stay Subject rankings (Today,
  Subject Detail, composite) and are never evidence that an Event is popular.
- One publisher, several feed modes: each ranked list is a `NewsRankingDefinition` whose `name`
  is the existing publisher `Source` (新浪财经, Gematsu, 3DM, Hacker News), so an article already
  stored from the publisher's chronological feed is the same `ContentItem`. Registry keys
  (`sina_finance_hot`, `gematsu_popular`, `game_3dm_hot`, `hacker_news_top`) identify the feed
  mode for source attempts, health, and `--source`; no new Source identity exists.
- A ranked article with no Event yet is not shown and no Event is made for Trending; it is
  counted per list (`pending_event_count`) until the next `events` stage assigns it.
- Topics = the Event's derived `EventSubjectLink`s minus ranked-story (`event`-type) Subjects,
  which are the listed story itself. EventCompanyLink rules are unchanged (DQ-3).
- Undated list rows: the publication time is read from the article page only for articles not
  already stored (Gematsu `article:published_time`, crawl delay 10 s; 3DM JSON-LD `pubDate`,
  1 s apart); a failed lookup leaves the time unknown. Existing rows are never modified.
- The Subject-centric `services/ranking.trending()` layer (composite rows, weights, freshness,
  contributions, related-news previews) was removed. The composite RRF itself is unchanged and
  still feeds Subject Detail, where it is now labelled "Cross-Source Rank" (it is not Trending).

### Data-model decision

Existing storage is sufficient; **no migration**. Each ranked-list observation is one
`IngestionBatch` (existing idempotent snapshot record: unique `(source_key, batch_key)` and
`(rank_list_id, observed_at)`) anchored to an internal `RankList` (`list_type = news`, already
allowed by the `rank_list_type_valid` CHECK; name `news:<list_key>`), with
`result = {kind: news_ranking, list_key, list_name, ranking_kind, window, entries: [{rank,
content_item_id, native_value}], publication_lookups, counts}`. History is append-only (one
snapshot per run; a repeated run ID replays with no request). `ContentItem`, `Event`, `Subject`,
and all canonical tables are unchanged. Trade-off: `entries[].content_item_id` has no foreign
key (content rows are never deleted by any code path; the read model skips unknown IDs). The
first attempt used `list_type = news_ranking`, which the CHECK constraint rejected; `news` was
used instead rather than adding a migration.

### Adapter changes

- `adapters/source/base.py`: `RankedContentItem(rank, item: ContentSourceItem, native_value)`;
  `ContentSourceItem` (chronological, never ranked) is unchanged.
- New `adapters/source/news_rankings.py`: `SinaFinanceHotAdapter` (新浪财经 热门新闻排行 ·
  新闻榜 · 点击量排行: `GetTopDataList.php`, `top_type=day`, `top_cat=finance_news_0_suda`,
  today's Beijing `top_time`, `top_order=DESC`, 20 rows; rank = the publisher's row order, the
  page's 序号; `native_value` = `top_num` clicks as published; `window` = `day:YYYY-MM-DD` from
  the rows' `top_time`; published time from `create_date`+`create_time` Beijing; outlet kept
  as `metadata.media`), `GematsuPopularAdapter` (homepage `section.gematsu-popular`),
  `Game3DMHotAdapter` (`/news/` 近期热门 `div.net`; article ID as external ID),
  `HackerNewsTopContentAdapter` (reuses `HackerNewsAdapter` for official `topstories`; rank =
  HN position, `native_value` = points, published = item `time`). Unparseable pages raise
  (source failure, previous snapshot kept); invalid rows are dropped without renumbering.
- `adapters/source/hacker_news.py`: Top Stories metadata also keeps the item `time`.
- `adapters/source/content_registry.py`: `NewsRankingDefinition`, `NEWS_RANKINGS`.

### Backend changes

- New `services/news_ranking_ingestion.py` (`ingest_news_ranking`, `persist_news_ranking`,
  `complete_publication_times`): fetch outside transactions → date lookups for new undated
  articles → one transaction storing content (existing rows reused), deterministic linking of
  new rows (SAVEPOINT), and the snapshot. Duplicate ranks, mixed `observed_at`, or an all-invalid
  list raise.
- `services/content_ingestion.py`: `store_content_items` (per-item stored ID, new or existing)
  and `link_new_content` extracted from `persist_content` (behaviour unchanged);
  `ingest_all_content` also runs the ranked lists.
- `services/intelligence_update.py`: the content group is `[*CONTENT_SOURCES, *NEWS_RANKINGS]`;
  `content_stage` ingests ranked lists with batch key `update:<run-id>` (run-ID replay).
- `services/update_operations.py`: a ranked-list attempt is `success` (a snapshot was stored,
  even with 0 new articles) or `replayed`, with `details.batch_id`.
- New `services/trending.py` (`trending`, `domain_trending`, `current_snapshots`);
  `api/sources.py` `GET /api/v1/trending` now returns it (`limit` default 20, 1–100).
  Removed `services/ranking.trending()` and `services/news.related_preview()` (only used there).
- `services/news.py` / `api/news.py`: optional `content_type` filter (`news|feed`);
  `services/today.py`: Finance Today lists and `/sources` counts use the feed's own
  `content_type` (the 新浪财经 7×24 list stays `feed` items); `/sources` lists the four ranked
  lists as `content_type: news_ranking` (never searchable).
- `cli.py`: `ingest-content`, `content-update-all --source`, and `intelligence-update --source`
  accept the ranked-list keys.

API semantics, old → new: `GET /api/v1/trending` returned per domain the latest composite
run's Subjects (`rank`, `subject_id`, `final_score`, per-source weight/freshness/contribution,
trend, related-news preview). It now returns per domain `{domain, status, ordering, lists[],
total, items[]}` where each item is `{event (the Events list summary shape), external_rankings
[{source_key, source, list_key, list_name, ranking_kind, window, rank, native_value,
observed_at, content_item_id, title, url, language}], topics[]}`; no score field exists.

### Frontend changes

- `app/trending/page.tsx`: Event cards (headline → `/event/[id]`, publisher-rank chips such as
  "Gematsu · Popular Posts #2", last-reported time, report and source counts, Topic chips →
  `/subject/[id]`), the lists with their stated window and observation time, a Methodology note
  (ranks are source-specific, no platform score, display-order rule, entity lists live on
  Today), unavailable/empty/pending states; no platform position number.
- `components/shared.tsx`: `TrendingEventCard` and `listWindowLabel` replace
  `TrendingTopicSummary`. `app/page.tsx`: Home "Trending Events" (5 compact cards per domain;
  the Finance "Market Pulse" label is gone).
- `app/subject/[id]/page.tsx`: the composite card is "Cross-Source Rank / 来源榜单综合排名" and
  no longer links to Trending. `app/today/page.tsx`: news cards request their own
  `content_type`. `lib/api.ts`: Trending types replaced; `fetchNews` `content_type`.
  `lib/i18n.tsx`: shared strings; zh nav label 综合趋势 → 热门. `app/globals.css`: card styles.

### Migrations

None. Alembic head stays `20260930_12`.

### Tests

- New `tests/unit/test_news_rankings.py` (19): Sina rank/window/value/time kept exactly, row
  gaps not renumbered, malformed responses raise, missing window not invented; Gematsu popular
  order (not the chronological list), no synthesized dates or values, layout change raises; 3DM
  block only, foreign links rejected without renumbering, missing block raises, JSON-LD
  `pubDate`; HN rank/points/time; adapter requests through `httpx.MockTransport`; registry
  shape; date lookups only for new undated articles with the crawl delay.
- New `tests/integration/test_trending_postgres.py` (9): snapshot keeps native ranks and reuses
  the feed's row unmodified (no `RankObservation`); same run replays, new runs append history
  and Trending reads the latest; invalid snapshots refused; Event-centric output with one card
  per Event, every rank kept across two lists, Topic filtering, pending count, no Event made
  for Trending, no score keys; unavailable domain; reviewed merge resolves to one canonical
  card; API shape and 422; ingest date lookups and run replay; content stage batch key.
- Updated: `test_update_operations.py` (+1 ranked-list attempt test; inventory),
  `test_intelligence_update.py` (content group), `test_subject_detail_postgres.py` (removed
  the Subject-trending test of the removed layer; `/sources` non-searchable types).
- Results: pytest **776 passed, 1 skipped** (isolated `industry_intelligence_test`); the new
  modules also pass against the dev-database configuration (28 passed, rolled back); unit
  suite 580 passed; Ruff check and format check clean (177 files). ESLint passed; TypeScript
  passed; Next.js production build passed (11 routes, isolated scratchpad copy;
  `frontend/.next` untouched).

### Real-data verification (2026-09-30)

- Backup first: `pg_dump -Fc` `native-industry_intelligence-20260930T034449Z-pre-trending.dump`
  (35 table-data entries) in the local backups directory. Pre-run audit 0/0/6 info.
- One lock-protected run: `intelligence-update --skip-ai --source sina_finance_hot --source
  gematsu_popular --source game_3dm_hot --source hacker_news_top` (run
  `20260930T034459Z-c66efe7d`, 03:44:59–03:46:17Z) → **SUCCESS, exit 0**; 43 HTTP requests
  (Sina 1; Gematsu 1 + 1 article date; 3DM 1 + 8 article dates; HN 31), all answered.
  Content: Sina 20 new; Gematsu 4 listed, 3 already stored from the RSS, 1 new; 3DM 8 new;
  HN 30 new (26 `same_article` links to the existing HN story Subjects). ContentItems +59,
  Events +59 (all seeds), EventSubjectLinks 31, EventCompanyLinks 0, Subjects +0,
  RankObservations +0, AI artifacts +0; postcheck audit 0/0.
- Trending (live, read-only): Finance — 新浪财经 · 热门新闻排行 · 新闻榜 · 点击量排行 (daily
  list 2026-09-30): #1 "A股利好！又一批增持回购来了" (native value 3,957), #2 "23年后，美军灰溜溜
  走了，伊拉克全国放假四天", #3 "9月30日收盘：三大指数收跌 国债收益率续创多年新高"; 20 cards.
  Gaming — Gematsu Popular Posts #1 "Mobile Suit Gundam: Battle Operation 3 Network Test
  Version appears on SteamDB", #2 "Intergalactic: The Heretic Prophet to be fully revealed in
  2027"; 3DM 近期热门 #1 "欧美怕吗？中国自研光刻机啥时候到 黄仁勋马斯克惊人一致", #5 "《生化危机9》
  收入或破5亿美元 Xbox版销量占比为8.6%"; 12 cards. Computer — HN Top Stories #1 "Livenerf: Has
  Opus 5.5 been nerfed yet?"; 30 cards. Pending (unassigned) 0 in every list.
- Browser (isolated production build on 3060 against the API on 8060, live DB): Finance and
  Gaming render Event cards with publisher-rank chips; headline → `/event/[id]` with the report
  in its timeline; Home "Trending Events" per domain; 简体中文 at 375 px without horizontal
  overflow (nav 今日 | 热门 | 搜索); console errors 0; the API log shows only `GET` requests;
  `/today` 新浪财经 7×24 list and paging contain only `feed` items; Subject Detail shows
  "Cross-Source Rank". After: audit 0 errors / 0 warnings / 6 info; `source-health` 29/29
  healthy; DQ-1 and DQ-2.1 dry-runs no-op.

### Known limitations

- Topics are sparse live: no Sina, Gematsu, or 3DM ranked article has a Content ↔ Subject link
  yet, and HN links point only at ranked-story Subjects (excluded as Topics).
- A publisher-edited slug creates a second ContentItem and Event for the same article (live:
  the Gematsu Gundam post, stored from the RSS under its earlier URL); no fuzzy or cross-URL
  dedupe is applied (Events prefer false negatives).
- 3DM 近期热门 mixes hardware/tech items into the Gaming list and includes week-old articles;
  it is shown as published.
- Sina's list is a daily (`top_type=day`) list for the Beijing date, not a rolling 24 h window;
  at the 10:00 Sydney run it reflects about 8 hours of that day.
- Hacker News Top Stories is requested twice per daily run (the Subject ranking and the ranked
  article list; 31 extra Firebase requests); HN stories are link submissions, not only news.
- Computer uses HN only; IT之家 日榜 and 虎嗅 48小时热文 (existing ranked-story Subject lists) are
  deferred as ranked article lists (IT之家 日榜 URLs differ from its RSS URLs).
- Gematsu Popular Posts has 4 entries and no stated window or basis; none is inferred.
- Snapshot entries reference ContentItems without a foreign key (see Data-model decision).
- From the next scheduled 10:00 run, the four ranked lists are part of the automatic content
  stage (about 45 additional requests per run).

### Next step

Owner review. Candidates: Computer ranked article lists from IT之家 日榜 / 虎嗅 (URL mapping to
the RSS), reuse of the HN ranking snapshot to avoid the duplicate request, and Topic coverage
for ranked articles (catalog work, never relaxed linking).
