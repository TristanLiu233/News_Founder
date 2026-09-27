"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_BASE_URL, fetchToday, fetchTrending, type NewsSource, type TodayList, type TodayResponse, type TrendingResponse } from "../lib/api";

type Locale = "en" | "zh-CN";
const copy = {
  en: { today: "Today", trending: "Trending", titleToday: "What is happening today", titleTrending: "Cross-platform attention", introToday: "Latest news and source-native rankings, kept semantically distinct.", introTrending: "Canonical subjects fused with freshness-adjusted reciprocal rank fusion.", sources: "sources", unavailable: "Source unavailable", loading: "Loading…", breakdown: "Source breakdown", latestNews: "Latest News", marketPulse: "Market Pulse", relatedNews: "Related News", openOriginal: "Open Original ↗", viewMore: "View More", coverage: {chinese: "Chinese sources", international: "International sources", both: "Chinese + International"}, trend: {up: "Up", down: "Down", unchanged: "Unchanged", new: "New"}, domains: {computer: "Computer / AI / Technology", finance: "Finance", gaming: "Gaming"}},
  "zh-CN": { today: "今日榜单", trending: "综合趋势", titleToday: "今日动态", titleTrending: "跨平台关注趋势", introToday: "最新资讯与来源原生榜单按真实语义分开展示。", introTrending: "通过带时效性的倒数排名融合生成规范主题综合榜。", sources: "个来源", unavailable: "来源暂不可用", loading: "加载中…", breakdown: "来源明细", latestNews: "最新资讯", marketPulse: "市场动态", relatedNews: "相关新闻", openOriginal: "查看原文 ↗", viewMore: "查看更多", coverage: {chinese: "中文来源", international: "国际来源", both: "中文 + 国际来源"}, trend: {up: "上升", down: "下降", unchanged: "不变", new: "新上榜"}, domains: {computer: "计算机 / AI / 科技", finance: "金融", gaming: "游戏"}},
} as const;

function TodayListCard({ list, locale }: { list: TodayList; locale: Locale }) {
  const t = copy[locale];
  return <article className="ranking-card"><header className="list-header"><div><h3>{list.source}</h3><p>{list.list_name}</p></div></header>
    {list.status !== "ready" ? <p>{t.unavailable}</p> : <ol className="ranking-list">{list.items.slice(0, 5).map(item => <li key={item.rank}><span className="rank">#{item.rank}</span><div>{item.url ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a> : <strong>{item.title}</strong>}<p>{item.raw_metric ?? "—"} · {list.metric_label}</p></div></li>)}</ol>}</article>;
}

function NewsCard({ feed, locale }: { feed: NewsSource; locale: Locale }) {
  const t = copy[locale];
  return <article className="ranking-card"><header className="list-header"><div><h3>{feed.source}</h3><p>{feed.content_type === "feed" ? "7×24" : t.latestNews}</p></div></header>
    {feed.items.length === 0 ? <p>{t.unavailable}</p> : <ol className="ranking-list">{feed.items.map(item => <li key={item.id}><div><a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a><p>{new Date(item.published_at ?? item.observed_at).toLocaleString(locale)} · {t.openOriginal}</p></div></li>)}</ol>}</article>;
}

export default function Home() {
  const [locale, setLocale] = useState<Locale>("en");
  const [view, setView] = useState<"today" | "trending">("today");
  const [today, setToday] = useState<TodayResponse>();
  const [trending, setTrending] = useState<TrendingResponse>();
  const [error, setError] = useState<string>();
  useEffect(() => { if (new URLSearchParams(window.location.search).get("view") === "trending") setView("trending"); const saved = localStorage.getItem("locale"); if (saved === "en" || saved === "zh-CN") setLocale(saved); }, []);
  useEffect(() => { const controller = new AbortController(); Promise.all([fetchToday(controller.signal), fetchTrending(controller.signal)]).then(([a,b]) => { setToday(a); setTrending(b); }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Backend unavailable"); }); return () => controller.abort(); }, []);
  const changeLocale = (value: Locale) => { setLocale(value); localStorage.setItem("locale", value); };
  const t = copy[locale];
  return <main><div className="shell">
    <nav>Industry Intelligence <span><button onClick={() => setView("today")}>{t.today}</button> <button onClick={() => setView("trending")}>{t.trending}</button> <Link href="/search">{locale === "en" ? "Search" : "搜索"}</Link> <button onClick={() => changeLocale(locale === "en" ? "zh-CN" : "en")}>{locale === "en" ? "中文" : "EN"}</button></span></nav>
    <p className="eyebrow">12-source intelligence</p><h1>{view === "today" ? t.titleToday : t.titleTrending}</h1><p className="intro">{view === "today" ? t.introToday : t.introTrending}</p>
    {!today && !error && <div className="notice">{t.loading}</div>}{error && <div className="notice error">{error}</div>}
    {view === "today" ? today?.domains.map(domain => <section className="domain" key={domain.domain}><div className="domain-heading"><h2>{t.domains[domain.domain]}</h2><span>{domain.lists.length} {t.sources}</span></div>{domain.domain === "finance" && <><h2>{t.latestNews}</h2>{today.finance_news.map(feed => <NewsCard key={feed.source_key} feed={feed} locale={locale}/>)}{API_BASE_URL && <p><a href={`${API_BASE_URL}/api/v1/news?domain=finance&limit=20`} target="_blank" rel="noopener noreferrer">{t.viewMore}</a></p>}<h2>{t.marketPulse}</h2></>}{domain.lists.map(list => <TodayListCard key={list.source_key} list={list} locale={locale}/>)}</section>) : trending?.domains.map(domain => <section className="domain" key={domain.domain}><div className="domain-heading"><h2>{domain.domain === "finance" ? t.marketPulse : t.domains[domain.domain]}</h2><span>{domain.items.length}</span></div>{domain.items.slice(0, 20).map(item => <article className="ranking-card" key={item.subject_id}><header className="list-header"><div><h3>#{item.rank} {item.localized_names[locale] ?? item.canonical_name}</h3><p>{item.source_count} {t.sources} · {t.coverage[item.language_coverage]} · {t.trend[item.trend]} {item.rank_change ? `${item.rank_change > 0 ? "+" : ""}${item.rank_change}` : ""}</p></div><strong>{item.final_score.toFixed(6)}</strong></header><details><summary>{t.breakdown}</summary><ol className="ranking-list">{item.sources.map(source => <li key={source.source_key}><span className="rank">#{source.source_rank}</span><div><strong>{source.source}</strong><p>{source.rank_list} · weight {source.weight} · freshness {source.freshness.toFixed(3)} · contribution {source.contribution.toFixed(6)}</p></div></li>)}</ol></details>{item.related_news_count > 0 && <details><summary>{t.relatedNews} ({item.related_news_count})</summary><ul className="ranking-list">{item.related_news_preview.map(news => <li key={news.id}><div><a href={news.url} target="_blank" rel="noopener noreferrer">{news.title}</a><p>{news.source} · {new Date(news.published_at ?? news.observed_at).toLocaleString(locale)}</p></div></li>)}</ul></details>}</article>)}</section>)}
  </div></main>;
}
