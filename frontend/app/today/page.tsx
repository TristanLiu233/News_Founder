"use client";

import { useEffect, useState } from "react";

import { DiscoverTabs, DomainTabs, ExternalLink, InfoNote, isDomain, NewsList, PageState } from "../../components/shared";
import { fetchNews, fetchSources, fetchToday, type Domain, type NewsSource, type SourceInfo, type TodayList, type TodayResponse } from "../../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../../lib/i18n";

const PREVIEW = 5;
const NEWS_PAGE = 10;

const copy = {
  en: {eyebrow: "Discover", title: "Today", intro: "What each source is showing right now, in its own order.", about: "About this page", aboutText: "Every list keeps its source's own order and nothing is merged across sources. News feeds are chronological: position means recency, not importance. Source rankings show each publisher's own list.", latestNews: "Latest News", newsNote: "Chronological feeds: position means recency, not importance.", sourceRankings: "Source Rankings", marketPulse: "Market Pulse", marketPulseNote: "Source-native market rankings such as crypto trends, market cap and A-share turnover.", sourceCount: (count: number) => `${count} sources`, unavailable: "Source unavailable", partial: "Partial snapshot: some rows could not be parsed.", noItems: "No items yet.", noNewsSources: "No news sources for this domain yet.", updated: "Updated", feed: "7×24 feed", news: "News", loadFailed: "Could not load more items."},
  "zh-CN": {eyebrow: "发现", title: "今日", intro: "各来源此刻展示的内容，保持来源自身顺序。", about: "关于本页", aboutText: "每个榜单保持来源自身的顺序，不做跨来源合并。资讯流按时间排列：位置代表时间先后，不代表重要性。来源榜单展示各发布方自己的排行。", latestNews: "最新资讯", newsNote: "按时间排列的资讯流：位置代表时间先后，不代表重要性。", sourceRankings: "来源榜单", marketPulse: "市场动态", marketPulseNote: "来源原生的市场榜单，如加密货币热搜、市值与 A 股成交额。", sourceCount: (count: number) => `${count} 个来源`, unavailable: "来源暂不可用", partial: "部分快照：有条目未能解析。", noItems: "暂无条目。", noNewsSources: "该领域暂无资讯来源。", updated: "更新于", feed: "7×24 快讯", news: "新闻", loadFailed: "无法加载更多条目。"},
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
    {list.status !== "ready" ? <p className="empty">{t.unavailable}</p> : list.items.length === 0 ? <p className="empty">{t.noItems}</p> :
      <ol className="ranking-list">{items.map(item => <li key={item.rank}><span className="rank">#{item.rank}</span><div>
        {item.url ? <ExternalLink href={item.url} locale={locale}>{item.title}</ExternalLink> : <strong>{item.title}</strong>}
        <p>{formatMetric(item.raw_metric, locale)} · {list.metric_label}</p>
      </div></li>)}</ol>}
    {list.partial && <p className="partial">{t.partial}</p>}
    {list.items.length > PREVIEW && <button type="button" className="link-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? c.showLess : `${c.viewMore} (${list.items.length - PREVIEW})`}</button>}
  </article>;
}

function NewsSourceCard({ feed, domain, locale }: { feed: NewsSource; domain: Domain; locale: Locale }) {
  const [items, setItems] = useState(feed.items);
  const [expanded, setExpanded] = useState(false);
  const [exhausted, setExhausted] = useState(feed.items.length < PREVIEW);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const t = copy[locale];
  const c = common[locale];
  async function more() {
    if (!expanded && items.length > PREVIEW) { setExpanded(true); return; }
    setLoading(true); setFailed(false);
    try {
      // Chronological pagination through the existing `/news` limit/offset API.
      // Only this card's feed (a publisher may also have ranked articles of another type).
      const page = await fetchNews({domain, source: feed.source, content_type: feed.content_type, limit: NEWS_PAGE, offset: items.length});
      const known = new Set(items.map(item => item.id));
      setItems([...items, ...page.items.filter(item => !known.has(item.id))]);
      setExhausted(page.items.length < NEWS_PAGE);
      setExpanded(true);
    } catch { setFailed(true); } finally { setLoading(false); }
  }
  const visible = expanded ? items : items.slice(0, PREVIEW);
  const canLoad = !exhausted || (!expanded && items.length > PREVIEW);
  return <article className="ranking-card">
    <header className="list-header"><div><h3>{feed.source}</h3><p>{feed.content_type === "feed" ? t.feed : t.news}</p></div></header>
    {items.length === 0 ? <p className="empty">{t.noItems}</p> : <NewsList items={visible} locale={locale}/>}
    {failed && <p className="partial">{t.loadFailed}</p>}
    <div className="card-actions">
      {canLoad && <button type="button" className="link-button" disabled={loading} onClick={more}>{loading ? c.loading : c.viewMore}</button>}
      {expanded && <button type="button" className="link-button" onClick={() => setExpanded(false)}>{c.showLess}</button>}
    </div>
  </article>;
}

/** Computer and Gaming news: the domain's `news`/`feed` sources from `/sources`, each read through `/news`. */
async function loadDomainNews(domain: Domain, sources: SourceInfo[], signal: AbortSignal): Promise<NewsSource[]> {
  const feeds = sources.filter(source => source.domain === domain && (source.content_type === "news" || source.content_type === "feed"));
  return Promise.all(feeds.map(async source => {
    const contentType = source.content_type as NewsSource["content_type"];
    const page = await fetchNews({domain, source: source.name, content_type: contentType, limit: PREVIEW}, signal);
    return {source_key: source.key, source: source.name, content_type: contentType, status: "ready", items: page.items};
  }));
}

function LatestNews({ feeds, domain, locale }: { feeds: NewsSource[]; domain: Domain; locale: Locale }) {
  const t = copy[locale];
  return <section className="domain" aria-labelledby="latest-news">
    <div className="domain-heading"><h2 id="latest-news">{t.latestNews}</h2><span>{t.sourceCount(feeds.length)}</span></div>
    <p className="section-note">{t.newsNote}</p>
    {feeds.length === 0 ? <p className="empty">{t.noNewsSources}</p> :
      <div className="card-grid">{feeds.map(feed => <NewsSourceCard key={feed.source_key} feed={feed} domain={domain} locale={locale}/>)}</div>}
  </section>;
}

export default function TodayPage() {
  const { locale } = useLocale();
  const [domain, setDomain] = useState<Domain>("computer");
  const [data, setData] = useState<TodayResponse>();
  const [error, setError] = useState<string>();
  const [sources, setSources] = useState<SourceInfo[]>();
  const [news, setNews] = useState<Partial<Record<Domain, NewsSource[]>>>({});
  const [newsError, setNewsError] = useState<string>();
  useEffect(() => {
    const restore = () => { const value = new URLSearchParams(window.location.search).get("domain"); setDomain(isDomain(value) ? value : "computer"); };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    fetchToday(controller.signal).then(setData).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    fetchSources(controller.signal).then(setSources).catch(reason => { if (!controller.signal.aborted) setNewsError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    // Finance news comes with `/today`; Computer and Gaming news is loaded per domain on demand.
    if (domain === "finance" || !sources || news[domain]) return;
    const controller = new AbortController();
    setNewsError(undefined);
    loadDomainNews(domain, sources, controller.signal)
      .then(feeds => setNews(current => ({...current, [domain]: feeds})))
      .catch(reason => { if (!controller.signal.aborted) setNewsError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [domain, sources, news]);
  function select(value: Domain) { setDomain(value); window.history.pushState(null, "", `/today?domain=${value}`); }
  const t = copy[locale];
  const lists = data?.domains.find(item => item.domain === domain)?.lists ?? [];
  const grid = <div className="card-grid">{lists.map(list => <RankingListCard key={list.source_key} list={list} locale={locale}/>)}</div>;
  const domainNews = news[domain];
  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1>
    <DiscoverTabs current="today" domain={domain} locale={locale}/>
    <DomainTabs value={domain} onChange={select} locale={locale}/>
    <p className="intro">{t.intro}</p>
    <InfoNote title={t.about}><p>{t.aboutText}</p></InfoNote>
    {domain === "finance" ? <>
      <PageState loading={!data} error={error} locale={locale}/>
      {data && <>
        <LatestNews feeds={data.finance_news} domain="finance" locale={locale}/>
        <details className="domain secondary">
          <summary><span className="summary-title">{t.marketPulse}</span> <span className="summary-meta">{t.sourceCount(lists.length)}</span></summary>
          <p className="section-note">{t.marketPulseNote}</p>
          {grid}
        </details>
      </>}
    </> : <>
      <PageState loading={!domainNews && !newsError} error={newsError} locale={locale}/>
      {domainNews && <LatestNews feeds={domainNews} domain={domain} locale={locale}/>}
      <section className="domain" aria-labelledby="source-rankings">
        <div className="domain-heading"><h2 id="source-rankings">{t.sourceRankings}</h2>{data && <span>{t.sourceCount(lists.length)}</span>}</div>
        <PageState loading={!data} error={error} locale={locale}/>
        {data && grid}
      </section>
    </>}
  </div></main>;
}
