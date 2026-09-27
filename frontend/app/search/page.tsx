"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { API_BASE_URL } from "../../lib/api";

type Locale = "en" | "zh-CN";
type Result = { subject_id: string; display_name: string; canonical_name: string; domain: string;
  subject_type: string; matched_on: string; matched_alias: string | null; recent_title: string | null;
  latest_sources: {source: string; latest_rank: number; latest_observed_at: string}[] };
type Response = {items: Result[]; total: number; offset: number; limit: number;
  mode_used: string; semantic_search_available: boolean};
const labels = {
  en: {archive: "Industry archive", today: "Today", trending: "Trending", title: "Search across the archive", intro: "Find canonical subjects across languages, sources and historical rankings.", query: "Search query", placeholder: "Company, game, repository or technology…", search: "Search", domain: "Domain", subject_type: "Subject type", source: "Source", language: "Content language", mode: "Search mode", all: "All", loading: "Searching…", empty: "No results", error: "Search failed. Please try again.", fallback: "Semantic search unavailable. Showing keyword results.", initial: "Enter at least two characters to search the archive.", results: "results", previous: "Previous", next: "Next", match: "Matched on", latest: "Latest observed rank", types: {event: "Event", company: "Company", stock: "Stock", game: "Game", repository: "Repository", paper: "Paper", model: "Model", technology: "Technology"}, domains: {computer: "Computer", finance: "Finance", gaming: "Gaming"}, modes: {hybrid: "Hybrid", lexical: "Keyword", semantic: "Semantic"}, matches: {canonical_name: "Canonical name", alias: "Alias", source_title: "Indexed content", semantic: "Semantic similarity"}},
  "zh-CN": {archive: "行业信息档案", today: "今日榜单", trending: "综合趋势", title: "搜索历史信息", intro: "跨语言、来源与历史榜单，查找统一的规范主题。", query: "搜索关键词", placeholder: "公司、游戏、代码仓库或技术…", search: "搜索", domain: "领域", subject_type: "主题类型", source: "来源", language: "内容语言", mode: "搜索模式", all: "全部", loading: "搜索中…", empty: "未找到结果", error: "搜索失败，请重试。", fallback: "语义搜索当前不可用。正在显示关键词搜索结果。", initial: "输入至少两个字符以搜索历史信息。", results: "条结果", previous: "上一页", next: "下一页", match: "匹配字段", latest: "最近观测排名", types: {event: "事件", company: "公司", stock: "股票", game: "游戏", repository: "代码仓库", paper: "论文", model: "模型", technology: "技术"}, domains: {computer: "计算机", finance: "金融", gaming: "游戏"}, modes: {hybrid: "混合检索", lexical: "关键词", semantic: "语义"}, matches: {canonical_name: "规范名称", alias: "别名", source_title: "索引内容", semantic: "语义相似度"}},
};
const sourceOptions = ["Hacker News", "GitHub", "IT之家", "虎嗅", "CoinGecko", "CoinPaprika", "东方财富", "新浪财经", "Steam", "itch.io", "游民星空", "3DM"];
const emptyForm = {q: "", domain: "", subject_type: "", source: "", language: "", mode: "hybrid"};

export default function SearchPage() {
  const [locale, setLocale] = useState<Locale>("en");
  const [form, setForm] = useState(emptyForm);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [data, setData] = useState<Response>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const t = labels[locale];
  useEffect(() => {
    const saved = localStorage.getItem("locale");
    if (saved === "en" || saved === "zh-CN") setLocale(saved);
    const restore = () => {
      const params = new URLSearchParams(window.location.search);
      setForm(Object.fromEntries(Object.entries(emptyForm).map(([key, value]) => [key, params.get(key) ?? value])) as typeof emptyForm);
      setSubmitted(params.get("q") ? params.toString() : null);
    };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    if (!submitted) return;
    const controller = new AbortController();
    setLoading(true); setError(false); setData(undefined);
    const params = new URLSearchParams(submitted); params.set("locale", locale);
    fetch(`${API_BASE_URL}/api/v1/search?${params}`, {signal: controller.signal, cache: "no-store"})
      .then(async response => { if (!response.ok) throw new Error(); return response.json(); })
      .then(setData).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [submitted, locale]);
  function navigate(params: URLSearchParams) {
    window.history.pushState(null, "", `/search?${params}`); setSubmitted(params.toString());
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams();
    Object.entries(form).forEach(([key, value]) => { if (value.trim()) params.set(key, value.trim()); });
    navigate(params);
  }
  function paginate(offset: number) {
    const params = new URLSearchParams(submitted ?? ""); params.set("offset", String(offset)); navigate(params);
  }
  const changeLocale = () => { const value = locale === "en" ? "zh-CN" : "en"; setLocale(value); localStorage.setItem("locale", value); };
  const options: Record<string, Record<string,string>> = {domain: t.domains, subject_type: t.types, source: Object.fromEntries(sourceOptions.map(s => [s,s])), language: {en: locale === "en" ? "English" : "英文", "zh-CN": locale === "en" ? "Chinese" : "中文"}, mode: t.modes};
  return <main><div className="shell">
    <nav><Link href="/">Industry Intelligence</Link><span><Link href="/">{t.today}</Link> <Link href="/?view=trending">{t.trending}</Link> <Link href="/search" aria-current="page">{t.search}</Link> <button onClick={changeLocale}>{locale === "en" ? "中文" : "EN"}</button></span></nav>
    <p className="eyebrow">{t.archive}</p><h1>{t.title}</h1><p className="intro">{t.intro}</p>
    <form className="search-form" onSubmit={submit}>
      <label htmlFor="query">{t.query}</label><div className="query-row"><input id="query" name="q" type="search" minLength={2} maxLength={200} required placeholder={t.placeholder} value={form.q} onChange={e => setForm({...form, q: e.target.value})}/><button type="submit">{t.search}</button></div>
      <div className="search-filters">{Object.entries(options).map(([key, values]) => <label key={key}>{t[key as keyof typeof emptyForm & keyof typeof t] as string}<select aria-label={t[key as keyof typeof emptyForm & keyof typeof t] as string} value={form[key as keyof typeof form]} onChange={e => setForm({...form, [key]: e.target.value})}>{key !== "mode" && <option value="">{t.all}</option>}{Object.entries(values).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}</div>
    </form>
    <div aria-live="polite">
      {!submitted && <p className="notice">{t.initial}</p>}
      {loading && <p className="notice">{t.loading}</p>}
      {error && <p role="alert" className="notice error">{t.error}</p>}
      {data && <>{!data.semantic_search_available && <p className="notice">{t.fallback}</p>}<p className="result-count">{data.total} {t.results} · {t.modes[data.mode_used as keyof typeof t.modes]}</p>{data.items.length === 0 && <p className="notice">{t.empty}</p>}{data.items.map(item => <article className="ranking-card" key={item.subject_id}><h2>{item.display_name}</h2><p>{t.domains[item.domain as keyof typeof t.domains]} · {t.types[item.subject_type as keyof typeof t.types]}</p><p>{t.match}: {t.matches[item.matched_on as keyof typeof t.matches]}{item.matched_alias && ` · ${item.matched_alias}`}</p>{item.recent_title && <p>{item.recent_title}</p>}<p className="result-sources">{item.latest_sources.map(s => `${s.source} · ${t.latest} #${s.latest_rank} (${new Date(s.latest_observed_at).toLocaleDateString(locale)})`).join(" / ")}</p></article>)}<div className="pagination"><button disabled={data.offset === 0} onClick={() => paginate(Math.max(0, data.offset-data.limit))}>{t.previous}</button><button disabled={data.offset + data.limit >= data.total} onClick={() => paginate(data.offset+data.limit)}>{t.next}</button></div></>}
    </div>
  </div></main>;
}
