"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState, type ReactNode } from "react";

import { DiscoverTabs, DOMAINS, DomainTabs, EventList, isDomain } from "../../components/shared";
import { fetchCompanies, fetchEvents, fetchSearch, fetchSources, type CompaniesPage, type Domain, type EventsPage, type SearchResponse, type SourceInfo } from "../../lib/api";
import { common, useLocale } from "../../lib/i18n";

const EVENT_LIMIT = 5;
const COMPANY_LIMIT = 5;
const labels = {
  en: {eyebrow: "Discover", title: "Search", intro: "Find events by headline, and topics and companies by name in English or Chinese.", query: "Search query", placeholder: "Headline, company, game, coin or repository…", search: "Search", moreFilters: "Topic filters", activeFilters: (count: number) => `(${count} active)`, subject_type: "Topic type", source: "Source", language: "Content language", all: "All", loading: "Searching…", empty: "No events, topics or companies match this search.", error: "Search failed. Please try again.", sectionError: "This part of the results could not be loaded.", initial: "Enter at least two characters.", count: (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`, previous: "Previous", next: "Next", match: "Matched on", latest: "Latest observed rank", matches: {canonical_name: "Name", alias: "Other name", source_title: "Recent headline", semantic: "Similar meaning"}, languages: {en: "English", "zh-CN": "Chinese"},
    events: "Events", eventsNote: "Headline matches, most recently reported first.", moreEvents: (shown: number, total: number) => `Showing ${shown} of ${total} matching events.`, event: ["event", "events"], topics: "Topics", topic: ["topic", "topics"], companies: "Companies", company: ["company", "companies"], inRankings: (count: number) => `In ${count} external ${count === 1 ? "ranking" : "rankings"}`, profiled: "Profile available"},
  "zh-CN": {eyebrow: "发现", title: "搜索", intro: "按标题查找事件，按中英文名称查找主题与公司。", query: "搜索关键词", placeholder: "标题、公司、游戏、加密货币或代码仓库…", search: "搜索", moreFilters: "主题筛选", activeFilters: (count: number) => `（已启用 ${count} 项）`, subject_type: "主题类型", source: "来源", language: "内容语言", all: "全部", loading: "搜索中…", empty: "没有匹配的事件、主题或公司。", error: "搜索失败，请重试。", sectionError: "这部分结果暂时无法加载。", initial: "请输入至少两个字符。", count: (count: number) => `${count} 个`, previous: "上一页", next: "下一页", match: "匹配", latest: "最近观测排名", matches: {canonical_name: "名称", alias: "别名", source_title: "近期标题", semantic: "含义相近"}, languages: {en: "英文", "zh-CN": "中文"},
    events: "事件", eventsNote: "标题匹配，按最近报道时间由新到旧。", moreEvents: (shown: number, total: number) => `显示 ${shown} / ${total} 个匹配事件。`, event: ["", ""], topics: "主题", topic: ["", ""], companies: "公司", company: ["", ""], inRankings: (count: number) => `出现在 ${count} 个外部排行榜`, profiled: "有公司资料"},
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
  const [events, setEvents] = useState<EventsPage | null>();
  const [companies, setCompanies] = useState<CompaniesPage | null>();
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
    setLoading(true); setError(false); setData(undefined); setEvents(undefined); setCompanies(undefined);
    const params = new URLSearchParams(submitted); params.set("locale", locale);
    const q = params.get("q") ?? "";
    const scope = isDomain(params.get("domain")) ? params.get("domain") as Domain : undefined;
    // Three separate lookups, each shown in its own section; one failing never hides the others.
    // Events and companies are only on the first page of topic results.
    const first = !Number(params.get("offset") ?? 0);
    fetchSearch(params, controller.signal)
      .then(setData).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    if (first) {
      fetchEvents({q, domain: scope, limit: EVENT_LIMIT}, controller.signal).then(setEvents).catch(() => { if (!controller.signal.aborted) setEvents(null); });
      fetchCompanies({q, domain: scope, limit: COMPANY_LIMIT}, controller.signal).then(setCompanies).catch(() => { if (!controller.signal.aborted) setCompanies(null); });
    } else { setEvents({items: [], total: 0, limit: EVENT_LIMIT, offset: 0}); setCompanies({items: [], total: 0, limit: COMPANY_LIMIT, offset: 0}); }
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
      {data && events !== undefined && companies !== undefined && data.total === 0 && !events?.total && !companies?.total && <p className="notice">{t.empty}</p>}
      {events === null && !error && <p role="alert" className="notice error">{t.sectionError}</p>}
      {events && events.items.length > 0 && <section className="detail-section" aria-labelledby="search-events">
        <h2 id="search-events" className="section-title">{t.events} <span className="section-count">{t.count(events.total, ...t.event as [string, string])}</span></h2>
        <p className="section-note">{t.eventsNote}</p>
        <article className="ranking-card"><EventList items={events.items} locale={locale} showDomain={!domain} compact/></article>
        {events.total > events.items.length && <p className="section-note">{t.moreEvents(events.items.length, events.total)}</p>}
      </section>}
      {data && data.items.length > 0 && <section className="detail-section" aria-labelledby="search-topics">
        <h2 id="search-topics" className="section-title">{t.topics} <span className="section-count">{t.count(data.total, ...t.topic as [string, string])}</span></h2>
        {data.items.map(item => <article className="ranking-card search-result" key={item.subject_id}><h3><Link href={`/subject/${item.subject_id}`} className="subject-link">{item.display_name}</Link></h3><p>{c.shortDomains[item.domain]} · {c.subjectTypes[item.subject_type] ?? item.subject_type}</p><p>{t.match}{locale === "zh-CN" ? "：" : ": "}{t.matches[item.matched_on as keyof typeof t.matches] ?? item.matched_on}{item.matched_alias && ` · ${item.matched_alias}`}</p>{item.recent_title && <p>{item.recent_title}</p>}<p className="result-sources">{item.latest_sources.map(s => `${s.source} · ${t.latest} #${s.latest_rank} (${new Date(s.latest_observed_at).toLocaleDateString(locale)})`).join(" / ")}</p></article>)}
        {data.total > data.limit && <div className="pagination"><button disabled={data.offset === 0} onClick={() => paginate(Math.max(0, data.offset-data.limit))}>{t.previous}</button><button disabled={data.offset + data.limit >= data.total} onClick={() => paginate(data.offset+data.limit)}>{t.next}</button></div>}
      </section>}
      {companies === null && !error && <p role="alert" className="notice error">{t.sectionError}</p>}
      {companies && companies.items.length > 0 && <section className="detail-section" aria-labelledby="search-companies">
        <h2 id="search-companies" className="section-title">{t.companies} <span className="section-count">{t.count(companies.total, ...t.company as [string, string])}</span></h2>
        <article className="ranking-card"><ul className="event-list">{companies.items.map(company => <li key={company.id}>
          <Link className="event-title" href={`/company/${company.id}`}>{company.display_names[locale]}</Link>
          <p className="meta">{company.rankings.length > 0 && <span>{t.inRankings(company.rankings.length)}</span>}{company.has_profile && <span>{t.profiled}</span>}</p>
        </li>)}</ul></article>
      </section>}
    </div>
  </div></main>;
}
