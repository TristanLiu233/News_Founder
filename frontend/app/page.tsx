"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { NewsList, TrendBadge, trendingDomainLabel } from "../components/shared";
import { fetchNews, fetchTrending, type NewsItem, type TrendingResponse } from "../lib/api";
import { common, useLocale } from "../lib/i18n";

const HIGHLIGHTS = 5;
const LATEST_NEWS = 5;

const copy = {
  en: {eyebrow: "Compact intelligence dashboard", title: "Industry Intelligence", intro: "Top 5 cross-platform attention per domain and the latest finance news. Scores are never compared across domains.", highlights: "Trending highlights", latestNews: "Latest Finance News", viewTrending: "View Trending", viewToday: "View Today", notRanked: "No composite ranking yet.", noNews: "No finance news yet."},
  "zh-CN": {eyebrow: "精简情报概览", title: "行业情报", intro: "各领域综合关注度 Top 5 与最新财经资讯。不同领域的分数互不比较。", highlights: "趋势精选", latestNews: "最新财经资讯", viewTrending: "查看综合趋势", viewToday: "查看今日", notRanked: "暂无综合排名。", noNews: "暂无财经资讯。"},
} as const;

export default function Home() {
  const router = useRouter();
  const { locale } = useLocale();
  const [trending, setTrending] = useState<TrendingResponse>();
  const [news, setNews] = useState<NewsItem[]>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    // Phase 3.1 links used `/?view=trending`; keep them working after the route split.
    const view = new URLSearchParams(window.location.search).get("view");
    if (view === "trending" || view === "today") router.replace(`/${view}`);
  }, [router]);
  useEffect(() => {
    const controller = new AbortController();
    const fail = (reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); };
    fetchTrending(controller.signal, {limit: HIGHLIGHTS}).then(setTrending).catch(fail);
    fetchNews({domain: "finance", limit: LATEST_NEWS}, controller.signal).then(page => setNews(page.items)).catch(fail);
    return () => controller.abort();
  }, []);
  const t = copy[locale];
  const c = common[locale];
  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1><p className="intro">{t.intro}</p>
    {error && <p role="alert" className="notice error">{c.unavailable} <small>{error}</small></p>}
    <section aria-labelledby="highlights"><h2 id="highlights" className="section-title">{t.highlights}</h2>
      {!trending && !error && <p className="notice">{c.loading}</p>}
      <div className="card-grid">{trending?.domains.map(domain => <article className="ranking-card compact" key={domain.domain}>
        <header className="list-header"><h3>{trendingDomainLabel(domain.domain, locale)}</h3></header>
        {domain.items.length === 0 ? <p className="empty">{t.notRanked}</p> : <ol className="ranking-list">{domain.items.slice(0, HIGHLIGHTS).map(item => <li key={item.subject_id}>
          <span className="rank">#{item.rank}</span>
          <div className="row-main"><Link href={`/subject/${item.subject_id}`}>{item.display_names[locale]}</Link><TrendBadge trend={item.trend} change={item.rank_change} locale={locale}/></div>
        </li>)}</ol>}
        <p className="card-footer"><Link href={`/trending?domain=${domain.domain}`}>{t.viewTrending} →</Link></p>
      </article>)}</div>
    </section>
    <section aria-labelledby="latest-news"><h2 id="latest-news" className="section-title">{t.latestNews}</h2>
      <article className="ranking-card compact">
        {!news && !error && <p className="empty">{c.loading}</p>}
        {news?.length === 0 && <p className="empty">{t.noNews}</p>}
        {news && news.length > 0 && <NewsList items={news.slice(0, LATEST_NEWS)} locale={locale}/>}
        <p className="card-footer"><Link href="/today?domain=finance">{t.viewToday} →</Link></p>
      </article>
    </section>
  </div></main>;
}
