"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { DOMAINS, EventList, NewsList, TrendBadge, TrendingEventCard } from "../components/shared";
import {
  fetchCompanyRanking, fetchCompanyRankings, fetchEvents, fetchNews, fetchTrending,
  type CompanyRankingDetail, type CompanyRankingEntry, type CompanyRankingSummary, type Domain, type EventsPage,
  type NewsPage, type TrendingResponse,
} from "../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../lib/i18n";

/**
 * Home is Event-first (Product-1): the first section answers "what is happening now" with the
 * Event-centric Trending read model (publisher-ranked Events per domain, in the API's display order,
 * each with its persisted summary). Latest reports and Events, external company rankings, and the
 * Companies/Search entry points follow. Home composes existing read APIs only: nothing is scored,
 * ranked, or generated here, and no request triggers ingestion or AI.
 */
const TRENDING_TOP = 4;
const NEWS_PER_DOMAIN = 3;
const LATEST_EVENTS = 5;
const RANKING_TOP = 5;
// The API maximum for the ranking list; only a handful of rankings are registered.
const RANKINGS_LIMIT = 100;

const copy = {
  en: {
    title: "Industry Intelligence",
    intro: "What is happening now in Computer, Finance and Gaming: events on publishers' own ranked lists first, then the latest reports, external company rankings and search.",
    today: "Today at a Glance", todayNote: "Latest reports from each domain's news sources, newest first.", viewToday: "View Today", noNews: "No reports for this domain yet.",
    events: "Latest Events", eventsNote: "Most recently reported events across all domains.", viewEvents: "View all events", noEvents: "No events yet.",
    trending: "Trending Events", trendingNote: "Events whose reports are on publishers' own ranked lists, in each publisher's own order. Summaries are AI-generated from the listed reports or are publisher excerpts.", viewTrending: "View Trending", noTrending: "No trending events for this domain yet.",
    rankings: "External Company Rankings", rankingsNote: "Rankings are shown as published by their original sources.", viewRankings: "View all rankings",
    noRankings: "No external rankings have been collected yet.", rankingFailed: "This ranking could not be loaded.",
    snapshot: "As of", notEnoughHistory: "Not enough history yet", openRanking: "Open ranking",
    metrics: {market_cap: "Market cap", revenue: "Revenue", total_assets: "Total assets"} as Record<string, string>,
    sectionFailed: "This section is unavailable right now.",
    explore: "Explore",
    companies: "Companies", companiesNote: "Companies that appear in external rankings, with sourced profiles where available.", viewCompanies: "Browse companies",
    search: "Search topics", searchNote: "Find a topic (company, asset, game, repository, story) by name in any supported language.",
    searchLabel: "Search query", searchPlaceholder: "e.g. NVIDIA, 比特币, Warframe", searchButton: "Search",
  },
  "zh-CN": {
    title: "行业情报",
    intro: "计算机、金融、游戏三个领域正在发生什么：先看登上发布方自有榜单的事件，再看最新报道、外部公司排行榜与搜索。",
    today: "今日速览", todayNote: "各领域资讯来源的最新报道，按时间由新到旧。", viewToday: "查看今日", noNews: "该领域暂无报道。",
    events: "最近事件", eventsNote: "各领域最近被报道的事件。", viewEvents: "查看全部事件", noEvents: "暂无事件。",
    trending: "热门事件", trendingNote: "报道登上发布方自有榜单的事件，按各发布方自己的排名顺序展示。摘要由 AI 根据所列报道生成，或为发布方原文摘录。", viewTrending: "查看热门", noTrending: "该领域暂无热门事件。",
    rankings: "外部公司排行榜", rankingsNote: "排行榜按原始来源发布的内容原样展示。", viewRankings: "查看全部排行榜",
    noRankings: "尚未采集到外部排行榜。", rankingFailed: "该榜单暂时无法加载。",
    snapshot: "数据日期", notEnoughHistory: "尚无足够历史数据", openRanking: "打开榜单",
    metrics: {market_cap: "市值", revenue: "营收", total_assets: "总资产"} as Record<string, string>,
    sectionFailed: "该部分暂时无法加载。",
    explore: "探索",
    companies: "公司", companiesNote: "出现在外部排行榜中的公司，以及已有来源的公司资料。", viewCompanies: "浏览公司",
    search: "搜索主题", searchNote: "按名称查找主题（公司、资产、游戏、代码仓库、文章），支持中英文。",
    searchLabel: "搜索关键词", searchPlaceholder: "例如 NVIDIA、比特币、Warframe", searchButton: "搜索",
  },
} as const;

/** One independent section load: a failure only affects the section that made the request. */
type Load<T> = {status: "loading"} | {status: "ready"; data: T} | {status: "error"};

function useLoad<T>(load: (signal: AbortSignal) => Promise<T>): Load<T> {
  const [state, setState] = useState<Load<T>>({status: "loading"});
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal)
      .then(data => setState({status: "ready", data}))
      .catch(() => { if (!controller.signal.aborted) setState({status: "error"}); });
    return () => controller.abort();
  }, [load]);
  return state;
}

// Module-level loaders keep `useLoad` dependencies stable, so every request is made once per page load.
const newsLoaders = Object.fromEntries(DOMAINS.map(domain => [
  domain, (signal: AbortSignal) => fetchNews({domain, limit: NEWS_PER_DOMAIN}, signal),
])) as Record<Domain, (signal: AbortSignal) => Promise<NewsPage>>;
const loadEvents = (signal: AbortSignal) => fetchEvents({limit: LATEST_EVENTS}, signal);
// One request returns the Top 5 of every domain.
const loadTrending = (signal: AbortSignal) => fetchTrending(signal, {limit: TRENDING_TOP});

type RankingCardData = {summary: CompanyRankingSummary; detail?: CompanyRankingDetail};

/** Live rankings only (a stored snapshot with entries); each ranking's Top 5 loads independently. */
async function loadRankings(signal: AbortSignal): Promise<RankingCardData[]> {
  const page = await fetchCompanyRankings({limit: RANKINGS_LIMIT}, signal);
  const live = page.items.filter(item => item.status === "ready" && item.latest_snapshot && item.latest_snapshot.entry_count > 0);
  const details = await Promise.allSettled(live.map(item => fetchCompanyRanking(item.key, {limit: RANKING_TOP}, signal)));
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  return live.map((summary, index) => {
    const result = details[index];
    return {summary, detail: result.status === "fulfilled" ? result.value : undefined};
  });
}

function SectionHeader({ id, title, link, linkText }: { id: string; title: string; link: string; linkText: string }) {
  return <div className="home-section-head">
    <h2 id={id} className="section-title">{title}</h2>
    <Link href={link}>{linkText} →</Link>
  </div>;
}

function SectionState<T>({ state, locale, children }: { state: Load<T>; locale: Locale; children: (data: T) => ReactNode }) {
  if (state.status === "loading") return <p className="empty" aria-live="polite">{common[locale].loading}</p>;
  if (state.status === "error") return <p className="empty" role="alert">{copy[locale].sectionFailed}</p>;
  return <>{children(state.data)}</>;
}

function TodayCard({ domain, locale }: { domain: Domain; locale: Locale }) {
  const state = useLoad(newsLoaders[domain]);
  const t = copy[locale];
  return <article className="ranking-card compact" aria-labelledby={`today-${domain}`}>
    <header className="list-header"><h3 id={`today-${domain}`}>{common[locale].shortDomains[domain]}</h3></header>
    <SectionState state={state} locale={locale}>
      {page => page.items.length === 0 ? <p className="empty">{t.noNews}</p> : <NewsList items={page.items.slice(0, NEWS_PER_DOMAIN)} locale={locale}/>}
    </SectionState>
    <p className="card-footer"><Link href={`/today?domain=${domain}`}>{t.viewToday} →</Link></p>
  </article>;
}

function formatMetric(entry: CompanyRankingEntry, detail: CompanyRankingDetail, locale: Locale) {
  if (detail.ranking.metric_unit === "USD" && entry.metric_value !== null) {
    return new Intl.NumberFormat(locale, {style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2}).format(entry.metric_value);
  }
  return entry.raw_value;
}

function RankingCard({ card, locale }: { card: RankingCardData; locale: Locale }) {
  const t = copy[locale];
  const { summary, detail } = card;
  const snapshot = detail?.snapshot ?? summary.latest_snapshot;
  // Movement exists only when the backend compared with an earlier snapshot; it is never inferred here.
  const hasHistory = Boolean(detail?.previous_snapshot);
  return <article className="ranking-card compact" aria-labelledby={`ranking-${summary.key}`}>
    <header className="list-header"><div>
      <h3 id={`ranking-${summary.key}`} lang={summary.primary_language}>{summary.name}</h3>
      <p>{summary.publisher} · {common[locale].shortDomains[summary.domain]} · {t.metrics[summary.metric_type] ?? summary.metric_label}</p>
      {snapshot && <p>{t.snapshot} <time dateTime={snapshot.effective_at}>{formatDateTime(snapshot.effective_at, locale)}</time></p>}
    </div></header>
    {!detail ? <p className="empty" role="alert">{t.rankingFailed}</p> : <>
      <ol className="ranking-list">{detail.entries.slice(0, RANKING_TOP).map(entry => <li key={entry.company_id}>
        <span className="rank">#{entry.rank}</span>
        <div>
          <div className="row-main"><Link href={`/company/${entry.company_id}`}>{entry.display_name}</Link>{hasHistory && entry.movement && <TrendBadge trend={entry.movement} change={entry.rank_change} locale={locale}/>}</div>
          {formatMetric(entry, detail, locale) && <p>{formatMetric(entry, detail, locale)}</p>}
        </div>
      </li>)}</ol>
      {!hasHistory && <p className="section-note">{t.notEnoughHistory}</p>}
    </>}
    <p className="card-footer"><Link href={`/rankings?domain=${summary.domain}&ranking=${encodeURIComponent(summary.key)}`}>{t.openRanking} →</Link></p>
  </article>;
}

/** A plain entry to `/search`; the Search page reads `?q=` and runs the query itself. */
function SearchEntry({ locale }: { locale: Locale }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const t = copy[locale];
  function submit(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (q.length >= 2) router.push(`/search?q=${encodeURIComponent(q)}`);
  }
  return <article className="ranking-card compact" aria-labelledby="home-search">
    <header className="list-header"><h3 id="home-search">{t.search}</h3></header>
    <p className="section-note">{t.searchNote}</p>
    <form className="home-search" role="search" onSubmit={submit}>
      <label className="visually-hidden" htmlFor="home-query">{t.searchLabel}</label>
      <input id="home-query" type="search" minLength={2} maxLength={200} required placeholder={t.searchPlaceholder} value={query} onChange={e => setQuery(e.target.value)}/>
      <button type="submit">{t.searchButton}</button>
    </form>
  </article>;
}

export default function Home() {
  const router = useRouter();
  const { locale } = useLocale();
  useEffect(() => {
    // Phase 3.1 links used `/?view=trending`; keep them working after the route split.
    const view = new URLSearchParams(window.location.search).get("view");
    if (view === "trending" || view === "today") router.replace(`/${view}`);
  }, [router]);
  const trending = useLoad<TrendingResponse>(loadTrending);
  const events = useLoad<EventsPage>(loadEvents);
  const rankings = useLoad<RankingCardData[]>(loadRankings);
  const t = copy[locale];
  const c = common[locale];
  return <main><div className="shell">
    <header className="dashboard-header">
      <h1>{t.title}</h1>
      <p className="intro">{t.intro}</p>
    </header>

    <section className="home-hero" aria-labelledby="home-trending">
      <SectionHeader id="home-trending" title={t.trending} link="/trending" linkText={t.viewTrending}/>
      <p className="section-note">{t.trendingNote}</p>
      <SectionState state={trending} locale={locale}>
        {data => <div className="card-grid home-grid">{DOMAINS.map(domain => {
          const current = data.domains.find(item => item.domain === domain);
          const items = current?.status === "ready" ? current.items.slice(0, TRENDING_TOP) : [];
          return <article className="ranking-card compact" key={domain} aria-labelledby={`trending-${domain}`}>
            <header className="list-header"><h3 id={`trending-${domain}`}>{c.shortDomains[domain]}</h3></header>
            {items.length === 0 ? <p className="empty">{t.noTrending}</p> : <ul className="trending-events">{items.map(item => <TrendingEventCard key={item.event.id} item={item} locale={locale} compact showSummary/>)}</ul>}
            <p className="card-footer"><Link href={`/trending?domain=${domain}`}>{t.viewTrending} →</Link></p>
          </article>;
        })}</div>}
      </SectionState>
    </section>

    <section aria-labelledby="home-today">
      <SectionHeader id="home-today" title={t.today} link="/today" linkText={t.viewToday}/>
      <p className="section-note">{t.todayNote}</p>
      <div className="card-grid home-grid">{DOMAINS.map(domain => <TodayCard key={domain} domain={domain} locale={locale}/>)}</div>
    </section>

    <section aria-labelledby="home-events">
      <SectionHeader id="home-events" title={t.events} link="/events" linkText={t.viewEvents}/>
      <p className="section-note">{t.eventsNote}</p>
      <article className="ranking-card compact">
        <SectionState state={events} locale={locale}>
          {page => page.items.length === 0 ? <p className="empty">{t.noEvents}</p> : <EventList items={page.items} locale={locale} showDomain compact/>}
        </SectionState>
      </article>
    </section>

    <section aria-labelledby="home-rankings">
      <SectionHeader id="home-rankings" title={t.rankings} link="/rankings" linkText={t.viewRankings}/>
      <p className="section-note">{t.rankingsNote}</p>
      <SectionState state={rankings} locale={locale}>
        {cards => cards.length === 0 ? <p className="empty">{t.noRankings}</p> : <div className="card-grid home-grid">{cards.map(card => <RankingCard key={card.summary.key} card={card} locale={locale}/>)}</div>}
      </SectionState>
    </section>

    <section aria-labelledby="home-explore">
      <div className="home-section-head"><h2 id="home-explore" className="section-title">{t.explore}</h2></div>
      <div className="card-grid home-explore">
        <article className="ranking-card compact" aria-labelledby="home-companies">
          <header className="list-header"><h3 id="home-companies">{t.companies}</h3></header>
          <p className="section-note">{t.companiesNote}</p>
          <p className="card-footer"><Link href="/companies">{t.viewCompanies} →</Link></p>
        </article>
        <SearchEntry locale={locale}/>
      </div>
    </section>
  </div></main>;
}
