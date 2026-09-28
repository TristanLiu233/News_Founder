"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { DomainTabs, isDomain, PageState } from "../../components/shared";
import { fetchCompanies, type CompaniesPage, type Domain } from "../../lib/api";
import { common, useLocale } from "../../lib/i18n";

const LIMIT = 30;
const copy = {
  en: {eyebrow: "Company intelligence", title: "Companies", intro: "Find ranked companies by canonical, legal, localized or source-published name. Company search is separate from Subject search.", search: "Search companies", placeholder: "Company, alias, ticker, CIK or LEI", submit: "Search", all: "All domains", profile: "Profile available", limited: "Ranking identity only", rankings: "Current rankings", showing: (from: number, to: number, total: number) => `Showing ${from}–${to} of ${total}`, empty: "No companies match these filters.", previous: "Previous", next: "Next"},
  "zh-CN": {eyebrow: "公司情报", title: "公司", intro: "按标准名称、法定名称、本地化名称或来源名称查找已上榜公司。公司搜索与 Subject 搜索互相独立。", search: "搜索公司", placeholder: "公司、别名、股票代码、CIK 或 LEI", submit: "搜索", all: "全部领域", profile: "已有公司资料", limited: "仅排行榜身份", rankings: "当前榜单", showing: (from: number, to: number, total: number) => `显示 ${from}–${to} / ${total}`, empty: "没有符合条件的公司。", previous: "上一页", next: "下一页"},
} as const;

type View = {q: string; domain?: Domain; offset: number};
function readView(): View {
  const params = new URLSearchParams(window.location.search);
  const domain = params.get("domain");
  const offset = Number(params.get("offset") ?? 0);
  return {q: params.get("q") ?? "", domain: isDomain(domain) ? domain : undefined, offset: Number.isFinite(offset) && offset >= 0 ? offset : 0};
}
function url(view: View) {
  const params = new URLSearchParams();
  if (view.q) params.set("q", view.q);
  if (view.domain) params.set("domain", view.domain);
  if (view.offset) params.set("offset", String(view.offset));
  return `/companies${params.size ? `?${params}` : ""}`;
}

export default function Companies() {
  const {locale} = useLocale(); const t = copy[locale];
  const [view, setView] = useState<View>(); const [query, setQuery] = useState("");
  const [page, setPage] = useState<CompaniesPage>(); const [error, setError] = useState<string>();
  useEffect(() => { const restore = () => { const next = readView(); setView(next); setQuery(next.q); }; restore(); window.addEventListener("popstate", restore); return () => window.removeEventListener("popstate", restore); }, []);
  useEffect(() => { if (!view) return; const controller = new AbortController(); setPage(undefined); setError(undefined); fetchCompanies({q: view.q || undefined, domain: view.domain, limit: LIMIT, offset: view.offset}, controller.signal).then(setPage).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }); return () => controller.abort(); }, [view]);
  function navigate(next: View) { window.history.pushState(null, "", url(next)); setView(next); }
  function submit(event: FormEvent) { event.preventDefault(); navigate({q: query.trim(), domain: view?.domain, offset: 0}); }
  const from = page && page.total ? page.offset + 1 : 0; const to = page ? page.offset + page.items.length : 0;
  return <main><div className="shell"><p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1><p className="intro">{t.intro}</p>
    <form className="search-form company-search" onSubmit={submit}><label htmlFor="company-query">{t.search}</label><div className="query-row"><input id="company-query" value={query} placeholder={t.placeholder} onChange={event => setQuery(event.target.value)}/><button type="submit">{t.submit}</button></div>
      <button className="domain-reset" type="button" aria-pressed={!view?.domain} onClick={() => view && navigate({...view, domain: undefined, offset: 0})}>{t.all}</button>
      {view && <DomainTabs value={view.domain} onChange={domain => navigate({...view, domain, offset: 0})} locale={locale} label={(domain, active) => common[active].shortDomains[domain]}/>} 
    </form>
    <PageState loading={!page && !error} error={error} locale={locale}/>
    {page && <><p className="result-count">{t.showing(from, to, page.total)}</p>{page.items.length === 0 ? <p className="notice">{t.empty}</p> : <ul className="company-directory">{page.items.map(company => <li key={company.id}>
      <div><Link className="company-name" href={`/company/${company.id}`}>{company.display_names[locale]}</Link>{locale === "zh-CN" && company.display_names[locale] !== company.canonical_name && <span className="company-meta">{company.canonical_name}</span>}</div>
      <div className="company-summary">{company.industry && <span>{company.industry}</span>}{company.country && <span>{company.country}</span>}{company.categories.map(item => <span key={item}>{item}</span>)}</div>
      <span className={`tag ${company.has_profile ? "current" : ""}`}>{company.has_profile ? t.profile : t.limited}</span>
      {company.rankings.length > 0 && <p className="meta">{t.rankings}: {company.rankings.map(item => `${common[locale].shortDomains[item.domain]} #${item.rank}`).join(" · ")}</p>}
    </li>)}</ul>}
      <div className="pagination"><button type="button" disabled={page.offset === 0} onClick={() => navigate({...view!, offset: Math.max(0, page.offset - LIMIT)})}>{t.previous}</button><button type="button" disabled={page.offset + page.items.length >= page.total} onClick={() => navigate({...view!, offset: page.offset + LIMIT})}>{t.next}</button></div></>}
  </div></main>;
}
