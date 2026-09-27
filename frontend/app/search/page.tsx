"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState, type ReactNode } from "react";

import { DOMAINS } from "../../components/shared";
import { fetchSearch, fetchSources, type SearchResponse, type SourceInfo } from "../../lib/api";
import { useLocale } from "../../lib/i18n";

const labels = {
  en: {archive: "Industry archive", title: "Search across the archive", intro: "Find canonical subjects across languages, sources and historical rankings.", query: "Search query", placeholder: "Company, game, repository or technology…", search: "Search", domain: "Domain", subject_type: "Subject type", source: "Source", language: "Content language", mode: "Search mode", all: "All", loading: "Searching…", empty: "No results", error: "Search failed. Please try again.", fallback: "Semantic search unavailable. Showing keyword results.", initial: "Enter at least two characters to search the archive.", results: "results", previous: "Previous", next: "Next", match: "Matched on", latest: "Latest observed rank", types: {event: "Event", company: "Company", stock: "Stock", game: "Game", repository: "Repository", paper: "Paper", model: "Model", technology: "Technology"}, domains: {computer: "Computer", finance: "Finance", gaming: "Gaming"}, modes: {hybrid: "Hybrid", lexical: "Keyword", semantic: "Semantic"}, matches: {canonical_name: "Canonical name", alias: "Alias", source_title: "Indexed content", semantic: "Semantic similarity"}, languages: {en: "English", "zh-CN": "Chinese"}},
  "zh-CN": {archive: "行业信息档案", title: "搜索历史信息", intro: "跨语言、来源与历史榜单，查找统一的规范主题。", query: "搜索关键词", placeholder: "公司、游戏、代码仓库或技术…", search: "搜索", domain: "领域", subject_type: "主题类型", source: "来源", language: "内容语言", mode: "搜索模式", all: "全部", loading: "搜索中…", empty: "未找到结果", error: "搜索失败，请重试。", fallback: "语义搜索当前不可用。正在显示关键词搜索结果。", initial: "输入至少两个字符以搜索历史信息。", results: "条结果", previous: "上一页", next: "下一页", match: "匹配字段", latest: "最近观测排名", types: {event: "事件", company: "公司", stock: "股票", game: "游戏", repository: "代码仓库", paper: "论文", model: "模型", technology: "技术"}, domains: {computer: "计算机", finance: "金融", gaming: "游戏"}, modes: {hybrid: "混合检索", lexical: "关键词", semantic: "语义"}, matches: {canonical_name: "规范名称", alias: "别名", source_title: "索引内容", semantic: "语义相似度"}, languages: {en: "英文", "zh-CN": "中文"}},
};
const emptyForm = {q: "", domain: "", subject_type: "", source: "", language: "", mode: "hybrid"};
type FormKey = keyof typeof emptyForm;

export default function SearchPage() {
  const { locale } = useLocale();
  const [form, setForm] = useState(emptyForm);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [data, setData] = useState<SearchResponse>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const t = labels[locale];
  useEffect(() => {
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
    // The filter is built from the backend registry: machine keys as values, brands as labels.
    const controller = new AbortController();
    fetchSources(controller.signal).then(items => {
      const searchable = items.filter(item => item.searchable);
      setSources(searchable);
      // Older URLs carried display names; show them as the equivalent stable key.
      setForm(current => {
        const legacy = searchable.find(item => item.name === current.source);
        return legacy ? {...current, source: legacy.key} : current;
      });
    }).catch(() => undefined);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!submitted) return;
    const controller = new AbortController();
    setLoading(true); setError(false); setData(undefined);
    const params = new URLSearchParams(submitted); params.set("locale", locale);
    fetchSearch(params, controller.signal)
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
  const choices = (values: Record<string, string>) => Object.entries(values).map(([value, label]) => <option key={value} value={value}>{label}</option>);
  const select = (key: FormKey, children: ReactNode) => <label key={key}>{t[key as keyof typeof t] as string}
    <select aria-label={t[key as keyof typeof t] as string} value={form[key]} onChange={e => setForm({...form, [key]: e.target.value})}>
      {key !== "mode" && <option value="">{t.all}</option>}{children}
    </select></label>;
  const known = sources.some(item => item.key === form.source);
  return <main><div className="shell">
    <p className="eyebrow">{t.archive}</p><h1>{t.title}</h1><p className="intro">{t.intro}</p>
    <form className="search-form" onSubmit={submit}>
      <label htmlFor="query">{t.query}</label><div className="query-row"><input id="query" name="q" type="search" minLength={2} maxLength={200} required placeholder={t.placeholder} value={form.q} onChange={e => setForm({...form, q: e.target.value})}/><button type="submit">{t.search}</button></div>
      <div className="search-filters">
        {select("domain", choices(t.domains))}
        {select("subject_type", choices(t.types))}
        {select("source", <>
          {form.source && !known && <option value={form.source}>{form.source}</option>}
          {DOMAINS.map(domain => <optgroup key={domain} label={t.domains[domain]}>{sources.filter(item => item.domain === domain).map(item => <option key={item.key} value={item.key}>{item.name}</option>)}</optgroup>)}
        </>)}
        {select("language", choices(t.languages))}
        {select("mode", choices(t.modes))}
      </div>
    </form>
    <div aria-live="polite">
      {!submitted && <p className="notice">{t.initial}</p>}
      {loading && <p className="notice">{t.loading}</p>}
      {error && <p role="alert" className="notice error">{t.error}</p>}
      {data && <>{!data.semantic_search_available && <p className="notice">{t.fallback}</p>}<p className="result-count">{data.total} {t.results} · {t.modes[data.mode_used as keyof typeof t.modes]}</p>{data.items.length === 0 && <p className="notice">{t.empty}</p>}{data.items.map(item => <article className="ranking-card" key={item.subject_id}><h2><Link href={`/subject/${item.subject_id}`} className="subject-link">{item.display_name}</Link></h2><p>{t.domains[item.domain]} · {t.types[item.subject_type as keyof typeof t.types]}</p><p>{t.match}: {t.matches[item.matched_on as keyof typeof t.matches]}{item.matched_alias && ` · ${item.matched_alias}`}</p>{item.recent_title && <p>{item.recent_title}</p>}<p className="result-sources">{item.latest_sources.map(s => `${s.source} · ${t.latest} #${s.latest_rank} (${new Date(s.latest_observed_at).toLocaleDateString(locale)})`).join(" / ")}</p></article>)}<div className="pagination"><button disabled={data.offset === 0} onClick={() => paginate(Math.max(0, data.offset-data.limit))}>{t.previous}</button><button disabled={data.offset + data.limit >= data.total} onClick={() => paginate(data.offset+data.limit)}>{t.next}</button></div></>}
    </div>
  </div></main>;
}
