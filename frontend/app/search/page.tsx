"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState, type ReactNode } from "react";

import { DiscoverTabs, DOMAINS, DomainTabs, isDomain } from "../../components/shared";
import { fetchSearch, fetchSources, type Domain, type SearchResponse, type SourceInfo } from "../../lib/api";
import { common, useLocale } from "../../lib/i18n";

const labels = {
  en: {eyebrow: "Discover", title: "Search", intro: "Find topics across languages, sources and ranking history.", query: "Search query", placeholder: "Company, game, repository or technology…", search: "Search", moreFilters: "More filters", activeFilters: (count: number) => `(${count} active)`, subject_type: "Topic type", source: "Source", language: "Content language", all: "All", loading: "Searching…", empty: "No results", error: "Search failed. Please try again.", initial: "Enter at least two characters to search topics.", results: "results", previous: "Previous", next: "Next", match: "Matched on", latest: "Latest observed rank", matches: {canonical_name: "Canonical name", alias: "Alias", source_title: "Indexed content", semantic: "Semantic similarity"}, languages: {en: "English", "zh-CN": "Chinese"}},
  "zh-CN": {eyebrow: "发现", title: "搜索", intro: "跨语言、来源与历史榜单查找主题。", query: "搜索关键词", placeholder: "公司、游戏、代码仓库或技术…", search: "搜索", moreFilters: "更多筛选", activeFilters: (count: number) => `（已启用 ${count} 项）`, subject_type: "主题类型", source: "来源", language: "内容语言", all: "全部", loading: "搜索中…", empty: "未找到结果", error: "搜索失败，请重试。", initial: "输入至少两个字符以搜索主题。", results: "条结果", previous: "上一页", next: "下一页", match: "匹配字段", latest: "最近观测排名", matches: {canonical_name: "规范名称", alias: "别名", source_title: "索引内容", semantic: "语义相似度"}, languages: {en: "英文", "zh-CN": "中文"}},
};
/** `mode` has no control: the backend default is used, and a `mode` from an older URL is passed through unchanged. */
const emptyForm = {q: "", domain: "", subject_type: "", source: "", language: "", mode: ""};
type FormKey = keyof typeof emptyForm;
const ADVANCED: FormKey[] = ["subject_type", "source", "language"];

export default function SearchPage() {
  const { locale } = useLocale();
  const [form, setForm] = useState(emptyForm);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [data, setData] = useState<SearchResponse>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const t = labels[locale];
  const c = common[locale];
  useEffect(() => {
    const restore = () => {
      const params = new URLSearchParams(window.location.search);
      const restored = Object.fromEntries(Object.entries(emptyForm).map(([key, value]) => [key, params.get(key) ?? value])) as typeof emptyForm;
      setForm(restored);
      if (ADVANCED.some(key => restored[key])) setFiltersOpen(true);
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
  function formParams(values: typeof emptyForm) {
    const params = new URLSearchParams();
    Object.entries(values).forEach(([key, value]) => { if (value.trim()) params.set(key, value.trim()); });
    return params;
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    navigate(formParams(form));
  }
  /** A domain tab re-runs the current query; without a query it only records the domain in the URL. */
  function selectDomain(domain: Domain | "") {
    const next = {...form, domain};
    setForm(next);
    const params = formParams(next);
    if (next.q.trim().length >= 2) navigate(params);
    else { window.history.pushState(null, "", params.toString() ? `/search?${params}` : "/search"); setSubmitted(null); setData(undefined); }
  }
  function paginate(offset: number) {
    const params = new URLSearchParams(submitted ?? ""); params.set("offset", String(offset)); navigate(params);
  }
  const choices = (values: Record<string, string>) => Object.entries(values).map(([value, label]) => <option key={value} value={value}>{label}</option>);
  const select = (key: FormKey, children: ReactNode) => <label key={key}>{t[key as keyof typeof t] as string}
    <select aria-label={t[key as keyof typeof t] as string} value={form[key]} onChange={e => setForm({...form, [key]: e.target.value})}>
      <option value="">{t.all}</option>{children}
    </select></label>;
  const known = sources.some(item => item.key === form.source);
  const activeAdvanced = ADVANCED.filter(key => form[key]).length;
  const domain = isDomain(form.domain) ? form.domain : undefined;
  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1>
    <DiscoverTabs current="search" domain={domain} locale={locale}/>
    <DomainTabs value={domain} onChange={selectDomain} onAll={() => selectDomain("")} locale={locale}/>
    <p className="intro">{t.intro}</p>
    <form className="search-form subject-search" onSubmit={submit}>
      <label htmlFor="query">{t.query}</label><div className="query-row"><input id="query" name="q" type="search" minLength={2} maxLength={200} required placeholder={t.placeholder} value={form.q} onChange={e => setForm({...form, q: e.target.value})}/><button type="submit">{t.search}</button></div>
      <details className="more-filters" open={filtersOpen} onToggle={e => setFiltersOpen(e.currentTarget.open)}>
        <summary>{t.moreFilters}{activeAdvanced > 0 && ` ${t.activeFilters(activeAdvanced)}`}</summary>
        <div className="search-filters">
          {select("subject_type", choices(c.subjectTypes))}
          {select("source", <>
            {form.source && !known && <option value={form.source}>{form.source}</option>}
            {DOMAINS.map(item => <optgroup key={item} label={c.shortDomains[item]}>{sources.filter(source => source.domain === item).map(source => <option key={source.key} value={source.key}>{source.name}</option>)}</optgroup>)}
          </>)}
          {select("language", choices(t.languages))}
        </div>
      </details>
    </form>
    <div aria-live="polite">
      {!submitted && <p className="notice">{t.initial}</p>}
      {loading && <p className="notice">{t.loading}</p>}
      {error && <p role="alert" className="notice error">{t.error}</p>}
      {data && <><p className="result-count">{data.total} {t.results}</p>{data.items.length === 0 && <p className="notice">{t.empty}</p>}{data.items.map(item => <article className="ranking-card" key={item.subject_id}><h2><Link href={`/subject/${item.subject_id}`} className="subject-link">{item.display_name}</Link></h2><p>{c.shortDomains[item.domain]} · {c.subjectTypes[item.subject_type] ?? item.subject_type}</p><p>{t.match}: {t.matches[item.matched_on as keyof typeof t.matches]}{item.matched_alias && ` · ${item.matched_alias}`}</p>{item.recent_title && <p>{item.recent_title}</p>}<p className="result-sources">{item.latest_sources.map(s => `${s.source} · ${t.latest} #${s.latest_rank} (${new Date(s.latest_observed_at).toLocaleDateString(locale)})`).join(" / ")}</p></article>)}<div className="pagination"><button disabled={data.offset === 0} onClick={() => paginate(Math.max(0, data.offset-data.limit))}>{t.previous}</button><button disabled={data.offset + data.limit >= data.total} onClick={() => paginate(data.offset+data.limit)}>{t.next}</button></div></>}
    </div>
  </div></main>;
}
