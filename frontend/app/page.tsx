"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { DOMAINS, EventList, NewsList, TrendBadge, TrendingTopicSummary } from "../components/shared";
import {
  fetchCompanyRanking, fetchCompanyRankings, fetchEvents, fetchNews, fetchTrending,
  type CompanyRankingDetail, type CompanyRankingEntry, type CompanyRankingSummary, type Domain, type EventsPage,
  type NewsPage, type TrendingResponse,
} from "../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../lib/i18n";

/**
 * Home composes existing read APIs only: latest news per domain, the latest Events (backend order),
 * the current Trending Top 5 per domain, and live external company rankings. Nothing is scored here.
 */
const NEWS_PER_DOMAIN = 3;
const LATEST_EVENTS = 5;
const TRENDING_TOP = 5;
const RANKING_TOP = 5;
// The API maximum for the ranking list; only a handful of rankings are registered.
const RANKINGS_LIMIT = 100;

const copy = {
  en: {
    title: "Industry Intelligence",
    intro: "Latest reports, events, trending topics and external company rankings for Computer, Finance and Gaming.",
    domainLinks: "Open Today by domain",
    today: "Today at a Glance", todayNote: "Latest reports from each domain's news sources, newest first.", viewToday: "View Today", noNews: "No reports for this domain yet.",
    events: "Latest Events", eventsNote: "Most recently reported events across all domains.", viewEvents: "View all events", noEvents: "No events yet.",
    trending: "Trending Topics", viewTrending: "View Trending", noTrending: "No trending topics for this domain yet.",
    rankings: "External Company Rankings", rankingsNote: "Rankings are shown as published by their original sources.", viewRankings: "View all rankings",
    noRankings: "No external rankings have been collected yet.", rankingFailed: "This ranking could not be loaded.",
    snapshot: "As of", notEnoughHistory: "Not enough history yet", openRanking: "Open ranking",
    metrics: {market_cap: "Market cap", revenue: "Revenue", total_assets: "Total assets"} as Record<string, string>,
    sectionFailed: "This section is unavailable right now.",
  },
  "zh-CN": {
    title: "行业情报",
    intro: "汇集计算机、金融、游戏三个领域的最新报道、事件、热门主题与外部公司排行榜。",
    domainLinks: "按领域打开今日",
    today: "今日速览", todayNote: "各领域资讯来源的最新报道，按时间由新到旧。", viewToday: "查看今日", noNews: "该领域暂无报道。",
    events: "最近事件", eventsNote: "各领域最近被报道的事件。", viewEvents: "查看全部事件", noEvents: "暂无事件。",
    trending: "热门主题", viewTrending: "查看综合趋势", noTrending: "该领域暂无热门主题。",
    rankings: "外部公司排行榜", rankingsNote: "排行榜按原始来源发布的内容原样展示。", viewRankings: "查看全部排行榜",
    noRankings: "尚未采集到外部排行榜。", rankingFailed: "该榜单暂时无法加载。",
    snapshot: "数据日期", notEnoughHistory: "尚无足够历史数据", openRanking: "打开榜单",
    metrics: {market_cap: "市值", revenue: "营收", total_assets: "总资产"} as Record<string, string>,
    sectionFailed: "该部分暂时无法加载。",
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

export default function Home() {
  const router = useRouter();
  const { locale } = useLocale();
  useEffect(() => {
    // Phase 3.1 links used `/?view=trending`; keep them working after the route split.
    const view = new URLSearchParams(window.location.search).get("view");
    if (view === "trending" || view === "today") router.replace(`/${view}`);
  }, [router]);
  const events = useLoad<EventsPage>(loadEvents);
  const trending = useLoad<TrendingResponse>(loadTrending);
  const rankings = useLoad<RankingCardData[]>(loadRankings);
  const t = copy[locale];
  const c = common[locale];
  return <main><div className="shell">
    <header className="dashboard-header">
      <h1>{t.title}</h1>
      <p className="intro">{t.intro}</p>
      <nav aria-label={t.domainLinks}><ul className="domain-links">
        {DOMAINS.map(domain => <li key={domain}><Link href={`/today?domain=${domain}`}>{c.shortDomains[domain]}</Link></li>)}
      </ul></nav>
    </header>

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

    <section aria-labelledby="home-trending">
      <SectionHeader id="home-trending" title={t.trending} link="/trending" linkText={t.viewTrending}/>
      <SectionState state={trending} locale={locale}>
        {data => <div className="card-grid home-grid">{DOMAINS.map(domain => {
          const current = data.domains.find(item => item.domain === domain);
          const items = current?.status === "ready" ? current.items.slice(0, TRENDING_TOP) : [];
          return <article className="ranking-card compact" key={domain} aria-labelledby={`trending-${domain}`}>
            <header className="list-header"><h3 id={`trending-${domain}`}>{c.shortDomains[domain]}{domain === "finance" && ` · ${c.marketPulse}`}</h3></header>
            {items.length === 0 ? <p className="empty">{t.noTrending}</p> : <ol className="ranking-list">{items.map(item => <li key={item.subject_id}>
              <span className="rank">#{item.rank}</span>
              <div><TrendingTopicSummary item={item} locale={locale}/></div>
            </li>)}</ol>}
            <p className="card-footer"><Link href={`/trending?domain=${domain}`}>{t.viewTrending} →</Link></p>
          </article>;
        })}</div>}
      </SectionState>
    </section>

    <section aria-labelledby="home-rankings">
      <SectionHeader id="home-rankings" title={t.rankings} link="/rankings" linkText={t.viewRankings}/>
      <p className="section-note">{t.rankingsNote}</p>
      <SectionState state={rankings} locale={locale}>
        {cards => cards.length === 0 ? <p className="empty">{t.noRankings}</p> : <div className="card-grid home-grid">{cards.map(card => <RankingCard key={card.summary.key} card={card} locale={locale}/>)}</div>}
      </SectionState>
    </section>
  </div></main>;
}
