"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { DomainTabs, EventList, isDomain, PageState } from "../../components/shared";
import { fetchEvents, type Domain, type EventsPage, type EventType } from "../../lib/api";
import { common, useLocale } from "../../lib/i18n";

const LIMIT = 20;
const EVENT_TYPES: EventType[] = ["earnings", "product_launch", "acquisition", "funding", "regulatory", "leadership", "partnership", "security_incident", "game_release", "general"];
const copy = {
  en: {eyebrow: "Event intelligence", title: "Events", intro: "What happened: each Event groups existing news and feed items that report the same real-world occurrence, using deterministic, auditable rules. Times are when sources first and last reported it, not verified occurrence times. Source count shows coverage, not importance.", all: "All domains", type: "Event type", allTypes: "All types", showing: (from: number, to: number, total: number) => `Showing ${from}–${to} of ${total}`, empty: "No events match these filters.", emptyDomain: "Events are built only from persisted news and feed items; this domain has none yet.", previous: "Previous", next: "Next", filtered: "Showing events related to one", subject: "Subject", company: "Company", clear: "Clear"},
  "zh-CN": {eyebrow: "事件情报", title: "事件", intro: "发生了什么：每个事件把报道同一现实事项的已有新闻与快讯聚合在一起，规则确定且可审计。时间为来源首次与最近报道的时间，并非经过核实的发生时间。来源数只表示报道覆盖面，不代表重要性。", all: "全部领域", type: "事件类型", allTypes: "全部类型", showing: (from: number, to: number, total: number) => `显示 ${from}–${to} / ${total}`, empty: "没有符合条件的事件。", emptyDomain: "事件仅由已保存的新闻与快讯生成；该领域暂时没有内容。", previous: "上一页", next: "下一页", filtered: "仅显示与以下对象相关的事件：", subject: "主题", company: "公司", clear: "清除"},
} as const;

type View = {domain?: Domain; type?: EventType; subject?: string; company?: string; offset: number};
function isEventType(value: string | null): value is EventType {
  return EVENT_TYPES.includes(value as EventType);
}
function readView(): View {
  const params = new URLSearchParams(window.location.search);
  const domain = params.get("domain"); const type = params.get("type");
  const offset = Number(params.get("offset") ?? 0);
  return {
    domain: isDomain(domain) ? domain : undefined,
    type: isEventType(type) ? type : undefined,
    subject: params.get("subject_id") ?? undefined,
    company: params.get("company_id") ?? undefined,
    offset: Number.isFinite(offset) && offset >= 0 ? offset : 0,
  };
}
function url(view: View) {
  const params = new URLSearchParams();
  if (view.domain) params.set("domain", view.domain);
  if (view.type) params.set("type", view.type);
  if (view.subject) params.set("subject_id", view.subject);
  if (view.company) params.set("company_id", view.company);
  if (view.offset) params.set("offset", String(view.offset));
  return `/events${params.size ? `?${params}` : ""}`;
}

export default function Events() {
  const {locale} = useLocale(); const t = copy[locale]; const c = common[locale];
  const [view, setView] = useState<View>();
  const [page, setPage] = useState<EventsPage>(); const [error, setError] = useState<string>();
  useEffect(() => { const restore = () => setView(readView()); restore(); window.addEventListener("popstate", restore); return () => window.removeEventListener("popstate", restore); }, []);
  useEffect(() => {
    if (!view) return;
    const controller = new AbortController(); setPage(undefined); setError(undefined);
    fetchEvents({domain: view.domain, event_type: view.type, subject_id: view.subject, company_id: view.company, limit: LIMIT, offset: view.offset}, controller.signal)
      .then(setPage).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [view]);
  function navigate(next: View) { window.history.pushState(null, "", url(next)); setView(next); }
  const from = page && page.total ? page.offset + 1 : 0; const to = page ? page.offset + page.items.length : 0;
  const scoped = view?.subject || view?.company;
  return <main><div className="shell"><p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1><p className="intro">{t.intro}</p>
    <div className="search-form event-filters">
      <button className="domain-reset" type="button" aria-pressed={!view?.domain} onClick={() => view && navigate({...view, domain: undefined, offset: 0})}>{t.all}</button>
      {view && <DomainTabs value={view.domain} onChange={domain => navigate({...view, domain, offset: 0})} locale={locale} label={(domain, active) => common[active].shortDomains[domain]}/>}
      <label className="event-type-filter">{t.type}
        <select value={view?.type ?? ""} onChange={event => view && navigate({...view, type: isEventType(event.target.value) ? event.target.value : undefined, offset: 0})}>
          <option value="">{t.allTypes}</option>
          {EVENT_TYPES.map(type => <option key={type} value={type}>{c.eventTypes[type]}</option>)}
        </select>
      </label>
      {scoped && <p className="meta">{t.filtered} {view?.subject ? <Link href={`/subject/${view.subject}`}>{t.subject}</Link> : <Link href={`/company/${view?.company}`}>{t.company}</Link>} · <button type="button" className="link-button" onClick={() => view && navigate({...view, subject: undefined, company: undefined, offset: 0})}>{t.clear}</button></p>}
    </div>
    <PageState loading={!page && !error} error={error} locale={locale}/>
    {page && <>
      <p className="result-count">{t.showing(from, to, page.total)}</p>
      {page.items.length === 0 ? <p className="notice">{view?.domain && !view.type && !scoped ? t.emptyDomain : t.empty}</p> : <EventList items={page.items} locale={locale} showDomain={!view?.domain}/>}
      <div className="pagination"><button type="button" disabled={page.offset === 0} onClick={() => navigate({...view!, offset: Math.max(0, page.offset - LIMIT)})}>{t.previous}</button><button type="button" disabled={page.offset + page.items.length >= page.total} onClick={() => navigate({...view!, offset: page.offset + LIMIT})}>{t.next}</button></div>
    </>}
  </div></main>;
}
