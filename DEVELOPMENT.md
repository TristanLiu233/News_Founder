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
- Scheduled AI is bounded (Product-1): the local scheduler runs `intelligence-update --ai-limit 12`
  (hard cap 15 in code, deterministic selection, cache first, provider preflight); page and GET
  requests never call AI, a crawler, or the update. AI never mutates canonical (Layer-2) data.
- Read `AGENTS.md` and the current code, migrations, and tests before relying on any plan.

## Current Baseline

- Branch `phase-7`; Alembic head `20260930_12` (unchanged by Trending-1 and Product-1).
- Backend: pytest 822 passed, 1 skipped (pre-existing Hacker News skip) on the isolated
  `industry_intelligence_test`; unit 613; Ruff check and format check clean (after Product-1).
- Frontend: ESLint, TypeScript, and the Next.js production build (11 routes) pass.
- Live dev database: `data-quality-audit` 0 errors / 0 warnings / 6 info; `source-health`
  30/30 healthy (25 earlier sources + 5 publisher ranked lists).
- Local runtime: native PostgreSQL 16 (Local-1); daily launchd `intelligence-update --ai-limit 12`
  at 10:00 Australia/Sydney (bounded AI since Product-1; `--skip-ai` before).

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

## 2026-09-30 — Trending-1.1 Hardening & Deduplication

Status: complete (2026-09-30). Not committed (owner instruction: no commit, no push).

### Goal

Harden Trending-1 for the four issues found in its live smoke, without new publishers or a
Trending redesign: (A) Hacker News Top Stories fetched twice per daily run; (B) no Topics on
live Trending cards; (C) a publisher-edited URL created a duplicate article and Event;
(D) ranking time semantics must stay exactly publisher-specific.

### Starting state

`phase-7` at `3764f67`, clean tree, no remote. Baseline: pytest 776 passed, 1 skipped
(isolated `industry_intelligence_test`); Ruff check/format clean (177 files);
`data-quality-audit` 0 errors / 0 warnings / 6 info; frontend ESLint, TypeScript, and Next.js
build (11 routes, isolated copy) pass.

### Audits (before any change)

A. HN duplicate fetch. (a) `/topstories.json` and (b) every `/item/<id>.json` are fetched by
`adapters/source/hacker_news.HackerNewsAdapter.fetch` for both consumers: the Subject ranking
(`SOURCES["hacker_news"]` → `services/source_ingestion.ingest` in the `rankings` stage) and the
ranked article list (`NEWS_RANKINGS["hacker_news_top"]` → `HackerNewsTopContentAdapter` wraps a
second `HackerNewsAdapter` → `services/news_ranking_ingestion.ingest_news_ranking` in the
`content` stage). (c) The ranking path persists one `RankObservation` per story (rank, title,
URL, points, `item_metadata` with `hn_url`, author, comments, and — since Trending-1 — the item
`time`) plus an `IngestionBatch` keyed `update:<run-id>`. (d/e) That snapshot already holds
everything the ranked article list needs (story ID from `hn_url`). (f) Both run in the same
`intelligence-update` run with the same batch key, rankings stage first. Trending-1 smoke:
31 requests for the article list on top of the ranking's 31.

B. Topics. Ranked articles go through the same deterministic linker as feed content
(`persist_news_ranking` → `link_new_content` → `link_content_items`, then the `links` stage's
`relink_all_content` over all content, then `build_events`/`relink_all_events`); replayed rows
were linked when first stored. Not A–D. A read-only dry-run of `relink_all_content` over the
62 articles of the latest snapshots: 26 links, all `same_article` from HN articles to their HN
ranked-story Subjects (excluded as Topics by design), 0 added/removed; Sina 20, Gematsu 4, and
3DM 8 articles have 0 links. The catalog has no Subject for the games and products they name
(巫师3, 生化危机, 卧龙, Monster Hunter, Satisfactory, Intergalactic, Gundam, 光刻机 → none; 小米
and Windows exist only as computer-domain ranked stories, never name-linked). Cause **E**: the
pipeline is correct and these articles have no canonical Topic.

C. Article identity. `ContentItem` identity is `(source_id, identity_key)` with
`identity_key = external:<external_id>` else `url:<normalized url>`; an incoming item whose
normalized URL the same source already stored is a replay first. Gematsu RSS rows use the
WordPress GUID (`external_id = https://www.gematsu.com/?p=1081377`). The Popular Posts HTML
carries only the slug URL, so the edited slug
(`…network-test-version-appears-on-steamdb` vs `…network-test-appears-on-steamdb`) matched
neither URL nor identity and became `url:`-keyed row `356691e4` → Event `37643076` (the RSS row
is `6f374d65` → Event `d960de20`). The edited-slug article page (already fetched for its date)
publishes the same stable ID three ways: `<link rel="alternate" type="application/json"
href="…/wp-json/wp/v2/posts/1081377">`, body class `postid-1081377`, and the Disqus identifier
`1081377 https://www.gematsu.com/?p=1081377`. A stable publisher identity exists. 3DM ranked
rows use the article number; HN rows the story ID; Sina rows the URL (which embeds the doc ID;
no edited Sina URL observed).

D. Semantics. Stored: Sina `list_key news_click_day`, `ranking_kind click_count`, window
`day:<top_time>` (the publisher's `top_type=day` list for a Beijing calendar date); Gematsu
`popular_posts`/`popular`, no window; 3DM `recent_hot`/`hot`, no window; HN `top_stories`/`top`.
No "24h", "rolling", or "today" claim exists in code, API, UI strings, or docs. Imprecision:
the UI window label "daily list, 2026-09-30" / "日榜，2026-09-30" does not say the date is the
publisher's Beijing calendar date.

### Root causes (summary)

- A: two independent `HackerNewsAdapter.fetch` calls for the same upstream list in one run.
- B: none in the pipeline — the live ranked articles name nothing in the canonical catalog
  (outcome **B**: pipeline already correct; no legitimate live Topics exist).
- C: the ranked-list path had only the mutable slug URL, while the publisher's stable ID (the
  WordPress post ID in the RSS GUID) was on the article page but never read.
- D: no false claim; one imprecise label (the Beijing calendar date was not stated).

### Changes

- HN (A): `NewsRankingDefinition.reuse_ranking` (`hacker_news_top` → `hacker_news`);
  `news_ranking_ingestion.reused_ranking_snapshot` reads this run's `hacker_news`
  `IngestionBatch` (same batch key `update:<run-id>`) and its `RankObservation`s;
  `HackerNewsTopContentAdapter.entries_from_observations` + `hn_story_id` build the ranked
  articles (rank, points, story ID from `hn_url`, `time`) with no request. The snapshot records
  `reused_ranking_batch_id` and shares the ranking's `observed_at`. Fallback: the adapter fetches
  only when this run stored no `hacker_news` snapshot (manual `ingest-content`, a run without the
  ranking, or a failed ranking fetch). No cache layer was added.
- Identity (C): the article-page hook is now `article_details(url) -> ArticleDetails
  (published_at, external_id)` (`lookup_articles` flag); `complete_article_details` looks up
  new-URL entries lacking a date or an ID and never replaces values the row carries.
  `parse_gematsu_article` takes the post ID from the page's own
  `…/wp-json/wp/v2/posts/<id>` link, fails closed (no ID) when the body `postid-<id>` names a
  different post or the link is missing or foreign, and returns it in the RSS GUID form
  `https://www.gematsu.com/?p=<id>`. An edited-slug article then collides with the RSS row's
  identity key `external:https://www.gematsu.com/?p=<id>` in `store_content_items` and resolves
  to that row (never modified). 3DM's lookup adds only the date (the row already carries the
  article number). No title, fuzzy, or AI matching; no historical row was merged.
- Semantics (D): the window label is "daily list, Beijing date <date>" / "日榜，北京时间 <date>";
  the Trending Methodology note states that each list keeps its publisher's name and time scope
  and that lists stating no period are given none. Machine values unchanged (`day:<date>`,
  `news_click_day`, `recent_hot`, `popular_posts`, `top_stories`).
- Topics (B): no code change (none needed); regression tests added.

### Files changed

`backend/src/industry_intelligence/adapters/source/content_registry.py`,
`backend/src/industry_intelligence/adapters/source/news_rankings.py`,
`backend/src/industry_intelligence/services/news_ranking_ingestion.py`,
`backend/tests/unit/test_news_rankings.py`, `backend/tests/integration/test_trending_postgres.py`,
new `backend/tests/fixtures/gematsu_article_identity.html` (identity markers only, from the live
page), `frontend/app/trending/page.tsx`, `frontend/lib/i18n.tsx`, plus `DEVELOPMENT.md`,
`AGENTS.md`, `CLAUDE.md`, `README.md`.

### Data model / migration

No data-model change and no migration (head `20260930_12`). Snapshot `result` gains
`reused_ranking_batch_id`; `publication_lookups` is now `article_lookups`
(`lookups`, `dates_found`, `ids_found`); older snapshots keep their old key (read by nothing).

### Tests

- Unit (`test_news_rankings.py`, 19 → 24): Gematsu post ID in GUID form; fail-closed on
  disagreeing, missing, or foreign markers; adapters' `article_details`; HN entries from stored
  observations (no `time` → date unknown); list semantics are the publisher's own (no 24 h,
  rolling, or today claim; only Sina states a period); lookups only for new entries missing a
  date or ID, row values never replaced, crawl delay; adapters without article pages never
  looked up.
- Integration (`test_trending_postgres.py`, 9 → 13): HN requested once per run for both
  consumers (counting `MockTransport`: 1 `topstories`, 1 request per story; ranking observations
  still stored; article snapshot reuses the ranking batch; Trending shows both HN cards in HN
  order); HN article list fetches itself only without a same-run snapshot; an edited URL
  resolves to the feed row by the stable ID (one row, one Event, row unmodified) while a
  similar-titled article with a different ID stays a separate row and Event; Topics come from
  the existing linker (`exact_name` → EventSubjectLink → `topics[]`) and no Topic is invented
  for an article naming no catalog Subject.
- Results: pytest **785 passed, 1 skipped** (isolated test DB; 776 + 9); unit 585; the Trending
  modules also pass against the dev-database configuration (37 passed, rolled back); Ruff check
  and format check clean (177 files); ESLint passed; TypeScript passed; Next.js production build
  passed (isolated scratchpad copy).

### Live verification (2026-09-30)

- Backup first: `native-industry_intelligence-20260930T042757Z-pre-trending-1.1.dump`
  (35 table-data entries).
- One lock-protected run: `intelligence-update --skip-ai --source hacker_news --source
  hacker_news_top --source gematsu_popular` (run `20260930T042806Z-da804a5b`) → **SUCCESS,
  exit 0**. Requests: 32 in total — HN `topstories` 1, HN items 30 (30 distinct), Gematsu list 1,
  Gematsu article lookups 0. Before (Trending-1 design): HN 31 for the Subject ranking + 31 for
  the article list = 62; now 31 for both. The `hacker_news_top` snapshot
  (`ddf35273`) has `reused_ranking_batch_id = a1739d0c` (the `hacker_news` batch of the run) and
  the same `observed_at` (04:28:06.93Z). Deltas: RankObservations +30, Subjects +6 (new HN story
  Subjects, existing ranking behaviour), ContentItems +3, Events +3, composite runs +1 (computer),
  content links +6 (all HN `same_article`), AI artifacts 0.
- Topics: live Trending cards with Topics — computer 0/30, finance 0/20, gaming 0/12 (outcome
  B, as audited).
- URL identity: no live reproduction was available — all four Gematsu Popular Posts URLs were
  already stored, so no lookup ran. The Trending-1 duplicate (`356691e4`/Event `37643076` vs
  RSS `6f374d65`/Event `d960de20`, same post 1081377) is unchanged: its edited URL is now a
  stored URL and matches that row first. The fix prevents new cases (fixture/integration
  tests); it does not repair the existing one.
- Labels (browser, isolated build on 3060 / API 8060): English "新浪财经 · 热门新闻排行 · 新闻榜 ·
  点击量排行 (daily list, Beijing date 2026-09-30)", Chinese "(日榜，北京时间 2026-09-30)";
  Gematsu "Popular Posts" and 3DM "近期热门" with no period; no "24h/rolling/today" text in
  either locale; fresh tab: 12 Gaming cards, no overflow, console errors 0.
- After: `data-quality-audit` 0 errors / 0 warnings / 6 info; `source-health` 29/29 healthy.

### Known limitations

- The existing Gematsu duplicate (post 1081377) remains two ContentItems and two Events; a
  reviewed `event-merge d960de20… 37643076… --reason "same Gematsu post 1081377"` would show one
  card (owner decision; not run).
- The stable-ID lookup runs only for list URLs not stored yet; a URL already stored under a
  duplicate row keeps mapping to that row.
- If this run's HN Subject ranking fails, the article list fetches Top Stories itself (a second
  attempt only after the first produced no snapshot).
- The HN article list inherits the ranking path's row filter (a story resolving to a Subject
  already seen in the snapshot is skipped there); not observed live.
- Sina identity stays URL-based (the URL embeds the doc ID; no edited Sina URL observed).
- Live Topics stay empty until the catalog holds Subjects the listed articles name.

### Next step

Owner decisions: the reviewed Event merge for the Gematsu duplicate; catalog coverage for
Topics; Computer ranked lists (IT之家 日榜 / 虎嗅) remain deferred.

## 2026-09-30 — Product-1 — Event-First Home, Source Expansion & Controlled AI

Status: complete (2026-09-30).

### Starting state

- Branch `phase-7` at `3de5108` ("fix: harden native-ranked Trending ingestion and article
  identity"), clean working tree, no remote, nothing pushed. Alembic head `20260930_12`.
- Baseline (actual, rerun before any change): pytest **785 passed, 1 skipped** (pre-existing
  Hacker News skip) on the isolated `industry_intelligence_test`; Ruff check clean, Ruff format
  check clean (178 files incl. `ops/`); `data-quality-audit` 0 errors / 0 warnings / 6 info
  (exit 0) on the dev database; frontend ESLint, TypeScript, and Next.js production build (11
  routes, isolated scratchpad copy) pass.

### Scope

- A: Home becomes Event-first (Trending Events as the first major section), reusing the
  Event-centric Trending read model; one short summary per card from persisted data only.
- B: qualify native ranked-news capabilities (HN beststories, TapTap, IGN, Kotaku, EastMoney);
  implement only what passes the qualification audit.
- C: bounded, deterministic, cache-first scheduled AI through the existing Phase 7 pipeline;
  enable it in the local scheduler only if every safety gate is proven.

### Explicit exclusions

No Event clustering, Subject identity, entity-threshold, or EventCompanyLink change; no
bilingual AI; no AI ranking or hotness score; no Redis or generic cache; no PostgreSQL,
deployment, or scheduler-time change; no Docker dependency; no bulk AI generation; no push.

### Audit D — Home (before any change)

- Home page: `frontend/app/page.tsx` (client component; module-level loaders, per-section
  `useLoad`). Order was: dashboard header with domain links → Today at a Glance
  (`/api/v1/news` × 3) → Latest Events (`/api/v1/events?limit=5`) → Trending Events
  (`/api/v1/trending?limit=5`, third section) → External Company Rankings.
- Trending section / Event card: `TrendingEventCard` in `frontend/components/shared.tsx`
  (compact on Home); types in `frontend/lib/api.ts`; read model
  `backend/src/industry_intelligence/services/trending.py` (`domain_trending`) behind
  `GET /api/v1/trending` (`api/sources.py`). Home already reused this read model; no second
  query or ranking existed.
- AI artifact selection: `services/ai_insights.py` (`_insight`: `available` only for a `valid`
  artifact whose input hash equals the current evidence + primary prompt hash in the Event's
  primary locale; `failed`/`rejected`/`stale`/`not_generated` otherwise; legacy cross-language
  rows never read). Only Event Detail used it.
- Summary fallback: none. Events have no summary column; the only persisted non-AI text is
  `ContentItem.description` (publisher excerpt ≤ 280 characters). Live: of the 12 Trending
  cards Home would show, 3 (Gematsu) had an excerpt; Hacker News, 新浪财经, and 3DM rows have
  none.
- i18n: shared strings in `frontend/lib/i18n.tsx` (`common`), page strings in the Home `copy`;
  CSS in `frontend/app/globals.css`.

### Audit E — source capability and compliance

All requests with the application's honest User-Agent, serially, robots.txt first, one request
per page.

| Candidate | Domain | Native signal | News/Event vs Entity | Compliance | Decision | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| Hacker News `beststories` | computer | official Firebase list (HN's own "best": highest-voted recent) | news (link submissions) | official API | Deferred | 200 ids; 23 of its first 30 are not in the stored Top 30, but it is the same publisher and the same kind of link submission as Top Stories (Computer already shows 30 HN cards, Home 4); +31 requests per run for no new publisher breadth |
| TapTap 热门/热玩/热卖/预约/新品榜 | gaming | native ranked lists | **entity** (games) | robots allows `/top/*`, but the list data comes from `/webapiv2/app-top/v2/hits`, which robots.txt disallows (serialized into the page) | Deferred | entity ranking (never an Event rank); its only machine form is a robots-disallowed internal API; no Event consumer; not needed for the Home question |
| IGN | gaming | — | — | robots.txt header: automated retrieval prohibited without written permission (Ziff Davis) | Deferred | publisher policy; no page fetched |
| Kotaku | gaming | none on the homepage (only "Latest"; RSS `/feed` is chronological) | news, chronological | robots allows `/` (Cloudflare-fronted, 200) | Deferred | no Popular/Most Read/Trending section; chronology is not popularity |
| 东方财富 网友点击排行榜 | finance | `native_numeric_rank`: the publisher prints positions 1–10 (`span.no`) under "网友点击排行榜" (reader click ranking); no click counts and no period stated | news articles (`finance.eastmoney.com/a/<code>.html`) | `finance.eastmoney.com/robots.txt` 404 (allowed, RFC 9309); `www.eastmoney.com/robots.txt` `Allow: /`; static HTML on the 热点扫描 column page | **Implemented** | explicit native popularity list of an existing publisher; stable article code shared with its 7×24 feed |
| 东方财富 专题 | finance | `editorial_hot_selection` (dated editorial topics) | topics | same page | not a ranking | editorial selection, never read as a rank |
| 东方财富 热点扫描 column | finance | `chronological_feed` | news | same page | not a ranking | chronological column |

### Audit F — AI pipeline, CLI, scheduler

- `intelligence-update` ran the AI stage whenever a provider was configured and `--skip-ai` was
  absent; `--ai-limit` (default 10) had **no upper bound** (any integer was accepted) and chose
  the N most recent Events of any domain (`ai_enrichment.select_events`).
- Cache: `find_cached` (input hash + provider + model), `mark_stale`, `_settled_attempt`
  (`skip_settled`) — reused unchanged.
- Validators: `ai_evidence.validate_output` (schema, own-Event evidence refs, advice/prediction,
  single-source, figures, locale) — reused unchanged; primary prompts `event_summary_v4` /
  `event_why_it_matters_v6` unchanged.
- No code-level provider precheck existed (Phase 7.1 used a manual `models.retrieve`). A 401/403
  became `AIRequestRejected` and the batch continued with the same credential (one failed
  artifact per task).
- Scheduler: `ops/scheduler/daily_update.py` hardcoded `--skip-ai`; the launchd plist only runs
  the wrapper; production `deploy/systemd/industry-intelligence-update.service` runs
  `--skip-ai` (not deployed).
- The explicit `ai-event-enrich` command (limit ≤ 100) is a separate manual command and stays
  unchanged.

### Design decisions

- Home is Event-first: the Trending read model is the hero (4 Events per domain, three columns
  from 56rem, one column on phones); Today at a Glance and Latest Events follow as secondary
  sections, then External Company Rankings, then an Explore row (Companies entry and a Search
  form that navigates to `/search?q=`). `/today` and its backend are unchanged.
- Card summary (`trending.card_summaries`, added to each Trending item as `summary`): (1) the
  first point of the Event's displayable AI Summary (`ai_insights.available_summaries`, exactly
  the `available` state), else (2) a stored publisher excerpt of one of its reports
  (`events.event_excerpts`: representative report first, then publication order;
  `clean_excerpt` collapses whitespace, drops a trailing "Read more"/"[…]", and discards
  excerpts shorter than 20 characters or repeating the headline), else `null` — the card then
  has no summary line. There is no persisted non-AI Event summary to use between (1) and (2).
  The UI labels the text "AI summary"/"AI 摘要" or "Excerpt · <source>"/"摘录 · <source>" and
  marks it with its own `lang`. No new type (`HomeEvent`, score) exists.
- EastMoney is a second feed mode of the existing 东方财富 `Source` (`NewsRankingDefinition`
  `eastmoney_click_rank`, catalog order after 新浪财经): rank = the printed number (never
  recounted), `ranking_kind` `click_rank` (the publisher's own label), `window` none,
  `native_value` none; external ID = the article code, so an article already stored from the
  7×24 feed is the same ContentItem; new articles get their publication time from the article
  page (`div.infos`, Beijing time; 1 s apart; only for URLs not stored yet).
- Bounded AI (`ai_enrichment.select_bounded_events`, rule `trending_then_recent_round_robin`):
  per domain (computer, finance, gaming), candidates are the Events on that domain's Trending in
  the Trending read model's own order (list catalog order, then native rank), then the domain's
  most recently reported Events (`last_published_at` desc, ID); only eligible Events count
  (canonical, non-empty, not a redirect shell, primary locale present — the Phase 7 rule);
  domains are taken round-robin until the cap. No score, no AI in selection. Trending-first
  was chosen over pure recency so AI summaries land on the Events Home shows first; this uses
  only publisher-native list membership and order already shown. The rule does not force a
  single/multi-source mix (live Trending Events are single-source).
- Cap: `--ai-limit` default 12, allowed 1–15 (`validate_max_events`; argparse type, and
  `run_update`/`execute_update`/`plan_update` refuse before any lock, request, or write). The
  option name was kept (repository-consistent) instead of adding `--ai`/`--ai-max-events`.
- Cache first: the stage plans with the provider identity first (`enrich_events(dry_run=True)`);
  only when a task needs generating is the provider preflighted (`ai.provider.preflight`;
  Anthropic: Models API `GET /v1/models/{model}`, no Messages request). A refused preflight
  fails the stage (`provider_preflight_failed`, 0 requests). A 401/403 during generation
  (`AIAuthenticationFailed`, still stored as `request_rejected`) stops the batch; the remaining
  tasks are reported `not_attempted`. A failed AI stage only degrades a run (unchanged).
- Scheduler: all gates proven (below), so the launchd wrapper runs
  `intelligence-update --ai-limit 12` (≤ 24 primary-locale requests per run, usually fewer
  because cached artifacts replay). Time, plist, lock, and exit codes unchanged; `--skip-ai`
  stays the documented rollback (`AI_ARGS` in the wrapper). Production systemd stays
  `--skip-ai` (deployment out of scope).

### Data-model decision

No migration (head `20260930_12`). `GET /api/v1/trending` items gain the additive `summary`
field; EastMoney snapshots are ordinary `IngestionBatch` rows (`result.kind = news_ranking`,
`news:eastmoney_click_rank`). `StageResult` gains an output-only `details` list (the AI stage's
per-Event plan), not stored in `update_runs`.

### Files changed

- Backend: `adapters/source/news_rankings.py` (`parse_eastmoney_click_rank`,
  `parse_eastmoney_published`, `EastmoneyClickRankAdapter`), `adapters/source/content_registry.py`
  (`eastmoney_click_rank`), `ai/provider.py` (`AIAuthenticationFailed`, `preflight`),
  `ai/providers/anthropic.py` (401/403 mapping, `preflight`), `services/ai_enrichment.py`
  (cap, `select_bounded_events`, `round_robin`, per-Event plan rows, stop on refused
  credential, `event_ids is not None`), `services/ai_insights.py` (`available_summaries`),
  `services/events.py` (`clean_excerpt`, `event_excerpts`), `services/trending.py`
  (`card_summaries`, `summary`, `summaries=` switch), `services/intelligence_update.py`
  (bounded AI stage, cap validation, `details`, text output), `cli.py` (`--ai-limit` type).
- Frontend: `app/page.tsx` (hierarchy, hero, Search entry), `components/shared.tsx`
  (`TrendingEventCard` `showSummary`), `lib/api.ts` (`EventCardSummary`), `lib/i18n.tsx`
  (summary labels), `app/globals.css` (hero, summary, Explore).
- Ops: `ops/scheduler/daily_update.py` (`--ai-limit 12`).
- Tests/fixtures: new `tests/fixtures/eastmoney_click_rank.html`,
  `tests/fixtures/eastmoney_article.html` (trimmed live captures), new
  `tests/unit/test_bounded_ai.py`, new `tests/integration/test_product1_postgres.py`; updated
  `tests/unit/test_news_rankings.py`, `tests/unit/test_ai_provider.py`,
  `tests/unit/test_scheduler_wrapper.py`, `tests/integration/test_trending_postgres.py`,
  `tests/integration/test_operations_postgres.py`.
- Docs: `DEVELOPMENT.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`.

### Tests

- Order kept for the new source: fixtures → parser tests → adapter test → implementation →
  registry → ingestion → consumer → live smoke.
- `test_news_rankings.py` 24 → 31: numbered list with article codes; editorial 专题 never read;
  printed number kept and bad rows dropped without renumbering; missing block raises (2);
  article time is Beijing time or absent; adapter requests; registry is a feed mode of the
  existing 东方财富 Source; list semantics (no window, no value).
- `test_ai_provider.py` 26 → 30: 401/403 → `AIAuthenticationFailed` with one request; preflight
  uses only the Models API and maps a refusal; providers without a preflight pass.
- New `test_bounded_ai.py` (17): default 12 / cap 15; out-of-range or non-integer caps refused
  (8); CLI refuses `0`, `16`, `all`, `unlimited`, `335`, empty; round-robin balancing and
  exhaustion (pure, deterministic); `execute_update`/`run_update`/`plan_update` refuse an invalid
  cap before the lock or any stage.
- New `test_product1_postgres.py` (9, `*_test` only): capped deterministic round-robin; Trending
  Events before more recent ones; mixed-language and redirected Events never selected;
  cache-first stage (4 requests + 1 preflight, then 0 requests and no preflight); refused
  preflight → 0 requests, no artifact, run degraded; refused credential mid-batch → 1 request,
  1 failed artifact, 3 not attempted; validator rejection stays failed and is never rendered
  (excerpt shown instead); card summary priority (excerpt → valid AI; stale, rejected, failed
  ignored; none → absent); GET requests (13 routes incl. `/trending`, `/today`, Event
  detail/content/AI, news, search) with provider, update, ingestion, and `HTTPClient.get`
  replaced by failures, and every `/api/` route GET/HEAD only.
- Updated: Trending API card keys include `summary` (None without excerpt/AI); run options
  record `ai_limit` 12; scheduler wrapper runs `--ai-limit 12`.
- Results: pytest **822 passed, 1 skipped** (isolated test DB); unit **613**; Ruff check and
  format check clean; ESLint, TypeScript, Next.js production build (11 routes, isolated copy)
  pass.

### Real-data verification (2026-09-30)

- Backup first: `native-industry_intelligence-20260930T075557Z-pre-product-1.dump` (35
  table-data entries, `PGDMP` header).
- EastMoney live smoke: `intelligence-update --skip-ai --source eastmoney_click_rank` (run
  `20260930T075604Z-11d2b497`) → **SUCCESS, exit 0**; 11 requests (list page 1 + 10 article
  pages), all HTTP 200; fetched 10, stored 10, replayed 0, invalid 0; 10/10 publication times
  found, window none, native value none; Events +10 (seeds), ContentItems +10, Subjects +0;
  audit 0/0; `source-health` **30/30 healthy** (`eastmoney_click_rank` success). Finance
  Trending: 新浪财经 (20) then 东方财富 网友点击排行榜 #1–#10; pending 0.
- AI plan (read-only, `--plan --ai-limit 10`): 10 Events (computer 4, finance 3, gaming 3),
  20 tasks, 0 cached, 20 to generate; every Event single-source, primary locale en ×7 /
  zh-CN ×3.
- AI smoke: `intelligence-update --ai-limit 10 --source cmc_tech_market_cap` (run
  `20260930T080441Z-6730efde`; the CompaniesMarketCap source replayed the stored daily
  snapshot, so Trending and the selection did not change) → **SUCCESS, exit 0**: selected 10,
  cached 0, provider preflight 1 (`GET /v1/models/<model>` 200), Messages requests 20 (all HTTP
  200), valid 20, validation failures 0, other failures 0, rejected 0; 28,748 input / 1,363
  output tokens; model `claude-sonnet-5`; `ai_artifacts` 209 → 229 (valid 81); every other
  table unchanged; no credential in logs. Afterwards the plan shows 20/20 cached, 0 to
  generate; the default-cap plan (12) would generate only 4 tasks.
- Human audit (all 20 artifacts against the persisted evidence): 18 clean passes; 2 minor notes
  — `d4863980` zh-CN Summary adds 计划 ("plans") and 被视为 to the headline "A股利好！又一批增持
  回购来了" (framing word not in the evidence); `04197252` Why It Matters says "among other
  platforms" from a truncated excerpt ending "and …". 0 wrong numbers, 0 advice or prediction,
  0 wrong evidence refs, 0 language issues. 9 of 10 Why It Matters correctly abstain
  (`insufficient_evidence`): 8 of 10 Events are headline-only reports. Neither note was
  rejected (owner decision; `ai-artifact-reject` hides an artifact).
- Scheduler gate: cap enforced in code (unit), cache reuse (integration + live 20/20 cached),
  provider preflight (integration + live), validators unchanged and mandatory, 10-Event smoke
  success, GET never invokes a provider (integration + browser). → wrapper switched to
  `--ai-limit 12`; `env -i` wrapper `--plan` exit 0; `launchctl print` shows the agent loaded,
  `Hour` 10 `Minute` 0, last exit 0.
- Browser (isolated production build on 3070 against the API on 8070, live DB): Home sections
  in order Trending Events → Today at a Glance → Latest Events → External Company Rankings →
  Explore; 12 hero cards (4 per domain): 10 AI summaries, 1 Gematsu excerpt (Satisfactory), 1
  card without a summary (新浪财经, headline only); no empty card, no raw AI state text; headline
  → `/event/<id>` (What Happened, Why It Matters, Timeline); Search entry 比特币 →
  `/search?q=比特币` → Bitcoin `42d6184f`; English and 简体中文 at 1024 px (3 columns) and 375 px
  (hero at ≈ 265 px), no horizontal overflow; AI text keeps its own `lang`; `/trending?domain=
  finance` shows 20 cards, 30 expanded with 东方财富 · 网友点击排行榜 #1–#3…; console errors 0; the
  browser contacted only `localhost:3070`/`8070` (aborted `_rsc` entries are Next.js link
  prefetches); the API log shows only GET; `ai_artifacts` unchanged by the browser run.
- After: `data-quality-audit` 0 errors / 0 warnings / 6 info; `source-health` 30/30 healthy.

### Known limitations

- Most Trending Events are headline-only reports (Hacker News, 新浪财经, 3DM, 东方财富 list rows),
  so their AI Summary mostly restates the headline and Why It Matters abstains; the summary adds
  context only where a report has an excerpt.
- Bounded selection favours current Trending Events, which are single-source today; multi-source
  Events get AI only when they are among a domain's Trending or most recent Events.
- Two audited artifacts carry minor framing notes (see above); new scheduled batches are not
  reviewed automatically — read them with `GET /api/v1/events/{id}/ai` and hide a defective one
  with `ai-artifact-reject` as before.
- 东方财富 网友点击排行榜 states no period and no click counts; none is shown. It lives on a column
  page (`/a/crdsm.html`); a layout change raises (source failure, previous snapshot kept).
- Deferred sources: HN beststories, TapTap (entity lists, robots-disallowed data API), IGN
  (publisher prohibition), Kotaku (no popularity section).
- Production systemd still runs `--skip-ai` (nothing deployed; a deployment decision).
- The Trending-1 Gematsu duplicate (post 1081377) remains until a reviewed `event-merge`.

### Next step

Owner review of the first scheduled bounded-AI run (2026-10-01 10:00 Australia/Sydney:
`daily-update.log`, `update_runs` AI counts, `source-health`), and of the two noted artifacts.
