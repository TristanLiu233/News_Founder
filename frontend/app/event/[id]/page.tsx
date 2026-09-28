"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ExternalLink, PageState } from "../../../components/shared";
import { ApiError, fetchEvent, fetchEventContent, type EventContentItem, type EventDetail } from "../../../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../../../lib/i18n";

const TIMELINE_PAGE = 10;
const copy = {
  en: {eyebrow: "Event", overview: "Overview", type: "Event type", timeBasis: "Times are source publication times (first and last report), not verified occurrence times.", coverage: "Coverage", reportsLabel: "Reports", sourcesLabel: "Sources", languagesLabel: "Languages", subjects: "Related Subjects", noSubjects: "No deterministic Subject relation.", companies: "Related Companies", noCompanies: "No deterministic Company relation.", entityNote: "Derived only from the reports' existing deterministic subject links.", timeline: "Timeline", timelineNote: "Each report as published by its source, oldest first, linking to the original article.", notFound: "Event not found", notFoundNote: "No Event or merge redirect exists for this ID.", allEvents: "Browse all events", loadFailed: "Could not load this section.", coverageNote: "Source count is coverage, not importance."},
  "zh-CN": {eyebrow: "事件", overview: "概览", type: "事件类型", timeBasis: "时间为来源发布时间（首次与最近报道），并非经过核实的发生时间。", coverage: "报道覆盖", reportsLabel: "报道数", sourcesLabel: "来源数", languagesLabel: "语言", subjects: "相关主题", noSubjects: "暂无确定性主题关联。", companies: "相关公司", noCompanies: "暂无确定性公司关联。", entityNote: "仅来自报道已有的确定性主题关联。", timeline: "时间线", timelineNote: "按来源发布时间从早到晚列出每篇报道，并链接到原文。", notFound: "未找到该事件", notFoundNote: "该 ID 没有对应的事件或合并重定向。", allEvents: "浏览全部事件", loadFailed: "该部分加载失败。", coverageNote: "来源数只表示报道覆盖面，不代表重要性。"},
} as const;

function Timeline({ eventId, total, locale }: { eventId: string; total: number; locale: Locale }) {
  const [items, setItems] = useState<EventContentItem[]>();
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setItems(undefined); setFailed(false);
    fetchEventContent(eventId, {limit: TIMELINE_PAGE, order: "asc"}, controller.signal)
      .then(page => setItems(page.items))
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [eventId]);
  async function more() {
    if (!items) return;
    setLoading(true);
    try {
      const page = await fetchEventContent(eventId, {limit: TIMELINE_PAGE, offset: items.length, order: "asc"});
      const known = new Set(items.map(item => item.id));
      setItems([...items, ...page.items.filter(item => !known.has(item.id))]);
    } catch { setFailed(true); } finally { setLoading(false); }
  }
  const t = copy[locale]; const c = common[locale];
  return <section className="detail-section" aria-labelledby="timeline"><h2 id="timeline" className="section-title">{t.timeline} ({total})</h2>
    <article className="ranking-card">
      <p className="section-note">{t.timelineNote}</p>
      {failed && <p className="empty">{t.loadFailed}</p>}
      {!items && !failed && <p className="empty">{c.loading}</p>}
      {items && <ol className="event-timeline">{items.map(item => <li key={item.id}>
        <time dateTime={item.published_at ?? item.observed_at}>{formatDateTime(item.published_at ?? item.observed_at, locale)}</time>
        <div><ExternalLink href={item.url} locale={locale}><span lang={item.language}>{item.title}</span></ExternalLink>
          <p className="meta">{item.source} · {c.languages[item.language] ?? item.language}</p></div>
      </li>)}</ol>}
      {items && items.length < total && <button type="button" className="link-button" disabled={loading} onClick={more}>{loading ? c.loading : c.viewMore}</button>}
    </article>
  </section>;
}

export default function EventPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { locale } = useLocale();
  const [detail, setDetail] = useState<EventDetail>();
  const [status, setStatus] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState<string>();
  const loaded = useRef<EventDetail | undefined>(undefined);
  useEffect(() => {
    // After a redirect replace, the canonical detail is already loaded.
    if (loaded.current?.id === id) return;
    const controller = new AbortController();
    setStatus("loading"); setDetail(undefined);
    fetchEvent(id, controller.signal).then(value => {
      loaded.current = value;
      setDetail(value); setStatus("ready");
      // One permanent URL per canonical Event: replace merged duplicate IDs.
      if (value.id !== id) router.replace(`/event/${value.id}`);
    }).catch(reason => {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 422)) { setStatus("missing"); return; }
      setError(reason instanceof Error ? reason.message : String(reason)); setStatus("error");
    });
    return () => controller.abort();
  }, [id, router]);
  const t = copy[locale]; const c = common[locale];
  if (status === "missing") return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.notFound}</h1><p className="intro">{t.notFoundNote}</p>
    <p><Link href="/events">{t.allEvents} →</Link></p>
  </div></main>;
  return <main><div className="shell">
    <PageState loading={status === "loading"} error={status === "error" ? error : undefined} locale={locale}/>
    {detail && <>
      <p className="eyebrow">{t.eyebrow} · {c.shortDomains[detail.domain]}{detail.event_type !== "general" && ` · ${c.eventTypes[detail.event_type] ?? detail.event_type}`}</p>
      <h1 className="subject-title" lang={detail.title_language}>{detail.canonical_title}</h1>
      <div className="detail-grid">
        <section className="ranking-card"><h2>{t.overview}</h2><dl className="facts">
          <dt>{c.firstReported}</dt><dd><time dateTime={detail.time.first_published_at}>{formatDateTime(detail.time.first_published_at, locale)}</time></dd>
          <dt>{c.lastReported}</dt><dd><time dateTime={detail.time.last_published_at}>{formatDateTime(detail.time.last_published_at, locale)}</time></dd>
          <dt>{t.type}</dt><dd>{c.eventTypes[detail.event_type] ?? detail.event_type}</dd>
        </dl><p className="section-note">{t.timeBasis}</p></section>
        <section className="ranking-card"><h2>{t.coverage}</h2><dl className="facts">
          <dt>{t.reportsLabel}</dt><dd>{detail.content_count}</dd>
          <dt>{t.sourcesLabel}</dt><dd>{detail.sources.map(source => `${source.name} (${source.content_count})`).join(" · ")}</dd>
          <dt>{t.languagesLabel}</dt><dd>{detail.languages.map(item => `${c.languages[item.language] ?? item.language} (${item.content_count})`).join(" · ")}</dd>
        </dl><p className="section-note">{t.coverageNote}</p></section>
      </div>
      <div className="detail-grid">
        <section className="detail-section" aria-labelledby="event-subjects"><h2 id="event-subjects" className="section-title">{t.subjects}</h2>
          {detail.subjects.length ? <ul className="chips">{detail.subjects.map(subject => <li key={subject.id}><Link href={`/subject/${subject.id}`}>{subject.display_names[locale]}</Link></li>)}</ul> : <p className="empty">{t.noSubjects}</p>}
        </section>
        <section className="detail-section" aria-labelledby="event-companies"><h2 id="event-companies" className="section-title">{t.companies}</h2>
          {detail.companies.length ? <ul className="chips">{detail.companies.map(company => <li key={company.id}><Link href={`/company/${company.id}`}>{company.display_names[locale]}</Link></li>)}</ul> : <p className="empty">{t.noCompanies}</p>}
        </section>
      </div>
      <p className="section-note">{t.entityNote}</p>
      <Timeline eventId={detail.id} total={detail.content_count} locale={locale}/>
    </>}
  </div></main>;
}
