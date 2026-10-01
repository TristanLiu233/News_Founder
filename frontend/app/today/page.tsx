"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { DiscoverTabs, DomainTabs, EventList, ExternalLink, InfoNote, isDomain, PageState } from "../../components/shared";
import { fetchToday, fetchTodayEvents, type Domain, type TodayEventsDomain, type TodayList, type TodayResponse } from "../../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../../lib/i18n";

/**
 * Today (Product-2) answers "what is new?": the newest canonical Events of one domain, latest report
 * first, within 24 hours before the latest collection. Recency only — popular Events are on Trending.
 * Each source's own current list (stories, repositories, games, Market Pulse) stays available in a
 * collapsed secondary section, loaded only when opened.
 */
const DEFAULT_LIMIT = 10;
const EXPANDED_LIMIT = 30;
const PREVIEW = 5;

const copy = {
  en: {
    eyebrow: "Discover", title: "Today",
    intro: "The newest events in this domain, latest report first.",
    collected: (hours: number, date: string) => `Reports published in the ${hours} hours before the latest collection (${date}).`,
    showing: (shown: number, total: number) => `Showing ${shown} of ${total} events`,
    showMore: (count: number) => `Show up to ${count}`, showFewer: "Show fewer", allEvents: "Browse all events",
    empty: "No new events in the 24 hours before the latest collection.",
    unavailable: "No reports have been collected for this domain yet.",
    about: "About this page",
    aboutText: [
      "Each row is one event: reports about the same occurrence from one or more sources are shown once.",
      "The order is the time of the latest report, newest first. It is not a judgement of importance; events that publishers rank as popular are on Trending.",
      "The 24-hour window ends at the latest collection, so the list does not change between daily updates.",
    ],
    sourceRankings: "Source rankings", marketPulse: "Market Pulse",
    rankingsNote: "Each source's own current list, in its own order: stories, repositories and games. Popular articles are combined into events on Trending.",
    marketPulseNote: "Source-native market rankings such as crypto trends, market cap and A-share turnover.",
    sourceCount: (count: number) => `${count} sources`, unavailableList: "Source unavailable", partial: "Partial snapshot: some rows could not be parsed.", noItems: "No items yet.", updated: "Updated",
  },
  "zh-CN": {
    eyebrow: "发现", title: "今日",
    intro: "该领域最新的事件，按最近报道时间由新到旧排列。",
    collected: (hours: number, date: string) => `最近一次采集（${date}）前 ${hours} 小时内发布的报道。`,
    showing: (shown: number, total: number) => `显示 ${shown} / ${total} 个事件`,
    showMore: (count: number) => `最多显示 ${count} 个`, showFewer: "收起", allEvents: "浏览全部事件",
    empty: "最近一次采集前 24 小时内没有新事件。",
    unavailable: "该领域尚未采集到报道。",
    about: "关于本页",
    aboutText: [
      "每一行是一个事件：一个或多个来源对同一事件的报道只显示一次。",
      "排序依据最近一篇报道的时间，由新到旧，不代表重要性；发布方榜单上的热门事件见“热门”。",
      "24 小时窗口以最近一次采集为终点，因此在两次每日更新之间列表保持不变。",
    ],
    sourceRankings: "来源榜单", marketPulse: "市场动态",
    rankingsNote: "各来源当前的自有榜单，保持来源自身顺序：文章、代码仓库与游戏。热门文章会在“热门”中归并为事件。",
    marketPulseNote: "来源原生的市场榜单，如加密货币热搜、市值与 A 股成交额。",
    sourceCount: (count: number) => `${count} 个来源`, unavailableList: "来源暂不可用", partial: "部分快照：有条目未能解析。", noItems: "暂无条目。", updated: "更新于",
  },
} as const;

function formatMetric(value: TodayList["items"][number]["raw_metric"], locale: Locale) {
  if (value === undefined || value === null) return "—";
  return typeof value === "number" ? value.toLocaleString(locale) : String(value);
}

function RankingListCard({ list, locale }: { list: TodayList; locale: Locale }) {
  const [expanded, setExpanded] = useState(false);
  const t = copy[locale];
  const c = common[locale];
  const items = expanded ? list.items : list.items.slice(0, PREVIEW);
  return <article className="ranking-card">
    <header className="list-header"><div><h3>{list.source}</h3><p>{list.list_name}</p></div>{list.observed_at && <time dateTime={list.observed_at}>{t.updated} {formatDateTime(list.observed_at, locale)}</time>}</header>
    {list.status !== "ready" ? <p className="empty">{t.unavailableList}</p> : list.items.length === 0 ? <p className="empty">{t.noItems}</p> :
      <ol className="ranking-list">{items.map(item => <li key={item.rank}><span className="rank">#{item.rank}</span><div>
        {item.url ? <ExternalLink href={item.url} locale={locale}>{item.title}</ExternalLink> : <strong>{item.title}</strong>}
        <p>{formatMetric(item.raw_metric, locale)} · {list.metric_label}</p>
      </div></li>)}</ol>}
    {list.partial && <p className="partial">{t.partial}</p>}
    {list.items.length > PREVIEW && <button type="button" className="link-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? c.showLess : `${c.viewMore} (${list.items.length - PREVIEW})`}</button>}
  </article>;
}

/** The domain's source lists, requested only the first time the section is opened. */
function SourceLists({ domain, locale }: { domain: Domain; locale: Locale }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<TodayResponse>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!open || data) return;
    const controller = new AbortController();
    fetchToday(controller.signal).then(setData).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [open, data]);
  const t = copy[locale];
  const lists = data?.domains.find(item => item.domain === domain)?.lists ?? [];
  const finance = domain === "finance";
  return <details className="domain secondary" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><span className="summary-title">{finance ? t.marketPulse : t.sourceRankings}</span>{data && <> <span className="summary-meta">{t.sourceCount(lists.length)}</span></>}</summary>
    <p className="section-note">{finance ? t.marketPulseNote : t.rankingsNote}</p>
    {open && <PageState loading={!data} error={error} locale={locale}/>}
    {data && <div className="card-grid">{lists.map(list => <RankingListCard key={list.source_key} list={list} locale={locale}/>)}</div>}
  </details>;
}

export default function TodayPage() {
  const { locale } = useLocale();
  const [domain, setDomain] = useState<Domain>();
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [data, setData] = useState<TodayEventsDomain & {limit: number; windowHours: number}>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const restore = () => { const value = new URLSearchParams(window.location.search).get("domain"); setDomain(isDomain(value) ? value : "computer"); setLimit(DEFAULT_LIMIT); };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    if (!domain) return;
    const controller = new AbortController();
    setError(undefined);
    fetchTodayEvents({domain, limit}, controller.signal)
      .then(response => setData({...response.domains[0], limit, windowHours: response.window_hours}))
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [domain, limit]);
  function select(value: Domain) { setDomain(value); setLimit(DEFAULT_LIMIT); setData(undefined); window.history.pushState(null, "", `/today?domain=${value}`); }
  const t = copy[locale];
  const current = data && data.domain === domain && data.limit === limit ? data : undefined;
  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1>
    <DiscoverTabs current="today" domain={domain} locale={locale}/>
    {domain && <DomainTabs value={domain} onChange={select} locale={locale}/>}
    <p className="intro">{t.intro}</p>
    <InfoNote title={t.about}>{t.aboutText.map(text => <p key={text}>{text}</p>)}</InfoNote>
    <PageState loading={!current && !error} error={error} locale={locale}/>
    {current && domain && <section className="domain" aria-labelledby="today-heading">
      <div className="domain-heading"><h2 id="today-heading">{common[locale].shortDomains[domain]}</h2></div>
      {current.status !== "ready" ? <p className="notice">{t.unavailable}</p> : <>
        {current.collected_at && <p className="section-note">{t.collected(current.windowHours, formatDateTime(current.collected_at, locale))}</p>}
        {current.items.length === 0 ? <p className="notice">{t.empty}</p> : <article className="ranking-card"><EventList items={current.items} locale={locale}/></article>}
        <div className="pagination">
          {current.items.length > 0 && <span className="result-count">{t.showing(current.items.length, current.total)}</span>}
          {current.total > current.items.length && limit < EXPANDED_LIMIT && <button type="button" onClick={() => setLimit(EXPANDED_LIMIT)}>{t.showMore(EXPANDED_LIMIT)}</button>}
          {limit > DEFAULT_LIMIT && <button type="button" onClick={() => setLimit(DEFAULT_LIMIT)}>{t.showFewer}</button>}
          <Link href={`/events?domain=${domain}`}>{t.allEvents} →</Link>
        </div>
      </>}
    </section>}
    {domain && <SourceLists key={domain} domain={domain} locale={locale}/>}
  </div></main>;
}
